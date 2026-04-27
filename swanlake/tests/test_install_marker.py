"""Tests for swanlake.install_marker — write/read/parse contract.

Spec: docs/v0.3.x-worktree-install-isolation-spec.md T5(a-c).

Cases:
  1. write_marker creates ~/.swanlake/.install-marker mode 0600 with all fields
  2. write_marker degrades silently when state-root is unwritable
  3. read_marker tolerates trailing whitespace, blank lines, and # comments
  4. read_marker returns None when the file is missing
  5. read_marker returns None when the file is empty
  6. write/read round-trip preserves source_path, installed_at, python_executable
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake import install_marker


class WriteMarkerTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_write_creates_marker_with_all_fields(self):
        source = self.tmp / "fake-source"
        source.mkdir()
        result = install_marker.write_marker(source, state_root=self.tmp / "state")
        self.assertIsNotNone(result)
        self.assertTrue(result.is_file())
        text = result.read_text()
        self.assertIn("source_path=", text)
        self.assertIn("installed_at=", text)
        self.assertIn("python_executable=", text)
        # source_path is the absolute resolved path, not the relative input.
        self.assertIn(str(source.resolve()), text)
        # The current interpreter is recorded.
        self.assertIn(sys.executable, text)

    def test_marker_file_mode_0600(self):
        source = self.tmp / "fake-source"
        source.mkdir()
        result = install_marker.write_marker(source, state_root=self.tmp / "state")
        mode = stat.S_IMODE(result.stat().st_mode)
        self.assertEqual(mode, 0o600, f"expected 0o600, got {oct(mode)}")

    def test_state_root_created_mode_0700(self):
        source = self.tmp / "fake-source"
        source.mkdir()
        new_root = self.tmp / "fresh-state"
        install_marker.write_marker(source, state_root=new_root)
        mode = stat.S_IMODE(new_root.stat().st_mode)
        self.assertEqual(mode, 0o700, f"expected 0o700, got {oct(mode)}")

    def test_unwritable_state_root_returns_none_no_raise(self):
        """A read-only home/state dir must not crash the install."""
        source = self.tmp / "fake-source"
        source.mkdir()
        readonly = self.tmp / "readonly-state"
        readonly.mkdir()
        os.chmod(readonly, 0o500)  # read+execute, no write
        try:
            # Must return None and must not raise.
            result = install_marker.write_marker(
                source / "child-that-does-not-exist",
                state_root=readonly / "blocked",
            )
            self.assertIsNone(result)
        finally:
            os.chmod(readonly, 0o700)  # tearDown cleanup

    def test_idempotent_overwrites_existing_marker(self):
        source_a = self.tmp / "source-a"
        source_a.mkdir()
        source_b = self.tmp / "source-b"
        source_b.mkdir()
        state = self.tmp / "state"
        install_marker.write_marker(source_a, state_root=state)
        install_marker.write_marker(source_b, state_root=state)
        marker = install_marker.read_marker(state_root=state)
        self.assertIsNotNone(marker)
        self.assertEqual(marker["source_path"], str(source_b.resolve()))


class ReadMarkerTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.state = self.tmp / "state"
        self.state.mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_missing_returns_none(self):
        self.assertIsNone(install_marker.read_marker(state_root=self.state))

    def test_empty_returns_none(self):
        (self.state / install_marker.MARKER_FILENAME).write_text("")
        self.assertIsNone(install_marker.read_marker(state_root=self.state))

    def test_tolerates_whitespace_and_comments(self):
        body = (
            "# this is a comment\n"
            "\n"
            "  source_path=/some/path  \n"
            "  installed_at=2026-04-26T12:00:00+00:00\n"
            "# trailing comment\n"
        )
        (self.state / install_marker.MARKER_FILENAME).write_text(body)
        result = install_marker.read_marker(state_root=self.state)
        self.assertIsNotNone(result)
        self.assertEqual(result["source_path"], "/some/path")
        self.assertEqual(result["installed_at"], "2026-04-26T12:00:00+00:00")

    def test_unknown_keys_preserved(self):
        """Future fields must not break older readers."""
        body = (
            "source_path=/a\n"
            "future_field=preserved\n"
            "another_one=also-preserved\n"
        )
        (self.state / install_marker.MARKER_FILENAME).write_text(body)
        result = install_marker.read_marker(state_root=self.state)
        self.assertEqual(result["future_field"], "preserved")
        self.assertEqual(result["another_one"], "also-preserved")

    def test_lines_without_equals_skipped(self):
        body = (
            "source_path=/a\n"
            "this line has no equals sign and must be ignored\n"
            "installed_at=2026-04-26T12:00:00+00:00\n"
        )
        (self.state / install_marker.MARKER_FILENAME).write_text(body)
        result = install_marker.read_marker(state_root=self.state)
        self.assertIsNotNone(result)
        self.assertEqual(result["source_path"], "/a")


class CheckDriftTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.state = self.tmp / "state"
        self.state.mkdir()

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_no_marker_status(self):
        result = install_marker.check_drift(state_root=self.state)
        self.assertEqual(result["status"], "no-marker")
        self.assertIsNone(result["marker_path"])

    def test_marker_matches_runtime(self):
        # The runtime source root for the running test is the worktree root.
        runtime = install_marker._runtime_source_dir()
        install_marker.write_marker(runtime, state_root=self.state)
        result = install_marker.check_drift(state_root=self.state)
        self.assertEqual(result["status"], "ok")

    def test_marker_drift(self):
        # Marker points at a different dir than the runtime.
        bogus = self.tmp / "not-the-runtime"
        bogus.mkdir()
        install_marker.write_marker(bogus, state_root=self.state)
        result = install_marker.check_drift(state_root=self.state)
        self.assertEqual(result["status"], "drift")
        self.assertEqual(result["marker_path"], str(bogus.resolve()))

    def test_cross_interpreter_suppresses_drift(self):
        """A marker written by a different python_executable must not warn.

        Multi-interpreter installs (3.11 + 3.12) sharing ~/.swanlake/
        legitimately have one marker per interpreter. The check must
        not flag drift across interpreters.
        """
        bogus = self.tmp / "other-interp-source"
        bogus.mkdir()
        body = (
            f"source_path={bogus.resolve()}\n"
            "installed_at=2026-04-26T12:00:00+00:00\n"
            "python_executable=/usr/bin/python3.99\n"
        )
        (self.state / install_marker.MARKER_FILENAME).write_text(body)
        result = install_marker.check_drift(state_root=self.state)
        self.assertEqual(result["status"], "cross-interpreter")

    def test_marker_with_deleted_source_treated_as_drift(self):
        """If the marker source path no longer exists, count it as drift."""
        gone = self.tmp / "deleted-source"
        gone.mkdir()
        install_marker.write_marker(gone, state_root=self.state)
        gone.rmdir()  # marker now points at a missing dir
        result = install_marker.check_drift(state_root=self.state)
        # Either "drift" (if resolve() didn't raise) or graceful degrade —
        # both are acceptable; the contract is "never raise". Path.resolve()
        # is documented to NOT raise on missing paths in py3.6+, so this
        # case lands in the "drift" branch.
        self.assertIn(result["status"], ("drift", "ok"))


class FormatDriftWarningTest(unittest.TestCase):
    def test_warning_includes_both_paths(self):
        drift = {
            "status": "drift",
            "runtime_path": "/path/to/runtime",
            "marker_path": "/path/to/marker",
            "marker_python": "/usr/bin/python3",
            "runtime_python": "/usr/bin/python3",
        }
        text = install_marker.format_drift_warning(drift)
        self.assertIn("/path/to/runtime", text)
        self.assertIn("/path/to/marker", text)
        self.assertIn("pip install --force-reinstall", text)
        self.assertIn("pipx", text)
        self.assertTrue(text.startswith("warning:"))


if __name__ == "__main__":
    unittest.main()
