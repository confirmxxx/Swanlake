"""Tests for swanlake.commands.doctor -- 8-probe health check.

Cases:
  1. all-pass: every probe returns "pass" -> exit 0.
  2. single-warn: one probe returns "warn", rest pass -> exit 1.
  3. single-fail: one probe returns "fail", rest pass -> exit 2.
  4. --fix-suggestions: detail column carries the remediation hint.
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake.commands import doctor as doctor_cmd
from swanlake import state as _state


def _ns(**kw) -> Namespace:
    defaults = {
        "json": False,
        "quiet": False,
        "cmd": "doctor",
        "fix_suggestions": False,
    }
    defaults.update(kw)
    return Namespace(**defaults)


def _pass_probe(name: str):
    def fn():
        return {"status": "pass", "detail": f"{name} ok", "fix": None}
    return fn


def _warn_probe(name: str):
    def fn():
        return {
            "status": "warn",
            "detail": f"{name} stale",
            "fix": f"swanlake fix-{name}",
        }
    return fn


def _fail_probe(name: str):
    def fn():
        return {
            "status": "fail",
            "detail": f"{name} missing",
            "fix": f"install-{name}",
        }
    return fn


class DoctorComputeTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmpdir.cleanup()

    def test_all_pass_exit_zero(self):
        fake = tuple((name, _pass_probe(name)) for name, _ in doctor_cmd.PROBES)
        with patch.object(doctor_cmd, "PROBES", fake):
            report = doctor_cmd.compute()
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["worst"], "pass")

    def test_single_warn_exit_one(self):
        fake = (
            (doctor_cmd.PROBES[0][0], _warn_probe(doctor_cmd.PROBES[0][0])),
        ) + tuple(
            (name, _pass_probe(name)) for name, _ in doctor_cmd.PROBES[1:]
        )
        with patch.object(doctor_cmd, "PROBES", fake):
            report = doctor_cmd.compute()
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["worst"], "warn")

    def test_single_fail_exit_two(self):
        fake = (
            (doctor_cmd.PROBES[0][0], _fail_probe(doctor_cmd.PROBES[0][0])),
        ) + tuple(
            (name, _pass_probe(name)) for name, _ in doctor_cmd.PROBES[1:]
        )
        with patch.object(doctor_cmd, "PROBES", fake):
            report = doctor_cmd.compute()
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["worst"], "fail")

    def test_fix_suggestions_renders_in_detail(self):
        fake = (
            (doctor_cmd.PROBES[0][0], _fail_probe(doctor_cmd.PROBES[0][0])),
        ) + tuple(
            (name, _pass_probe(name)) for name, _ in doctor_cmd.PROBES[1:]
        )
        captured = io.StringIO()
        with patch.object(doctor_cmd, "PROBES", fake), \
             patch("sys.stdout", captured):
            rc = doctor_cmd.run(_ns(fix_suggestions=True))
        out = captured.getvalue()
        # The fail row must include the fix hint.
        self.assertEqual(rc, 2)
        self.assertIn("fix:", out)
        self.assertIn("install-", out)
        # Pass rows must NOT include "fix:" (no remediation needed).
        # Trim the table and check that pass rows render cleanly.
        # We only assert at least one "pass" row appeared without "fix:".
        lines = [ln for ln in out.splitlines() if "pass" in ln]
        # Some pass row exists in the table.
        self.assertTrue(any("fix:" not in ln for ln in lines))

    def test_probe_exception_degrades_to_fail(self):
        """If a probe raises, doctor degrades it to status=fail without crash."""
        def boom():
            raise RuntimeError("simulated probe failure")

        fake = (
            ("explosive", boom),
        ) + tuple(
            (name, _pass_probe(name)) for name, _ in doctor_cmd.PROBES[1:]
        )
        with patch.object(doctor_cmd, "PROBES", fake):
            report = doctor_cmd.compute()
        # Failed probe -> overall fail / exit 2.
        self.assertEqual(report["exit_code"], 2)
        first = report["probes"][0]
        self.assertEqual(first["status"], "fail")
        self.assertIn("RuntimeError", first["detail"])


if __name__ == "__main__":
    unittest.main()
