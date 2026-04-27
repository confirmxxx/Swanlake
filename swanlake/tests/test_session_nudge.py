"""Tests for the v0.4 L2 SessionStart nudge hook + adapter wiring.

Two test classes:

  SessionNudgeHookTest -- subprocess against the bundled bash script.
    Verifies the hook's contract (exit 0 always, stderr-only output,
    silence on opt-out / clean / out-of-scope, nudge on the
    actionable case).

  SessionNudgeAdapterTest -- enable/disable verbs on the CC adapter.
    Verifies the script lands in ~/.claude/hooks/, settings.json
    grows a SessionStart entry, the manifest tracks it, and disable
    reverses everything.

Tests NEVER touch the operator's real ~/.claude/. Each test patches
CC_DIR to a tempfile.TemporaryDirectory() and runs the hook against
synthetic project trees under the same tmp.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake.commands.adapt import cc as cc_adapter
from swanlake import state as _state


# Public sentinel from defense-beacon/SPEC.md. Not a canary literal.
BEACON_HEADER = "<!-- DEFENSE BEACON v1 -- do not remove. Surface: test -->"


def _hook_script_path() -> Path:
    """Path to the bundled SessionStart hook script."""
    return (
        Path(__file__).resolve().parents[1]
        / "adapters"
        / "templates"
        / "cc"
        / "hooks"
        / "swanlake-session-nudge.sh"
    )


def _run_hook(
    *,
    project_dir: Path,
    nudge_scope: Path,
    stdin: str = "",
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Subprocess the hook with controlled env + stdin."""
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    env["SWANLAKE_NUDGE_SCOPE"] = str(nudge_scope)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(_hook_script_path())],
        input=stdin,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


class SessionNudgeHookTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.scope = self.tmp / "projects"
        self.scope.mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _mk_project(
        self,
        name: str,
        *,
        with_claude_md: bool = False,
        with_beacon: bool = False,
        with_optout: bool = False,
    ) -> Path:
        proj = self.scope / name
        proj.mkdir()
        if with_claude_md:
            body = f"# {name}\n"
            if with_beacon:
                body += "\n" + BEACON_HEADER + "\n"
            (proj / "CLAUDE.md").write_text(body)
        if with_optout:
            (proj / ".swanlake-no-beacon").write_text("")
        return proj

    def test_unbeaconed_project_prints_nudge(self):
        proj = self._mk_project("unbeaconed", with_claude_md=True)
        result = _run_hook(project_dir=proj, nudge_scope=self.scope)
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("swanlake:", result.stderr)
        self.assertIn("CLAUDE.md but no beacon attribution", result.stderr)
        self.assertIn("swanlake init project --type cc", result.stderr)
        self.assertIn(".swanlake-no-beacon", result.stderr)

    def test_beaconed_project_is_silent(self):
        proj = self._mk_project(
            "beaconed", with_claude_md=True, with_beacon=True
        )
        result = _run_hook(project_dir=proj, nudge_scope=self.scope)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_opted_out_project_is_silent(self):
        proj = self._mk_project(
            "opted-out",
            with_claude_md=True,
            with_optout=True,
        )
        result = _run_hook(project_dir=proj, nudge_scope=self.scope)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_no_claude_md_is_silent(self):
        proj = self._mk_project("no-claude-md")
        result = _run_hook(project_dir=proj, nudge_scope=self.scope)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_out_of_scope_is_silent(self):
        # Project lives outside the configured scope -> hook should
        # silently exit 0 even though CLAUDE.md is present and
        # unbeaconed.
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        (outside / "CLAUDE.md").write_text("# outside\n")
        result = _run_hook(project_dir=outside, nudge_scope=self.scope)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_ancestor_optout_marker_silences(self):
        # Drop the marker at the scope level itself; descendants
        # should be silenced.
        (self.scope / ".swanlake-no-beacon").write_text("")
        proj = self._mk_project("descendant", with_claude_md=True)
        result = _run_hook(project_dir=proj, nudge_scope=self.scope)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_hook_uses_stdin_cwd_when_env_unset(self):
        proj = self._mk_project("via-stdin", with_claude_md=True)
        # Empty CLAUDE_PROJECT_DIR (the env var is unset by passing
        # extra_env that does not include it -- but _run_hook always
        # sets it). Drop the var explicitly via subprocess invocation.
        env = os.environ.copy()
        env["SWANLAKE_NUDGE_SCOPE"] = str(self.scope)
        env.pop("CLAUDE_PROJECT_DIR", None)
        payload = json.dumps({
            "session_id": "test", "transcript_path": "x",
            "cwd": str(proj),
        })
        result = subprocess.run(
            ["bash", str(_hook_script_path())],
            input=payload,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("swanlake:", result.stderr)
        self.assertIn(str(proj), result.stderr)

    def test_hook_exits_zero_on_garbage_stdin(self):
        # Garbage stdin must not propagate a non-zero exit.
        env = os.environ.copy()
        env["SWANLAKE_NUDGE_SCOPE"] = str(self.scope)
        env.pop("CLAUDE_PROJECT_DIR", None)
        result = subprocess.run(
            ["bash", str(_hook_script_path())],
            input="not-json{{{",
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0)


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
        "enable_session_nudge": False,
        "disable_session_nudge": False,
    }
    defaults.update(kw)
    return Namespace(**defaults)


class SessionNudgeAdapterTest(unittest.TestCase):
    """Exercise --enable-session-nudge / --disable-session-nudge."""

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

    def test_enable_drops_hook_and_patches_settings(self):
        adapter = self._adapter()
        rc = adapter.enable_session_nudge()
        self.assertEqual(rc, 0)
        # Hook script lives in ~/.claude/hooks/.
        dst = self.tmp_cc / "hooks" / "swanlake-session-nudge.sh"
        self.assertTrue(dst.exists())
        # Executable.
        self.assertTrue(dst.stat().st_mode & 0o100)
        # settings.json carries a SessionStart hook entry.
        settings = json.loads((self.tmp_cc / "settings.json").read_text())
        bucket = settings["hooks"]["SessionStart"]
        self.assertEqual(len(bucket), 1)
        self.assertEqual(
            bucket[0]["hooks"][0]["command"], str(dst)
        )
        # Manifest tracks the install.
        manifest_path = _state.state_path(cc_adapter.MANIFEST_FILENAME)
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(
            manifest["session_nudge"]["path"], str(dst)
        )
        self.assertEqual(
            manifest["session_nudge"]["event"], "SessionStart"
        )

    def test_enable_is_idempotent(self):
        adapter = self._adapter()
        adapter.enable_session_nudge()
        # Second enable must not duplicate the settings entry.
        adapter.enable_session_nudge()
        settings = json.loads((self.tmp_cc / "settings.json").read_text())
        self.assertEqual(len(settings["hooks"]["SessionStart"]), 1)

    def test_disable_reverses_enable(self):
        adapter = self._adapter()
        adapter.enable_session_nudge()
        dst = self.tmp_cc / "hooks" / "swanlake-session-nudge.sh"
        self.assertTrue(dst.exists())
        # Disable.
        rc = adapter.disable_session_nudge()
        self.assertEqual(rc, 0)
        self.assertFalse(dst.exists())
        # settings.json no longer carries the SessionStart entry
        # (and the empty hooks dict is dropped).
        settings_path = self.tmp_cc / "settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
            self.assertNotIn("SessionStart", settings.get("hooks", {}))
        # Manifest no longer carries the session_nudge key.
        manifest_path = _state.state_path(cc_adapter.MANIFEST_FILENAME)
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            self.assertNotIn("session_nudge", manifest)

    def test_dry_run_writes_nothing(self):
        adapter = self._adapter()
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            rc = adapter.enable_session_nudge(dry_run=True)
        self.assertEqual(rc, 0)
        dst = self.tmp_cc / "hooks" / "swanlake-session-nudge.sh"
        self.assertFalse(dst.exists())
        self.assertIn("would: install SessionStart hook", captured.getvalue())

    def test_run_dispatcher_routes_enable(self):
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            rc = cc_adapter.run(_ns(
                enable_session_nudge=True,
                cc_dir=str(self.tmp_cc),
            ))
        self.assertEqual(rc, 0)
        dst = self.tmp_cc / "hooks" / "swanlake-session-nudge.sh"
        self.assertTrue(dst.exists())

    def test_run_dispatcher_rejects_both_flags(self):
        # --enable + --disable in the same call is a USAGE error.
        rc = cc_adapter.run(_ns(
            enable_session_nudge=True,
            disable_session_nudge=True,
            cc_dir=str(self.tmp_cc),
        ))
        self.assertEqual(rc, 2)

    def test_uninstall_full_sweep_removes_session_nudge(self):
        """A regular `swanlake adapt cc --uninstall` should also
        remove a previously-enabled session-nudge hook + settings entry,
        because the enable verb tracked it under installed[]."""
        adapter = self._adapter()
        adapter.install()  # also drops v0.2 hooks
        adapter.enable_session_nudge()
        dst = self.tmp_cc / "hooks" / "swanlake-session-nudge.sh"
        self.assertTrue(dst.exists())
        # Full uninstall.
        rc = adapter.uninstall()
        self.assertEqual(rc, 0)
        self.assertFalse(dst.exists())


if __name__ == "__main__":
    unittest.main()
