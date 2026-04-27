"""Tests for swanlake.scan -- per-project audit (v0.4 L1).

Cases:
  1. classify a fresh project (no CLAUDE.md, no opt-out, no cmas/) ->
     scaffold-cc.
  2. classify a CLAUDE.md-only project (no beacon header) -> deploy-beacon.
  3. classify a beaconed project (CLAUDE.md with the v1 header sentinel)
     -> clean.
  4. classify an opted-out project (.swanlake-no-beacon at root) ->
     opted-out, regardless of beacon state.
  5. classify a CMA-shaped fresh project (cmas/ but no CLAUDE.md) ->
     scaffold-cma.
  6. summary counts match the per-project rows.
  7. filter actionable / clean narrows the projects list AND recomputes
     summary against the narrowed list.
  8. SKIP_DIRS dirs (node_modules, .venv) are not reported as projects.
  9. include_nested picks up deep-nested monorepo packages.
  10. scan output (table + JSON) never echoes a canary tail.

NOTE on test fixtures: the v0.4 spec defines the beacon-header sentinel
as the literal `<!-- DEFENSE BEACON v` substring. We use the SENTINEL
string directly in fixtures because it is NOT a canary literal -- it is
the public Defense Beacon v1 header. Per-surface attribution markers
(`beacon-attrib-<surface>-<8>`) are constructed at runtime when needed,
matching the discipline in test_coverage.py.
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake import scan as _scan
from swanlake.commands import scan as scan_cmd


# Public sentinel from defense-beacon/SPEC.md. Not a canary literal --
# safe to embed in test source.
BEACON_HEADER = "<!-- DEFENSE BEACON v1 -- do not remove. Surface: test -->"

# Constructed at runtime so the contiguous attribution literal never
# appears in this source file (matches test_coverage.py discipline).
_PREFIX = "beacon-" + "attrib"
TAIL_SENTINEL = "TestX0YZ"
PLACEHOLDER_MARKER = f"{_PREFIX}-fixture-{TAIL_SENTINEL}"


def _ns(**kw) -> Namespace:
    defaults = {
        "json": False,
        "quiet": False,
        "cmd": "scan",
        "projects_root": None,
        "include_nested": False,
        "filter": "all",
    }
    defaults.update(kw)
    return Namespace(**defaults)


class ScanClassifyTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.projects = self.tmp / "projects"
        self.projects.mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _mk_project(
        self,
        name: str,
        *,
        with_claude_md: bool = False,
        with_beacon: bool = False,
        with_optout: bool = False,
        with_cmas: bool = False,
        beacon_body: str | None = None,
    ) -> Path:
        proj = self.projects / name
        proj.mkdir()
        if with_claude_md:
            body = "# " + name + "\n"
            if with_beacon:
                body += "\n" + (beacon_body or BEACON_HEADER) + "\n"
            (proj / "CLAUDE.md").write_text(body)
        if with_optout:
            (proj / ".swanlake-no-beacon").write_text("")
        if with_cmas:
            (proj / "cmas").mkdir()
        return proj

    def test_fresh_project_recommends_scaffold_cc(self):
        self._mk_project("alpha")
        payload = _scan.scan(projects_root=self.projects)
        rows = payload["projects"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["recommended_action"], "scaffold-cc")
        self.assertFalse(rows[0]["has_claude_md"])

    def test_claude_md_no_beacon_recommends_deploy_beacon(self):
        self._mk_project("beta", with_claude_md=True)
        payload = _scan.scan(projects_root=self.projects)
        rows = payload["projects"]
        self.assertEqual(rows[0]["recommended_action"], "deploy-beacon")
        self.assertTrue(rows[0]["has_claude_md"])
        self.assertFalse(rows[0]["has_beacon"])

    def test_beaconed_project_is_clean(self):
        self._mk_project("gamma", with_claude_md=True, with_beacon=True)
        payload = _scan.scan(projects_root=self.projects)
        rows = payload["projects"]
        self.assertEqual(rows[0]["recommended_action"], "clean")
        self.assertTrue(rows[0]["has_beacon"])

    def test_opted_out_project_is_opted_out_regardless_of_beacon(self):
        # Opt-out wins over both has_claude_md and has_beacon.
        self._mk_project(
            "delta",
            with_claude_md=True,
            with_beacon=True,
            with_optout=True,
        )
        payload = _scan.scan(projects_root=self.projects)
        rows = payload["projects"]
        self.assertEqual(rows[0]["recommended_action"], "opted-out")
        self.assertTrue(rows[0]["has_optout"])

    def test_cma_shaped_fresh_project_recommends_scaffold_cma(self):
        self._mk_project("epsilon", with_cmas=True)
        payload = _scan.scan(projects_root=self.projects)
        rows = payload["projects"]
        self.assertEqual(rows[0]["recommended_action"], "scaffold-cma")
        self.assertTrue(rows[0]["is_cma_shaped"])

    def test_summary_counts_match_rows(self):
        self._mk_project("a")  # scaffold-cc
        self._mk_project("b", with_claude_md=True)  # deploy-beacon
        self._mk_project("c", with_claude_md=True, with_beacon=True)  # clean
        self._mk_project("d", with_optout=True)  # opted-out
        self._mk_project("e", with_cmas=True)  # scaffold-cma
        payload = _scan.scan(projects_root=self.projects)
        s = payload["summary"]
        self.assertEqual(s["n_total"], 5)
        # actionable = scaffold-cc + deploy-beacon + scaffold-cma = 3
        self.assertEqual(s["n_actionable"], 3)
        self.assertEqual(s["n_clean"], 1)
        self.assertEqual(s["n_optout"], 1)
        self.assertEqual(s["n_cma"], 1)

    def test_filter_actionable_narrows_and_recomputes_summary(self):
        self._mk_project("a")  # scaffold-cc
        self._mk_project("b", with_claude_md=True, with_beacon=True)  # clean
        self._mk_project("c", with_optout=True)  # opted-out
        full = _scan.scan(projects_root=self.projects)
        narrowed = _scan.filter_payload(full, filter_mode="actionable")
        # Narrowed list contains only the scaffold-cc row.
        self.assertEqual(len(narrowed["projects"]), 1)
        self.assertEqual(
            narrowed["projects"][0]["recommended_action"], "scaffold-cc"
        )
        # Summary recomputed against the narrowed list.
        self.assertEqual(narrowed["summary"]["n_total"], 1)
        self.assertEqual(narrowed["summary"]["n_actionable"], 1)
        self.assertEqual(narrowed["summary"]["n_clean"], 0)

    def test_filter_clean_picks_only_clean(self):
        self._mk_project("a")  # scaffold-cc
        self._mk_project("b", with_claude_md=True, with_beacon=True)  # clean
        full = _scan.scan(projects_root=self.projects)
        narrowed = _scan.filter_payload(full, filter_mode="clean")
        self.assertEqual(len(narrowed["projects"]), 1)
        self.assertEqual(
            narrowed["projects"][0]["recommended_action"], "clean"
        )

    def test_skip_dirs_are_not_reported(self):
        # node_modules at the projects-root level should be skipped.
        (self.projects / "node_modules").mkdir()
        # Also a dot-prefixed dir.
        (self.projects / ".cache").mkdir()
        # And a real project to confirm normal walks still work.
        self._mk_project("real-proj")
        payload = _scan.scan(projects_root=self.projects)
        names = {Path(r["path"]).name for r in payload["projects"]}
        self.assertIn("real-proj", names)
        self.assertNotIn("node_modules", names)
        self.assertNotIn(".cache", names)

    def test_include_nested_picks_up_deep_packages(self):
        # Build projects/Monorepo/packages/core/agent/CLAUDE.md
        deep = self.projects / "Monorepo" / "packages" / "core" / "agent"
        deep.mkdir(parents=True)
        (deep / "CLAUDE.md").write_text("# nested\n" + BEACON_HEADER + "\n")
        # Default scan (one-level) should NOT find the nested project.
        flat = _scan.scan(projects_root=self.projects)
        flat_names = {Path(r["path"]).name for r in flat["projects"]}
        # Monorepo is a top-level dir and gets reported (no CLAUDE.md at
        # its root, so action will be scaffold-cc).
        self.assertIn("Monorepo", flat_names)
        self.assertNotIn("agent", flat_names)
        # include_nested should find the deep package.
        nested = _scan.scan(projects_root=self.projects, include_nested=True)
        nested_names = {Path(r["path"]).name for r in nested["projects"]}
        self.assertIn("agent", nested_names)


class ScanCommandTest(unittest.TestCase):
    """End-to-end CLI handler tests (table + JSON output, no canary leaks)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.projects = self.tmp / "projects"
        self.projects.mkdir()
        # One project carrying a constructed attribution marker so the
        # canary-leak check has something to detect.
        proj = self.projects / "p1"
        proj.mkdir()
        (proj / "CLAUDE.md").write_text(
            "# p1\n\n"
            f"{BEACON_HEADER}\n"
            f"Attribution: {PLACEHOLDER_MARKER}\n"
        )

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_scan_run_returns_clean(self):
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            rc = scan_cmd.run(_ns(projects_root=str(self.projects)))
        self.assertEqual(rc, 0)
        self.assertIn("p1", captured.getvalue())
        self.assertIn("clean", captured.getvalue())

    def test_scan_run_json_returns_clean(self):
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            rc = scan_cmd.run(_ns(
                projects_root=str(self.projects), json=True
            ))
        self.assertEqual(rc, 0)
        # JSON shape includes the schema + projects + summary.
        self.assertIn('"schema"', captured.getvalue())
        self.assertIn('"projects"', captured.getvalue())
        self.assertIn('"summary"', captured.getvalue())

    def test_scan_does_not_echo_canary_tail(self):
        """Critical: scan output (table OR JSON) must never contain
        the 8-char tail of any attribution marker."""
        for json_flag in (False, True):
            captured = io.StringIO()
            with patch("sys.stdout", captured):
                scan_cmd.run(_ns(
                    projects_root=str(self.projects), json=json_flag
                ))
            self.assertNotIn(
                TAIL_SENTINEL, captured.getvalue(),
                f"canary tail leaked in {'json' if json_flag else 'table'} output"
            )
            self.assertNotIn(PLACEHOLDER_MARKER, captured.getvalue())

    def test_scan_filter_actionable(self):
        # Add a clean project + an actionable one.
        proj2 = self.projects / "needs-beacon"
        proj2.mkdir()
        (proj2 / "CLAUDE.md").write_text("# no beacon here\n")
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            rc = scan_cmd.run(_ns(
                projects_root=str(self.projects),
                filter="actionable",
            ))
        self.assertEqual(rc, 0)
        out = captured.getvalue()
        self.assertIn("needs-beacon", out)
        self.assertIn("deploy-beacon", out)
        # Clean project should be filtered out.
        self.assertNotIn("p1 ", out)


if __name__ == "__main__":
    unittest.main()
