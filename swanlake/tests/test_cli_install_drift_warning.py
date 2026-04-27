"""Tests for swanlake.cli._maybe_warn_install_drift — startup drift check.

Spec: docs/v0.3.x-worktree-install-isolation-spec.md T5 (test_cli_install_drift_warning).

Cases:
  1. No marker present -> no warning, exit 0.
  2. Marker matches runtime -> no warning, exit 0.
  3. Marker mismatches runtime -> warning printed once to stderr.
  4. --quiet anywhere in argv suppresses the warning.
  5. SWANLAKE_NO_INSTALL_DRIFT_WARN=1 suppresses the warning.
  6. check_drift raising must not crash the CLI.
  7. --version from a drifted install still warns (early-exit path).
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake import cli as cli_mod
from swanlake import install_marker
from swanlake import state as _state


class CLIDriftWarningTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.state = self.tmp / "state"
        self.state.mkdir()
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.state)
        # Snapshot env so per-test mutations don't leak.
        self._env_snapshot = os.environ.get(install_marker.DRIFT_WARN_ENV)
        os.environ.pop(install_marker.DRIFT_WARN_ENV, None)
        # Force the install-marker module to look at our state root too —
        # check_drift defaults to its own _state_root() helper which reads
        # SWANLAKE_STATE_ROOT, so set the env var to point there.
        self._stateroot_env_snapshot = os.environ.get("SWANLAKE_STATE_ROOT")
        os.environ["SWANLAKE_STATE_ROOT"] = str(self.state)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        if self._env_snapshot is None:
            os.environ.pop(install_marker.DRIFT_WARN_ENV, None)
        else:
            os.environ[install_marker.DRIFT_WARN_ENV] = self._env_snapshot
        if self._stateroot_env_snapshot is None:
            os.environ.pop("SWANLAKE_STATE_ROOT", None)
        else:
            os.environ["SWANLAKE_STATE_ROOT"] = self._stateroot_env_snapshot
        self._tmpdir.cleanup()

    def _capture_stderr(self, argv):
        buf = io.StringIO()
        with patch.object(sys, "stderr", buf):
            cli_mod._maybe_warn_install_drift(argv)
        return buf.getvalue()

    def test_no_marker_no_warning(self):
        # No marker file at all -> silent, no exception.
        out = self._capture_stderr(["--version"])
        self.assertEqual(out, "")

    def test_marker_matches_runtime_no_warning(self):
        # Marker points at the actual runtime source dir.
        runtime = install_marker._runtime_source_dir()
        install_marker.write_marker(runtime, state_root=self.state)
        out = self._capture_stderr(["status"])
        self.assertEqual(out, "")

    def test_marker_drift_emits_warning(self):
        # Marker points at a fake other dir.
        bogus = self.tmp / "agent-worktree"
        bogus.mkdir()
        install_marker.write_marker(bogus, state_root=self.state)
        out = self._capture_stderr(["status"])
        self.assertIn("warning:", out)
        self.assertIn("install marker", out)
        self.assertIn(str(bogus.resolve()), out)
        self.assertIn("pip install --force-reinstall", out)
        self.assertIn("pipx", out)

    def test_quiet_flag_suppresses_warning(self):
        bogus = self.tmp / "agent-worktree"
        bogus.mkdir()
        install_marker.write_marker(bogus, state_root=self.state)
        out = self._capture_stderr(["status", "--quiet"])
        self.assertEqual(out, "")

    def test_env_override_suppresses_warning(self):
        bogus = self.tmp / "agent-worktree"
        bogus.mkdir()
        install_marker.write_marker(bogus, state_root=self.state)
        os.environ[install_marker.DRIFT_WARN_ENV] = "1"
        out = self._capture_stderr(["status"])
        self.assertEqual(out, "")

    def test_check_drift_raising_does_not_crash(self):
        bogus = self.tmp / "agent-worktree"
        bogus.mkdir()
        install_marker.write_marker(bogus, state_root=self.state)
        with patch.object(install_marker, "check_drift", side_effect=RuntimeError("boom")):
            # Must not raise.
            out = self._capture_stderr(["status"])
        self.assertEqual(out, "")

    def test_version_argv_still_triggers_check(self):
        """`--version` SystemExits inside parse_args. The drift check runs
        before parse_args, so a drifted install still warns even when
        the only thing the operator typed was `swanlake --version`.
        """
        bogus = self.tmp / "agent-worktree"
        bogus.mkdir()
        install_marker.write_marker(bogus, state_root=self.state)
        # Direct call: _maybe_warn_install_drift is the layer under test
        # and it's called pre-parse_args inside main(). Verify the check
        # itself fires for argv=['--version'].
        out = self._capture_stderr(["--version"])
        self.assertIn("warning:", out)

    def test_main_with_version_argv_still_warns(self):
        """End-to-end: calling main(['--version']) emits the warning to
        stderr before SystemExit fires from action='version'.
        """
        bogus = self.tmp / "agent-worktree"
        bogus.mkdir()
        install_marker.write_marker(bogus, state_root=self.state)
        buf_err = io.StringIO()
        buf_out = io.StringIO()
        with patch.object(sys, "stderr", buf_err), \
             patch.object(sys, "stdout", buf_out):
            with self.assertRaises(SystemExit):
                cli_mod.main(["--version"])
        # The version line lands on stdout (argparse default for action='version'
        # in py3.4+); the drift warning lands on stderr. Both must appear.
        self.assertIn("warning:", buf_err.getvalue())
        self.assertIn("install marker", buf_err.getvalue())


if __name__ == "__main__":
    unittest.main()
