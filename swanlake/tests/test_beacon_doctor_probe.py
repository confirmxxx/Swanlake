"""Tests for the SWANLAKE_NOTION_TOKEN doctor probe (v0.3.x bonus)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake import coverage as _cov
from swanlake import state as _state
from swanlake.commands.doctor import _probe_notion_token, PROBES


class NotionTokenProbeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)
        # Ensure clean env.
        os.environ.pop("SWANLAKE_NOTION_TOKEN", None)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmp.cleanup()
        os.environ.pop("SWANLAKE_NOTION_TOKEN", None)

    def test_probe_in_canonical_list(self):
        names = [name for name, _ in PROBES]
        self.assertIn("notion verify token", names)

    def test_no_notion_surface_passes(self):
        cov_payload = {
            "schema": 1,
            "surfaces": {"cms-x": {"source": "manual", "paths": []}},
        }
        _cov._write_coverage(cov_payload)
        result = _probe_notion_token()
        self.assertEqual(result["status"], "pass")

    def test_notion_surface_without_token_warns(self):
        cov_payload = {
            "schema": 1,
            "surfaces": {
                "notion-workspace": {
                    "source": "manual",
                    "paths": [],
                    "type": "notion",
                },
            },
        }
        _cov._write_coverage(cov_payload)
        result = _probe_notion_token()
        self.assertEqual(result["status"], "warn")
        self.assertIn("SWANLAKE_NOTION_TOKEN", result["detail"])

    def test_notion_surface_with_token_passes(self):
        cov_payload = {
            "schema": 1,
            "surfaces": {
                "notion-x": {
                    "source": "manual",
                    "paths": [],
                    "type": "notion",
                },
            },
        }
        _cov._write_coverage(cov_payload)
        os.environ["SWANLAKE_NOTION_TOKEN"] = "secret_test_value"
        try:
            result = _probe_notion_token()
        finally:
            os.environ.pop("SWANLAKE_NOTION_TOKEN", None)
        self.assertEqual(result["status"], "pass")
        self.assertIn("set", result["detail"])


if __name__ == "__main__":
    unittest.main()
