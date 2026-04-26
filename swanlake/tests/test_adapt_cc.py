"""Tests for swanlake.commands.adapt.cc -- Claude Code adapter.

Cases:
  1. install creates hook files (with patched CC_DIR).
  2. install is idempotent (second call doesn't duplicate settings entries).
  3. install creates a backup when overwriting existing hooks.
  4. verify detects a missing hook.
  5. uninstall reads the manifest and reverses the install.
  6. install errors cleanly when the target Claude Code dir is missing.

Tests NEVER touch the operator's real ~/.claude/. Each test patches
CC_DIR to a tempfile.TemporaryDirectory().
"""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake.commands.adapt import cc as cc_adapter
from swanlake import state as _state


def _ns(**kw) -> Namespace:
    defaults = {
        "json": False,
        "quiet": False,
        "cmd": "adapt",
        "adapt_target": "cc",
        "dry_run": False,
        "uninstall": False,
        "cc_dir": None,
        "skill_only": False,
    }
    defaults.update(kw)
    return Namespace(**defaults)


class CCAdapterTest(unittest.TestCase):
    def setUp(self):
        # Tmp state root for manifest writes.
        self._tmpdir_state = tempfile.TemporaryDirectory()
        self.tmp_state = Path(self._tmpdir_state.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp_state)

        # Tmp CC dir -- ALWAYS use this in tests, never ~/.claude.
        self._tmpdir_cc = tempfile.TemporaryDirectory()
        self.tmp_cc = Path(self._tmpdir_cc.name) / ".claude"
        self.tmp_cc.mkdir(parents=True)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmpdir_state.cleanup()
        self._tmpdir_cc.cleanup()

    def _adapter(self):
        return cc_adapter.ClaudeCodeAdapter(cc_dir=self.tmp_cc)

    def test_install_creates_hook_files(self):
        adapter = self._adapter()
        rc = adapter.install()
        self.assertEqual(rc, 0)
        for hook_name in cc_adapter.HOOK_NAMES:
            hp = self.tmp_cc / "hooks" / hook_name
            self.assertTrue(hp.exists(), f"missing hook: {hp}")
            # Executable bit set.
            self.assertTrue(hp.stat().st_mode & 0o100)
        # All bundled skills installed -- discovered dynamically so the
        # test stays correct if more skills land in the templates dir.
        skill_templates = cc_adapter._discover_skill_templates()
        self.assertGreaterEqual(len(skill_templates), 1)
        for skill_name, _src in skill_templates:
            self.assertTrue(
                (self.tmp_cc / "skills" / skill_name / "SKILL.md").exists(),
                f"missing skill: {skill_name}",
            )
        # Manifest written.
        self.assertTrue(adapter.manifest_path.exists())
        manifest = json.loads(adapter.manifest_path.read_text())
        # 4 hooks + N skills installed entries.
        installed_paths = {e["path"] for e in manifest["installed"]}
        self.assertEqual(
            len(installed_paths),
            len(cc_adapter.HOOK_NAMES) + len(skill_templates),
        )
        # skills_installed manifest field tracks every skill by name.
        self.assertEqual(
            sorted(manifest.get("skills_installed", [])),
            sorted(name for name, _ in skill_templates),
        )

    def test_install_is_idempotent(self):
        adapter = self._adapter()
        rc1 = adapter.install()
        # Snapshot mtimes after first install.
        hooks = list((self.tmp_cc / "hooks").iterdir())
        mtimes_before = {p.name: p.stat().st_mtime for p in hooks}
        # Settings.json count of canary-match command entries.
        settings = json.loads(adapter.settings_path.read_text())
        post_use = settings["hooks"]["PostToolUse"]
        canary_cmd = str(self.tmp_cc / "hooks" / "canary-match.sh")
        count_before = sum(
            1 for entry in post_use
            for h in (entry.get("hooks") or [])
            if isinstance(h, dict) and h.get("command") == canary_cmd
        )

        rc2 = adapter.install()
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)

        # Hook files unchanged on second install (content was identical).
        for p in (self.tmp_cc / "hooks").iterdir():
            if p.name.endswith(".bak"):
                continue
            self.assertEqual(mtimes_before.get(p.name), p.stat().st_mtime,
                             f"{p.name} was rewritten on idempotent install")
        # settings.json must NOT have a duplicated canary-match entry.
        settings2 = json.loads(adapter.settings_path.read_text())
        post_use2 = settings2["hooks"]["PostToolUse"]
        count_after = sum(
            1 for entry in post_use2
            for h in (entry.get("hooks") or [])
            if isinstance(h, dict) and h.get("command") == canary_cmd
        )
        self.assertEqual(count_before, count_after,
                         "settings.json duplicated the hook entry on re-install")

    def test_install_creates_backup_when_overwriting(self):
        adapter = self._adapter()
        # Pre-existing different hook content -> install must back it up.
        target = self.tmp_cc / "hooks" / "canary-match.sh"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/usr/bin/env bash\necho prior content\n")
        rc = adapter.install()
        self.assertEqual(rc, 0)
        # Backup file exists in same dir with .bak-swanlake- prefix.
        backups = list((self.tmp_cc / "hooks").glob("canary-match.sh.bak-swanlake-*"))
        self.assertEqual(len(backups), 1, f"expected exactly one backup; saw {backups}")
        # Backup carries the prior content.
        self.assertIn("prior content", backups[0].read_text())

    def test_verify_detects_missing_hook(self):
        adapter = self._adapter()
        adapter.install()
        # Remove one hook; verify must report it missing.
        (self.tmp_cc / "hooks" / "canary-match.sh").unlink()
        results = list(adapter.verify())
        canary = next(r for r in results if r.surface_id == "canary-match.sh")
        self.assertEqual(canary.status, "missing")
        # Other hooks still intact.
        skill = next(r for r in results if r.surface_id == "skill")
        self.assertEqual(skill.status, "intact")

    def test_uninstall_reads_manifest(self):
        adapter = self._adapter()
        adapter.install()
        # Sanity: hooks present.
        self.assertTrue((self.tmp_cc / "hooks" / "canary-match.sh").exists())
        skill_templates = cc_adapter._discover_skill_templates()
        # Sanity: every bundled skill landed.
        for skill_name, _src in skill_templates:
            self.assertTrue(
                (self.tmp_cc / "skills" / skill_name / "SKILL.md").exists(),
                f"setup: {skill_name} not installed",
            )

        rc = adapter.uninstall()
        self.assertEqual(rc, 0)
        # All four hooks removed.
        for hook_name in cc_adapter.HOOK_NAMES:
            self.assertFalse(
                (self.tmp_cc / "hooks" / hook_name).exists(),
                f"{hook_name} not removed",
            )
        # Every bundled skill removed (manifest-driven, not hardcoded).
        for skill_name, _src in skill_templates:
            self.assertFalse(
                (self.tmp_cc / "skills" / skill_name / "SKILL.md").exists(),
                f"{skill_name} SKILL.md not removed",
            )
            # Now-empty skill dir should have been pruned too.
            self.assertFalse(
                (self.tmp_cc / "skills" / skill_name).exists(),
                f"{skill_name} dir not pruned",
            )
        # Manifest removed.
        self.assertFalse(adapter.manifest_path.exists())

    def test_install_without_cc_dir_errors_cleanly(self):
        missing = Path(self._tmpdir_cc.name) / "nonexistent-claude"
        adapter = cc_adapter.ClaudeCodeAdapter(cc_dir=missing)
        captured_err = io.StringIO()
        with patch("sys.stderr", captured_err):
            rc = adapter.install()
        self.assertEqual(rc, 2)
        self.assertIn("does not exist", captured_err.getvalue())

    def test_uninstall_removes_settings_entries(self):
        """Regression for F1: uninstall must drop the settings.json hook
        entries it added, not just the hook script files. Otherwise the
        operator's CC session is left pointing at missing files."""
        adapter = self._adapter()

        # Install populates settings.json with our hook entries.
        rc = adapter.install()
        self.assertEqual(rc, 0)

        settings = json.loads(adapter.settings_path.read_text())
        canary_cmd = str(self.tmp_cc / "hooks" / "canary-match.sh")
        firewall_cmd = str(self.tmp_cc / "hooks" / "bash-firewall.sh")

        def _has_command(settings_dict, event, command):
            bucket = (settings_dict.get("hooks") or {}).get(event) or []
            for entry in bucket:
                if isinstance(entry, dict):
                    for h in entry.get("hooks") or []:
                        if isinstance(h, dict) and h.get("command") == command:
                            return True
            return False

        self.assertTrue(_has_command(settings, "PostToolUse", canary_cmd))
        self.assertTrue(_has_command(settings, "PreToolUse", firewall_cmd))

        # Manifest must record the additions for later cleanup.
        manifest = json.loads(adapter.manifest_path.read_text())
        added = manifest.get("settings_added") or []
        commands_recorded = {entry.get("command") for entry in added}
        self.assertIn(canary_cmd, commands_recorded)
        self.assertIn(firewall_cmd, commands_recorded)

        # Uninstall must drop those entries from settings.json.
        rc2 = adapter.uninstall()
        self.assertEqual(rc2, 0)

        if adapter.settings_path.exists():
            settings_after = json.loads(adapter.settings_path.read_text())
            self.assertFalse(
                _has_command(settings_after, "PostToolUse", canary_cmd),
                "settings.json still references removed canary-match hook",
            )
            self.assertFalse(
                _has_command(settings_after, "PreToolUse", firewall_cmd),
                "settings.json still references removed bash-firewall hook",
            )

    def test_install_warns_on_malformed_hooks_bucket(self):
        """F8: when settings.json has hooks.<event> as a non-list (string,
        dict, ...), _patch_settings used to silently return False and the
        operator wondered why hooks never fired. Now it warns to stderr."""
        adapter = self._adapter()
        # Pre-populate settings.json with PostToolUse as a string -- a
        # schema-broken value the adapter cannot patch.
        adapter.settings_path.write_text(json.dumps({
            "hooks": {"PostToolUse": "this should have been a list"}
        }, indent=2))

        captured_err = io.StringIO()
        with patch("sys.stderr", captured_err):
            rc = adapter.install()
        # Install completes (other surfaces still get installed) but the
        # malformed bucket triggered a stderr warning.
        self.assertEqual(rc, 0)
        err = captured_err.getvalue()
        self.assertIn("swanlake adapt cc:", err)
        self.assertIn("PostToolUse", err)
        self.assertIn("not a list", err)
        # The string we planted survives -- we did not silently overwrite it.
        settings_after = json.loads(adapter.settings_path.read_text())
        self.assertEqual(
            settings_after["hooks"]["PostToolUse"],
            "this should have been a list",
        )

    def test_atomic_write_preserves_existing_mode(self):
        """Regression for v0.2.1 #4: _atomic_write defaulted to mode=0o644
        and unconditionally chmod-ed, so an operator who tightened
        ~/.claude/settings.json to 0o600 (sane for a file with personal
        API tokens) saw it widened back to 0o644 on every adapt run.

        The new contract: when mode=None (the default) and the target
        file exists, inherit its current mode."""
        target = Path(self._tmpdir_cc.name) / "preserve-mode-target"
        target.write_text("initial\n", encoding="utf-8")
        os.chmod(target, 0o600)
        # Sanity precondition.
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

        cc_adapter._atomic_write(target, "rewritten\n")
        self.assertEqual(target.read_text(), "rewritten\n")
        # Mode preserved -- did NOT widen to 0o644.
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_atomic_write_uses_default_mode_for_new_file(self):
        """A brand-new file (no prior mode to inherit) lands at 0o644."""
        target = Path(self._tmpdir_cc.name) / "brand-new-target"
        self.assertFalse(target.exists())
        cc_adapter._atomic_write(target, "fresh\n")
        self.assertTrue(target.exists())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_atomic_write_explicit_mode_overrides_inherit(self):
        """Hook scripts pass mode=0o755 explicitly; that must win even if
        the file already exists with a tighter mode."""
        target = Path(self._tmpdir_cc.name) / "hook-target.sh"
        target.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        os.chmod(target, 0o600)
        cc_adapter._atomic_write(target, "#!/usr/bin/env bash\nnew\n", mode=0o755)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

    def test_install_does_not_widen_hardened_settings_mode(self):
        """End-to-end variant of the regression: an operator pre-hardens
        settings.json to 0o600, then runs adapt cc. After install, mode
        must still be 0o600 (not 0o644)."""
        adapter = self._adapter()
        # Pre-create a hardened settings.json with valid empty hooks dict.
        adapter.settings_path.write_text("{}\n", encoding="utf-8")
        os.chmod(adapter.settings_path, 0o600)

        rc = adapter.install()
        self.assertEqual(rc, 0)
        # File was patched (hooks added), so the write path ran.
        post = json.loads(adapter.settings_path.read_text())
        self.assertIn("hooks", post)
        # And the mode survived.
        self.assertEqual(
            stat.S_IMODE(adapter.settings_path.stat().st_mode), 0o600,
            "adapt cc widened a hardened settings.json from 0o600 to 0o644",
        )

    def test_uninstall_preserves_unrelated_settings_entries(self):
        """Operator-managed hook entries unrelated to swanlake must survive
        an uninstall pass -- we only drop the entries we added."""
        adapter = self._adapter()

        # Pre-populate settings.json with an operator hook we did NOT install.
        operator_cmd = "/usr/local/bin/operator-only-hook.sh"
        operator_settings = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [{"type": "command", "command": operator_cmd}],
                    }
                ]
            }
        }
        adapter.settings_path.write_text(json.dumps(operator_settings, indent=2))

        adapter.install()
        adapter.uninstall()

        # Operator hook still present.
        self.assertTrue(adapter.settings_path.exists())
        settings_after = json.loads(adapter.settings_path.read_text())
        post = (settings_after.get("hooks") or {}).get("PostToolUse") or []
        operator_still_present = any(
            isinstance(e, dict)
            and any(
                isinstance(h, dict) and h.get("command") == operator_cmd
                for h in (e.get("hooks") or [])
            )
            for e in post
        )
        self.assertTrue(
            operator_still_present,
            "uninstall destroyed an operator-managed hook entry",
        )


class CCSkillOnlyTest(unittest.TestCase):
    """Tests for `swanlake adapt cc --skill-only` (v0.2.1 #8).

    The flag lets operators with their own production hooks install just
    the /swanlake slash-command skill without replacing or patching
    anything else. Same flag on uninstall reverses only the skill,
    leaving prior full-install state intact for a later non-skill-only
    cleanup pass.
    """

    def setUp(self):
        self._tmpdir_state = tempfile.TemporaryDirectory()
        self.tmp_state = Path(self._tmpdir_state.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp_state)

        self._tmpdir_cc = tempfile.TemporaryDirectory()
        self.tmp_cc = Path(self._tmpdir_cc.name) / ".claude"
        self.tmp_cc.mkdir(parents=True)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmpdir_state.cleanup()
        self._tmpdir_cc.cleanup()

    def _adapter(self):
        return cc_adapter.ClaudeCodeAdapter(cc_dir=self.tmp_cc)

    def test_skill_only_install_writes_only_skill(self):
        adapter = self._adapter()
        rc = adapter.install(skill_only=True)
        self.assertEqual(rc, 0)
        # All bundled skills present.
        skill_templates = cc_adapter._discover_skill_templates()
        for skill_name, _src in skill_templates:
            self.assertTrue(
                (self.tmp_cc / "skills" / skill_name / "SKILL.md").exists(),
                f"--skill-only missed skill {skill_name}",
            )
        # No hook files.
        hooks_dir = self.tmp_cc / "hooks"
        if hooks_dir.exists():
            for hook_name in cc_adapter.HOOK_NAMES:
                self.assertFalse(
                    (hooks_dir / hook_name).exists(),
                    f"--skill-only wrote hook script {hook_name}",
                )
        # No settings.json.
        self.assertFalse(
            adapter.settings_path.exists(),
            "--skill-only created settings.json (should never touch it)",
        )
        # Manifest present, records only the skills, and remembers the mode.
        self.assertTrue(adapter.manifest_path.exists())
        manifest = json.loads(adapter.manifest_path.read_text())
        self.assertTrue(manifest.get("skill_only"))
        kinds = {e.get("kind") for e in manifest.get("installed", [])}
        self.assertEqual(kinds, {"skill"})
        # skills_installed names every bundled skill.
        self.assertEqual(
            sorted(manifest.get("skills_installed", [])),
            sorted(name for name, _ in skill_templates),
        )

    def test_skill_only_install_does_not_touch_existing_settings(self):
        """Operator's preexisting settings.json must survive byte-identically
        and ALL bundled skills must land regardless of how many ship."""
        adapter = self._adapter()
        existing = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "*",
                        "hooks": [{"type": "command", "command": "/op/own.sh"}],
                    }
                ]
            },
            "operator_setting": True,
        }
        adapter.settings_path.write_text(
            json.dumps(existing, indent=2), encoding="utf-8"
        )
        before_bytes = adapter.settings_path.read_bytes()
        before_mtime = adapter.settings_path.stat().st_mtime

        # Sleep granularity guard: read mtime BEFORE install, ensure
        # the file is unchanged AFTER. We compare bytes for equality.
        rc = adapter.install(skill_only=True)
        self.assertEqual(rc, 0)
        # Bytes identical -- no patch ran.
        self.assertEqual(adapter.settings_path.read_bytes(), before_bytes)
        # mtime identical -- no rewrite even with same content.
        self.assertEqual(adapter.settings_path.stat().st_mtime, before_mtime)
        # All bundled skills landed despite skill-only mode.
        skill_templates = cc_adapter._discover_skill_templates()
        for skill_name, _src in skill_templates:
            self.assertTrue(
                (self.tmp_cc / "skills" / skill_name / "SKILL.md").exists(),
                f"--skill-only with operator settings missed {skill_name}",
            )

    def test_skill_only_dry_run_plans_only_skill(self):
        """Dry-run output for --skill-only must mention every bundled skill
        as its own action, with zero hook or patch-settings lines."""
        adapter = self._adapter()
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            rc = adapter.install(dry_run=True, skill_only=True)
        self.assertEqual(rc, 0)
        out = captured.getvalue()
        skill_templates = cc_adapter._discover_skill_templates()
        # Each bundled skill appears in the plan by name.
        for skill_name, _src in skill_templates:
            self.assertIn(
                skill_name, out,
                f"--skill-only dry-run did not mention skill {skill_name}",
            )
        # No hook lines, no patch-settings lines.
        self.assertNotIn("hook", out)
        self.assertNotIn("patch-settings", out)
        # Nothing actually written.
        for skill_name, _src in skill_templates:
            self.assertFalse(
                (self.tmp_cc / "skills" / skill_name / "SKILL.md").exists()
            )
        self.assertFalse(adapter.settings_path.exists())

    def test_skill_only_uninstall_after_skill_only_install(self):
        """A skill-only install reverses cleanly with --skill-only
        uninstall: every bundled skill removed."""
        adapter = self._adapter()
        adapter.install(skill_only=True)
        skill_templates = cc_adapter._discover_skill_templates()
        for skill_name, _src in skill_templates:
            self.assertTrue(
                (self.tmp_cc / "skills" / skill_name / "SKILL.md").exists()
            )
        rc = adapter.uninstall(skill_only=True)
        self.assertEqual(rc, 0)
        for skill_name, _src in skill_templates:
            self.assertFalse(
                (self.tmp_cc / "skills" / skill_name / "SKILL.md").exists(),
                f"--skill-only uninstall left {skill_name} behind",
            )
        # Manifest gone (it had nothing left after skill removal).
        self.assertFalse(adapter.manifest_path.exists())

    def test_skill_only_uninstall_preserves_full_install_entries(self):
        """If a prior full install left hook + settings entries in the
        manifest, a --skill-only uninstall must remove every skill
        and leave the rest intact for a later full uninstall pass."""
        adapter = self._adapter()
        # Full install populates manifest with hooks + skills + settings.
        adapter.install()
        # Pre-state sanity: skill + at least one hook + settings entries.
        full_manifest = json.loads(adapter.manifest_path.read_text())
        kinds_before = {e.get("kind") for e in full_manifest["installed"]}
        self.assertIn("skill", kinds_before)
        self.assertIn("hook", kinds_before)
        self.assertTrue(full_manifest.get("settings_added"))

        skill_templates = cc_adapter._discover_skill_templates()

        # Skill-only uninstall: every skill goes, hooks + settings stay.
        rc = adapter.uninstall(skill_only=True)
        self.assertEqual(rc, 0)
        for skill_name, _src in skill_templates:
            self.assertFalse(
                (self.tmp_cc / "skills" / skill_name / "SKILL.md").exists(),
                f"skill {skill_name} survived --skill-only uninstall",
            )
        # Hooks still present.
        for hook_name in cc_adapter.HOOK_NAMES:
            self.assertTrue(
                (self.tmp_cc / "hooks" / hook_name).exists(),
                f"--skill-only uninstall removed hook {hook_name}",
            )
        # Manifest still records the surviving entries.
        self.assertTrue(adapter.manifest_path.exists())
        leftover = json.loads(adapter.manifest_path.read_text())
        kinds_after = {e.get("kind") for e in leftover["installed"]}
        self.assertNotIn("skill", kinds_after)
        self.assertIn("hook", kinds_after)
        self.assertTrue(leftover.get("settings_added"))
        # skills_installed cleared.
        self.assertEqual(leftover.get("skills_installed", []), [])


class CCMultiSkillTest(unittest.TestCase):
    """v0.2.1 #9: multi-skill install/uninstall + sha256 idempotency tests."""

    def setUp(self):
        self._tmpdir_state = tempfile.TemporaryDirectory()
        self.tmp_state = Path(self._tmpdir_state.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp_state)

        self._tmpdir_cc = tempfile.TemporaryDirectory()
        self.tmp_cc = Path(self._tmpdir_cc.name) / ".claude"
        self.tmp_cc.mkdir(parents=True)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmpdir_state.cleanup()
        self._tmpdir_cc.cleanup()

    def _adapter(self):
        return cc_adapter.ClaudeCodeAdapter(cc_dir=self.tmp_cc)

    def test_install_skips_skill_if_content_matches(self):
        """Pre-create a skill with byte-identical template content; install
        must report it as `noop-skill` and not rewrite the file."""
        adapter = self._adapter()
        skill_templates = cc_adapter._discover_skill_templates()
        # Pick the first skill alphabetically and pre-seed it from the
        # template -- byte-identical to what the adapter would write.
        skill_name, src = skill_templates[0]
        dst = self.tmp_cc / "skills" / skill_name / "SKILL.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        template_text = src.read_text(encoding="utf-8")
        dst.write_text(template_text, encoding="utf-8")
        mtime_before = dst.stat().st_mtime

        # Dry-run plan must mark this skill as noop-skill.
        plan = adapter._plan(skill_only=True)
        actions_for_skill = [
            step for step in plan if step.get("skill") == skill_name
        ]
        self.assertEqual(len(actions_for_skill), 1)
        self.assertEqual(actions_for_skill[0]["action"], "noop-skill")

        # Real install must not rewrite the file (mtime unchanged).
        rc = adapter.install(skill_only=True)
        self.assertEqual(rc, 0)
        self.assertEqual(
            dst.stat().st_mtime, mtime_before,
            "byte-identical skill was rewritten on install",
        )
        self.assertEqual(
            dst.read_text(encoding="utf-8"), template_text,
            "byte-identical skill content drifted on install",
        )
        # No backup file created for an unchanged skill.
        backups = list(dst.parent.glob(f"{dst.name}.bak-swanlake-*"))
        self.assertEqual(backups, [])

    def test_install_overwrites_skill_if_content_differs(self):
        """Pre-create a skill with operator-modified content; install must
        report `update-skill`, back up the prior content, and overwrite
        with the template."""
        adapter = self._adapter()
        skill_templates = cc_adapter._discover_skill_templates()
        skill_name, src = skill_templates[0]
        dst = self.tmp_cc / "skills" / skill_name / "SKILL.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        operator_content = "# operator's pinned skill content\n"
        dst.write_text(operator_content, encoding="utf-8")

        # Plan must mark this skill as update-skill.
        plan = adapter._plan(skill_only=True)
        actions_for_skill = [
            step for step in plan if step.get("skill") == skill_name
        ]
        self.assertEqual(len(actions_for_skill), 1)
        self.assertEqual(actions_for_skill[0]["action"], "update-skill")

        # Real install must back up + overwrite.
        rc = adapter.install(skill_only=True)
        self.assertEqual(rc, 0)
        template_text = src.read_text(encoding="utf-8")
        self.assertEqual(
            dst.read_text(encoding="utf-8"), template_text,
            "update-skill did not overwrite with template",
        )
        backups = list(dst.parent.glob(f"{dst.name}.bak-swanlake-*"))
        self.assertEqual(
            len(backups), 1,
            f"expected exactly one backup of operator skill; saw {backups}",
        )
        self.assertEqual(
            backups[0].read_text(encoding="utf-8"), operator_content,
            "backup did not preserve the operator's prior skill content",
        )
        # Manifest tracks the overwrite for restore on uninstall.
        manifest = json.loads(adapter.manifest_path.read_text())
        modified_paths = {e.get("path") for e in manifest.get("modified", [])}
        self.assertIn(str(dst), modified_paths)

    def test_dry_run_lists_each_skill_action_separately(self):
        """Per-skill plan visibility: every bundled skill must appear on
        its own line in the dry-run output, with its action verb and
        skill name."""
        adapter = self._adapter()
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            rc = adapter.install(dry_run=True, skill_only=True)
        self.assertEqual(rc, 0)
        out = captured.getvalue()
        skill_templates = cc_adapter._discover_skill_templates()
        skill_lines = [
            line for line in out.splitlines()
            if "skill" in line.lower()
        ]
        # One line per bundled skill.
        self.assertEqual(
            len(skill_lines), len(skill_templates),
            f"expected {len(skill_templates)} skill lines, "
            f"saw {len(skill_lines)}: {skill_lines}",
        )
        # Each skill named in its own line. Match by destination path
        # so the catch-all ``swanlake`` (a substring of ``swanlake-*``)
        # disambiguates correctly.
        for skill_name, _src in skill_templates:
            dst_token = f"/skills/{skill_name}/SKILL.md"
            matching = [line for line in skill_lines if dst_token in line]
            self.assertEqual(
                len(matching), 1,
                f"skill {skill_name} not on its own line: "
                f"matches={matching}",
            )

    def test_install_handles_mixed_skill_states(self):
        """Setup: one skill matches (noop), one differs (update),
        one missing (create). All three actions must coexist correctly
        on a single install pass."""
        adapter = self._adapter()
        skill_templates = cc_adapter._discover_skill_templates()
        if len(skill_templates) < 3:
            self.skipTest("need at least 3 bundled skills for this test")
        match_name, match_src = skill_templates[0]
        diff_name, _diff_src = skill_templates[1]
        # third stays missing -- skill_templates[2]

        # Pre-seed match_name with template content.
        match_dst = self.tmp_cc / "skills" / match_name / "SKILL.md"
        match_dst.parent.mkdir(parents=True, exist_ok=True)
        match_dst.write_text(match_src.read_text(encoding="utf-8"))
        # Pre-seed diff_name with operator content.
        diff_dst = self.tmp_cc / "skills" / diff_name / "SKILL.md"
        diff_dst.parent.mkdir(parents=True, exist_ok=True)
        diff_dst.write_text("# pinned\n", encoding="utf-8")

        plan = adapter._plan(skill_only=True)
        actions_by_skill = {
            step.get("skill"): step.get("action")
            for step in plan if "skill" in step
        }
        self.assertEqual(actions_by_skill[match_name], "noop-skill")
        self.assertEqual(actions_by_skill[diff_name], "update-skill")
        # Every other bundled skill is create-skill.
        for skill_name, _src in skill_templates[2:]:
            self.assertEqual(actions_by_skill[skill_name], "create-skill")


if __name__ == "__main__":
    unittest.main()
