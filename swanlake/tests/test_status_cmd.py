"""Tests for swanlake.commands.status -- composite report + exit code mapping."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake.commands import status as status_cmd
from swanlake import state as _state


def _ns(**kw) -> Namespace:
    """Build an argparse-shaped Namespace with sensible defaults."""
    defaults = {"json": False, "quiet": False, "cmd": "status"}
    defaults.update(kw)
    return Namespace(**defaults)


def _all_clean_dim(name: str):
    def fn():
        return {"status": "clean", "detail": f"{name} ok"}
    return fn


def _alarm_dim():
    def fn():
        return {"status": "alarm", "detail": "1 hits / 1 fires"}
    return fn


def _drift_dim():
    def fn():
        return {"status": "drift", "detail": "stale"}
    return fn


def _raising_dim():
    def fn():
        raise RuntimeError("simulated dim failure")
    return fn


class StatusCompositeTest(unittest.TestCase):
    """Exercise compute() with the dimensions table monkey-patched."""

    def setUp(self):
        # Tests run in isolation against a tmp state root so coverage / bench
        # files do not leak from the operator's real ~/.swanlake.
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmpdir.cleanup()

    def test_all_clean_returns_zero(self):
        fake_dims = tuple(
            (name, _all_clean_dim(name))
            for name, _ in status_cmd.DIMENSIONS
        )
        with patch.object(status_cmd, "DIMENSIONS", fake_dims):
            report = status_cmd.compute()
        self.assertEqual(report["overall"], "CLEAN")
        self.assertEqual(report["exit_code"], 0)
        for d in report["dimensions"]:
            self.assertEqual(d["status"], "clean")

    def test_canary_hits_returns_two(self):
        fake_dims = (
            ("reconciler", _all_clean_dim("reconciler")),
            ("canary", _alarm_dim()),
            ("inject", _all_clean_dim("inject")),
            ("exfil", _all_clean_dim("exfil")),
            ("closure", _all_clean_dim("closure")),
            ("coverage", _all_clean_dim("coverage")),
            ("bench", _all_clean_dim("bench")),
        )
        with patch.object(status_cmd, "DIMENSIONS", fake_dims):
            report = status_cmd.compute()
        self.assertEqual(report["overall"], "ALARM")
        self.assertEqual(report["exit_code"], 2)

    def test_reconciler_drift_returns_one(self):
        fake_dims = (
            ("reconciler", _drift_dim()),
            ("canary", _all_clean_dim("canary")),
            ("inject", _all_clean_dim("inject")),
            ("exfil", _all_clean_dim("exfil")),
            ("closure", _all_clean_dim("closure")),
            ("coverage", _all_clean_dim("coverage")),
            ("bench", _all_clean_dim("bench")),
        )
        with patch.object(status_cmd, "DIMENSIONS", fake_dims):
            report = status_cmd.compute()
        self.assertEqual(report["overall"], "DRIFT")
        self.assertEqual(report["exit_code"], 1)

    def test_dimension_failure_degrades_gracefully(self):
        """A raising dimension must not crash the report. Status -> unknown,
        severity -> 1, overall continues to compute against the other six."""
        fake_dims = (
            ("reconciler", _raising_dim()),
            ("canary", _all_clean_dim("canary")),
            ("inject", _all_clean_dim("inject")),
            ("exfil", _all_clean_dim("exfil")),
            ("closure", _all_clean_dim("closure")),
            ("coverage", _all_clean_dim("coverage")),
            ("bench", _all_clean_dim("bench")),
        )
        with patch.object(status_cmd, "DIMENSIONS", fake_dims):
            report = status_cmd.compute()
        # Reconciler row degraded to unknown.
        recon_row = next(d for d in report["dimensions"] if d["name"] == "reconciler")
        self.assertEqual(recon_row["status"], "unknown")
        self.assertIn("RuntimeError", recon_row["detail"])
        # Worst severity = 1 (unknown), so overall is DRIFT, exit 1.
        self.assertEqual(report["overall"], "DRIFT")
        self.assertEqual(report["exit_code"], 1)

    def test_alarm_beats_drift(self):
        """ALARM trumps DRIFT when both fire."""
        fake_dims = (
            ("reconciler", _drift_dim()),
            ("canary", _alarm_dim()),
            ("inject", _all_clean_dim("inject")),
            ("exfil", _all_clean_dim("exfil")),
            ("closure", _all_clean_dim("closure")),
            ("coverage", _all_clean_dim("coverage")),
            ("bench", _all_clean_dim("bench")),
        )
        with patch.object(status_cmd, "DIMENSIONS", fake_dims):
            report = status_cmd.compute()
        self.assertEqual(report["overall"], "ALARM")


class StatusOutputTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmpdir.cleanup()

    def test_json_flag_produces_parseable_json(self):
        fake_dims = tuple(
            (name, _all_clean_dim(name))
            for name, _ in status_cmd.DIMENSIONS
        )
        captured = io.StringIO()
        with patch.object(status_cmd, "DIMENSIONS", fake_dims):
            with patch("sys.stdout", captured):
                exit_code = status_cmd.run(_ns(json=True))
        self.assertEqual(exit_code, 0)
        out = captured.getvalue()
        # Must parse as JSON cleanly.
        report = json.loads(out)
        self.assertEqual(report["overall"], "CLEAN")
        self.assertEqual(report["exit_code"], 0)
        # Sanity: must include every dimension by name.
        names = {d["name"] for d in report["dimensions"]}
        for n, _ in status_cmd.DIMENSIONS:
            self.assertIn(n, names)

    def test_human_output_includes_overall(self):
        fake_dims = tuple(
            (name, _all_clean_dim(name))
            for name, _ in status_cmd.DIMENSIONS
        )
        captured = io.StringIO()
        with patch.object(status_cmd, "DIMENSIONS", fake_dims):
            with patch("sys.stdout", captured):
                status_cmd.run(_ns(json=False))
        out = captured.getvalue()
        self.assertIn("overall: CLEAN", out)
        self.assertIn("[exit 0]", out)
        # Header / underline rendered.
        self.assertIn("dimension", out)
        self.assertIn("status", out)


class StatusRealDimensionsTest(unittest.TestCase):
    """End-to-end smoke against the real dimension functions, with state
    pointed at an empty tmp dir so we exercise the missing-input degraded
    paths rather than the operator's real state."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmpdir.cleanup()

    def test_no_config_shows_useful_error(self):
        """When dependencies are missing, the report still completes -- each
        dim degrades to unknown / missing rather than raising."""
        # Force every dim to operate against empty / non-existent inputs by
        # pointing the per-hook env vars at non-existent dirs.
        with patch.dict(os.environ, {
            "SWANLAKE_CANARY_HITS": str(self.tmp / "missing-canary"),
            "SWANLAKE_CONTENT_HITS": str(self.tmp / "missing-content"),
            "SWANLAKE_EXFIL_HITS": str(self.tmp / "missing-exfil"),
        }):
            # Reset the cached module so it re-reads the env we just set.
            from swanlake import _compat as compat
            compat.reset_cache()
            try:
                report = status_cmd.compute()
            finally:
                compat.reset_cache()
        # Report must still complete -- every row present.
        self.assertEqual(len(report["dimensions"]), 7)
        # coverage + bench will be missing/informational because the tmp
        # state root has neither file. Coverage missing = severity 2 ALARM.
        coverage_row = next(d for d in report["dimensions"]
                            if d["name"] == "coverage")
        self.assertEqual(coverage_row["status"], "missing")
        self.assertIn("coverage.json", coverage_row["detail"])


if __name__ == "__main__":
    unittest.main()
