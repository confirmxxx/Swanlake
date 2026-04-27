"""Tests for swanlake.commands.init_project -- v0.4 L3 scaffold.

Cases:
  1. cc scaffold creates CLAUDE.md + canon/operating-rules.md +
     .swanlake-no-beacon.example.
  2. cma scaffold adds cmas/ + zones.example.yaml on top.
  3. CLAUDE.md template substitutes {project_name} placeholder.
  4. CLAUDE.md template imports `@canon/operating-rules.md`
     (D8 -- drift-resistant by construction).
  5. canon/operating-rules.md byte-matches the bundled canon
     copy (drift detection).
  6. refuses non-empty target dir without --force; --force overwrites.
  7. refuses target opted-out via .swanlake-no-beacon.
  8. allows .git/ in target (operator may have run `git init` first).
  9. CLI handler routes through `swanlake init project` correctly
     (--type required; missing --type returns USAGE).
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

from swanlake.commands import init_project as init_project_pkg
from swanlake.commands.init_project import scaffold as scaffold_mod


def _ns(**kw) -> Namespace:
    defaults = {
        "json": False,
        "quiet": False,
        "cmd": "init",
        "init_op": "project",
        "target": None,
        "type": None,
        "force": False,
        "name": None,
        "add_surface": None,
    }
    defaults.update(kw)
    return Namespace(**defaults)


class CcScaffoldTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_cc_scaffold_creates_expected_files(self):
        target = self.tmp / "newproj"
        rc, payload = scaffold_mod.scaffold(target, project_type="cc")
        self.assertEqual(rc, 0, msg=payload)
        self.assertTrue((target / "CLAUDE.md").is_file())
        self.assertTrue((target / "canon" / "operating-rules.md").is_file())
        self.assertTrue((target / ".swanlake-no-beacon.example").is_file())
        # CMA-specific entries should NOT exist for cc type.
        self.assertFalse((target / "cmas").exists())
        self.assertFalse((target / "zones.example.yaml").exists())

    def test_cc_claude_md_substitutes_project_name(self):
        target = self.tmp / "alpha-project"
        scaffold_mod.scaffold(target, project_type="cc")
        body = (target / "CLAUDE.md").read_text()
        self.assertIn("alpha-project", body)
        # Must NOT contain the literal placeholder.
        self.assertNotIn("{project_name}", body)

    def test_cc_claude_md_imports_canon_rules(self):
        """D8: scaffold uses @canon/operating-rules.md import, not
        inline embed -- drift-resistant by construction."""
        target = self.tmp / "beta"
        scaffold_mod.scaffold(target, project_type="cc")
        body = (target / "CLAUDE.md").read_text()
        self.assertIn("@canon/operating-rules.md", body)

    def test_cc_canon_byte_matches_bundled_copy(self):
        """R5: the canon file in the scaffold must byte-match the
        bundled template copy (which is itself a copy of the repo's
        canon/operating-rules.md). Drift here would be a packaging
        bug -- this test detects it."""
        target = self.tmp / "gamma"
        scaffold_mod.scaffold(target, project_type="cc")
        scaffolded = (target / "canon" / "operating-rules.md").read_bytes()
        bundled = (
            scaffold_mod._templates_root() / "cc" / "canon" / "operating-rules.md"
        ).read_bytes()
        self.assertEqual(scaffolded, bundled)

    def test_payload_lists_created_files(self):
        target = self.tmp / "delta"
        rc, payload = scaffold_mod.scaffold(target, project_type="cc")
        self.assertEqual(rc, 0)
        created = payload["created"]
        self.assertEqual(payload["target"], str(target))
        self.assertEqual(payload["type"], "cc")
        # All three files in the created list.
        self.assertTrue(any("CLAUDE.md" in p for p in created))
        self.assertTrue(any("operating-rules.md" in p for p in created))
        self.assertTrue(any(".swanlake-no-beacon.example" in p for p in created))


class CmaScaffoldTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_cma_scaffold_creates_extra_files(self):
        target = self.tmp / "myaca"
        rc, payload = scaffold_mod.scaffold(target, project_type="cma")
        self.assertEqual(rc, 0, msg=payload)
        # cc-shared files.
        self.assertTrue((target / "CLAUDE.md").is_file())
        self.assertTrue((target / "canon" / "operating-rules.md").is_file())
        # cma-specific.
        self.assertTrue((target / "cmas").is_dir())
        self.assertTrue((target / "cmas" / ".gitkeep").is_file())
        self.assertTrue((target / "zones.example.yaml").is_file())

    def test_cma_zones_example_has_yaml_shape(self):
        target = self.tmp / "agentproj"
        scaffold_mod.scaffold(target, project_type="cma")
        body = (target / "zones.example.yaml").read_text()
        # Just check the file has the expected top-level structure.
        self.assertIn("zones:", body)
        self.assertIn("PUBLIC:", body)
        self.assertIn("INTERNAL:", body)
        self.assertIn("PRIVILEGED:", body)
        self.assertIn("REFLEX:", body)


class RefusalsTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_refuses_unknown_type(self):
        target = self.tmp / "x"
        rc, payload = scaffold_mod.scaffold(target, project_type="bogus")
        self.assertEqual(rc, 2)
        self.assertIn("unknown --type", payload["error"])

    def test_refuses_non_empty_target_without_force(self):
        target = self.tmp / "existing"
        target.mkdir()
        (target / "important-work.txt").write_text("don't overwrite me")
        rc, payload = scaffold_mod.scaffold(target, project_type="cc")
        self.assertEqual(rc, 2)
        self.assertIn("non-empty", payload["error"])
        # The pre-existing file is untouched.
        self.assertEqual(
            (target / "important-work.txt").read_text(),
            "don't overwrite me",
        )

    def test_force_allows_non_empty_target(self):
        target = self.tmp / "force-me"
        target.mkdir()
        (target / "old-readme.md").write_text("legacy")
        rc, payload = scaffold_mod.scaffold(target, project_type="cc", force=True)
        self.assertEqual(rc, 0)
        # Scaffold files now exist alongside the old file (--force does
        # not rm-rf, it just removes the empty-dir gate).
        self.assertTrue((target / "CLAUDE.md").is_file())
        self.assertTrue((target / "old-readme.md").is_file())

    def test_allows_git_init_dir(self):
        """A pre-existing .git/ dir should NOT count as non-empty --
        the operator may have run `git init` first."""
        target = self.tmp / "preinitted"
        target.mkdir()
        (target / ".git").mkdir()
        (target / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        rc, payload = scaffold_mod.scaffold(target, project_type="cc")
        self.assertEqual(rc, 0, msg=payload)
        self.assertTrue((target / "CLAUDE.md").is_file())

    def test_refuses_opted_out_target(self):
        # Build an opted-out tree: parent has .swanlake-no-beacon, child
        # is the target.
        parent = self.tmp / "scope"
        parent.mkdir()
        (parent / ".swanlake-no-beacon").write_text("")
        target = parent / "child"
        rc, payload = scaffold_mod.scaffold(target, project_type="cc")
        self.assertEqual(rc, 2)
        self.assertIn("opted out", payload["error"])
        # The target dir was not created or scaffolded.
        self.assertFalse((target / "CLAUDE.md").exists())


class CliHandlerTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_cli_run_creates_files(self):
        target = self.tmp / "via-cli"
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            rc = init_project_pkg.run(_ns(target=str(target), type="cc"))
        self.assertEqual(rc, 0)
        self.assertTrue((target / "CLAUDE.md").is_file())
        self.assertIn("created:", captured.getvalue())

    def test_cli_run_json_output(self):
        target = self.tmp / "json-target"
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            rc = init_project_pkg.run(_ns(
                target=str(target), type="cma", json=True
            ))
        self.assertEqual(rc, 0)
        out = captured.getvalue()
        self.assertIn('"created"', out)
        self.assertIn('"type"', out)
        self.assertIn('"cma"', out)

    def test_cli_run_missing_type_is_usage_error(self):
        target = self.tmp / "no-type"
        captured = io.StringIO()
        with patch("sys.stdout", captured), patch("sys.stderr", io.StringIO()):
            rc = init_project_pkg.run(_ns(target=str(target), type=None))
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
