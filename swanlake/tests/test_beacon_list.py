"""Tests for `swanlake beacon list`."""
from __future__ import annotations

import io
import json
import sys
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake.commands.beacon import list as list_cmd
from swanlake.exit_codes import CLEAN


def _ns(**kw) -> Namespace:
    defaults = {
        "json": False,
        "quiet": False,
        "cmd": "beacon",
        "beacon_op": "list",
    }
    defaults.update(kw)
    return Namespace(**defaults)


class BeaconListTest(unittest.TestCase):
    def test_table_lists_seven_rows(self):
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            rc = list_cmd.run(_ns())
        self.assertEqual(rc, CLEAN)
        out = captured.getvalue()
        # All 7 type IDs appear.
        for tid in (
            "claude-md",
            "vault",
            "notion",
            "supabase-env",
            "vercel-env",
            "github-public",
            "claude-routine",
        ):
            self.assertIn(tid, out)
        self.assertIn("7 surface types known", out)

    def test_json_emits_full_payload(self):
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            rc = list_cmd.run(_ns(json=True))
        self.assertEqual(rc, CLEAN)
        payload = json.loads(captured.getvalue())
        self.assertIn("surfaces", payload)
        self.assertEqual(len(payload["surfaces"]), 7)
        # Spot-check that scope and deploy_method are present per row.
        for row in payload["surfaces"]:
            self.assertIn(row["scope"], ("local", "remote"))
            self.assertIn(row["deploy_method"], (
                "local-write", "remote-checklist", "pr-checklist",
            ))

    def test_quiet_suppresses_stdout(self):
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            rc = list_cmd.run(_ns(quiet=True))
        self.assertEqual(rc, CLEAN)
        self.assertEqual(captured.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
