"""Tests for swanlake.commands.sync -- confirmation gating + dispatch.

Spec section A7. Cases:
  1. Confirm-returns-False -> sync function never called, exit 0.
  2. --yes -> sync function called once, exit code passed through.
  3. SWANLAKE_NONINTERACTIVE env -> bypasses prompt without --yes.
  4. Non-TTY without --yes / NONINTERACTIVE -> exit 2 (USAGE).
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake.commands import sync as sync_cmd
from swanlake import state as _state


def _ns(**kw) -> Namespace:
    defaults = {
        "json": False,
        "quiet": False,
        "cmd": "sync",
        "yes": False,
        "dry_run": False,
    }
    defaults.update(kw)
    return Namespace(**defaults)


class SyncConfirmGatingTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)
        # Force NONINTERACTIVE off; individual tests opt in.
        self._env_patch = patch.dict(
            os.environ, {"SWANLAKE_NONINTERACTIVE": ""}, clear=False
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        _state.set_state_root(self._original_root)
        self._tmpdir.cleanup()

    def test_aborts_when_operator_says_no(self):
        """Operator types 'n' at the prompt -> sync NOT called, exit 0."""
        captured = io.StringIO()
        called = []

        def fake_run_sync_all():
            called.append(True)
            return 0

        # Simulate TTY + 'n' input.
        with patch("sys.stdin.isatty", return_value=True), \
             patch("swanlake.commands.sync._is_tty", return_value=True), \
             patch("swanlake.safety.sys.stdin.isatty", return_value=True), \
             patch("builtins.input", return_value="n"), \
             patch("reconciler.sync_vault.run_sync_all", side_effect=fake_run_sync_all), \
             patch("sys.stdout", captured):
            rc = sync_cmd.run(_ns(yes=False))
        self.assertEqual(rc, 0)
        self.assertEqual(called, [], "sync must not be called when operator declines")
        self.assertIn("aborted", captured.getvalue())

    def test_yes_flag_bypasses_prompt(self):
        """--yes skips the prompt and dispatches to reconciler exactly once."""
        captured = io.StringIO()
        called = []

        def fake_run_sync_all():
            called.append(True)
            return 0

        with patch("reconciler.sync_vault.run_sync_all", side_effect=fake_run_sync_all), \
             patch("sys.stdout", captured):
            rc = sync_cmd.run(_ns(yes=True))
        self.assertEqual(rc, 0)
        self.assertEqual(len(called), 1)
        self.assertIn("auto-confirmed", captured.getvalue())

    def test_noninteractive_env_bypasses_prompt(self):
        """SWANLAKE_NONINTERACTIVE=1 bypasses the prompt without --yes."""
        captured = io.StringIO()
        called = []

        def fake_run_sync_all():
            called.append(True)
            return 0

        with patch.dict(os.environ, {"SWANLAKE_NONINTERACTIVE": "1"}), \
             patch("reconciler.sync_vault.run_sync_all", side_effect=fake_run_sync_all), \
             patch("sys.stdout", captured):
            rc = sync_cmd.run(_ns(yes=False))
        self.assertEqual(rc, 0)
        self.assertEqual(len(called), 1)

    def test_non_tty_without_yes_exits_two(self):
        """No TTY + no --yes + no NONINTERACTIVE -> exit 2 USAGE."""
        captured_err = io.StringIO()
        called = []

        def fake_run_sync_all():
            called.append(True)
            return 0

        with patch("swanlake.commands.sync._is_tty", return_value=False), \
             patch("reconciler.sync_vault.run_sync_all", side_effect=fake_run_sync_all), \
             patch("sys.stderr", captured_err):
            rc = sync_cmd.run(_ns(yes=False))
        self.assertEqual(rc, 2)
        self.assertEqual(called, [])
        self.assertIn("no TTY", captured_err.getvalue())

    def test_propagates_reconciler_exit_code(self):
        """When reconciler returns 1 (per-file errors), sync returns 1."""
        with patch("reconciler.sync_vault.run_sync_all", return_value=1), \
             patch("sys.stdout", io.StringIO()):
            rc = sync_cmd.run(_ns(yes=True))
        self.assertEqual(rc, 1)

    def test_sync_dry_run_does_not_call_reconciler(self):
        """--dry-run prints a preview and never invokes the reconciler.

        Bug #3: the v0.2.1 spec + the operator-facing skill assumed the
        flag existed; v0.2.2 ships it. The contract:
          - reconciler.sync_vault.run_sync_all is NOT called
          - exit code is 0 (always; preview-failure surfaces in stdout)
          - operator never sees a confirmation prompt
        """
        captured = io.StringIO()
        called = []

        def fake_run_sync_all():
            called.append(True)
            return 0

        # No TTY, no --yes, no NONINTERACTIVE -- normal sync would exit 2
        # USAGE here. --dry-run must short-circuit that gate too, since
        # it touches nothing and is safe in any environment (cron, CI,
        # the `swanlake-upd` flow that probes for the flag).
        with patch("swanlake.commands.sync._is_tty", return_value=False), \
             patch("reconciler.sync_vault.run_sync_all", side_effect=fake_run_sync_all), \
             patch("builtins.input") as mock_input, \
             patch("sys.stdout", captured):
            rc = sync_cmd.run(_ns(yes=False, dry_run=True))

        self.assertEqual(rc, 0, "--dry-run must exit 0 even when preview is degraded")
        self.assertEqual(
            called, [],
            "reconciler.sync_vault.run_sync_all must not be called on --dry-run",
        )
        self.assertEqual(
            mock_input.call_count, 0,
            "--dry-run must never prompt the operator",
        )
        out = captured.getvalue()
        self.assertIn("dry-run", out, "preview banner must mention dry-run")
        self.assertIn("exit: 0", out, "preview must end with explicit exit 0 line")


if __name__ == "__main__":
    unittest.main()
