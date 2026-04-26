"""Claude Code adapter -- spec section A8 + T9a.

Installs four PostToolUse / PreToolUse hook scripts into ~/.claude/hooks/
patches ~/.claude/settings.json (idempotent, additive), drops a
slash-command skill at ~/.claude/skills/swanlake/SKILL.md, and writes
a manifest at ~/.swanlake/cc-adapter-manifest.json so subsequent
--uninstall calls can reverse exactly what was done.

Idempotency:
  - install() is safe to call repeatedly. Existing matching settings.json
    entries are detected by `command` and not duplicated. Existing files
    on disk are left in place if their content is byte-identical;
    otherwise a timestamped backup is written before overwrite.
  - uninstall() reads the manifest and reverses each step. Files we
    installed are removed; files we modified are restored from the
    backup we wrote during install.

Tests must NEVER touch the operator's real ~/.claude/. The CC_DIR
constant is patched to a tmp directory in unit tests.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import time
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
SKILL_REL = Path("skills") / "swanlake" / "SKILL.md"
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
    """Claude Code adapter -- installs hooks + skill + settings patch."""

    name = "cc"

    def __init__(self, cc_dir: Path | None = None) -> None:
        self.cc_dir = (cc_dir if cc_dir is not None else CC_DIR).expanduser()

    @property
    def hooks_dir(self) -> Path:
        return self.cc_dir / "hooks"

    @property
    def skill_path(self) -> Path:
        return self.cc_dir / SKILL_REL

    @property
    def settings_path(self) -> Path:
        return self.cc_dir / "settings.json"

    @property
    def manifest_path(self) -> Path:
        return _state.state_path(MANIFEST_FILENAME)

    # --- install ---

    def _plan(self) -> list[dict[str, Any]]:
        """Return the install plan as a list of {action, path, ...} dicts."""
        plan: list[dict[str, Any]] = []
        templates = _templates_dir()
        for hook_name in HOOK_NAMES:
            src = templates / hook_name
            dst = self.hooks_dir / hook_name
            if not dst.exists():
                plan.append({"action": "create-hook", "src": str(src), "dst": str(dst)})
            elif dst.read_text(encoding="utf-8") != src.read_text(encoding="utf-8"):
                plan.append({"action": "replace-hook", "src": str(src), "dst": str(dst)})
            else:
                plan.append({"action": "noop-hook", "dst": str(dst)})
        # Skill
        skill_src = templates / "SKILL.md"
        if not self.skill_path.exists():
            plan.append({"action": "create-skill", "src": str(skill_src), "dst": str(self.skill_path)})
        else:
            plan.append({"action": "noop-skill", "dst": str(self.skill_path)})
        # settings.json patch
        for hook_name, event in HOOK_EVENT.items():
            command = str(self.hooks_dir / hook_name)
            plan.append({
                "action": "patch-settings",
                "event": event,
                "command": command,
            })
        return plan

    def install(self, dry_run: bool = False) -> int:
        if not self.cc_dir.exists():
            eprint(
                f"swanlake adapt cc: {self.cc_dir} does not exist. "
                f"Install Claude Code first (https://claude.ai/code), then re-run."
            )
            return USAGE
        plan = self._plan()
        if dry_run:
            for step in plan:
                print_line(f"would: {step['action']}  {step.get('dst', step.get('command', ''))}",
                           quiet=False)
            return CLEAN

        manifest = _read_manifest(self.manifest_path)
        installed: list[dict[str, Any]] = list(manifest.get("installed") or [])
        modified: list[dict[str, Any]] = list(manifest.get("modified") or [])

        templates = _templates_dir()

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

        # Skill.
        skill_src = templates / "SKILL.md"
        skill_content = skill_src.read_text(encoding="utf-8")
        if not self.skill_path.exists():
            _atomic_write(self.skill_path, skill_content)
            installed.append({"kind": "skill", "path": str(self.skill_path)})

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

        settings_added: list[dict[str, str]] = list(
            manifest.get("settings_added") or []
        )
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
        manifest["installed_at"] = datetime.now(timezone.utc).isoformat()
        _write_manifest(self.manifest_path, manifest)

        return CLEAN

    # --- uninstall ---

    def uninstall(self, dry_run: bool = False) -> int:
        manifest = _read_manifest(self.manifest_path)
        installed = manifest.get("installed") or []
        modified = manifest.get("modified") or []
        settings_added = manifest.get("settings_added") or []
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
        for entry in installed:
            path = Path(entry.get("path", ""))
            if path.exists():
                try:
                    path.unlink()
                except OSError as e:
                    eprint(f"swanlake adapt cc --uninstall: cannot remove {path}: {e}")

        # Restore files we modified, in reverse order of modification.
        for entry in reversed(modified):
            path = Path(entry.get("path", ""))
            backup = Path(entry.get("backup", ""))
            if backup.exists():
                try:
                    shutil.copy2(backup, path)
                except OSError as e:
                    eprint(f"swanlake adapt cc --uninstall: cannot restore {path}: {e}")

        # Drop the manifest -- a future install rebuilds it.
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
        if self.skill_path.exists():
            yield AdapterVerifyResult("skill", "intact", str(self.skill_path))
        else:
            yield AdapterVerifyResult("skill", "missing", str(self.skill_path))

    def list_surfaces(self) -> Iterable[tuple[str, str]]:
        for hook_name in HOOK_NAMES:
            yield (hook_name, "cc-hook")
        yield ("skill", "cc-skill")


# --- CLI handler used by swanlake.commands.adapt.run ---


def run(args) -> int:
    quiet = bool(getattr(args, "quiet", False))
    cc_dir = getattr(args, "cc_dir", None)
    adapter = ClaudeCodeAdapter(
        cc_dir=Path(cc_dir).expanduser() if cc_dir else None
    )
    if getattr(args, "uninstall", False):
        return adapter.uninstall(dry_run=getattr(args, "dry_run", False))
    return adapter.install(dry_run=getattr(args, "dry_run", False))


__all__ = ["ClaudeCodeAdapter", "run", "CC_DIR", "HOOK_NAMES"]
