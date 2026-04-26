"""Claude Code adapter -- spec section A8 + T9a.

Installs four PostToolUse / PreToolUse hook scripts into ~/.claude/hooks/
patches ~/.claude/settings.json (idempotent, additive), drops the bundled
slash-command skills under ~/.claude/skills/<skill-name>/SKILL.md, and
writes a manifest at ~/.swanlake/cc-adapter-manifest.json so subsequent
--uninstall calls can reverse exactly what was done.

Skill discovery:
  Skills are discovered dynamically by walking
  ``templates/cc/skills/*/SKILL.md``. There are no hardcoded skill names.
  Adding a new skill is a matter of dropping a directory into the
  templates tree -- no adapter code change required.

Idempotency:
  - install() is safe to call repeatedly. Existing matching settings.json
    entries are detected by `command` and not duplicated. Existing files
    on disk are left in place if their sha256 matches the template;
    otherwise a timestamped backup is written before overwrite.
  - uninstall() reads the manifest and reverses each step. Files we
    installed are removed; files we modified are restored from the
    backup we wrote during install.

Tests must NEVER touch the operator's real ~/.claude/. The CC_DIR
constant is patched to a tmp directory in unit tests.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from swanlake import state as _state
from swanlake.commands.adapt.base import Adapter, AdapterVerifyResult
from swanlake.exit_codes import ALARM, CLEAN, USAGE
from swanlake.output import eprint, print_json, print_line


CC_DIR = Path.home() / ".claude"
HOOK_NAMES = (
    "canary-match.sh",
    "content-safety-check.sh",
    "bash-firewall.sh",
    "exfil-monitor.sh",
)
SKILLS_REL = Path("skills")
MANIFEST_FILENAME = "cc-adapter-manifest.json"

# Mapping of hook script -> the settings.json hook event it should be
# wired into. The operator's full taxonomy is richer; we install the
# minimum the templates need.
HOOK_EVENT = {
    "canary-match.sh": "PostToolUse",
    "content-safety-check.sh": "PostToolUse",
    "exfil-monitor.sh": "PostToolUse",
    "bash-firewall.sh": "PreToolUse",
}


def _templates_dir() -> Path:
    """Resolve the templates dir bundled with the swanlake package."""
    return Path(__file__).resolve().parents[2] / "adapters" / "templates" / "cc"


def _skills_templates_dir() -> Path:
    """Resolve the per-skill templates dir."""
    return _templates_dir() / "skills"


def _discover_skill_templates() -> list[tuple[str, Path]]:
    """Walk ``templates/cc/skills/<name>/SKILL.md`` and return
    ``[(name, src_path), ...]`` sorted by skill name for deterministic
    install order. Skill directories without a SKILL.md are skipped.
    """
    base = _skills_templates_dir()
    if not base.exists():
        return []
    out: list[tuple[str, Path]] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if skill_md.is_file():
            out.append((child.name, skill_md))
    return out


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, text: str, mode: int | None = None) -> None:
    """Atomic write. Preserves existing file mode by default.

    If `mode` is None and the target file already exists, the existing
    mode is reused. This protects operator hardening: if someone tightens
    ~/.claude/settings.json to 0o600 because it carries personal API
    tokens, a later `swanlake adapt cc` must not silently widen it to
    0o644.

    Callers that need a specific mode (e.g. hook scripts that must be
    executable) pass `mode=0o755` explicitly.

    For brand-new files where no prior mode exists, default to 0o644.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        if path.exists():
            try:
                mode = stat.S_IMODE(path.stat().st_mode)
            except OSError:
                mode = 0o644
        else:
            mode = 0o644
    tmp = path.with_suffix(path.suffix + ".swanlake-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _ts_suffix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema": 1, "installed": [], "modified": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": 1, "installed": [], "modified": []}


def _write_manifest(path: Path, data: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(data, sort_keys=True, indent=2) + "\n")


def _hook_entries(hook_path: Path) -> dict[str, Any]:
    """Build a settings.json hook entry dict for the given absolute hook path.

    The shape mirrors the Claude Code settings.json hook schema:
        {"matcher": "*", "hooks": [{"type": "command", "command": "...sh"}]}

    We use matcher="*" by default so the hook fires on every tool. The
    operator can narrow it post-install.
    """
    return {
        "matcher": "*",
        "hooks": [{"type": "command", "command": str(hook_path)}],
    }


def _settings_already_has_command(
    settings: dict[str, Any], event: str, command: str
) -> bool:
    """Match by `command` field as the spec requires for idempotency."""
    hooks = (settings.get("hooks") or {}).get(event) or []
    if not isinstance(hooks, list):
        return False
    for entry in hooks:
        if not isinstance(entry, dict):
            continue
        for h in entry.get("hooks") or []:
            if isinstance(h, dict) and h.get("command") == command:
                return True
    return False


def _patch_settings(
    settings: dict[str, Any], event: str, command: str, hook_path: Path
) -> bool:
    """Add a hook entry idempotently. Return True iff settings changed."""
    if _settings_already_has_command(settings, event, command):
        return False
    hooks = settings.setdefault("hooks", {})
    bucket = hooks.setdefault(event, [])
    if not isinstance(bucket, list):
        # An operator-managed settings.json that stored hooks.<event> as
        # something other than a list (dict, string, ...) silently dropped
        # our patch in earlier versions. Surface the warning so the
        # operator can fix the schema rather than wonder why hooks never
        # fired.
        eprint(
            f"swanlake adapt cc: settings.json 'hooks.{event}' is not a list "
            f"({type(bucket).__name__}); refusing to patch. Fix manually."
        )
        return False
    bucket.append(_hook_entries(hook_path))
    return True


class ClaudeCodeAdapter(Adapter):
    """Claude Code adapter -- installs hooks + skills + settings patch."""

    name = "cc"

    def __init__(self, cc_dir: Path | None = None) -> None:
        self.cc_dir = (cc_dir if cc_dir is not None else CC_DIR).expanduser()

    @property
    def hooks_dir(self) -> Path:
        return self.cc_dir / "hooks"

    @property
    def skills_dir(self) -> Path:
        return self.cc_dir / SKILLS_REL

    def skill_path(self, skill_name: str) -> Path:
        return self.skills_dir / skill_name / "SKILL.md"

    @property
    def settings_path(self) -> Path:
        return self.cc_dir / "settings.json"

    @property
    def manifest_path(self) -> Path:
        return _state.state_path(MANIFEST_FILENAME)

    # --- install ---

    def _plan(self, skill_only: bool = False) -> list[dict[str, Any]]:
        """Return the install plan as a list of {action, path, ...} dicts.

        When `skill_only` is True, the plan contains only the per-skill
        write steps. Hook copying and settings.json patching are
        omitted entirely so an operator running their own production
        hooks sees no surprise mutations to ~/.claude/settings.json.
        """
        plan: list[dict[str, Any]] = []
        templates = _templates_dir()
        if not skill_only:
            for hook_name in HOOK_NAMES:
                src = templates / hook_name
                dst = self.hooks_dir / hook_name
                if not dst.exists():
                    plan.append({"action": "create-hook", "src": str(src), "dst": str(dst)})
                elif dst.read_text(encoding="utf-8") != src.read_text(encoding="utf-8"):
                    plan.append({"action": "replace-hook", "src": str(src), "dst": str(dst)})
                else:
                    plan.append({"action": "noop-hook", "dst": str(dst)})
        # Skills -- one plan entry per discovered skill, in both modes.
        for skill_name, skill_src in _discover_skill_templates():
            dst = self.skill_path(skill_name)
            src_text = skill_src.read_text(encoding="utf-8")
            if not dst.exists():
                plan.append({
                    "action": "create-skill",
                    "skill": skill_name,
                    "src": str(skill_src),
                    "dst": str(dst),
                })
                continue
            try:
                dst_text = dst.read_text(encoding="utf-8")
            except OSError:
                plan.append({
                    "action": "update-skill",
                    "skill": skill_name,
                    "src": str(skill_src),
                    "dst": str(dst),
                })
                continue
            if _sha256(dst_text) == _sha256(src_text):
                plan.append({
                    "action": "noop-skill",
                    "skill": skill_name,
                    "dst": str(dst),
                })
            else:
                plan.append({
                    "action": "update-skill",
                    "skill": skill_name,
                    "src": str(skill_src),
                    "dst": str(dst),
                })
        # settings.json patch -- skipped in skill-only mode.
        if not skill_only:
            for hook_name, event in HOOK_EVENT.items():
                command = str(self.hooks_dir / hook_name)
                plan.append({
                    "action": "patch-settings",
                    "event": event,
                    "command": command,
                })
        return plan

    def install(self, dry_run: bool = False, skill_only: bool = False) -> int:
        if not self.cc_dir.exists():
            eprint(
                f"swanlake adapt cc: {self.cc_dir} does not exist. "
                f"Install Claude Code first (https://claude.ai/code), then re-run."
            )
            return USAGE
        plan = self._plan(skill_only=skill_only)
        if dry_run:
            for step in plan:
                action = step["action"]
                if action.endswith("-skill"):
                    print_line(
                        f"would: {action}  {step.get('skill', '')}  -> "
                        f"{step.get('dst', '')}",
                        quiet=False,
                    )
                else:
                    print_line(
                        f"would: {action}  "
                        f"{step.get('dst', step.get('command', ''))}",
                        quiet=False,
                    )
            return CLEAN

        manifest = _read_manifest(self.manifest_path)
        installed: list[dict[str, Any]] = list(manifest.get("installed") or [])
        modified: list[dict[str, Any]] = list(manifest.get("modified") or [])
        # Persist the install mode so uninstall can refuse to drop hooks
        # the operator never asked us to install. A skill-only install
        # leaves the operator's existing hooks alone; uninstall must do
        # the same.
        manifest["skill_only"] = bool(skill_only)

        templates = _templates_dir()

        if not skill_only:
            # Hook scripts.
            self.hooks_dir.mkdir(parents=True, exist_ok=True)
            for hook_name in HOOK_NAMES:
                src = templates / hook_name
                dst = self.hooks_dir / hook_name
                content = src.read_text(encoding="utf-8")
                if dst.exists() and dst.read_text(encoding="utf-8") == content:
                    # Idempotent: already in place. Track as installed
                    # only if the manifest doesn't already record it (so
                    # uninstall removes it).
                    if not any(i.get("path") == str(dst) for i in installed):
                        installed.append({"kind": "hook", "path": str(dst)})
                    continue
                if dst.exists():
                    backup = dst.with_name(f"{dst.name}.bak-swanlake-{_ts_suffix()}")
                    shutil.copy2(dst, backup)
                    modified.append({
                        "kind": "hook-overwritten",
                        "path": str(dst),
                        "backup": str(backup),
                    })
                _atomic_write(dst, content, mode=0o755)
                if not any(i.get("path") == str(dst) for i in installed):
                    installed.append({"kind": "hook", "path": str(dst)})

        # Skills -- always installed, in both modes. Discovered dynamically.
        skills_installed: list[str] = list(manifest.get("skills_installed") or [])
        for skill_name, skill_src in _discover_skill_templates():
            dst = self.skill_path(skill_name)
            skill_content = skill_src.read_text(encoding="utf-8")
            if dst.exists():
                try:
                    existing = dst.read_text(encoding="utf-8")
                except OSError:
                    existing = None
                if existing is not None and _sha256(existing) == _sha256(skill_content):
                    # Byte-identical -- no rewrite, no backup, no mtime bump.
                    if not any(i.get("path") == str(dst) for i in installed):
                        installed.append({"kind": "skill", "path": str(dst)})
                    if skill_name not in skills_installed:
                        skills_installed.append(skill_name)
                    continue
                # Different content -- back up, then overwrite. Templates win
                # by adapter contract; operators who want to pin a custom
                # skill should remove the directory before install.
                backup = dst.with_name(f"{dst.name}.bak-swanlake-{_ts_suffix()}")
                shutil.copy2(dst, backup)
                modified.append({
                    "kind": "skill-overwritten",
                    "path": str(dst),
                    "backup": str(backup),
                })
            _atomic_write(dst, skill_content)
            if not any(i.get("path") == str(dst) for i in installed):
                installed.append({"kind": "skill", "path": str(dst)})
            if skill_name not in skills_installed:
                skills_installed.append(skill_name)

        settings_added: list[dict[str, str]] = list(
            manifest.get("settings_added") or []
        )
        if not skill_only:
            # settings.json patch.
            if self.settings_path.exists():
                try:
                    settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    eprint(
                        f"swanlake adapt cc: {self.settings_path} is unreadable; "
                        "skipping settings patch."
                    )
                    settings = None
            else:
                settings = {}

            if isinstance(settings, dict):
                changed = False
                for hook_name, event in HOOK_EVENT.items():
                    command = str(self.hooks_dir / hook_name)
                    if _patch_settings(settings, event, command, self.hooks_dir / hook_name):
                        changed = True
                        # Track the entry so uninstall() can find and remove it.
                        if not any(
                            a.get("event") == event and a.get("command") == command
                            for a in settings_added
                        ):
                            settings_added.append({"event": event, "command": command})
                if changed:
                    # Backup before overwrite (only when we actually change it).
                    backup = self.settings_path.with_name(
                        f"{self.settings_path.name}.bak-swanlake-{_ts_suffix()}"
                    )
                    if self.settings_path.exists():
                        shutil.copy2(self.settings_path, backup)
                        modified.append({
                            "kind": "settings",
                            "path": str(self.settings_path),
                            "backup": str(backup),
                        })
                    _atomic_write(
                        self.settings_path,
                        json.dumps(settings, sort_keys=True, indent=2) + "\n",
                    )

        manifest["installed"] = installed
        manifest["modified"] = modified
        manifest["settings_added"] = settings_added
        manifest["skills_installed"] = sorted(skills_installed)
        manifest["installed_at"] = datetime.now(timezone.utc).isoformat()
        _write_manifest(self.manifest_path, manifest)

        return CLEAN

    # --- uninstall ---

    def uninstall(self, dry_run: bool = False, skill_only: bool = False) -> int:
        manifest = _read_manifest(self.manifest_path)
        installed = manifest.get("installed") or []
        modified = manifest.get("modified") or []
        settings_added = manifest.get("settings_added") or []

        if skill_only:
            # Reverse only the skill-only install. Hook + settings entries
            # in the manifest (left over from a previous full install)
            # stay untouched -- the operator can drop --skill-only on a
            # later uninstall to fully clean up.
            installed = [e for e in installed if e.get("kind") == "skill"]
            modified = [e for e in modified if e.get("kind") == "skill-overwritten"]
            settings_added = []

        if not installed and not modified and not settings_added:
            print_line("nothing to uninstall (no manifest entries)", quiet=False)
            return CLEAN

        if dry_run:
            for entry in installed:
                print_line(f"would remove: {entry.get('path')}", quiet=False)
            for entry in modified:
                print_line(
                    f"would restore: {entry.get('path')}  from  {entry.get('backup')}",
                    quiet=False,
                )
            for entry in settings_added:
                print_line(
                    f"would drop settings entry: hooks.{entry.get('event')} -> "
                    f"{entry.get('command')}",
                    quiet=False,
                )
            return CLEAN

        # Drop the settings.json entries we added BEFORE removing the hook
        # files they reference, so the operator's CC session is never left
        # pointing at a missing hook script.
        if settings_added and self.settings_path.exists():
            try:
                settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                eprint(
                    f"swanlake adapt cc --uninstall: {self.settings_path} is "
                    "unreadable; skipping settings cleanup."
                )
                settings = None
            if isinstance(settings, dict):
                hooks_root = settings.get("hooks")
                if isinstance(hooks_root, dict):
                    for added in settings_added:
                        event = added.get("event")
                        command = added.get("command")
                        bucket = hooks_root.get(event)
                        if not isinstance(bucket, list):
                            continue
                        new_bucket = [
                            entry for entry in bucket
                            if not (
                                isinstance(entry, dict)
                                and any(
                                    isinstance(h, dict) and h.get("command") == command
                                    for h in (entry.get("hooks") or [])
                                )
                            )
                        ]
                        if new_bucket:
                            hooks_root[event] = new_bucket
                        else:
                            # Drop empty event buckets so settings.json stays tidy.
                            del hooks_root[event]
                    if not hooks_root:
                        del settings["hooks"]
                _atomic_write(
                    self.settings_path,
                    json.dumps(settings, sort_keys=True, indent=2) + "\n",
                )

        # Remove files we installed.
        removed_skill_dirs: set[Path] = set()
        for entry in installed:
            path = Path(entry.get("path", ""))
            if path.exists():
                try:
                    path.unlink()
                except OSError as e:
                    eprint(f"swanlake adapt cc --uninstall: cannot remove {path}: {e}")
            if entry.get("kind") == "skill":
                # Track the parent directory for cleanup if it's now empty.
                removed_skill_dirs.add(path.parent)

        # Drop now-empty skill directories so the operator's
        # ~/.claude/skills/ doesn't accumulate empty ghosts.
        for d in removed_skill_dirs:
            try:
                if d.exists() and not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass

        # Restore files we modified, in reverse order of modification.
        for entry in reversed(modified):
            path = Path(entry.get("path", ""))
            backup = Path(entry.get("backup", ""))
            if backup.exists():
                try:
                    shutil.copy2(backup, path)
                except OSError as e:
                    eprint(f"swanlake adapt cc --uninstall: cannot restore {path}: {e}")

        if skill_only:
            # Skill-only uninstall: drop only the skill entries from the
            # manifest, leave hook + settings entries (if any) so the
            # operator can later run a non-skill-only uninstall to
            # fully clean up. Re-load the original manifest because
            # `installed`/`modified`/`settings_added` were narrowed at
            # the top of this function.
            full_manifest = _read_manifest(self.manifest_path)
            full_manifest["installed"] = [
                e for e in (full_manifest.get("installed") or [])
                if e.get("kind") != "skill"
            ]
            full_manifest["modified"] = [
                e for e in (full_manifest.get("modified") or [])
                if e.get("kind") != "skill-overwritten"
            ]
            full_manifest["skills_installed"] = []
            if not (
                full_manifest.get("installed")
                or full_manifest.get("modified")
                or full_manifest.get("settings_added")
            ):
                # Manifest is empty after skill removal -- drop it.
                try:
                    self.manifest_path.unlink()
                except OSError:
                    pass
            else:
                _write_manifest(self.manifest_path, full_manifest)
            return CLEAN

        # Full uninstall: drop the manifest -- a future install rebuilds it.
        try:
            self.manifest_path.unlink()
        except OSError:
            pass
        return CLEAN

    # --- verify / list_surfaces ---

    def verify(self) -> Iterable[AdapterVerifyResult]:
        for hook_name in HOOK_NAMES:
            dst = self.hooks_dir / hook_name
            if not dst.exists():
                yield AdapterVerifyResult(hook_name, "missing", str(dst))
                continue
            yield AdapterVerifyResult(hook_name, "intact", str(dst))
        for skill_name, _src in _discover_skill_templates():
            dst = self.skill_path(skill_name)
            surface_id = "skill" if skill_name == "swanlake" else f"skill:{skill_name}"
            if dst.exists():
                yield AdapterVerifyResult(surface_id, "intact", str(dst))
            else:
                yield AdapterVerifyResult(surface_id, "missing", str(dst))

    def list_surfaces(self) -> Iterable[tuple[str, str]]:
        for hook_name in HOOK_NAMES:
            yield (hook_name, "cc-hook")
        for skill_name, _src in _discover_skill_templates():
            surface_id = "skill" if skill_name == "swanlake" else f"skill:{skill_name}"
            yield (surface_id, "cc-skill")


# --- CLI handler used by swanlake.commands.adapt.run ---


def run(args) -> int:
    quiet = bool(getattr(args, "quiet", False))
    cc_dir = getattr(args, "cc_dir", None)
    skill_only = bool(getattr(args, "skill_only", False))
    adapter = ClaudeCodeAdapter(
        cc_dir=Path(cc_dir).expanduser() if cc_dir else None
    )
    if getattr(args, "uninstall", False):
        return adapter.uninstall(
            dry_run=getattr(args, "dry_run", False),
            skill_only=skill_only,
        )
    return adapter.install(
        dry_run=getattr(args, "dry_run", False),
        skill_only=skill_only,
    )


__all__ = ["ClaudeCodeAdapter", "run", "CC_DIR", "HOOK_NAMES"]
