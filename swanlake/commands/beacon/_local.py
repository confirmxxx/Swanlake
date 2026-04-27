"""LOCAL deploy state machine -- the 12-step safety sequence (spec §5).

Every LOCAL deploy runs through `run_local_deploy()` which executes the
steps in order and aborts cleanly on any failure. The function returns
a structured result the caller writes into beacon-deploy-history.jsonl.

Design constraints (do not soften):

1. Step 5 (clean tree) has NO --allow-dirty escape hatch. D3 removed it
   from an earlier draft because the use case it solved (scratch repos)
   is satisfied by `git stash`, and the global env-var bypass widens
   attack surface (operator may export and never reset).
2. Step 7 generates the beacon block via subprocess to make-canaries.py
   per D9. Never imports the script.
3. Step 11 is `os.replace(tmp, target)` -- match the v0.2.1 F6 fix in
   swanlake.commands.adapt.cc::_atomic_write which preserves the
   existing file mode.
4. Step 8 backs up BEFORE step 11 writes. If step 11 fails the operator
   has the original at <state-root>/beacon-backups/<surface>/<ts>.bak.

The 12 steps map to these private functions:

    _step1_validate_surface_id    surface ID grammar check
    _step2_resolve_target         surfaces.yaml -> path; refuse traversal
    _step3_target_exists_or_mkdir target file or parent dir is writeable
    _step4_inside_git_tree        target dir is in a git working tree
    _step5_clean_git_tree         git status --porcelain is empty
    _step6_no_optout              no .swanlake-no-beacon ancestor
    _step7_generate_beacon        subprocess to make-canaries.py
    _step8_backup                 mode 0600 backup
    _step9_show_diff              redacted diff to stdout
    _step10_confirm               confirmation gate
    _step11_atomic_write          os.replace
    _step12_post_status           git status post-write + history append

Vault-adapter note (B4): vault `.md` surfaces share the same machine.
The only difference is the path-resolution step: vault paths come from
`<vault-root>/<surface-relative-path>` instead of being a direct
`CLAUDE.md` in a project root. Step 4 (inside-git-tree) still applies
-- the operator's vault is expected to be a git repo (the spec says so
implicitly: reversibility is bounded by `git checkout`).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import unified_diff
from pathlib import Path
from typing import Any

from swanlake import safety, state as _state
from swanlake.commands.beacon import _optout, _surfaces
from swanlake.commands.beacon._registry import (
    METHOD_LOCAL,
    SURFACE_ID_RE,
    get_type,
    infer_type,
)


# Per-type backup mode.
_BACKUP_MODE = 0o600

# Beacon header / fence regexes (spec R2: replace-not-stack logic).
_BEACON_BLOCK_RE = re.compile(
    r"<!--\s*DEFENSE BEACON v\d+[^>]*?Surface:\s*(?P<surface>[a-z0-9-]+)\s*-->"
    r".*?"
    r"<!--\s*END SURFACE ATTRIBUTION\s*[-—]+\s*(?P=surface)\s*-->",
    flags=re.DOTALL,
)

# Canary-shape redaction patterns for the diff-display step. The diff
# shows BEFORE / AFTER blocks but elides any literal that matches a
# real-canary shape (R6 / N9: never display canaries in human output).
_CANARY_REDACT_PATTERNS = (
    (re.compile(r"AKIA_BEACON_[0-9A-Fa-f]{20}"), "REDACTED(canary, type=aws)"),
    (re.compile(r"AIzaSy[A-Za-z0-9_\-]{30,}"), "REDACTED(canary, type=google)"),
    (re.compile(r"beacon-attrib-[a-z0-9-]+-[A-Za-z0-9]{8}"), "REDACTED(canary, type=attrib)"),
    (re.compile(r"ghp_beacon_[0-9a-fA-F]{40}"), "REDACTED(canary, type=ghp)"),
    (re.compile(r"sk_live_beacon_[0-9a-fA-F]{24,}"), "REDACTED(canary, type=stripe)"),
    (re.compile(r"xoxb-beacon-[A-Za-z0-9-]+"), "REDACTED(canary, type=slack)"),
    (re.compile(r"eyJhbGciOiJIUzI1NiJ9\.beacon_[0-9a-f]+\.[0-9a-f]+"), "REDACTED(canary, type=jwt)"),
    (re.compile(r"postgres://beacon:[^@\s]+@beacon-[^\s]+"), "REDACTED(canary, type=pg-url)"),
)


@dataclass
class DeployResult:
    """Structured outcome from run_local_deploy()."""

    surface: str
    type_id: str
    method: str = METHOD_LOCAL
    outcome: str = "error"
    target_path: str = ""
    backup_path: str | None = None
    post_git_status: str | None = None
    error: str | None = None
    diff: str = ""

    def as_history_record(self) -> dict[str, Any]:
        return {
            "op": "deploy",
            "surface": self.surface,
            "type": self.type_id,
            "method": self.method,
            "outcome": self.outcome,
            "backup_path": self.backup_path,
            "post_git_status": self.post_git_status,
            "summary": (
                {"error": self.error} if self.error else None
            ),
        }


@dataclass
class DeployContext:
    """Mutable state threaded through the 12 steps."""

    surface: str
    type_id: str
    target_path: Path | None = None
    new_content: str = ""
    current_content: str = ""
    backup_path: Path | None = None
    is_first_deploy: bool = True
    repo_root: Path | None = None
    surfaces_yaml: Path | None = None
    spec: _surfaces.SurfaceSpec | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# --- step helpers ---


def _step1_validate_surface_id(surface: str) -> str | None:
    if not SURFACE_ID_RE.match(surface):
        return (
            f"surface-id {surface!r} fails the grammar check "
            "[a-z0-9][a-z0-9-]{0,62}[a-z0-9]; refusing"
        )
    return None


def _step2_resolve_target(
    ctx: DeployContext,
    surfaces_yaml: Path | None,
    repo_root: Path | None,
) -> str | None:
    """Look up `ctx.surface` in surfaces.yaml; resolve to a Path.

    Vault-aware: if the spec carries `target: vault://relative/path` the
    target is resolved against `<vault_root>/relative/path`. The vault
    root is read from $SWANLAKE_VAULT_ROOT or surfaces.yaml `vault_root:`
    annotation.

    Refuses path traversal (.. resolves outside the repo / vault root).
    """
    ctx.surfaces_yaml = surfaces_yaml
    ctx.repo_root = repo_root
    if surfaces_yaml is None:
        return (
            "surfaces.yaml not found; copy "
            "defense-beacon/reference/surfaces.example.yaml to surfaces.yaml "
            "and add an entry for " + repr(ctx.surface)
        )

    try:
        specs = _surfaces.load_surfaces(surfaces_yaml)
    except OSError as e:
        return f"could not read surfaces.yaml: {e}"

    spec = next((s for s in specs if s.surface_id == ctx.surface), None)
    if spec is None:
        return (
            f"surface {ctx.surface!r} not in surfaces.yaml; add it first or "
            "run `swanlake init --add-surface " + ctx.surface + "`"
        )
    ctx.spec = spec
    if spec.type_id != ctx.type_id:
        # Surfaces.yaml carried an explicit type that overrides the
        # caller-supplied one. Update ctx so later checks use the right type.
        ctx.type_id = spec.type_id

    type_obj = get_type(ctx.type_id)
    if type_obj is None or not type_obj.is_local:
        # The deploy dispatcher should have caught this earlier; double-check.
        return (
            f"surface {ctx.surface!r} has type {ctx.type_id!r} which is REMOTE; "
            "use `swanlake beacon checklist --surface " + ctx.surface + "` instead"
        )

    target_str = spec.target
    if not target_str:
        return (
            f"surface {ctx.surface!r} has no `target:` annotation in surfaces.yaml; "
            "add `target: <abs-or-vault://-path>` to deploy LOCAL"
        )

    # Path resolution: three cases.
    if target_str.startswith("vault://"):
        vault_root = os.environ.get("SWANLAKE_VAULT_ROOT")
        if not vault_root:
            return (
                "vault://-prefixed target requires SWANLAKE_VAULT_ROOT env var "
                "(set it to your vault's git working tree root)"
            )
        rel = target_str[len("vault://"):].lstrip("/")
        target = (Path(vault_root).expanduser() / rel).resolve()
        # Path-traversal guard.
        try:
            target.relative_to(Path(vault_root).expanduser().resolve())
        except ValueError:
            return f"target {target_str!r} escapes SWANLAKE_VAULT_ROOT"
    elif target_str.startswith("/") or target_str.startswith("~"):
        target = Path(target_str).expanduser().resolve()
    else:
        # Relative target -- resolve against repo_root.
        if repo_root is None:
            return (
                f"target {target_str!r} is relative but Swanlake repo root is "
                "unknown; set SWANLAKE_REPO_ROOT or use an absolute path"
            )
        target = (repo_root / target_str).resolve()
        try:
            target.relative_to(repo_root.resolve())
        except ValueError:
            return f"target {target_str!r} escapes the Swanlake repo root"

    ctx.target_path = target
    ctx.is_first_deploy = not target.exists()
    return None


def _step3_target_exists_or_writeable(ctx: DeployContext) -> str | None:
    target = ctx.target_path
    assert target is not None
    if target.exists():
        if not os.access(target, os.W_OK):
            return f"target {target} is not writeable by this process"
        return None
    # First-time deploy: parent dir must be writeable.
    if not target.parent.exists():
        return f"target dir {target.parent} does not exist"
    if not os.access(target.parent, os.W_OK):
        return f"target dir {target.parent} is not writeable"
    return None


def _git_repo_root(path: Path) -> Path | None:
    """Return the git working-tree root for `path`, or None if not in one."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    if not out:
        return None
    return Path(out)


def _step4_inside_git_tree(ctx: DeployContext) -> str | None:
    target = ctx.target_path
    assert target is not None
    parent = target.parent
    root = _git_repo_root(parent)
    if root is None:
        return (
            f"target {target} is not in a git working tree. Either run "
            "`git init` in its containing repo, or paste manually -- "
            "Swanlake refuses LOCAL deploy outside git for reversibility reasons"
        )
    ctx.extra["git_root"] = str(root)
    return None


def _step5_clean_git_tree(ctx: DeployContext) -> str | None:
    git_root = ctx.extra.get("git_root")
    assert git_root
    try:
        proc = subprocess.run(
            ["git", "-C", git_root, "status", "--porcelain"],
            capture_output=True, text=True, check=False, timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return f"git status failed: {type(e).__name__}: {e}"
    if proc.returncode != 0:
        return f"git status returned exit {proc.returncode}: {proc.stderr.strip()}"
    if proc.stdout.strip():
        # Show first 5 lines so the operator sees what's dirty.
        head = "\n".join(proc.stdout.strip().splitlines()[:5])
        return (
            f"repo at {git_root} has uncommitted changes:\n{head}\n"
            "Commit or stash before deploying. Swanlake does not provide a "
            "dirty-tree override -- the clean-tree precondition is what makes "
            "the deploy reversible by `git checkout`."
        )
    return None


def _step6_no_optout(ctx: DeployContext) -> str | None:
    target = ctx.target_path
    assert target is not None
    excluded, marker = _optout.is_excluded(target, ctx.surface)
    if excluded:
        return (
            f"target {target} is opted out via {marker.path}; "
            "either remove the marker or pick a different surface"
        )
    return None


def _step7_generate_beacon(ctx: DeployContext, repo_root: Path | None) -> str | None:
    """Subprocess to make-canaries.py for `--surfaces <id>`. Read the emitted file.

    Per D9 / R5: gate on the script's --version output (>= 1.1.0). The
    emitted file lives at defense-beacon/reference/out/<surface>.md.
    """
    if repo_root is None:
        return "swanlake repo root unknown; cannot locate make-canaries.py"
    script = repo_root / "defense-beacon" / "reference" / "make-canaries.py"
    if not script.is_file():
        return f"make-canaries.py not at {script}"

    # Version gate (R5): refuse if the script is too old to honor the contract.
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--version"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return f"make-canaries.py --version failed: {type(e).__name__}"
    if proc.returncode != 0:
        return f"make-canaries.py --version exit {proc.returncode}: {proc.stderr.strip()}"
    version_str = (proc.stdout or proc.stderr).strip()
    # Format: "make-canaries.py 1.1.0"
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", version_str)
    if not m:
        return f"could not parse make-canaries.py version from {version_str!r}"
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if (major, minor, patch) < (1, 1, 0):
        return (
            f"make-canaries.py at {script} is version {major}.{minor}.{patch}; "
            "need >= 1.1.0; update the operator's defense-beacon checkout"
        )

    # Generate the beacon block.
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--surfaces", ctx.surface],
            capture_output=True, text=True, check=False, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return f"make-canaries.py --surfaces failed: {type(e).__name__}: {e}"
    if proc.returncode != 0:
        return f"make-canaries.py exit {proc.returncode}: {proc.stderr.strip()}"

    out_path = repo_root / "defense-beacon" / "reference" / "out" / f"{ctx.surface}.md"
    if not out_path.is_file():
        return f"expected make-canaries.py to write {out_path}; not found"
    try:
        ctx.new_content = out_path.read_text(encoding="utf-8")
    except OSError as e:
        return f"could not read generated beacon at {out_path}: {e}"
    if not ctx.new_content.strip():
        return f"generated beacon at {out_path} is empty"

    return None


def _read_current(target: Path) -> str:
    if not target.exists():
        return ""
    try:
        return target.read_text(encoding="utf-8")
    except OSError:
        return ""


def _compute_replaced(current: str, new_block: str, surface: str) -> tuple[str, str | None]:
    """Compute the post-deploy file content (R2 replace-not-stack).

    Returns (new_content, error_or_none). If the current file already has
    a beacon block for this surface, replace it. If it has a beacon for a
    DIFFERENT surface, refuse. If it has multiple blocks for the same
    surface, refuse. Otherwise append to the current content.
    """
    if not current.strip():
        return new_block, None

    matches = list(_BEACON_BLOCK_RE.finditer(current))
    if not matches:
        # No prior beacon. Append the new block (with a single newline gap).
        sep = "\n\n" if not current.endswith("\n") else "\n"
        return current + sep + new_block, None

    # Check for surface-id mismatch.
    other_surface = next(
        (m.group("surface") for m in matches if m.group("surface") != surface),
        None,
    )
    if other_surface is not None:
        return "", (
            f"target file is attributed to surface {other_surface!r}, not "
            f"{surface!r}; refusing to overwrite. Repair manually."
        )

    # Multiple blocks for the same surface -> refuse.
    if len(matches) > 1:
        return "", (
            f"target file has {len(matches)} beacon blocks for {surface!r}; "
            "clean up manually before re-deploy"
        )

    # Single matching block -> replace it.
    m = matches[0]
    return (current[:m.start()] + new_block.rstrip() + current[m.end():], None)


def _step6b_compute_new_content(ctx: DeployContext) -> str | None:
    """Compute post-deploy content using replace-not-stack logic (R2).

    Numbered as 6b because it sits between the opt-out check and the
    backup; the spec lists it as part of step 6 in the prose.
    """
    target = ctx.target_path
    assert target is not None
    ctx.current_content = _read_current(target)
    new_full, err = _compute_replaced(
        ctx.current_content, ctx.new_content, ctx.surface
    )
    if err:
        return err
    ctx.new_content = new_full
    return None


def _step8_backup(ctx: DeployContext, dry_run: bool) -> str | None:
    if dry_run:
        return None  # spec: no backup under --dry-run
    if ctx.is_first_deploy:
        # Nothing to back up; the file doesn't exist yet.
        return None
    backups_root = _state.state_path("beacon-backups") / ctx.surface
    try:
        backups_root.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"cannot create backup dir {backups_root}: {e}"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bak = backups_root / f"{ts}.bak"
    try:
        bak.write_text(ctx.current_content, encoding="utf-8")
        os.chmod(bak, _BACKUP_MODE)
    except OSError as e:
        return (
            f"cannot write backup at {bak}: {e}. Aborting before any write to target."
        )
    ctx.backup_path = bak
    return None


def _redact_canaries(text: str) -> str:
    out = text
    for pat, replacement in _CANARY_REDACT_PATTERNS:
        out = pat.sub(replacement, out)
    return out


def _step9_show_diff(ctx: DeployContext, quiet: bool, fp=None) -> str:
    """Build a redacted unified diff for stdout. Returns the diff text."""
    before = _redact_canaries(ctx.current_content).splitlines(keepends=True)
    after = _redact_canaries(ctx.new_content).splitlines(keepends=True)
    diff_iter = unified_diff(
        before, after,
        fromfile=f"{ctx.target_path} (BEFORE)",
        tofile=f"{ctx.target_path} (AFTER)",
        n=3,
    )
    diff = "".join(diff_iter)
    if not quiet:
        out = fp if fp is not None else sys.stdout
        if diff:
            out.write(diff)
            if not diff.endswith("\n"):
                out.write("\n")
        else:
            out.write(f"(no diff: target {ctx.target_path} is unchanged)\n")
    return diff


def _step10_confirm(ctx: DeployContext, yes: bool, dry_run: bool) -> str | None:
    if dry_run:
        return None
    prompt = f"Apply this deploy to {ctx.target_path}?"
    if not safety.confirm(prompt, yes=yes):
        return "aborted by operator (no write performed)"
    return None


def _atomic_write_preserving_mode(target: Path, text: str) -> None:
    """Mirror swanlake.commands.adapt.cc::_atomic_write (v0.2.1 F6 fix).

    Preserves existing file mode; defaults to 0644 for new files.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            import stat as _stat
            mode = _stat.S_IMODE(target.stat().st_mode)
        except OSError:
            mode = 0o644
    else:
        mode = 0o644
    fd, tmp_str = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.", suffix=".swanlake-tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(text)
            fp.flush()
            try:
                os.fsync(fp.fileno())
            except OSError:
                pass
        os.chmod(tmp_str, mode)
        os.replace(tmp_str, target)
    except Exception:
        try:
            os.unlink(tmp_str)
        except OSError:
            pass
        raise


def _step11_atomic_write(ctx: DeployContext, dry_run: bool) -> str | None:
    if dry_run:
        return None
    target = ctx.target_path
    assert target is not None
    try:
        _atomic_write_preserving_mode(target, ctx.new_content)
    except OSError as e:
        return (
            f"atomic write failed: {e}. Backup at {ctx.backup_path}; "
            f"target may be untouched -- inspect {target}."
        )
    return None


def _step12_post_status(ctx: DeployContext) -> str | None:
    """Run `git status` post-write so the operator sees the dirty file.

    Failure here is non-fatal -- the deploy is already done.
    """
    git_root = ctx.extra.get("git_root")
    if not git_root:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", git_root, "status", "--porcelain"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _resolve_repo_root() -> Path | None:
    try:
        from swanlake import _compat
        return _compat.find_repo_root()
    except Exception:
        return None


def _resolve_surfaces_yaml(repo_root: Path | None) -> Path | None:
    return _surfaces.discover_surfaces_yaml(repo_root)


def run_local_deploy(
    surface: str,
    *,
    type_id: str | None = None,
    dry_run: bool = False,
    yes: bool = False,
    quiet: bool = False,
    repo_root: Path | None = None,
    surfaces_yaml: Path | None = None,
) -> DeployResult:
    """Run the 12-step LOCAL deploy. Returns a DeployResult.

    Failure on any step yields outcome=`aborted-<reason>` (or `error`)
    and `error` set to the human-readable message. The caller writes
    the result to history.
    """
    type_id = type_id or infer_type(surface)
    ctx = DeployContext(surface=surface, type_id=type_id)
    res = DeployResult(surface=surface, type_id=type_id)

    if repo_root is None:
        repo_root = _resolve_repo_root()
    if surfaces_yaml is None:
        surfaces_yaml = _resolve_surfaces_yaml(repo_root)

    # Step 1
    err = _step1_validate_surface_id(surface)
    if err:
        res.outcome = "aborted-bad-surface-id"
        res.error = err
        return res

    # Step 2
    err = _step2_resolve_target(ctx, surfaces_yaml, repo_root)
    if err:
        res.outcome = "aborted-resolve-failed"
        res.error = err
        return res
    assert ctx.target_path is not None
    res.target_path = str(ctx.target_path)
    res.type_id = ctx.type_id  # may have been updated by step 2

    # Step 3
    err = _step3_target_exists_or_writeable(ctx)
    if err:
        res.outcome = "aborted-target-not-writeable"
        res.error = err
        return res

    # Step 4
    err = _step4_inside_git_tree(ctx)
    if err:
        res.outcome = "aborted-not-in-git"
        res.error = err
        return res

    # Step 5
    err = _step5_clean_git_tree(ctx)
    if err:
        res.outcome = "aborted-clean-tree"
        res.error = err
        return res

    # Step 6
    err = _step6_no_optout(ctx)
    if err:
        res.outcome = "skipped-by-optout"
        res.error = err
        return res

    # Step 7 (generate beacon)
    err = _step7_generate_beacon(ctx, repo_root)
    if err:
        res.outcome = "aborted-canary-gen"
        res.error = err
        return res

    # Step 6b (replace-not-stack content computation)
    err = _step6b_compute_new_content(ctx)
    if err:
        res.outcome = "aborted-replace-conflict"
        res.error = err
        return res

    # Step 8 (backup BEFORE write)
    err = _step8_backup(ctx, dry_run=dry_run)
    if err:
        res.outcome = "aborted-backup-failed"
        res.error = err
        return res
    if ctx.backup_path:
        res.backup_path = str(ctx.backup_path)

    # Step 9 (diff display)
    diff = _step9_show_diff(ctx, quiet=quiet)
    res.diff = diff

    # Step 10 (confirmation)
    err = _step10_confirm(ctx, yes=yes, dry_run=dry_run)
    if err:
        res.outcome = "aborted-no-confirm"
        res.error = err
        return res

    if dry_run:
        res.outcome = "dry-run"
        return res

    # Step 11 (atomic write)
    err = _step11_atomic_write(ctx, dry_run=dry_run)
    if err:
        res.outcome = "error"
        res.error = err
        return res

    # Step 12 (post-status + history append happens in caller)
    res.post_git_status = _step12_post_status(ctx)
    res.outcome = "deployed"
    return res


__all__ = [
    "DeployResult",
    "DeployContext",
    "run_local_deploy",
    "_compute_replaced",
    "_redact_canaries",
    "_atomic_write_preserving_mode",
]
