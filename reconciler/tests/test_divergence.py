"""Tests for the divergence frontmatter parser."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reconciler import divergence


class DivergenceTest(unittest.TestCase):
    def setUp(self):
        # TemporaryDirectory cleans up automatically on tearDown.
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)
        self._counter = 0

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(self, content: str, encoding: str = 'utf-8') -> Path:
        self._counter += 1
        p = self.tmpdir / f'note-{self._counter}.md'
        p.write_text(content, encoding=encoding)
        return p

    def _write_bytes(self, content: bytes) -> Path:
        self._counter += 1
        p = self.tmpdir / f'note-{self._counter}.md'
        p.write_bytes(content)
        return p

    def test_marked_intentional_returns_true(self):
        p = self._write("---\nswanlake-divergence: intentional\ntitle: foo\n---\nbody\n")
        self.assertTrue(divergence.is_divergent(p))

    def test_no_frontmatter_returns_false(self):
        p = self._write('# just a heading, no frontmatter\n')
        self.assertFalse(divergence.is_divergent(p))

    def test_frontmatter_without_marker_returns_false(self):
        p = self._write("---\ntitle: foo\n---\nbody\n")
        self.assertFalse(divergence.is_divergent(p))

    def test_marker_with_other_value_returns_false(self):
        """Only the literal value 'intentional' opts out."""
        p = self._write("---\nswanlake-divergence: maybe\n---\n")
        self.assertFalse(divergence.is_divergent(p))

    def test_missing_file_returns_false(self):
        self.assertFalse(divergence.is_divergent(self.tmpdir / 'nonexistent.md'))

    def test_crlf_line_endings_returns_true(self):
        """Windows-authored files use CRLF; must still detect the marker."""
        p = self._write_bytes(
            b'---\r\nswanlake-divergence: intentional\r\n---\r\nbody\r\n'
        )
        self.assertTrue(divergence.is_divergent(p))

    def test_no_trailing_newline_after_closing_returns_true(self):
        """Spec-legal Markdown: trailing newline after closing --- is optional."""
        p = self._write('---\nswanlake-divergence: intentional\n---')
        self.assertTrue(divergence.is_divergent(p))


if __name__ == '__main__':
    unittest.main()
