"""Tests for the `.swanlake-no-beacon` opt-out helper (B13)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake.commands.beacon import _optout


class OptOutMarkerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_marker_returns_none(self):
        target = self.root / "subdir" / "CLAUDE.md"
        target.parent.mkdir(parents=True)
        target.write_text("# stub")
        self.assertIsNone(_optout.find_marker(target, ceiling=self.root))

    def test_empty_marker_excludes_all(self):
        sub = self.root / "scratchpad"
        sub.mkdir()
        (sub / _optout.OPTOUT_FILENAME).write_text("")
        target = sub / "CLAUDE.md"
        target.write_text("# stub")
        marker = _optout.find_marker(target, ceiling=self.root)
        self.assertIsNotNone(marker)
        self.assertTrue(marker.excludes_all)
        self.assertTrue(marker.excludes("any-surface"))

    def test_marker_with_surface_filter(self):
        sub = self.root / "p1"
        sub.mkdir()
        (sub / _optout.OPTOUT_FILENAME).write_text("surfaces: [cms-foo, cms-bar]\n")
        target = sub / "CLAUDE.md"
        target.write_text("# stub")
        marker = _optout.find_marker(target, ceiling=self.root)
        self.assertIsNotNone(marker)
        self.assertFalse(marker.excludes_all)
        self.assertTrue(marker.excludes("cms-foo"))
        self.assertTrue(marker.excludes("cms-bar"))
        self.assertFalse(marker.excludes("cms-baz"))

    def test_ancestor_marker_applies_to_descendants(self):
        anc = self.root / "ancestor"
        deep = anc / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (anc / _optout.OPTOUT_FILENAME).write_text("")
        target = deep / "CLAUDE.md"
        target.write_text("# stub")
        excluded, marker = _optout.is_excluded(target, "any-surface", ceiling=self.root)
        self.assertTrue(excluded)
        self.assertEqual(marker.path, anc / _optout.OPTOUT_FILENAME)

    def test_ceiling_stops_walk(self):
        # Marker is ABOVE the ceiling -- must not be found.
        outer = self.root / "outer"
        inner = outer / "inner"
        inner.mkdir(parents=True)
        (outer / _optout.OPTOUT_FILENAME).write_text("")
        target = inner / "CLAUDE.md"
        target.write_text("# stub")
        # Ceiling = inner; walk stops there, never sees outer's marker.
        self.assertIsNone(_optout.find_marker(target, ceiling=inner))

    def test_malformed_marker_fails_closed(self):
        sub = self.root / "broken"
        sub.mkdir()
        # Looks like it should restrict to a list but the bracket is mangled.
        (sub / _optout.OPTOUT_FILENAME).write_text("surfaces: oops-no-brackets\n")
        target = sub / "CLAUDE.md"
        target.write_text("# stub")
        marker = _optout.find_marker(target, ceiling=self.root)
        # Falls back to exclude-all -- safer than treating malformed as absent.
        self.assertTrue(marker.excludes_all)


if __name__ == "__main__":
    unittest.main()
