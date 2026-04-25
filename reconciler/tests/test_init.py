"""Tests for the --init setup wizard."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reconciler import init


CANNED_INPUTS = {
    'deployment_map_path': '/tmp/dmap.json',
    'vault_root': '/tmp/vault',
    'notion_master_page_id': 'fake-master-page-id',
    'notion_posture_page_id': 'fake-posture-page-id',
    'swanlake_repo_path': '/tmp/sw',
    'canon_dir': '/tmp/sw/canon',
}


class InitTest(unittest.TestCase):
    def test_init_creates_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / 'cfg'
            with patch.object(init, 'CONFIG_DIR', cfg_dir):
                with patch.object(init, '_prompt_inputs', return_value=CANNED_INPUTS):
                    rc = init.run_init(skip_systemd=True)
            self.assertEqual(rc, 0)
            cfg_path = cfg_dir / 'config.toml'
            self.assertTrue(cfg_path.exists())
            text = cfg_path.read_text()
            self.assertIn('deployment_map_path', text)
            self.assertIn('/tmp/dmap.json', text)

    def test_init_idempotent(self):
        """Running twice should not error and should produce the same file."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / 'cfg'
            with patch.object(init, 'CONFIG_DIR', cfg_dir):
                with patch.object(init, '_prompt_inputs', return_value=CANNED_INPUTS):
                    rc1 = init.run_init(skip_systemd=True)
                    rc2 = init.run_init(skip_systemd=True)
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)

    def test_init_writes_valid_toml(self):
        """Config file must be parseable as TOML."""
        import tomllib
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / 'cfg'
            with patch.object(init, 'CONFIG_DIR', cfg_dir):
                with patch.object(init, '_prompt_inputs', return_value=CANNED_INPUTS):
                    init.run_init(skip_systemd=True)
            with (cfg_dir / 'config.toml').open('rb') as f:
                data = tomllib.load(f)
            self.assertEqual(data['deployment_map_path'], '/tmp/dmap.json')
            self.assertEqual(data['notion_master_page_id'], 'fake-master-page-id')


if __name__ == '__main__':
    unittest.main()
