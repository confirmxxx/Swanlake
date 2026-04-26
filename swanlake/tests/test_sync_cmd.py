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


if __name__ == "__main__":
    unittest.main()
