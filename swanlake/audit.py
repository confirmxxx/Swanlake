"""Append-only audit log for every CLI invocation.

Spec section A10: one JSON object per line in ~/.swanlake/audit.jsonl.
Atomic write, fcntl-locked append, rotation at 10 MB to audit.jsonl.1
(overwriting any prior .1). Never raises -- a broken audit log must not
break the CLI itself.

Schema:
    {
      "ts": "<ISO-UTC>",
      "cmd": "status",
      "subcmd": null,
      "args": ["--json"],          // canary-shaped values redacted
      "exit_code": 1,
      "duration_ms": 142,
      "swanlake_version": "0.2.0",
      "python_version": "3.12.3",
      "pid": 12345,
      "tty": true,
      "noninteractive": false,
      "error": null
    }

R6 mitigation: argv elements matching real-canary literal regexes are
redacted to `REDACTED(type=canary, pos=N)` before being written to the
log. The redaction patterns mirror defense-beacon/reference/canary-match.sh
verbatim (the three canonical real-canary shapes plus a github-token
beacon variant); the obviously-fake placeholder used in fixtures
(AKIA_BEACON_TESTFIXTURE000000000000) does not match any of them, so
test runs do not get spurious REDACTED lines.
"""
from __future__ import annotations

import fcntl
import json
import os
import platform
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from swanlake import __version__
from swanlake.state import ensure_state_root, state_path

AUDIT_FILENAME = "audit.jsonl"
ROTATED_FILENAME = "audit.jsonl.1"

# Rotate when the live file crosses 10 MB. Two files maximum, no cron.
ROTATION_BYTES = 10 * 1024 * 1024

# Real-canary literal patterns. Same shapes as
# defense-beacon/reference/canary-match.sh + the .claude/hooks/canary-literal-block.sh
# pre-write hook in this repo. Bounded character classes so the
# obviously-fake test-fixture placeholder (AKIA_BEACON_TESTFIXTURE0...)
# does NOT match -- TESTFIXTURE contains T,S,X which fall outside hex.
_CANARY_PATTERNS = (
    # AWS-shaped beacon literal.
    re.compile(r"^AKIA_BEACON_[0-9A-Fa-f]{20}$"),
    # Google API key shape.
    re.compile(r"^AIzaSy[A-Za-z0-9_\-]{30,}$"),
    # Per-surface attribution beacon.
    re.compile(r"^beacon-attrib-[a-z0-9-]+-[A-Za-z0-9]{8}$"),
    # GitHub PAT-shaped beacon variant.
    re.compile(r"^ghp_beacon_[0-9a-fA-F]{40}$"),
)

# Substring (un-anchored) variants of the same shapes for argv values
# that wrap a canary inside a larger string -- e.g. `--data=AKIA_BEACON_...`
# or `--token=beacon-attrib-...`. F4 fix: the original anchored regex set
# missed these because re.match anchors to start AND the literal must end
# the string. The substring patterns use `\b...\b` boundaries so we don't
# match across word boundaries that would split an unrelated identifier.
_CANARY_SUBSTRING_PATTERNS = (
    re.compile(r"\bAKIA_BEACON_[0-9A-Fa-f]{20}\b"),
    re.compile(r"\bAIzaSy[A-Za-z0-9_\-]{30,}\b"),
    re.compile(r"\bbeacon-attrib-[a-z0-9-]+-[A-Za-z0-9]{8}\b"),
    re.compile(r"\bghp_beacon_[0-9a-fA-F]{40}\b"),
)


def _is_canary_shaped(value: str) -> bool:
    """Return True iff `value` matches any real-canary literal regex.

    Whole-string match (not substring). Used to decide whether the entire
    argv element should be replaced with a position marker. For argv
    values that *contain* a canary inside a larger string (e.g.
    `--data=AKIA_BEACON_...`), see `_redact_canary_substrings`.
    """
    for pat in _CANARY_PATTERNS:
        if pat.match(value):
            return True
    return False


def _redact_canary_substrings(value: str) -> tuple[str, bool]:
    """Replace any canary-shaped substring inside `value` with REDACTED().

    Returns (new_value, did_change). Used when the whole argv element is
    not itself a canary literal but embeds one (the common shape:
    `--data=AKIA_BEACON_<hex>` from a curl-style CLI). The redaction is
    inline: `--data=AKIA_BEACON_...` -> `--data=REDACTED(type=canary)`.
    """
    changed = False
    out = value
    for pat in _CANARY_SUBSTRING_PATTERNS:
        new_out, n = pat.subn("REDACTED(type=canary)", out)
        if n:
            changed = True
            out = new_out
    return out, changed


def _redact_args(args: Sequence[str]) -> list[str]:
    """Return a copy of args with canary-shaped values replaced by markers.

    The marker carries the position so the operator can correlate against
    their shell history without ever logging the literal. Two passes:
      1. If the whole argv element matches a canary pattern, replace
         it entirely with `REDACTED(type=canary, pos=N)`.
      2. Otherwise, scrub canary substrings inline (covers shapes like
         `--data=<canary>`, `--token=<canary>`).
    """
    redacted: list[str] = []
    for i, raw in enumerate(args):
        if not isinstance(raw, str):
            # Coerce non-strings defensively -- argv is always strs in
            # practice but the audit module is on the hot path of every
            # CLI call; one type error here would silently break logging.
            redacted.append(str(raw))
            continue
        if _is_canary_shaped(raw):
            redacted.append(f"REDACTED(type=canary, pos={i})")
            continue
        scrubbed, changed = _redact_canary_substrings(raw)
        redacted.append(scrubbed if changed else raw)
    return redacted


def _maybe_rotate(audit_file: Path) -> None:
    """Rotate `audit_file` to `audit.jsonl.1` if it crosses ROTATION_BYTES.

    Atomic-ish: os.replace is atomic on POSIX. The window between the size
    check and the replace is small but non-zero; the worst-case outcome is
    one extra append going into the rotated file rather than the new live
    file. Acceptable for a forensic log.
    """
    try:
        if audit_file.exists() and audit_file.stat().st_size >= ROTATION_BYTES:
            rotated = audit_file.parent / ROTATED_FILENAME
            os.replace(audit_file, rotated)
    except OSError:
        # Rotation failures are silent -- the next append will hit the same
        # condition and try again.
        pass


def _atomic_append(audit_file: Path, line: str) -> None:
    """Append a single newline-terminated line under fcntl.flock.

    The append is not strictly atomic (we reuse the same fd), but flock
    serializes concurrent CLI invocations, and a single line shorter than
    PIPE_BUF (4096 on Linux) is atomic on POSIX append-mode writes.
    """
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    # Open append+binary so we control the encoding ourselves and avoid
    # any platform-dependent newline translation.
    with open(audit_file, "ab") as fp:
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
            fp.write(line.encode("utf-8"))
            fp.flush()
            try:
                os.fsync(fp.fileno())
            except OSError:
                # fsync can fail on some virtualized filesystems; the data
                # is still in the page cache and the line is in the file.
                pass
        finally:
            try:
                fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def _write_record(record: dict[str, Any]) -> None:
    """Write a single audit record. Never raises."""
    try:
        ensure_state_root()
        audit_file = state_path(AUDIT_FILENAME)
        _maybe_rotate(audit_file)
        # Compact JSON, sorted keys for stable diffs across runs.
        line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
        _atomic_append(audit_file, line)
    except Exception:
        # The audit log must not break the CLI. Swallow every failure.
        # Operators see "audit log empty" not "swanlake crashed".
        pass


def _is_noninteractive() -> bool:
    """Match the same env var safety.py honors for the bypass flag."""
    return os.environ.get("SWANLAKE_NONINTERACTIVE") == "1"


def _is_tty() -> bool:
    """Return True iff stdin is a real terminal."""
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


class AuditRecord:
    """Context manager that records one CLI invocation to ~/.swanlake/audit.jsonl.

    Usage:
        with AuditRecord(cmd="status", subcmd=None, argv=sys.argv[1:]) as rec:
            exit_code = run_status()
            rec.set_exit(exit_code)
        # On exit (including exception), the record is appended.

    The record's exit_code defaults to 2 (USAGE) so a subcommand that
    forgets to call set_exit() still produces a meaningful log line.
    On exception inside the with-block, exit_code falls back to whatever
    set_exit() last received (or 2 if nothing) and `error` is populated
    with the exception class name + message. The exception still
    propagates -- this context manager is observe-only.
    """

    def __init__(
        self,
        cmd: Optional[str],
        subcmd: Optional[str],
        argv: Sequence[str],
    ) -> None:
        self._cmd = cmd
        self._subcmd = subcmd
        self._argv = list(argv)
        self._exit_code = 2  # default to USAGE if subcommand never sets one
        self._error: Optional[str] = None
        self._t0: float = 0.0

    def set_exit(self, code: int) -> None:
        """Record the subcommand's chosen exit code."""
        try:
            self._exit_code = int(code)
        except (TypeError, ValueError):
            self._exit_code = 2

    def __enter__(self) -> "AuditRecord":
        self._t0 = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration_ms = int((time.monotonic() - self._t0) * 1000)
        if exc is not None:
            # Capture without ever embedding stack frames -- those can leak
            # local variable names that sometimes carry secrets.
            self._error = f"{exc_type.__name__}: {exc}"

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "cmd": self._cmd,
            "subcmd": self._subcmd,
            "args": _redact_args(self._argv),
            "exit_code": self._exit_code,
            "duration_ms": duration_ms,
            "swanlake_version": __version__,
            "python_version": platform.python_version(),
            "pid": os.getpid(),
            "tty": _is_tty(),
            "noninteractive": _is_noninteractive(),
            "error": self._error,
        }
        _write_record(record)
        # Never swallow exceptions -- caller sees them.
        return False
