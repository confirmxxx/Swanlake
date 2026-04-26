"""Tests for swanlake.state -- root creation perms and never-touch semantics."""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure the package under test is importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake import state


class StateRootCreationTest(unittest.TestCase):
    def test_ensure_state_root_creates_dir_mode_0700(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "swanlake"
            self.assertFalse(root.exists())
            state.ensure_state_root(root)
            self.assertTrue(root.is_dir())
            mode = stat.S_IMODE(root.stat().st_mode)
            self.assertEqual(mode, 0o700)

    def test_ensure_state_root_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "swanlake"
            state.ensure_state_root(root)
            state.ensure_state_root(root)  # second call must not raise
            self.assertTrue(root.is_dir())

    def test_existing_files_inside_root_are_untouched(self):
        """R3 mitigation: pre-existing files (canary-strings.txt etc.) must
        survive ensure_state_root() byte-for-byte."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "swanlake"
            root.mkdir()
            existing = root / "canary-strings.txt"
            existing.write_text("AKIA_BEACON_TESTFIXTURE000000000000\n")
            original_mtime = existing.stat().st_mtime
            original_content = existing.read_bytes()

            state.ensure_state_root(root)

            self.assertTrue(existing.exists())
            self.assertEqual(existing.read_bytes(), original_content)
            self.assertEqual(existing.stat().st_mtime, original_mtime)

    def test_existing_dir_with_loose_perms_is_tightened(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "swanlake"
            root.mkdir(mode=0o755)
            os.chmod(root, 0o755)
            state.ensure_state_root(root)
            self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)


class StateRootResolutionTest(unittest.TestCase):
    def test_cli_override_wins(self):
        with patch.dict(os.environ, {"SWANLAKE_STATE_ROOT": "/tmp/from-env"}):
            self.assertEqual(
                state.resolve_state_root("/tmp/from-cli"),
                Path("/tmp/from-cli"),
            )

    def test_env_used_when_no_cli(self):
        with patch.dict(os.environ, {"SWANLAKE_STATE_ROOT": "/tmp/from-env"}):
            self.assertEqual(
                state.resolve_state_root(None),
                Path("/tmp/from-env"),
            )

    def test_default_when_neither(self):
        env = {k: v for k, v in os.environ.items() if k != "SWANLAKE_STATE_ROOT"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                state.resolve_state_root(None),
                state.DEFAULT_STATE_ROOT,
            )

    def test_set_and_get_state_root_roundtrip(self):
        original = state.get_state_root()
        try:
            state.set_state_root("/tmp/swanlake-test-roundtrip")
            self.assertEqual(state.get_state_root(), Path("/tmp/swanlake-test-roundtrip"))
            self.assertEqual(
                state.state_path("audit.jsonl"),
                Path("/tmp/swanlake-test-roundtrip/audit.jsonl"),
            )
        finally:
            state.set_state_root(original)


if __name__ == "__main__":
    unittest.main()
