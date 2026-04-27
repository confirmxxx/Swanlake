"""Tests for v0.4 CLI argparse wiring + audit-log integration.

Covers the cross-cutting bits that don't naturally belong in the
per-layer test files:

  1. argparse builds the v0.4 subparsers (`scan`, `init project`,
     CC adapter --enable/--disable-session-nudge flags).
  2. `swanlake scan` dispatches via cli.main() and produces an
     audit-log row.
  3. `swanlake init project` dispatches via cli.main() and produces
     an audit-log row.
  4. main() resolves the init_op / scan_op subcmd field correctly
     (regression guard for the audit-record subcmd field).
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake import cli
from swanlake import state as _state
from swanlake.exit_codes import CLEAN


class V04ArgparseWiringTest(unittest.TestCase):
    def test_scan_subcommand_listed(self):
        parser = cli.build_parser()
        buf = io.StringIO()
        parser.print_help(buf)
        self.assertIn("scan", buf.getvalue())

    def test_scan_subparser_accepts_flags(self):
        parser = cli.build_parser()
        args = parser.parse_args([
            "scan",
            "--projects-root", "/tmp/x",
            "--include-nested",
            "--filter", "actionable",
            "--json",
        ])
        self.assertEqual(args.cmd, "scan")
        self.assertEqual(args.projects_root, "/tmp/x")
        self.assertTrue(args.include_nested)
        self.assertEqual(args.filter, "actionable")
        self.assertTrue(args.json)

    def test_init_project_subparser_requires_type(self):
        parser = cli.build_parser()
        # Missing --type should fail the parse with SystemExit.
        with patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["init", "project", "/tmp/x"])

    def test_init_project_subparser_accepts_type(self):
        parser = cli.build_parser()
        args = parser.parse_args([
            "init", "project", "/tmp/x",
            "--type", "cc",
            "--force",
            "--name", "myproj",
        ])
        self.assertEqual(args.cmd, "init")
        self.assertEqual(args.init_op, "project")
        self.assertEqual(args.type, "cc")
        self.assertTrue(args.force)
        self.assertEqual(args.name, "myproj")

    def test_cc_adapter_session_nudge_flags(self):
        parser = cli.build_parser()
        args = parser.parse_args([
            "adapt", "cc",
            "--enable-session-nudge",
        ])
        self.assertTrue(args.enable_session_nudge)
        self.assertFalse(args.disable_session_nudge)


class V04AuditIntegrationTest(unittest.TestCase):
    """Verify v0.4 commands produce audit-log rows via cli.main()."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)

        # Test projects root with no projects -- scan returns empty
        # but still writes an audit row.
        self.projects = self.tmp / "projects"
        self.projects.mkdir()

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmpdir.cleanup()

    def _read_audit(self) -> list[dict]:
        audit = self.tmp / "audit.jsonl"
        if not audit.exists():
            return []
        rows = []
        for line in audit.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def test_scan_appends_audit_row(self):
        with patch("sys.stdout", io.StringIO()):
            rc = cli.main([
                "scan",
                "--state-root", str(self.tmp),
                "--projects-root", str(self.projects),
            ])
        self.assertEqual(rc, CLEAN)
        rows = self._read_audit()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cmd"], "scan")
        self.assertEqual(rows[0]["exit_code"], CLEAN)

    def test_init_project_appends_audit_row_with_subcmd(self):
        target = self.tmp / "newproj"
        with patch("sys.stdout", io.StringIO()):
            rc = cli.main([
                "init", "project", str(target),
                "--state-root", str(self.tmp),
                "--type", "cc",
            ])
        self.assertEqual(rc, CLEAN)
        rows = self._read_audit()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cmd"], "init")
        # The audit record's subcmd field is resolved from init_op
        # (added in v0.4 main() dispatch).
        self.assertEqual(rows[0]["subcmd"], "project")
        self.assertEqual(rows[0]["exit_code"], CLEAN)


if __name__ == "__main__":
    unittest.main()
