"""Tests for the surfaces.yaml loader."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake.commands.beacon import _surfaces


class SurfacesParseTest(unittest.TestCase):
    def test_plain_id_lines(self):
        text = """
        # comments and blank lines tolerated

        cms-project-alpha
        vault-root
        repo-foo
        """
        specs = _surfaces.parse_surfaces_text(text)
        ids = [s.surface_id for s in specs]
        self.assertEqual(ids, ["cms-project-alpha", "vault-root", "repo-foo"])
        # Type inferred from prefix.
        type_map = {s.surface_id: s.type_id for s in specs}
        self.assertEqual(type_map["cms-project-alpha"], "claude-md")
        self.assertEqual(type_map["vault-root"], "vault")
        self.assertEqual(type_map["repo-foo"], "github-public")

    def test_annotated_block(self):
        text = """
        deploy-bar:
          type: vercel-env
          target: my-vercel-project
        cms-baz
        """
        specs = _surfaces.parse_surfaces_text(text)
        self.assertEqual(len(specs), 2)
        d = {s.surface_id: s for s in specs}
        self.assertEqual(d["deploy-bar"].type_id, "vercel-env")
        self.assertEqual(d["deploy-bar"].target, "my-vercel-project")
        self.assertEqual(d["cms-baz"].type_id, "claude-md")
        self.assertIsNone(d["cms-baz"].target)

    def test_invalid_id_skipped(self):
        text = """
        Bad-Caps
        valid-id
        """
        specs = _surfaces.parse_surfaces_text(text)
        ids = [s.surface_id for s in specs]
        self.assertEqual(ids, ["valid-id"])

    def test_load_from_file(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "surfaces.yaml"
            p.write_text("cms-test\nrepo-something\n")
            specs = _surfaces.load_surfaces(p)
            self.assertEqual(len(specs), 2)


class SurfacesWarningTest(unittest.TestCase):
    """E23 / E24 -- the parser used to silently skip malformed lines and
    silently last-write-win duplicate keys. Operators who typoed a
    surface ID or copy-pasted an annotated block got zero feedback. The
    sibling `parse_surfaces_text_with_warnings` returns a structured
    list of skipped lines + duplicate keys so file loaders can surface
    them to stderr."""

    def test_invalid_plain_id_yields_warning(self):
        specs, warnings = _surfaces.parse_surfaces_text_with_warnings(
            "Bad-Caps\nvalid-id\n"
        )
        ids = [s.surface_id for s in specs]
        self.assertEqual(ids, ["valid-id"])
        # The malformed first line surfaces as a warning row.
        self.assertEqual(len(warnings), 1)
        self.assertIn("line 1", warnings[0])
        self.assertIn("Bad-Caps", warnings[0])

    def test_invalid_annotated_header_yields_warning(self):
        specs, warnings = _surfaces.parse_surfaces_text_with_warnings(
            "Bad-Header:\n  type: claude-md\nvalid-id\n"
        )
        ids = [s.surface_id for s in specs]
        self.assertEqual(ids, ["valid-id"])
        self.assertTrue(any("Bad-Header" in w for w in warnings))

    def test_duplicate_keys_yield_warning(self):
        specs, warnings = _surfaces.parse_surfaces_text_with_warnings(
            "cms-foo:\n  type: claude-md\n  type: vault\n"
        )
        # Last-wins behaviour preserved (back-compat).
        self.assertEqual(specs[0].type_id, "vault")
        # Warning surfaces the collision.
        self.assertEqual(len(warnings), 1)
        self.assertIn("duplicate key", warnings[0])
        self.assertIn("type", warnings[0])

    def test_no_warnings_on_clean_input(self):
        specs, warnings = _surfaces.parse_surfaces_text_with_warnings(
            "cms-foo\nvault-bar\n"
        )
        self.assertEqual(len(specs), 2)
        self.assertEqual(warnings, [])

    def test_load_surfaces_emits_warnings_to_stderr(self):
        import io
        import sys
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "surfaces.yaml"
            p.write_text("Bad-Caps\nvalid-id\n")
            captured = io.StringIO()
            original = sys.stderr
            sys.stderr = captured
            try:
                specs = _surfaces.load_surfaces(p)
            finally:
                sys.stderr = original
        self.assertEqual([s.surface_id for s in specs], ["valid-id"])
        err = captured.getvalue()
        self.assertIn("Bad-Caps", err)
        self.assertIn("surfaces.yaml", err)


if __name__ == "__main__":
    unittest.main()
