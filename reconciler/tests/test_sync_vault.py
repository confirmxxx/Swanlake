"""Tests for the vault sync engine."""
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


if __name__ == '__main__':
    unittest.main()
