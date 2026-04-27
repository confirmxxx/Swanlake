"""Tests for the install-marker doctor probe (10th probe).

Spec: docs/v0.3.x-worktree-install-isolation-spec.md T3 + T5.

Cases:
  1. Probe pass when marker matches runtime
  2. Probe pass under cross-interpreter (multi-python host)
  3. Probe warn when marker is missing
  4. Probe fail when marker source mismatches runtime
  5. --fix-suggestions surfaces both paths and the remediation command
  6. Probe is registered in the PROBES tuple at the documented position
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

from swanlake import install_marker
from swanlake import state as _state
from swanlake.commands import doctor as doctor_cmd


def _ns(**kw) -> Namespace:
    defaults = {
        "json": False,
        "quiet": False,
        "cmd": "doctor",
        "fix_suggestions": False,
    }
    defaults.update(kw)
    return Namespace(**defaults)


class InstallMarkerProbeTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.state = self.tmp / "state"
        self.state.mkdir()
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.state)
        # install_marker.check_drift() reads its own state root via the
        # SWANLAKE_STATE_ROOT env var (it doesn't share the module-level
        # _STATE_ROOT mutable in swanlake.state). Set both.
        self._stateroot_env_snapshot = os.environ.get("SWANLAKE_STATE_ROOT")
        os.environ["SWANLAKE_STATE_ROOT"] = str(self.state)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        if self._stateroot_env_snapshot is None:
            os.environ.pop("SWANLAKE_STATE_ROOT", None)
        else:
            os.environ["SWANLAKE_STATE_ROOT"] = self._stateroot_env_snapshot
        self._tmpdir.cleanup()

    def test_probe_pass_when_marker_matches(self):
        runtime = install_marker._runtime_source_dir()
        install_marker.write_marker(runtime, state_root=self.state)
        result = doctor_cmd._probe_install_marker()
        self.assertEqual(result["status"], "pass")

    def test_probe_pass_when_cross_interpreter(self):
        bogus = self.tmp / "other-interp-source"
        bogus.mkdir()
        body = (
            f"source_path={bogus.resolve()}\n"
            "installed_at=2026-04-26T12:00:00+00:00\n"
            "python_executable=/usr/bin/python3.99\n"
        )
        (self.state / install_marker.MARKER_FILENAME).write_text(body)
        result = doctor_cmd._probe_install_marker()
        self.assertEqual(result["status"], "pass")
        self.assertIn("multi-interpreter", result["detail"])

    def test_probe_warn_when_marker_missing(self):
        # No marker file written.
        result = doctor_cmd._probe_install_marker()
        self.assertEqual(result["status"], "warn")
        self.assertIn("no install marker", result["detail"])
        self.assertIn("pip install --force-reinstall", result["fix"])
        self.assertIn("pipx", result["fix"])

    def test_probe_fail_when_marker_drifts(self):
        bogus = self.tmp / "agent-worktree"
        bogus.mkdir()
        install_marker.write_marker(bogus, state_root=self.state)
        result = doctor_cmd._probe_install_marker()
        self.assertEqual(result["status"], "fail")
        self.assertIn("worktree-install drift", result["detail"])
        self.assertIn(str(bogus.resolve()), result["detail"])
        self.assertIn("pip install --force-reinstall", result["fix"])

    def test_probe_registered_in_PROBES_at_position_10(self):
        names = [name for name, _ in doctor_cmd.PROBES]
        self.assertEqual(len(names), 10)
        self.assertEqual(names[-1], "install-marker matches runtime")

    def test_fix_suggestions_renders_marker_paths_in_table(self):
        bogus = self.tmp / "agent-worktree"
        bogus.mkdir()
        install_marker.write_marker(bogus, state_root=self.state)

        # Run doctor end-to-end; the install-marker probe should surface
        # both the runtime and marker paths in the detail column when
        # --fix-suggestions is set.
        captured = io.StringIO()
        with patch.object(sys, "stdout", captured):
            rc = doctor_cmd.run(_ns(fix_suggestions=True))
        out = captured.getvalue()
        self.assertEqual(rc, 2)  # ALARM — drift is a fail
        self.assertIn("install-marker", out)
        self.assertIn(str(bogus.resolve()), out)
        self.assertIn("fix:", out)
        self.assertIn("force-reinstall", out)


if __name__ == "__main__":
    unittest.main()
