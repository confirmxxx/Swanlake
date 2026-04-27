"""Operator acks for remote-only sync surfaces.

Architecture problem this module solves
---------------------------------------
The reconciler's freshness model assumes every sync engine writes a
local timestamp into ``last-sync.json``. That works for surfaces the
reconciler itself owns (vault, claude_md), but breaks for surfaces
synced by remote Claude Routines that have no filesystem access to the
operator's machine — most notably the Notion master page, which is
written by the security-watchdog Routine via the Notion MCP. Result:
``swanlake status`` shows a permanent ``notion: missing`` ALARM even
though the routine has been firing successfully.

Fix: a one-line operator ack. After confirming the routine ran, the
operator records an ack and the status reader treats the surface as
fresh until the ack itself ages past ``FRESH_WINDOW``. Forgotten acks
do NOT permanently mute the alarm — they age out exactly like a real
sync would, so the failure mode is "loud again in 24h", never "silent
forever".

Storage: append-only JSONL at ``~/.swanlake/reconciler-acks.jsonl``.
Append-only because acks are an audit trail; we want every ack ever
recorded to remain inspectable. The status reader folds the most
recent ack per surface into its freshness calculation.

Surface classification: optional ``[surfaces]`` table in
``~/.swanlake/config.toml`` mapping surface name -> sync class
(``local`` or ``remote``). Defaults are baked in for the three v0.3
surfaces so operators with older configs see the right behavior
without editing TOML: notion -> remote, vault/claude_md -> local.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# Default state root: kept in sync with swanlake.state.DEFAULT_STATE_ROOT
# but resolved independently so this module stays importable without the
# swanlake package (the reconciler ships standalone).
DEFAULT_STATE_ROOT = Path.home() / ".swanlake"
ACKS_FILENAME = "reconciler-acks.jsonl"

# Surfaces classified as REMOTE by default. Operators can override or
# extend by writing a [surfaces] table in ~/.swanlake/config.toml.
DEFAULT_REMOTE_SURFACES = frozenset({"notion"})

# Sync-class vocabulary used in config + ack records.
CLASS_LOCAL = "local"
CLASS_REMOTE = "remote"
VALID_CLASSES = frozenset({CLASS_LOCAL, CLASS_REMOTE})


@dataclass(frozen=True)
class Ack:
    """A single operator ack record."""

    surface: str
    synced_at: datetime  # the time the remote sync actually happened
    acked_at: datetime  # the time the operator recorded the ack
    note: str = ""


class UnknownSurface(ValueError):
    """Raised when ack is requested for a surface not in the config map."""


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _state_root() -> Path:
    """Resolve the active state root.

    Honors ``SWANLAKE_STATE_ROOT`` so tests + ``swanlake --state-root`` work.
    Importing ``swanlake.state`` would create a hard cycle (swanlake -> reconciler
    -> swanlake), so we replicate the env-var precedence locally.
    """
    env = os.environ.get("SWANLAKE_STATE_ROOT")
    if env:
        return Path(env).expanduser()
    return DEFAULT_STATE_ROOT


def acks_path(state_root: Path | None = None) -> Path:
    """Return the absolute path to the acks JSONL."""
    root = state_root if state_root is not None else _state_root()
    return root / ACKS_FILENAME


# ---------------------------------------------------------------------------
# Surface classification
# ---------------------------------------------------------------------------


def _config_path(state_root: Path | None = None) -> Path:
    root = state_root if state_root is not None else _state_root()
    return root / "config.toml"


def load_surface_classes(state_root: Path | None = None) -> dict[str, str]:
    """Return a {surface_name: class} mapping from config + defaults.

    Precedence: config-file ``[surfaces]`` table overrides defaults. An
    invalid class string in the config is dropped with a stderr warning;
    the default for that surface is kept so a typo can't accidentally
    re-enable the missing-alarm.
    """
    classes: dict[str, str] = {}
    # Bake defaults first so the config can override piecewise.
    for s in DEFAULT_REMOTE_SURFACES:
        classes[s] = CLASS_REMOTE
    # Reconciler's known local surfaces. Imported lazily to avoid cycles.
    try:
        from reconciler.status import SURFACES as _KNOWN
    except Exception:  # pragma: no cover - defensive
        _KNOWN = ("claude_md", "notion", "vault")
    for s in _KNOWN:
        classes.setdefault(s, CLASS_LOCAL)

    cfg = _config_path(state_root)
    if not cfg.exists():
        return classes

    try:
        with cfg.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        # Config unreadable: stick to defaults. A broken config should
        # not flip a remote surface back to "local" silently.
        return classes

    surfaces_tbl = data.get("surfaces")
    if not isinstance(surfaces_tbl, dict):
        return classes

    for name, value in surfaces_tbl.items():
        if not isinstance(value, str):
            continue
        v = value.strip().lower()
        # Tolerate operator shorthand: "cloud" is an alias for "remote".
        if v == "cloud":
            v = CLASS_REMOTE
        if v in VALID_CLASSES:
            classes[name] = v
        else:
            print(
                f"reconciler: ignoring unknown surface class "
                f"{value!r} for {name!r} in {cfg} "
                f"(valid: {sorted(VALID_CLASSES)})",
                file=sys.stderr,
            )
    return classes


def remote_surfaces(state_root: Path | None = None) -> tuple[str, ...]:
    """Return the tuple of surface names classified as remote, sorted."""
    classes = load_surface_classes(state_root)
    return tuple(sorted(s for s, c in classes.items() if c == CLASS_REMOTE))


def is_remote(surface: str, state_root: Path | None = None) -> bool:
    """True if ``surface`` is classified as remote in the active config."""
    return load_surface_classes(state_root).get(surface) == CLASS_REMOTE


# ---------------------------------------------------------------------------
# Ack write path
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp; accept the trailing ``Z`` shorthand.

    Always returns a tz-aware UTC datetime so ack ordering is well-defined.
    Raises ``ValueError`` on unparseable input — the CLI surfaces that as
    a usage error rather than a silent skip.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("empty timestamp")
    s = value.strip()
    # ``fromisoformat`` in 3.11+ accepts ``Z`` natively, but we normalize
    # for clarity and for the rare ``...+00:00Z`` typo.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        # Naive timestamp -> assume UTC (the operator is logging UTC; the
        # alternative is silent local-time interpretation which would
        # corrupt the ack ordering across timezones).
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _atomic_append_jsonl(path: Path, record: dict) -> None:
    """Append a JSON line atomically under fcntl.

    JSONL is append-only by design but we still take an exclusive flock
    on a sidecar lockfile so two parallel ``swanlake reconciler ack``
    invocations cannot interleave bytes mid-line.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    line = json.dumps(record, sort_keys=True, default=str) + "\n"
    with open(lock_path, "w") as lock_fp:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        finally:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)


def write_ack(
    surface: str,
    synced_at: datetime | None = None,
    note: str = "",
    state_root: Path | None = None,
) -> Ack:
    """Record an ack for ``surface``.

    The ack carries two timestamps: ``synced_at`` is the operator's claim
    about when the remote sync actually happened (defaults to now);
    ``acked_at`` is the time the ack record was written. Keeping both
    lets the audit log answer "when did the operator notice" separately
    from "when did the remote sync happen".

    Raises ``UnknownSurface`` if the surface is not in the config map.
    The CLI translates that to a usage error so a typo (``swanlake
    reconciler ack notin``) fails loudly instead of recording an ack
    against a name nobody ever reads.
    """
    classes = load_surface_classes(state_root)
    if surface not in classes:
        known = sorted(classes)
        raise UnknownSurface(
            f"unknown surface {surface!r}; known: {known}"
        )
    when_synced = synced_at or _utcnow()
    if when_synced.tzinfo is None:
        when_synced = when_synced.replace(tzinfo=timezone.utc)
    when_acked = _utcnow()
    record = {
        "surface": surface,
        "synced_at": when_synced.isoformat(),
        "acked_at": when_acked.isoformat(),
        "note": note,
        "class": classes[surface],
    }
    _atomic_append_jsonl(acks_path(state_root), record)
    return Ack(
        surface=surface,
        synced_at=when_synced,
        acked_at=when_acked,
        note=note,
    )


# ---------------------------------------------------------------------------
# Ack read path
# ---------------------------------------------------------------------------


def _iter_ack_lines(path: Path) -> Iterable[dict]:
    """Yield parsed ack records from a JSONL file.

    Bad lines are skipped silently — the file is append-only and a
    half-written line from a crash should not block the read path.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return
    except OSError:
        return


def latest_acks(state_root: Path | None = None) -> dict[str, Ack]:
    """Return {surface: most-recent-Ack} across the JSONL.

    "Most recent" is ordered by ``synced_at`` (the claimed actual sync
    time), not ``acked_at`` (the bookkeeping time). Two acks with the
    same ``synced_at`` use ``acked_at`` as a tiebreaker so a re-ack with
    a fresher note wins.
    """
    out: dict[str, Ack] = {}
    for rec in _iter_ack_lines(acks_path(state_root)):
        surface = rec.get("surface")
        if not isinstance(surface, str) or not surface:
            continue
        try:
            synced = parse_timestamp(rec.get("synced_at", ""))
            acked = parse_timestamp(rec.get("acked_at", ""))
        except ValueError:
            continue
        candidate = Ack(
            surface=surface,
            synced_at=synced,
            acked_at=acked,
            note=rec.get("note") or "",
        )
        prior = out.get(surface)
        if prior is None:
            out[surface] = candidate
            continue
        if (candidate.synced_at, candidate.acked_at) > (
            prior.synced_at,
            prior.acked_at,
        ):
            out[surface] = candidate
    return out
