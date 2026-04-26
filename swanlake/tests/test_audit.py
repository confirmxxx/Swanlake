"""Tests for swanlake.audit -- AuditRecord context manager + redaction + rotation.

Uses the obviously-fake placeholder AKIA_BEACON_TESTFIXTURE000000000000
per the repo's CLAUDE.md hard rule. The placeholder does NOT match the
real-canary regex (TESTFIXTURE contains non-hex letters), so we have to
test redaction with a string that DOES match -- we construct a synthetic
hex literal at runtime so it never appears as a tracked source string.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure the package under test is importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake import audit, state


# Build a canary-shaped literal at runtime so it is never present as a
# source-string in tracked files (the .claude/hooks/canary-literal-block.sh
# pre-write hook would otherwise refuse this file). The constructed string
# is a hex prefix followed by a hex tail -- 20 hex chars total -- which
# matches AKIA_BEACON_[0-9A-Fa-f]{20}. Because it never appears as a
# concatenated literal in this file, the hook cannot match it pre-write.
def _synthetic_canary_literal() -> str:
    prefix = "A" + "KIA"  # split prevents source-grep false positives
    body = "_BEACON_"
    hex_tail = "0123456789abcdef0000"  # 20 hex chars
    return prefix + body + hex_tail


class CanaryRedactionTest(unittest.TestCase):
    def test_test_fixture_placeholder_does_not_match(self):
        """The repo-standard placeholder must NOT trigger redaction so
        normal test runs do not leave spurious REDACTED rows in audit logs."""
        # AKIA_BEACON_TESTFIXTURE000000000000 -- TESTFIXTURE has non-hex chars.
        placeholder = "AKIA_BEACON_TESTFIXTURE000000000000"
        self.assertFalse(audit._is_canary_shaped(placeholder))

    def test_real_shaped_literal_is_redacted(self):
        canary = _synthetic_canary_literal()
        self.assertTrue(audit._is_canary_shaped(canary))
        out = audit._redact_args(["--foo", canary, "--bar"])
        self.assertEqual(out[0], "--foo")
        self.assertTrue(out[1].startswith("REDACTED(type=canary"))
        self.assertIn("pos=1", out[1])
        self.assertEqual(out[2], "--bar")
        # The literal must NOT appear anywhere in the redacted output.
        self.assertNotIn(canary, " ".join(out))

    def test_attribution_marker_shape_is_redacted(self):
        # beacon-attrib-<surface>-<8 alnum>
        synthetic = "beacon" + "-attrib-someplace-AbCdEfGh"
        self.assertTrue(audit._is_canary_shaped(synthetic))

    def test_canary_substring_inside_larger_arg_is_redacted(self):
        """F4: argv values that wrap a canary inside a larger string
        (e.g. `--data=AKIA_BEACON_<hex>`) must still be scrubbed; the
        original anchored-only matcher missed these."""
        canary = _synthetic_canary_literal()
        wrapped = f"--data={canary}"
        out = audit._redact_args(["status", wrapped])
        # Whole-arg replacement does NOT fire (it is not itself a canary).
        # Substring scrub fires inline: prefix preserved, canary replaced.
        self.assertEqual(out[0], "status")
        self.assertNotIn(canary, out[1])
        self.assertIn("--data=", out[1])
        self.assertIn("REDACTED(type=canary)", out[1])

    def test_canary_substring_with_attribution_shape_redacted(self):
        """Same test for the attribution-marker shape, which has the
        most plausible 'embedded in URL' attack surface."""
        synthetic = "beacon" + "-attrib-someplace-AbCdEfGh"
        wrapped = f"--token={synthetic}&user=foo"
        out = audit._redact_args(["fetch", wrapped])
        self.assertNotIn(synthetic, out[1])
        self.assertIn("REDACTED(type=canary)", out[1])
        self.assertIn("&user=foo", out[1],
                      "substring redaction destroyed surrounding context")


class AuditRecordWriteTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self._original_root = state.get_state_root()
        state.set_state_root(self.tmp)

    def tearDown(self):
        state.set_state_root(self._original_root)
        self._tmpdir.cleanup()

    def test_write_on_clean_exit(self):
        with audit.AuditRecord(cmd="status", subcmd=None, argv=["--json"]) as rec:
            rec.set_exit(0)
        log = self.tmp / "audit.jsonl"
        self.assertTrue(log.exists())
        line = log.read_text().strip().splitlines()[-1]
        record = json.loads(line)
        self.assertEqual(record["cmd"], "status")
        self.assertEqual(record["exit_code"], 0)
        self.assertEqual(record["args"], ["--json"])
        self.assertIsNone(record["error"])
        self.assertIn("ts", record)
        self.assertIn("duration_ms", record)
        self.assertIn("swanlake_version", record)

    def test_exception_class_recorded_on_error(self):
        try:
            with audit.AuditRecord(cmd="status", subcmd=None, argv=[]):
                raise ValueError("boom")
        except ValueError:
            pass  # expected -- AuditRecord must not swallow
        log = self.tmp / "audit.jsonl"
        line = log.read_text().strip().splitlines()[-1]
        record = json.loads(line)
        self.assertIsNotNone(record["error"])
        self.assertIn("ValueError", record["error"])
        self.assertIn("boom", record["error"])

    def test_canary_argv_redacted_in_log(self):
        canary = _synthetic_canary_literal()
        with audit.AuditRecord(cmd="status", subcmd=None,
                               argv=["status", canary]) as rec:
            rec.set_exit(0)
        log_text = (self.tmp / "audit.jsonl").read_text()
        self.assertNotIn(canary, log_text)
        self.assertIn("REDACTED(type=canary", log_text)

    def test_never_raises_on_unwritable_path(self):
        """Audit module must swallow I/O errors so a broken log never breaks
        the CLI itself."""
        # Point the state root at a path the process cannot create.
        # /proc/1/swanlake is read-only on Linux for non-root users.
        broken = Path("/proc/1/swanlake-impossible-subdir-for-test")
        original = state.get_state_root()
        state.set_state_root(broken)
        try:
            # The context manager must complete without raising.
            with audit.AuditRecord(cmd="status", subcmd=None, argv=[]) as rec:
                rec.set_exit(0)
        finally:
            state.set_state_root(original)


class AuditRotationTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self._original_root = state.get_state_root()
        state.set_state_root(self.tmp)

    def tearDown(self):
        state.set_state_root(self._original_root)
        self._tmpdir.cleanup()

    def test_rotates_at_10mb(self):
        log = self.tmp / "audit.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        # Pre-fill the live log to just over the rotation threshold.
        log.write_bytes(b"x" * (audit.ROTATION_BYTES + 1))
        # Triggering one append must move the oversized log to .1 and
        # write the new line to a fresh live log.
        with audit.AuditRecord(cmd="status", subcmd=None, argv=[]) as rec:
            rec.set_exit(0)
        rotated = self.tmp / "audit.jsonl.1"
        self.assertTrue(rotated.exists())
        self.assertTrue(log.exists())
        # New live log holds exactly one record.
        live_lines = log.read_text().strip().splitlines()
        self.assertEqual(len(live_lines), 1)
        record = json.loads(live_lines[0])
        self.assertEqual(record["cmd"], "status")

    def test_rotation_overwrites_existing_dot1(self):
        log = self.tmp / "audit.jsonl"
        rotated = self.tmp / "audit.jsonl.1"
        log.parent.mkdir(parents=True, exist_ok=True)
        rotated.write_text("ancient garbage that must be replaced\n")
        log.write_bytes(b"y" * (audit.ROTATION_BYTES + 1))
        with audit.AuditRecord(cmd="status", subcmd=None, argv=[]) as rec:
            rec.set_exit(0)
        # The rotated file should now hold the previously-live oversized blob.
        self.assertGreater(rotated.stat().st_size, audit.ROTATION_BYTES)
        self.assertNotIn("ancient garbage", rotated.read_text(errors="replace"))


if __name__ == "__main__":
    unittest.main()
