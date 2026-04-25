"""Persona isolation guard.

Phase 2/3 personas write to fixture files (and only fixture files).
This module:

  1. Builds a tmpdir-backed sandbox that personas may write to.
  2. Validates that any path a persona names lies inside the
     allowed roots — `experiments/white-cells/fixtures/`,
     the persona's tmpdir sandbox, OR a snapshot dir we copied
     from the operator's tree (read-only intent).
  3. Quarantines personas that escape: any attempt to touch
     `~/.claude/`, `~/projects/` outside `Swanlake/`, the Obsidian
     vault, or any operator dot-file.

The guard is enforced by the supervisor BEFORE writing any finding
the persona produced. A finding referencing a forbidden path is
quarantined as `persona-isolation-violation` and never reaches the
sink — same defense-in-depth shape as the canary post-filter.

This module deliberately does NOT chroot or seccomp the persona
process. It is a *contract-level* guard: personas are cooperative
code in this repo. The point is to fail loudly when a persona's
fixture-touching surface accidentally points at the wrong root,
not to defend against a hostile persona binary.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Repo-root resolution. experiments/white-cells/supervisor/isolation.py ->
# parents[2] is the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_WC_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES_ROOT = _WC_ROOT / "fixtures"

# Hard-coded forbidden roots. Anything under these is operator-owned and
# off-limits to personas. Resolved to absolute paths for prefix matching.
_FORBIDDEN_PREFIXES = (
    Path.home() / ".claude",
    Path.home() / ".ssh",
    Path.home() / ".gnupg",
    Path.home() / ".aws",
    Path.home() / ".config",
    Path("/mnt/c/Users") if Path("/mnt/c/Users").exists() else Path("/__no_such__"),
    Path("/etc"),
    Path("/root"),
)


@dataclass
class IsolationViolation:
    persona: str
    attempted_path: str
    reason: str


@dataclass
class PersonaSandbox:
    """Tmpdir-backed scratch area for a persona's writes.

    Created via `PersonaSandbox.create(persona_name)` — yields a
    directory under $TMPDIR/white-cells-<persona>-XXXX/. The persona
    is contractually allowed to write under this root; anything else
    is quarantined.

    Use as a context manager so the tmpdir is cleaned up on exit.
    """

    persona: str
    root: Path
    extra_allowed_roots: tuple[Path, ...] = field(default_factory=tuple)
    _cleanup: bool = True

    @classmethod
    def create(
        cls,
        persona: str,
        *,
        extra_allowed_roots: Iterable[Path] = (),
    ) -> "PersonaSandbox":
        d = Path(tempfile.mkdtemp(prefix=f"white-cells-{persona}-"))
        return cls(
            persona=persona,
            root=d,
            extra_allowed_roots=tuple(Path(r).resolve() for r in extra_allowed_roots),
        )

    def __enter__(self) -> "PersonaSandbox":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._cleanup and self.root.exists():
            import shutil
            shutil.rmtree(self.root, ignore_errors=True)

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        return (self.root, _FIXTURES_ROOT, *self.extra_allowed_roots)

    def is_path_allowed(self, candidate: str | os.PathLike) -> bool:
        try:
            p = Path(os.fspath(candidate)).resolve()
        except (ValueError, OSError):
            return False
        # Forbidden prefixes win over allowed; even if a fixture symlinks
        # into ~/.claude (which it shouldn't), the guard fails closed.
        for forbid in _FORBIDDEN_PREFIXES:
            try:
                forbid_resolved = forbid.resolve()
            except (FileNotFoundError, OSError):
                continue
            if _is_relative_to(p, forbid_resolved):
                return False
        for ok in self.allowed_roots:
            if _is_relative_to(p, ok):
                return True
        return False

    def assert_path_allowed(self, candidate: str | os.PathLike) -> None:
        if not self.is_path_allowed(candidate):
            raise PersonaIsolationError(
                IsolationViolation(
                    persona=self.persona,
                    attempted_path=str(candidate),
                    reason="path outside persona's allowed roots",
                )
            )


class PersonaIsolationError(Exception):
    def __init__(self, violation: IsolationViolation):
        super().__init__(
            f"persona-isolation-violation: persona={violation.persona} "
            f"attempted_path={violation.attempted_path!r} "
            f"reason={violation.reason}"
        )
        self.violation = violation


def _is_relative_to(child: Path, parent: Path) -> bool:
    """Backport of Path.is_relative_to (Py3.9+ has it; we keep this
    explicit because old Pythons fail differently on symlinks). Returns
    True iff `child` lies under `parent` after both are resolved."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


# Filesystem-prefix allowlist for path scanning. Only these top-level
# segments are interpreted as "candidate filesystem paths" for the
# isolation check. URL paths like `/v1/users/me` (mock-Notion routes),
# `/repos/...` (mock-GitHub routes), and `/v6/deployments` (mock-Vercel
# routes) start with `/` but are NOT filesystem paths and should be
# ignored by the guard.
_FILESYSTEM_PREFIXES = (
    "/home/",
    "/root/",
    "/etc/",
    "/usr/",
    "/var/",
    "/opt/",
    "/mnt/",
    "/tmp/",
    "/Users/",
)


def _looks_like_fs_path(s: str) -> bool:
    if not s.startswith("/") or len(s) < 2:
        return False
    return any(s.startswith(p) for p in _FILESYSTEM_PREFIXES)


def scan_finding_paths(finding: dict) -> list[str]:
    """Walk a finding dict for any string-typed *filesystem* path
    references the persona named. Conservative inclusion: only strings
    starting with a known filesystem prefix (/home/, /etc/, /tmp/, ...)
    qualify. URL-shaped paths (`/v1/users/me`) are skipped — they're
    fixture-API routes, not filesystem locations.

    Skips the `fixture_hits[].path` field entirely: that field is by
    schema construction a mock-API URL path and never a filesystem
    path.

    Caller passes the resulting list to `PersonaSandbox.is_path_allowed`
    one path at a time.
    """
    out: list[str] = []

    def _walk(v, *, in_fixture_hits: bool = False):
        if in_fixture_hits:
            # Per-schema invariant: fixture_hits is a list[dict] where
            # each dict's `path` is an API URL path. Skip it entirely.
            return
        if isinstance(v, str):
            if _looks_like_fs_path(v):
                out.append(v)
        elif isinstance(v, dict):
            for k, sub in v.items():
                if k == "fixture_hits":
                    continue
                _walk(sub)
        elif isinstance(v, (list, tuple)):
            for sub in v:
                _walk(sub)

    _walk(finding)
    return out


# Public surfaces for tests + supervisor wiring.
REPO_ROOT = _REPO_ROOT
WC_ROOT = _WC_ROOT
FIXTURES_ROOT = _FIXTURES_ROOT
FORBIDDEN_PREFIXES = _FORBIDDEN_PREFIXES
