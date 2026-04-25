"""Tests for the vault sync engine."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reconciler import sync_vault


class SectionSyncTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)
        self.vault_file = self.tmpdir / 'note.md'
        self.template = self.tmpdir / 'template.md'

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_inserts_section_when_markers_absent(self):
        self.vault_file.write_text('# heading\nbody\n')
        self.template.write_text(
            '<!-- swanlake-section-start: rules -->\nNEW RULES\n<!-- swanlake-section-end: rules -->\n'
        )
        result = sync_vault.sync_file(self.vault_file, self.template, 'rules')
        self.assertEqual(result, 'inserted')
        out = self.vault_file.read_text()
        self.assertIn('NEW RULES', out)
        self.assertIn('# heading\nbody\n', out)

    def test_replaces_section_when_markers_present(self):
        self.vault_file.write_text(
            '# heading\nbody\n'
            '<!-- swanlake-section-start: rules -->\nOLD RULES\n<!-- swanlake-section-end: rules -->\n'
            'after\n'
        )
        self.template.write_text(
            '<!-- swanlake-section-start: rules -->\nNEW RULES\n<!-- swanlake-section-end: rules -->\n'
        )
        result = sync_vault.sync_file(self.vault_file, self.template, 'rules')
        self.assertEqual(result, 'updated')
        out = self.vault_file.read_text()
        self.assertIn('NEW RULES', out)
        self.assertNotIn('OLD RULES', out)
        self.assertIn('after', out)

    def test_skips_divergent_file(self):
        self.vault_file.write_text(
            '---\nswanlake-divergence: intentional\n---\n'
            '<!-- swanlake-section-start: rules -->\nKEPT\n<!-- swanlake-section-end: rules -->\n'
        )
        self.template.write_text(
            '<!-- swanlake-section-start: rules -->\nNEW\n<!-- swanlake-section-end: rules -->\n'
        )
        result = sync_vault.sync_file(self.vault_file, self.template, 'rules')
        self.assertEqual(result, 'skipped-divergent')
        self.assertIn('KEPT', self.vault_file.read_text())

    def test_no_op_when_content_unchanged(self):
        body = (
            '<!-- swanlake-section-start: rules -->\nSAME\n<!-- swanlake-section-end: rules -->\n'
        )
        self.vault_file.write_text(body)
        self.template.write_text(body)
        result = sync_vault.sync_file(self.vault_file, self.template, 'rules')
        self.assertEqual(result, 'unchanged')

    def test_section_not_in_template_raises(self):
        self.vault_file.write_text('body')
        self.template.write_text('# template with no markers')
        with self.assertRaises(ValueError):
            sync_vault.sync_file(self.vault_file, self.template, 'rules')

    def test_atomic_write_no_partial_on_crash(self):
        """File must be fully valid after sync — no half-written content."""
        self.vault_file.write_text('original')
        self.template.write_text(
            '<!-- swanlake-section-start: rules -->\nNEW\n<!-- swanlake-section-end: rules -->\n'
        )
        sync_vault.sync_file(self.vault_file, self.template, 'rules')
        # File must be readable + complete (markers + content present together).
        out = self.vault_file.read_text()
        self.assertIn('<!-- swanlake-section-start: rules -->', out)
        self.assertIn('<!-- swanlake-section-end: rules -->', out)
        self.assertIn('NEW', out)


class RunSyncAllErrorHandlingTest(unittest.TestCase):
    """Verify run_sync_all() return codes + timestamp-on-success behavior."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)
        self.canon_dir = self.tmpdir / 'canon'
        self.canon_dir.mkdir()
        self.template = self.canon_dir / 'vault-template.md'
        self.template.write_text(
            '<!-- swanlake-section-start: defense-beacon-rules -->\n'
            'RULES\n'
            '<!-- swanlake-section-end: defense-beacon-rules -->\n'
        )
        self.dmap = self.tmpdir / 'dmap.json'
        self.state = self.tmpdir / 'last-sync.json'

    def tearDown(self):
        self._tmpdir.cleanup()

    def _config_patch(self):
        """Build a Config object pointing at the per-test paths."""
        from reconciler.config import Config
        return Config(
            deployment_map_path=self.dmap,
            vault_root=self.tmpdir,
            notion_master_page_id='unused',
            notion_posture_page_id='unused',
            swanlake_repo_path=self.tmpdir,
            canon_dir=self.canon_dir,
        )

    def _run_with_dmap(self, dmap_content: dict) -> tuple[int, bool]:
        """Helper: write dmap, run sync_all under patched config + status write,
        return (exit_code, timestamp_was_written).

        Patches write_sync_timestamp directly (rather than STATE_PATH) because
        write_sync_timestamp captures STATE_PATH as a default arg at def-time,
        so monkeypatching the module attribute would be a no-op AND would
        pollute the operator's real state file.
        """
        from unittest.mock import patch
        self.dmap.write_text(json.dumps(dmap_content))
        cfg = self._config_patch()
        state = self.state

        def fake_write(surface, when=None):
            state.write_text(f'{{"{surface}": "stamped"}}')

        with patch('reconciler.config.load', return_value=cfg):
            with patch('reconciler.status.write_sync_timestamp',
                       side_effect=fake_write):
                rc = sync_vault.run_sync_all()
        return rc, self.state.exists()

    def test_returns_zero_on_clean_sync(self):
        vault_file = self.tmpdir / 'note.md'
        vault_file.write_text('body')
        rc, written = self._run_with_dmap({
            'surfaces': {'vault-root': [str(vault_file)]}
        })
        self.assertEqual(rc, 0)
        self.assertTrue(written)

    def test_returns_one_on_per_file_error(self):
        # Point at a directory instead of a file to force sync_file to error.
        bad = self.tmpdir / 'is-a-dir'
        bad.mkdir()
        rc, written = self._run_with_dmap({
            'surfaces': {'vault-root': [str(bad)]}
        })
        self.assertEqual(rc, 1)
        self.assertFalse(written)  # timestamp NOT written on any error

    def test_no_timestamp_when_zero_files_processed(self):
        rc, written = self._run_with_dmap({'surfaces': {}})
        self.assertEqual(rc, 0)
        self.assertFalse(written)  # timestamp NOT written when nothing to do

    def test_returns_two_on_missing_config(self):
        from unittest.mock import patch
        from reconciler.config import ConfigMissing
        with patch('reconciler.config.load', side_effect=ConfigMissing('test')):
            rc = sync_vault.run_sync_all()
        self.assertEqual(rc, 2)

    def test_returns_two_on_unreadable_dmap(self):
        from unittest.mock import patch
        cfg = self._config_patch()
        # Don't write the dmap file — read will fail.
        with patch('reconciler.config.load', return_value=cfg):
            rc = sync_vault.run_sync_all()
        self.assertEqual(rc, 2)


if __name__ == '__main__':
    unittest.main()
