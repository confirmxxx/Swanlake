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


if __name__ == "__main__":
    unittest.main()
