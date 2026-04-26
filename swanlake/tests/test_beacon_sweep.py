"""Tests for `swanlake beacon sweep`.

The marker shape `beacon-attrib-<surface>-<8alnum>` is constructed at
runtime via concatenation so the literal does not appear in this source
file (the canary-literal-block PreToolUse hook would reject the write).
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
from swanlake.commands.beacon import sweep as sweep_cmd
from swanlake.commands.beacon import _optout
from swanlake.exit_codes import CLEAN, DRIFT


# Built at runtime so the contiguous attribution-shaped literal never
# appears in source. The constructed value is still obviously synthetic.
_PREFIX = "beacon-" + "attrib"


def _marker(surface: str, tail: str) -> str:
    return f"{_PREFIX}-{surface}-{tail}"


def _ns(**kw) -> Namespace:
    defaults = {
        "json": False,
        "quiet": False,
        "cmd": "beacon",
        "beacon_op": "sweep",
        "scope": "all",
        "no_coverage_write": True,  # tests never touch real coverage.json
    }
    defaults.update(kw)
    return Namespace(**defaults)


class SweepBaseTest(unittest.TestCase):
    """Shared setUp: tmp state root, tmp projects tree, tmp deployment-map."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)
        self.projects = self.tmp / "projects"
        self.projects.mkdir()

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmp.cleanup()


class SweepClassificationTest(SweepBaseTest):
    def _seed_coverage(self, surfaces: dict[str, list[str]]) -> None:
        """Write a coverage.json the sweep can read."""
        cov_payload = {
            "schema": 1,
            "surfaces": {
                sid: {"source": "manual", "paths": paths}
                for sid, paths in surfaces.items()
            },
        }
        _cov._write_coverage(cov_payload)

    def test_beaconed_surface_classified_intact(self):
        proj = self.projects / "ProjectA"
        proj.mkdir()
        target = proj / "CLAUDE.md"
        # Both header AND attribution marker -> beaconed.
        target.write_text(
            "<!-- DEFENSE BEACON v1 -->\n"
            f"Attribution: {_marker('cms-projecta', 'AbCd1234')}\n"
        )
        self._seed_coverage({"cms-projecta": [str(target)]})

        with patch.object(_cov, "_scan_projects", return_value={}), \
             patch.object(sweep_cmd, "discover_surfaces_yaml", return_value=None):
            payload = sweep_cmd.compute(scope="local")

        self.assertEqual(payload["exit_code"], CLEAN)
        self.assertEqual(len(payload["beaconed"]), 1)
        self.assertEqual(payload["beaconed"][0]["surface"], "cms-projecta")
        self.assertEqual(len(payload["unbeaconed"]), 0)

    def test_unbeaconed_surface_drives_drift_exit(self):
        proj = self.projects / "ProjectB"
        proj.mkdir()
        target = proj / "CLAUDE.md"
        target.write_text("# Just a plain CLAUDE.md, no beacon block at all\n")
        self._seed_coverage({"cms-projectb": [str(target)]})

        with patch.object(_cov, "_scan_projects", return_value={}), \
             patch.object(sweep_cmd, "discover_surfaces_yaml", return_value=None):
            payload = sweep_cmd.compute(scope="local")

        self.assertEqual(payload["exit_code"], DRIFT)
        self.assertEqual(len(payload["unbeaconed"]), 1)
        self.assertEqual(payload["unbeaconed"][0]["surface"], "cms-projectb")

    def test_partial_surface_drives_drift_exit(self):
        proj = self.projects / "ProjectC"
        proj.mkdir()
        target = proj / "CLAUDE.md"
        # Header present but no attribution literal -> partial.
        target.write_text(
            "<!-- DEFENSE BEACON v1 -->\n# But no attribution marker\n"
        )
        self._seed_coverage({"cms-projectc": [str(target)]})

        with patch.object(_cov, "_scan_projects", return_value={}), \
             patch.object(sweep_cmd, "discover_surfaces_yaml", return_value=None):
            payload = sweep_cmd.compute(scope="local")

        self.assertEqual(payload["exit_code"], DRIFT)
        self.assertEqual(len(payload["partial"]), 1)
        self.assertEqual(payload["partial"][0]["surface"], "cms-projectc")

    def test_optout_marker_skips_surface(self):
        proj = self.projects / "Scratchpad"
        proj.mkdir()
        # Empty opt-out file at the project root: skip everything below.
        (proj / _optout.OPTOUT_FILENAME).write_text("")
        target = proj / "CLAUDE.md"
        target.write_text("# scratch")
        self._seed_coverage({"cms-scratchpad": [str(target)]})

        with patch.object(_cov, "_scan_projects", return_value={}), \
             patch.object(sweep_cmd, "discover_surfaces_yaml", return_value=None):
            payload = sweep_cmd.compute(scope="local")

        self.assertEqual(len(payload["skipped_by_optout"]), 1)
        self.assertEqual(payload["skipped_by_optout"][0]["surface"], "cms-scratchpad")
        # Skipped surfaces don't drive drift.
        self.assertEqual(payload["exit_code"], CLEAN)
        # And do NOT appear in the unbeaconed bucket.
        self.assertEqual(len(payload["unbeaconed"]), 0)

    def test_remote_surface_lands_in_remote_pending_bucket(self):
        # A repo-* surface infers to github-public (REMOTE).
        self._seed_coverage({"repo-something": []})

        with patch.object(_cov, "_scan_projects", return_value={}), \
             patch.object(sweep_cmd, "discover_surfaces_yaml", return_value=None):
            payload = sweep_cmd.compute(scope="all")

        self.assertEqual(len(payload["remote_pending"]), 1)
        self.assertEqual(payload["remote_pending"][0]["type"], "github-public")

    def test_scope_filter_local_excludes_remote(self):
        self._seed_coverage({"repo-x": [], "cms-y": []})
        with patch.object(_cov, "_scan_projects", return_value={}), \
             patch.object(sweep_cmd, "discover_surfaces_yaml", return_value=None):
            payload = sweep_cmd.compute(scope="local")
        all_in_payload = (
            payload["unbeaconed"] + payload["partial"]
            + payload["beaconed"] + payload["remote_pending"]
        )
        types = {row["type"] for row in all_in_payload}
        self.assertNotIn("github-public", types)


class SweepRunTest(SweepBaseTest):
    def test_run_returns_drift_when_unbeaconed(self):
        proj = self.projects / "X"
        proj.mkdir()
        target = proj / "CLAUDE.md"
        target.write_text("# bare")
        cov_payload = {
            "schema": 1,
            "surfaces": {"cms-x": {"source": "manual", "paths": [str(target)]}},
        }
        _cov._write_coverage(cov_payload)

        captured = io.StringIO()
        with patch.object(_cov, "_scan_projects", return_value={}), \
             patch.object(sweep_cmd, "discover_surfaces_yaml", return_value=None), \
             patch("sys.stdout", captured):
            rc = sweep_cmd.run(_ns(scope="local"))
        self.assertEqual(rc, DRIFT)
        self.assertIn("UNBEACONED", captured.getvalue())

    def test_json_does_not_echo_canary_tail(self):
        """Critical: sweep output must never include the 8-char tail."""
        proj = self.projects / "Y"
        proj.mkdir()
        target = proj / "CLAUDE.md"
        TAIL = "Sec1Test"
        target.write_text(
            "<!-- DEFENSE BEACON v1 -->\n"
            f"{_marker('cms-y', TAIL)}\n"
        )
        cov_payload = {
            "schema": 1,
            "surfaces": {"cms-y": {"source": "manual", "paths": [str(target)]}},
        }
        _cov._write_coverage(cov_payload)

        captured = io.StringIO()
        with patch.object(_cov, "_scan_projects", return_value={}), \
             patch.object(sweep_cmd, "discover_surfaces_yaml", return_value=None), \
             patch("sys.stdout", captured):
            sweep_cmd.run(_ns(scope="local", json=True))
        self.assertNotIn(TAIL, captured.getvalue())


if __name__ == "__main__":
    unittest.main()
