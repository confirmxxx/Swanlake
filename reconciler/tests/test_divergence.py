"""Tests for the divergence frontmatter parser."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reconciler import divergence


class DivergenceTest(unittest.TestCase):
    def _write(self, content: str) -> Path:
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False)
        f.write(content)
        f.close()
        return Path(f.name)

    def test_marked_intentional_returns_true(self):
        p = self._write("""---
swanlake-divergence: intentional
title: foo
---
body
""")
        self.assertTrue(divergence.is_divergent(p))

    def test_no_frontmatter_returns_false(self):
        p = self._write('# just a heading, no frontmatter\n')
        self.assertFalse(divergence.is_divergent(p))

    def test_frontmatter_without_marker_returns_false(self):
        p = self._write("""---
title: foo
---
body
""")
        self.assertFalse(divergence.is_divergent(p))

    def test_marker_with_other_value_returns_false(self):
        """Only the literal value 'intentional' opts out."""
        p = self._write("""---
swanlake-divergence: maybe
---
""")
        self.assertFalse(divergence.is_divergent(p))

    def test_missing_file_returns_false(self):
        self.assertFalse(divergence.is_divergent(Path('/nonexistent/path.md')))


if __name__ == '__main__':
    unittest.main()
