"""Tests for `swanlake beacon verify`.

The wrapper delegates LOCAL surfaces to swanlake.commands.verify.compute
and dispatches REMOTE surfaces to per-type checkers. Tests focus on:
  - LOCAL passthrough preserves the existing verify status semantics
  - REMOTE dispatch picks the right checker per type
  - Notion token absence yields `unconfigured` (not a hard failure)
  - claude-routine always returns `manual` (no API path)
  - the matched canary literal NEVER appears in any output
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake import coverage as _cov
from swanlake import state as _state
from swanlake.commands.beacon import verify as verify_cmd
from swanlake.exit_codes import CLEAN, DRIFT, USAGE


_PREFIX = "beacon-" + "attrib"


def _marker(surface: str, tail: str) -> str:
    return f"{_PREFIX}-{surface}-{tail}"


def _ns(**kw) -> Namespace:
    defaults = {
        "json": False,
        "quiet": False,
        "cmd": "beacon",
        "beacon_op": "verify",
        "surface": None,
        "since": None,
    }
    defaults.update(kw)
    return Namespace(**defaults)


class VerifyLocalPassthroughTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmp.cleanup()

    def test_local_intact_delegates_to_verify_compute(self):
        target = self.tmp / "CLAUDE.md"
        target.write_text(_marker("cms-x", "AbCd1234") + "\n")
        cov_payload = {
            "schema": 1,
            "surfaces": {"cms-x": {"source": "manual", "paths": [str(target)]}},
        }
        _cov._write_coverage(cov_payload)

        with patch.object(verify_cmd, "discover_surfaces_yaml", return_value=None):
            report = verify_cmd.compute(only_surface="cms-x")
        self.assertEqual(report["exit_code"], CLEAN)
        self.assertEqual(len(report["surfaces"]), 1)
        self.assertEqual(report["surfaces"][0]["status"], "intact")

    def test_local_drift_drives_drift_exit(self):
        target = self.tmp / "CLAUDE.md"
        target.write_text("# bare CLAUDE.md\n")
        cov_payload = {
            "schema": 1,
            "surfaces": {"cms-y": {"source": "manual", "paths": [str(target)]}},
        }
        _cov._write_coverage(cov_payload)

        with patch.object(verify_cmd, "discover_surfaces_yaml", return_value=None):
            report = verify_cmd.compute(only_surface="cms-y")
        self.assertEqual(report["exit_code"], DRIFT)
        self.assertEqual(report["surfaces"][0]["status"], "drifted")

    def test_unknown_surface_returns_usage(self):
        cov_payload = {"schema": 1, "surfaces": {}}
        _cov._write_coverage(cov_payload)
        with patch.object(verify_cmd, "discover_surfaces_yaml", return_value=None):
            report = verify_cmd.compute(only_surface="cms-bogus")
        self.assertEqual(report["exit_code"], USAGE)
        self.assertIn("error", report)


class VerifyRemoteDispatchTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmp.cleanup()
        # Ensure the env var doesn't leak between tests.
        import os
        os.environ.pop(verify_cmd.NOTION_TOKEN_ENV, None)

    def _seed(self, surfaces: dict[str, dict]) -> None:
        cov_payload = {"schema": 1, "surfaces": surfaces}
        _cov._write_coverage(cov_payload)

    def test_notion_without_token_returns_unconfigured(self):
        import os
        os.environ.pop(verify_cmd.NOTION_TOKEN_ENV, None)
        self._seed({"cms-workspace": {"source": "manual", "paths": []}})
        # Force the type to notion via patching the lookup so the test
        # doesn't depend on prefix inference.
        with patch.object(verify_cmd, "_surface_type", return_value="notion"), \
             patch.object(verify_cmd, "_surface_target", return_value=None):
            report = verify_cmd.compute(only_surface="cms-workspace")
        self.assertEqual(report["surfaces"][0]["status"], "unconfigured")
        self.assertIn(verify_cmd.NOTION_TOKEN_ENV, report["surfaces"][0]["hint"])
        # `unconfigured` is not a drift signal.
        self.assertEqual(report["exit_code"], CLEAN)

    def test_claude_routine_always_manual(self):
        self._seed({"routine-x": {"source": "manual", "paths": []}})
        with patch.object(verify_cmd, "_surface_type", return_value="claude-routine"), \
             patch.object(verify_cmd, "_surface_target", return_value=None):
            report = verify_cmd.compute(only_surface="routine-x")
        self.assertEqual(report["surfaces"][0]["status"], "manual")
        self.assertEqual(report["exit_code"], CLEAN)

    def test_supabase_no_cli_returns_manual(self):
        self._seed({"deploy-x": {"source": "manual", "paths": []}})
        with patch.object(verify_cmd, "_surface_type", return_value="supabase-env"), \
             patch.object(verify_cmd, "_surface_target", return_value="MY_KEY@my-ref"), \
             patch("shutil.which", return_value=None):
            report = verify_cmd.compute(only_surface="deploy-x")
        self.assertEqual(report["surfaces"][0]["status"], "manual")

    def test_github_public_drifted_when_marker_absent(self):
        self._seed({"repo-x": {"source": "manual", "paths": []}})

        class _FakeResp:
            def __init__(self, body: bytes):
                self._body = body
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return self._body

        with patch.object(verify_cmd, "_surface_type", return_value="github-public"), \
             patch.object(verify_cmd, "_surface_target", return_value="acme/foo:README.md"), \
             patch("urllib.request.urlopen",
                   return_value=_FakeResp(b"# Just a README, no marker.\n")):
            report = verify_cmd.compute(only_surface="repo-x")
        self.assertEqual(report["surfaces"][0]["status"], "drifted")

    def test_remote_status_does_not_echo_marker(self):
        """The matched literal must never appear in the output payload."""
        self._seed({"repo-y": {"source": "manual", "paths": []}})
        body_with_marker = (
            "# README\n"
            f"{_marker('repo-y', 'CanaryX0')}\n"
        ).encode()

        class _FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self): return body_with_marker

        with patch.object(verify_cmd, "_surface_type", return_value="github-public"), \
             patch.object(verify_cmd, "_surface_target", return_value="acme/y:README.md"), \
             patch("urllib.request.urlopen", return_value=_FakeResp()):
            report = verify_cmd.compute(only_surface="repo-y")
        # The fake body contains a synthetic marker; the report must not
        # carry the tail (or the full marker) in any field.
        as_json = json.dumps(report)
        self.assertNotIn("CanaryX0", as_json)


class VerifyRunTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmp.cleanup()

    def test_run_returns_clean_on_intact(self):
        target = self.tmp / "CLAUDE.md"
        target.write_text(_marker("cms-z", "Sec1Test") + "\n")
        cov_payload = {
            "schema": 1,
            "surfaces": {"cms-z": {"source": "manual", "paths": [str(target)]}},
        }
        _cov._write_coverage(cov_payload)

        captured = io.StringIO()
        with patch.object(verify_cmd, "discover_surfaces_yaml", return_value=None), \
             patch("sys.stdout", captured):
            rc = verify_cmd.run(_ns(surface="cms-z"))
        self.assertEqual(rc, CLEAN)


if __name__ == "__main__":
    unittest.main()
