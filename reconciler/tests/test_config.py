"""Tests for reconciler.config -- new-path-first read order with legacy fallback.

Cases (per v0.2.1 spec follow-up #1):
  1. Only new path exists -> loaded, no warning.
  2. Only legacy path exists -> loaded, deprecation warning to stderr.
  3. Both exist -> new wins, no warning.
  4. Neither exists -> ConfigMissing raised.
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Make the reconciler package importable without an editable install
# in the test environment.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reconciler import config as cfg_mod


SAMPLE_TOML = (
    'deployment_map_path = "/tmp/dmap.json"\n'
    'vault_root = "/tmp/vault"\n'
    'notion_master_page_id = "page-master-1234"\n'
    'notion_posture_page_id = "page-posture-5678"\n'
    'swanlake_repo_path = "/tmp/swanlake"\n'
    'canon_dir = "/tmp/swanlake/canon"\n'
)

LEGACY_TOML = SAMPLE_TOML.replace(
    '"/tmp/swanlake"', '"/tmp/swanlake-legacy"'
)


class ConfigPathPrecedenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_root = Path(self._tmp.name)
        self.new_path = self.tmp_root / "new" / "config.toml"
        self.legacy_path = self.tmp_root / "legacy" / "config.toml"
        # Each test patches the module constants to point at tmp paths so
        # nothing reads or writes the operator's real ~/.swanlake or
        # ~/.config/swanlake-reconciler.
        self._patches = [
            patch.object(cfg_mod, "NEW_CONFIG_PATH", self.new_path),
            patch.object(cfg_mod, "LEGACY_CONFIG_PATH", self.legacy_path),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_only_new_path_no_warning(self):
        self._write(self.new_path, SAMPLE_TOML)
        captured = io.StringIO()
        with patch("sys.stderr", captured):
            c = cfg_mod.load()
        self.assertEqual(str(c.swanlake_repo_path), "/tmp/swanlake")
        self.assertEqual(captured.getvalue(), "")

    def test_only_legacy_path_emits_warning(self):
        self._write(self.legacy_path, LEGACY_TOML)
        captured = io.StringIO()
        with patch("sys.stderr", captured):
            c = cfg_mod.load()
        # Loaded from legacy.
        self.assertEqual(str(c.swanlake_repo_path), "/tmp/swanlake-legacy")
        # Deprecation hint surfaced.
        err = captured.getvalue()
        self.assertIn("legacy config", err)
        self.assertIn(str(self.new_path), err)
        self.assertIn("swanlake init", err)

    def test_both_present_new_wins_no_warning(self):
        self._write(self.new_path, SAMPLE_TOML)
        self._write(self.legacy_path, LEGACY_TOML)
        captured = io.StringIO()
        with patch("sys.stderr", captured):
            c = cfg_mod.load()
        # New path wins -- the non-"-legacy" repo path proves it.
        self.assertEqual(str(c.swanlake_repo_path), "/tmp/swanlake")
        # No deprecation warning when the new path is in use.
        self.assertEqual(captured.getvalue(), "")

    def test_neither_present_raises_config_missing(self):
        with self.assertRaises(cfg_mod.ConfigMissing) as ctx:
            cfg_mod.load()
        msg = str(ctx.exception)
        # Both paths named in the error so the operator knows where to look.
        self.assertIn(str(self.new_path), msg)
        self.assertIn(str(self.legacy_path), msg)


if __name__ == "__main__":
    unittest.main()
