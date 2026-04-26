"""Tests for `swanlake beacon checklist`.

Covers:
  - default output is stdout
  - --out FILE writes mode 0600 with stderr warning
  - REMOTE-only filter: LOCAL surfaces excluded by default
  - --surface restricts to one surface (any type)
  - per-type paste action templates render correctly
  - missing surfaces.yaml exits USAGE
  - History row appended (op=checklist)
  - The fenced block carries the make-canaries.py output
"""
from __future__ import annotations

import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake import state as _state
from swanlake.commands.beacon import _history, checklist as cl_cmd
from swanlake.commands.beacon._surfaces import SurfaceSpec
from swanlake.exit_codes import CLEAN, USAGE


_PREFIX = "beacon-" + "attrib"


def _synthetic_beacon(surface: str, tail: str) -> str:
    attrib = f"{_PREFIX}-{surface}-{tail}"
    return (
        f"<!-- DEFENSE BEACON v1 -- Surface: {surface} -->\n"
        f"<!-- BEGIN SURFACE ATTRIBUTION -- {surface} -->\n"
        f"- `{attrib}`\n"
        f"<!-- END SURFACE ATTRIBUTION -- {surface} -->\n"
    )


def _ns(**kw) -> Namespace:
    defaults = {
        "json": False,
        "quiet": False,
        "cmd": "beacon",
        "beacon_op": "checklist",
        "out": None,
        "surface": None,
        "include": "pending",
        "remind_export_stale": None,
    }
    defaults.update(kw)
    return Namespace(**defaults)


def _patched_subprocess_run(repo_root: Path, surface: str, tail: str):
    real_run = subprocess.run

    def _run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if (
            isinstance(cmd, list)
            and len(cmd) >= 2
            and cmd[1].endswith("make-canaries.py")
        ):
            if "--version" in cmd:
                fake = MagicMock()
                fake.returncode = 0
                fake.stdout = "make-canaries.py 1.1.0\n"
                fake.stderr = ""
                return fake
            out_path = repo_root / "defense-beacon" / "reference" / "out" / f"{surface}.md"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(_synthetic_beacon(surface, tail))
            fake = MagicMock()
            fake.returncode = 0
            fake.stdout = ""
            fake.stderr = ""
            return fake
        return real_run(*args, **kwargs)

    return _run


def _setup_fake_repo(tmp: Path) -> Path:
    """Build a minimal fake Swanlake repo with the marker for find_repo_root."""
    root = tmp / "fake-repo"
    root.mkdir()
    (root / "tools").mkdir()
    (root / "tools" / "status-segment.py").write_text("# stub\n")
    beacon_ref = root / "defense-beacon" / "reference"
    beacon_ref.mkdir(parents=True)
    (beacon_ref / "make-canaries.py").write_text("# stub for tests\n")
    (beacon_ref / "out").mkdir()
    return root


class ChecklistBaseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)
        self.repo = _setup_fake_repo(self.tmp)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmp.cleanup()

    def _seed_remote_surfaces(self, *specs: SurfaceSpec) -> Path:
        """Patch the surfaces loader to return the given specs."""
        return self.repo  # caller patches via _collect_remote_surfaces


class ChecklistStdoutTest(ChecklistBaseTest):
    def test_remote_surface_renders_to_stdout(self):
        captured = io.StringIO()
        with patch.object(
            cl_cmd, "_collect_remote_surfaces",
            return_value=[("repo-x", "github-public", "acme/x:README.md")],
        ), patch.object(cl_cmd, "_resolve_repo_root", return_value=self.repo), \
             patch(
                 "swanlake.commands.beacon.checklist.subprocess.run",
                 side_effect=_patched_subprocess_run(self.repo, "repo-x", "Tail0001"),
             ), patch("sys.stdout", captured):
            rc = cl_cmd.run(_ns())
        self.assertEqual(rc, CLEAN)
        out = captured.getvalue()
        self.assertIn("## repo-x", out)
        self.assertIn("paste action:", out)
        self.assertIn("acme/x:README.md", out)
        self.assertIn("swanlake beacon verify --surface repo-x", out)
        self.assertIn("DEFENSE BEACON v1", out)

    def test_no_remote_surfaces_returns_usage(self):
        captured_err = io.StringIO()
        with patch.object(
            cl_cmd, "_collect_remote_surfaces", return_value=[],
        ), patch.object(cl_cmd, "_resolve_repo_root", return_value=self.repo), \
             patch("sys.stderr", captured_err):
            rc = cl_cmd.run(_ns())
        self.assertEqual(rc, USAGE)
        self.assertIn("no REMOTE surfaces", captured_err.getvalue())

    def test_surface_filter_can_target_one_id(self):
        captured = io.StringIO()
        with patch.object(
            cl_cmd, "_collect_remote_surfaces",
            return_value=[("notion-x", "notion", "https://workspace/x")],
        ), patch.object(cl_cmd, "_resolve_repo_root", return_value=self.repo), \
             patch(
                 "swanlake.commands.beacon.checklist.subprocess.run",
                 side_effect=_patched_subprocess_run(self.repo, "notion-x", "AbcD0001"),
             ), patch("sys.stdout", captured):
            rc = cl_cmd.run(_ns(surface="notion-x"))
        self.assertEqual(rc, CLEAN)
        self.assertIn("notion-x", captured.getvalue())


class ChecklistOutFileTest(ChecklistBaseTest):
    def test_out_file_written_mode_0600(self):
        out_path = self.tmp / "checklist.md"
        captured_err = io.StringIO()
        with patch.object(
            cl_cmd, "_collect_remote_surfaces",
            return_value=[("repo-y", "github-public", "acme/y:CLAUDE.md")],
        ), patch.object(cl_cmd, "_resolve_repo_root", return_value=self.repo), \
             patch(
                 "swanlake.commands.beacon.checklist.subprocess.run",
                 side_effect=_patched_subprocess_run(self.repo, "repo-y", "Tail0002"),
             ), patch("sys.stderr", captured_err):
            rc = cl_cmd.run(_ns(out=str(out_path)))
        self.assertEqual(rc, CLEAN)
        self.assertTrue(out_path.exists())
        mode = stat.S_IMODE(out_path.stat().st_mode)
        self.assertEqual(mode, 0o600)
        # Stderr carries the disposal warning.
        err = captured_err.getvalue()
        self.assertIn("WARNING", err)
        self.assertIn("live canary tokens", err)
        # File body has the block.
        text = out_path.read_text()
        self.assertIn("repo-y", text)
        self.assertIn("DO NOT COMMIT", text)


class ChecklistHistoryTest(ChecklistBaseTest):
    def test_history_row_appended(self):
        with patch.object(
            cl_cmd, "_collect_remote_surfaces",
            return_value=[("repo-z", "github-public", "acme/z:README.md")],
        ), patch.object(cl_cmd, "_resolve_repo_root", return_value=self.repo), \
             patch(
                 "swanlake.commands.beacon.checklist.subprocess.run",
                 side_effect=_patched_subprocess_run(self.repo, "repo-z", "Tail0003"),
             ), patch("sys.stdout", io.StringIO()):
            cl_cmd.run(_ns())
        records = _history.read_all()
        self.assertTrue(any(
            r.get("op") == "checklist"
            and r.get("outcome") == "checklist-printed"
            for r in records
        ))


class ChecklistStalenessTest(ChecklistBaseTest):
    """v0.3.x bonus: --remind-export-stale warning."""

    def test_export_missing_warns(self):
        captured_err = io.StringIO()
        with patch.object(
            cl_cmd, "_collect_remote_surfaces",
            return_value=[("repo-x", "github-public", "acme/x:README.md")],
        ), patch.object(cl_cmd, "_resolve_repo_root", return_value=self.repo), \
             patch(
                 "swanlake.commands.beacon.checklist.subprocess.run",
                 side_effect=_patched_subprocess_run(self.repo, "repo-x", "Tail9999"),
             ), patch("sys.stdout", io.StringIO()), \
             patch("sys.stderr", captured_err):
            cl_cmd.run(_ns(remind_export_stale="30d"))
        err = captured_err.getvalue()
        self.assertIn("routines export not found", err)

    def test_export_fresh_no_warning(self):
        # Touch a recent export file.
        export = _state.state_path("routines-export.json")
        export.parent.mkdir(parents=True, exist_ok=True)
        export.write_text("{}")
        captured_err = io.StringIO()
        with patch.object(
            cl_cmd, "_collect_remote_surfaces",
            return_value=[("repo-x", "github-public", "acme/x:README.md")],
        ), patch.object(cl_cmd, "_resolve_repo_root", return_value=self.repo), \
             patch(
                 "swanlake.commands.beacon.checklist.subprocess.run",
                 side_effect=_patched_subprocess_run(self.repo, "repo-x", "Tail9998"),
             ), patch("sys.stdout", io.StringIO()), \
             patch("sys.stderr", captured_err):
            cl_cmd.run(_ns(remind_export_stale="30d"))
        # Fresh export -> no staleness warning. Other stderr (warnings) may
        # still appear, but the "stale" / "not found" tags should not.
        err = captured_err.getvalue()
        self.assertNotIn("routines export not found", err)
        self.assertNotIn("threshold", err)

    def test_export_stale_warns(self):
        import os as _os
        export = _state.state_path("routines-export.json")
        export.parent.mkdir(parents=True, exist_ok=True)
        export.write_text("{}")
        # Backdate by 60 days.
        old = _os.stat(export).st_mtime - 60 * 86400
        _os.utime(export, (old, old))
        captured_err = io.StringIO()
        with patch.object(
            cl_cmd, "_collect_remote_surfaces",
            return_value=[("repo-x", "github-public", "acme/x:README.md")],
        ), patch.object(cl_cmd, "_resolve_repo_root", return_value=self.repo), \
             patch(
                 "swanlake.commands.beacon.checklist.subprocess.run",
                 side_effect=_patched_subprocess_run(self.repo, "repo-x", "Tail9997"),
             ), patch("sys.stdout", io.StringIO()), \
             patch("sys.stderr", captured_err):
            cl_cmd.run(_ns(remind_export_stale="30d"))
        err = captured_err.getvalue()
        self.assertIn("threshold", err)

    def test_bad_duration_warns(self):
        captured_err = io.StringIO()
        with patch.object(
            cl_cmd, "_collect_remote_surfaces",
            return_value=[("repo-x", "github-public", "acme/x:README.md")],
        ), patch.object(cl_cmd, "_resolve_repo_root", return_value=self.repo), \
             patch(
                 "swanlake.commands.beacon.checklist.subprocess.run",
                 side_effect=_patched_subprocess_run(self.repo, "repo-x", "Tail9996"),
             ), patch("sys.stdout", io.StringIO()), \
             patch("sys.stderr", captured_err):
            cl_cmd.run(_ns(remind_export_stale="bogus"))
        self.assertIn("bad duration", captured_err.getvalue())


class ChecklistTemplateTest(unittest.TestCase):
    def test_paste_action_per_type(self):
        self.assertIn("workspace page", cl_cmd._paste_action("notion", "https://x/y"))
        self.assertIn("env var", cl_cmd._paste_action("supabase-env", "MY_KEY"))
        self.assertIn("env var", cl_cmd._paste_action("vercel-env", "MY_KEY"))
        self.assertIn("PR", cl_cmd._paste_action("github-public", "owner/repo:path"))
        self.assertIn("routine", cl_cmd._paste_action("claude-routine", "routine-id"))
        # Unknown types fall through.
        self.assertIn("paste the block", cl_cmd._paste_action("unknown", "x"))


if __name__ == "__main__":
    unittest.main()
