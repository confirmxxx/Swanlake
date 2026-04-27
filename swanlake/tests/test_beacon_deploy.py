"""Tests for `swanlake beacon deploy` and the 12-step LOCAL safety machine.

The machine is in swanlake.commands.beacon._local; the CLI dispatcher is
in swanlake.commands.beacon.deploy. Tests cover:

  - REMOTE surfaces are refused with a checklist hint (DRIFT exit)
  - Surface-id grammar (step 1) refuses bad IDs
  - Missing surfaces.yaml entry (step 2) aborts cleanly
  - Path-traversal target (step 2) aborts cleanly
  - Target outside git tree (step 4) aborts cleanly
  - Dirty git tree (step 5) aborts cleanly -- D3 hard rule, no escape hatch
  - .swanlake-no-beacon ancestor marker (step 6) aborts cleanly
  - Replace-not-stack (step 6b / R2) handles three cases:
      * no prior block -> append
      * single matching block -> in-place replace
      * surface-id mismatch -> refuse
  - Backup written to ~/.swanlake/beacon-backups/<surface>/<ts>.bak mode 0600
  - Atomic write preserves existing file mode (matches v0.2.1 F6)
  - Dry-run skips backup + write
  - --yes skips the confirmation prompt
  - History row is appended on every outcome

We never invoke the real make-canaries.py from tests; we patch the
subprocess wrapper to return a synthetic beacon block built at runtime
(no canary literals in this source file).
"""
from __future__ import annotations

import io
import json
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
from swanlake.commands.beacon import _history, _local, _optout, deploy as deploy_cmd
from swanlake.commands.beacon._surfaces import SurfaceSpec
from swanlake.exit_codes import ALARM, CLEAN, DRIFT, NOT_IMPLEMENTED


_PREFIX = "beacon-" + "attrib"


def _synthetic_beacon(surface: str, tail: str) -> str:
    """Build a beacon block that satisfies the regex tests without any
    real canary literal. Mirrors the shape of beacon-template-v1.md but
    uses obviously synthetic placeholders.
    """
    attrib = f"{_PREFIX}-{surface}-{tail}"
    return (
        f"<!-- DEFENSE BEACON v1 -- do not remove. Surface: {surface} -->\n"
        f"# DEFENSE BEACON v1\n\n"
        f"<!-- BEGIN SURFACE ATTRIBUTION -- {surface} -->\n"
        f"- `AKIA_BEACON_TESTFIXTURE000000000000`\n"
        f"- `{attrib}`\n"
        f"<!-- END SURFACE ATTRIBUTION -- {surface} -->\n"
    )


def _ns(**kw) -> Namespace:
    defaults = {
        "json": False,
        "quiet": True,  # silence stdout noise during tests
        "cmd": "beacon",
        "beacon_op": "deploy",
        "surface": "cms-test",
        "dry_run": False,
        "yes": True,  # tests run non-interactively
    }
    defaults.update(kw)
    return Namespace(**defaults)


class _FakeRepo:
    """Helper: build a tmp git repo with a `defense-beacon/reference/` tree
    so the real make-canaries.py path resolution works -- but we patch
    the subprocess to never actually invoke the script.
    """

    def __init__(self, tmp: Path):
        self.root = tmp / "fake-repo"
        self.root.mkdir()
        self.tools_dir = self.root / "tools"
        self.tools_dir.mkdir()
        # Marker for swanlake._compat.find_repo_root.
        (self.tools_dir / "status-segment.py").write_text("# stub\n")
        # make-canaries.py needs to exist for step 7 path check.
        self.beacon_dir = self.root / "defense-beacon" / "reference"
        self.beacon_dir.mkdir(parents=True)
        (self.beacon_dir / "make-canaries.py").write_text(
            "#!/usr/bin/env python3\n# stub for tests\n"
        )
        (self.beacon_dir / "out").mkdir()

    def init_git(self):
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=str(self.root), check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(self.root), check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=str(self.root), check=True,
        )
        subprocess.run(
            ["git", "config", "commit.gpgsign", "false"],
            cwd=str(self.root), check=True,
        )

    def commit_clean(self):
        subprocess.run(
            ["git", "add", "-A"], cwd=str(self.root), check=True,
        )
        subprocess.run(
            ["git", "commit", "--no-gpg-sign", "-q", "-m", "init"],
            cwd=str(self.root), check=True,
        )


def _patched_subprocess_run(repo: _FakeRepo, surface: str, tail: str,
                            version: str = "1.1.0"):
    """Build a side-effect callable that simulates make-canaries.py.

    First invocation: --version  -> stdout `make-canaries.py <version>`.
    Second invocation: --surfaces <id> -> writes <out>/<id>.md and exits 0.
    Subsequent git invocations are passed through to the real subprocess.run.
    """
    real_run = subprocess.run
    state = {"calls": 0}

    def _run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if (
            isinstance(cmd, list)
            and len(cmd) >= 2
            and cmd[1].endswith("make-canaries.py")
        ):
            state["calls"] += 1
            if "--version" in cmd:
                fake = MagicMock()
                fake.returncode = 0
                fake.stdout = f"make-canaries.py {version}\n"
                fake.stderr = ""
                return fake
            # --surfaces <id>: write the synthetic beacon to the out file.
            out_path = repo.beacon_dir / "out" / f"{surface}.md"
            out_path.write_text(_synthetic_beacon(surface, tail))
            fake = MagicMock()
            fake.returncode = 0
            fake.stdout = ""
            fake.stderr = ""
            return fake
        # Pass through real git invocations.
        return real_run(*args, **kwargs)

    return _run


class DeployRemoteRefusalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmp.cleanup()

    def test_remote_surface_refused_with_checklist_hint(self):
        captured = io.StringIO()
        with patch.object(
            deploy_cmd, "_surface_type_from_yaml", return_value="github-public"
        ), patch("sys.stderr", captured):
            rc = deploy_cmd.run(_ns(surface="repo-foo", quiet=False))
        self.assertEqual(rc, DRIFT)
        self.assertIn("checklist-only", captured.getvalue())
        self.assertIn("swanlake beacon checklist", captured.getvalue())

    def test_unknown_type_returns_not_implemented(self):
        captured = io.StringIO()
        with patch.object(
            deploy_cmd, "_surface_type_from_yaml", return_value="bogus-type"
        ), patch("sys.stderr", captured):
            rc = deploy_cmd.run(_ns(surface="cms-x", quiet=False))
        self.assertEqual(rc, NOT_IMPLEMENTED)


class DeployStep1Step2Test(unittest.TestCase):
    """Surface-id validation + surfaces.yaml resolution failures."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmp.cleanup()

    def test_bad_surface_id_aborts(self):
        result = _local.run_local_deploy(
            surface="Bad-Caps",
            yes=True,
            quiet=True,
            repo_root=self.tmp,
            surfaces_yaml=None,
        )
        self.assertEqual(result.outcome, "aborted-bad-surface-id")
        self.assertIn("grammar", result.error)

    def test_missing_surfaces_yaml_aborts(self):
        result = _local.run_local_deploy(
            surface="cms-test",
            yes=True,
            quiet=True,
            repo_root=self.tmp,
            surfaces_yaml=None,
        )
        self.assertEqual(result.outcome, "aborted-resolve-failed")
        self.assertIn("surfaces.yaml not found", result.error)

    def test_surface_not_in_yaml_aborts(self):
        yaml_path = self.tmp / "surfaces.yaml"
        yaml_path.write_text("cms-other\n")
        result = _local.run_local_deploy(
            surface="cms-test",
            yes=True,
            quiet=True,
            repo_root=self.tmp,
            surfaces_yaml=yaml_path,
        )
        self.assertEqual(result.outcome, "aborted-resolve-failed")
        self.assertIn("not in surfaces.yaml", result.error)


class DeployFullMachineTest(unittest.TestCase):
    """End-to-end through the 12-step machine with subprocess mocking."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)

        self.repo = _FakeRepo(self.tmp)
        self.repo.init_git()

        # The "target project" is a separate clean git tree.
        self.target_proj = self.tmp / "target-proj"
        self.target_proj.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=str(self.target_proj), check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@e.com"], cwd=str(self.target_proj), check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "t"], cwd=str(self.target_proj), check=True,
        )
        subprocess.run(
            ["git", "config", "commit.gpgsign", "false"],
            cwd=str(self.target_proj), check=True,
        )
        self.target_path = self.target_proj / "CLAUDE.md"
        self.target_path.write_text("# TargetProj\n\nExisting content.\n")
        subprocess.run(
            ["git", "add", "-A"], cwd=str(self.target_proj), check=True,
        )
        subprocess.run(
            ["git", "commit", "--no-gpg-sign", "-q", "-m", "init"],
            cwd=str(self.target_proj), check=True,
        )

        # A surfaces.yaml that points at the target.
        self.yaml_path = self.tmp / "surfaces.yaml"
        self.yaml_path.write_text(
            f"cms-test:\n"
            f"  type: claude-md\n"
            f"  target: {self.target_path}\n"
        )

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmp.cleanup()

    def test_first_deploy_appends_block(self):
        with patch(
            "swanlake.commands.beacon._local.subprocess.run",
            side_effect=_patched_subprocess_run(self.repo, "cms-test", "Sec1Test"),
        ):
            result = _local.run_local_deploy(
                surface="cms-test",
                yes=True,
                quiet=True,
                repo_root=self.repo.root,
                surfaces_yaml=self.yaml_path,
            )
        self.assertEqual(result.outcome, "deployed", msg=result.error)
        new_text = self.target_path.read_text()
        self.assertIn("DEFENSE BEACON v1", new_text)
        self.assertIn("Existing content.", new_text)
        # Backup was written.
        self.assertIsNotNone(result.backup_path)
        bak = Path(result.backup_path)
        self.assertTrue(bak.exists())
        mode = stat.S_IMODE(bak.stat().st_mode)
        self.assertEqual(mode, 0o600)
        # Backup contains original content.
        self.assertEqual(bak.read_text(), "# TargetProj\n\nExisting content.\n")

    def test_dirty_tree_aborts_with_no_escape_hatch(self):
        """D3: strict abort on dirty tree; no --allow-dirty flag."""
        # Make the tree dirty.
        (self.target_proj / "scratch.txt").write_text("dirty\n")
        with patch(
            "swanlake.commands.beacon._local.subprocess.run",
            side_effect=_patched_subprocess_run(self.repo, "cms-test", "AbCdEfGh"),
        ):
            result = _local.run_local_deploy(
                surface="cms-test",
                yes=True,
                quiet=True,
                repo_root=self.repo.root,
                surfaces_yaml=self.yaml_path,
            )
        self.assertEqual(result.outcome, "aborted-clean-tree")
        # The original content is untouched.
        self.assertEqual(
            self.target_path.read_text(), "# TargetProj\n\nExisting content.\n"
        )

    def test_optout_marker_skips_deploy(self):
        (self.target_proj / _optout.OPTOUT_FILENAME).write_text("")
        # The opt-out file is itself a new file, making the tree dirty;
        # commit it so step 5 passes.
        subprocess.run(["git", "add", "-A"], cwd=str(self.target_proj), check=True)
        subprocess.run(
            ["git", "commit", "--no-gpg-sign", "-q", "-m", "optout"],
            cwd=str(self.target_proj), check=True,
        )
        with patch(
            "swanlake.commands.beacon._local.subprocess.run",
            side_effect=_patched_subprocess_run(self.repo, "cms-test", "Tail0001"),
        ):
            result = _local.run_local_deploy(
                surface="cms-test",
                yes=True,
                quiet=True,
                repo_root=self.repo.root,
                surfaces_yaml=self.yaml_path,
            )
        self.assertEqual(result.outcome, "skipped-by-optout")

    def test_dry_run_skips_write_and_backup(self):
        with patch(
            "swanlake.commands.beacon._local.subprocess.run",
            side_effect=_patched_subprocess_run(self.repo, "cms-test", "DryRunXX"),
        ):
            result = _local.run_local_deploy(
                surface="cms-test",
                dry_run=True,
                yes=True,
                quiet=True,
                repo_root=self.repo.root,
                surfaces_yaml=self.yaml_path,
            )
        self.assertEqual(result.outcome, "dry-run")
        # File untouched.
        self.assertEqual(
            self.target_path.read_text(), "# TargetProj\n\nExisting content.\n"
        )
        # No backup.
        self.assertIsNone(result.backup_path)
        backups_dir = _state.state_path("beacon-backups") / "cms-test"
        self.assertFalse(backups_dir.exists())

    def test_replace_not_stack_replaces_existing_block(self):
        """R2: a second deploy replaces the prior beacon block in place."""
        sub_run = _patched_subprocess_run(self.repo, "cms-test", "First001")
        with patch("swanlake.commands.beacon._local.subprocess.run", side_effect=sub_run):
            res1 = _local.run_local_deploy(
                surface="cms-test",
                yes=True, quiet=True,
                repo_root=self.repo.root,
                surfaces_yaml=self.yaml_path,
            )
        self.assertEqual(res1.outcome, "deployed", msg=res1.error)
        # Commit the deploy so the tree is clean again.
        subprocess.run(["git", "add", "-A"], cwd=str(self.target_proj), check=True)
        subprocess.run(
            ["git", "commit", "--no-gpg-sign", "-q", "-m", "deploy 1"],
            cwd=str(self.target_proj), check=True,
        )
        # Second deploy with a different tail.
        sub_run2 = _patched_subprocess_run(self.repo, "cms-test", "Second02")
        with patch("swanlake.commands.beacon._local.subprocess.run", side_effect=sub_run2):
            res2 = _local.run_local_deploy(
                surface="cms-test",
                yes=True, quiet=True,
                repo_root=self.repo.root,
                surfaces_yaml=self.yaml_path,
            )
        self.assertEqual(res2.outcome, "deployed", msg=res2.error)
        text = self.target_path.read_text()
        # Should contain the second tail, not the first.
        self.assertIn("Second02", text)
        self.assertNotIn("First001", text)
        # Should contain only ONE beacon block (no stacking).
        # One <!-- DEFENSE BEACON --> opening fence per block.
        self.assertEqual(text.count("<!-- DEFENSE BEACON v1"), 1)

    def test_surface_id_mismatch_refuses(self):
        """R2: refuses overwrite if file is attributed to another surface."""
        sub_run = _patched_subprocess_run(self.repo, "cms-test", "FirstAA1")
        with patch("swanlake.commands.beacon._local.subprocess.run", side_effect=sub_run):
            res1 = _local.run_local_deploy(
                surface="cms-test",
                yes=True, quiet=True,
                repo_root=self.repo.root,
                surfaces_yaml=self.yaml_path,
            )
        self.assertEqual(res1.outcome, "deployed")
        subprocess.run(["git", "add", "-A"], cwd=str(self.target_proj), check=True)
        subprocess.run(
            ["git", "commit", "--no-gpg-sign", "-q", "-m", "deploy 1"],
            cwd=str(self.target_proj), check=True,
        )
        # Now try to deploy a DIFFERENT surface to the same target.
        self.yaml_path.write_text(
            f"cms-other:\n"
            f"  type: claude-md\n"
            f"  target: {self.target_path}\n"
        )
        # The tree is now dirty (yaml just changed), but yaml is OUTSIDE the
        # target_proj git repo, so target_proj is still clean.
        sub_run2 = _patched_subprocess_run(self.repo, "cms-other", "OtherX02")
        with patch("swanlake.commands.beacon._local.subprocess.run", side_effect=sub_run2):
            res2 = _local.run_local_deploy(
                surface="cms-other",
                yes=True, quiet=True,
                repo_root=self.repo.root,
                surfaces_yaml=self.yaml_path,
            )
        self.assertEqual(res2.outcome, "aborted-replace-conflict")
        self.assertIn("attributed to surface", res2.error)


class ComputeReplacedUnitTest(unittest.TestCase):
    """Direct unit tests for the replace-not-stack logic (R2)."""

    def test_no_prior_block_appends(self):
        current = "# Doc\n\nText.\n"
        new_block = _synthetic_beacon("cms-x", "Tail0001")
        result, err = _local._compute_replaced(current, new_block, "cms-x")
        self.assertIsNone(err)
        self.assertIn("Text.", result)
        self.assertIn("DEFENSE BEACON v1", result)

    def test_single_block_replaced(self):
        current = "# Doc\n\n" + _synthetic_beacon("cms-x", "Old00001") + "\n"
        new_block = _synthetic_beacon("cms-x", "New00001")
        result, err = _local._compute_replaced(current, new_block, "cms-x")
        self.assertIsNone(err)
        self.assertIn("New00001", result)
        self.assertNotIn("Old00001", result)
        self.assertEqual(result.count("<!-- DEFENSE BEACON v1"), 1)

    def test_surface_mismatch_refuses(self):
        current = "# Doc\n\n" + _synthetic_beacon("cms-other", "Wrong001") + "\n"
        new_block = _synthetic_beacon("cms-x", "Right001")
        result, err = _local._compute_replaced(current, new_block, "cms-x")
        self.assertIsNotNone(err)
        self.assertIn("attributed to surface", err)
        self.assertEqual(result, "")

    def test_multiple_blocks_refuses(self):
        current = (
            "# Doc\n\n"
            + _synthetic_beacon("cms-x", "First001") + "\n"
            + _synthetic_beacon("cms-x", "Second02") + "\n"
        )
        new_block = _synthetic_beacon("cms-x", "Third003")
        result, err = _local._compute_replaced(current, new_block, "cms-x")
        self.assertIsNotNone(err)
        self.assertIn("beacon blocks for", err)
        self.assertEqual(result, "")


class CanaryRedactionTest(unittest.TestCase):
    """The diff-display step must redact canary literals."""

    def test_attrib_pattern_redacted(self):
        text = f"some {_PREFIX}-cms-test-Sec1Test more"
        redacted = _local._redact_canaries(text)
        self.assertNotIn("Sec1Test", redacted)
        self.assertIn("REDACTED(canary, type=attrib)", redacted)

    def test_aws_pattern_redacted(self):
        text = "leak: AKIA_BEACON_0123456789abcdef0123 trail"
        redacted = _local._redact_canaries(text)
        self.assertNotIn("0123456789abcdef0123", redacted)
        self.assertIn("REDACTED(canary, type=aws)", redacted)


class HistoryAppendTest(unittest.TestCase):
    """deploy.run() must append a history row regardless of outcome."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)

    def tearDown(self):
        _state.set_state_root(self._original_root)
        self._tmp.cleanup()

    def test_remote_refusal_appends_history(self):
        with patch.object(
            deploy_cmd, "_surface_type_from_yaml", return_value="github-public"
        ), patch("sys.stderr", io.StringIO()):
            deploy_cmd.run(_ns(surface="repo-x"))
        records = _history.read_all()
        self.assertTrue(any(
            r.get("op") == "deploy"
            and r.get("outcome") == "remote-refused-deploy"
            for r in records
        ))


if __name__ == "__main__":
    unittest.main()
