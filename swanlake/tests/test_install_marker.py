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

    def test_marker_with_pip_req_build_prefix_self_heals(self):
        """v0.4.1 PRIMARY: tarball install via
        `pip install https://.../v0.4.0.tar.gz` extracts to
        `/tmp/pip-req-build-<random>/`. The cmdclass install hook
        captures THAT path. After install completes, pip wipes the
        dir, but the marker stays. Every subsequent CLI invocation
        used to fire a false-positive drift warning. The fix: detect
        the transient prefix, treat as first-run, rewrite the marker
        to point at the actual runtime source.
        """
        # The path string is the load-bearing signal — it doesn't
        # need to exist for the prefix check to fire. Pre-populate
        # the marker exactly as the cmdclass hook would have written
        # it during a tarball install.
        marker = self.state / install_marker.MARKER_FILENAME
        body = (
            "source_path=/tmp/pip-req-build-XYZ123\n"
            "installed_at=2026-04-26T12:00:00+00:00\n"
            f"python_executable={sys.executable}\n"
        )
        marker.write_text(body)

        result = install_marker.check_drift(state_root=self.state)
        # No drift fires.
        self.assertEqual(result["status"], "no-marker")

        # The marker was rewritten to the runtime source root.
        rewritten = install_marker.read_marker(state_root=self.state)
        self.assertIsNotNone(rewritten)
        runtime = install_marker._runtime_source_dir().resolve()
        self.assertEqual(rewritten["source_path"], str(runtime))

    def test_marker_with_pip_build_prefix_self_heals(self):
        """Variant of the above for the older `/tmp/pip-build-<random>/`
        prefix used by some pip versions / build paths."""
        marker = self.state / install_marker.MARKER_FILENAME
        marker.write_text(
            "source_path=/tmp/pip-build-abc456/swanlake\n"
            "installed_at=2026-04-26T12:00:00+00:00\n"
            f"python_executable={sys.executable}\n"
        )
        result = install_marker.check_drift(state_root=self.state)
        self.assertEqual(result["status"], "no-marker")

    def test_marker_with_pip_install_prefix_self_heals(self):
        """And the third documented prefix used by
        `pip._internal.utils.temp_dir.TempDirectory(kind='install')`."""
        marker = self.state / install_marker.MARKER_FILENAME
        marker.write_text(
            "source_path=/tmp/pip-install-deadbeef/pkg\n"
            "installed_at=2026-04-26T12:00:00+00:00\n"
            f"python_executable={sys.executable}\n"
        )
        result = install_marker.check_drift(state_root=self.state)
        self.assertEqual(result["status"], "no-marker")

    def test_is_transient_build_path_pure_function(self):
        """The helper is a pure string + filesystem check; verify
        each branch in isolation so future changes have a regression
        net for the prefix list and the existence-fallback."""
        self.assertTrue(install_marker._is_transient_build_path(
            "/tmp/pip-req-build-XXXX"
        ))
        self.assertTrue(install_marker._is_transient_build_path(
            "/tmp/pip-build-YYYY"
        ))
        self.assertTrue(install_marker._is_transient_build_path(
            "/tmp/pip-install-ZZZZ"
        ))
        # Existence fallback.
        self.assertTrue(install_marker._is_transient_build_path(
            "/this/path/does/not/exist/anywhere/12345"
        ))
        # Real, non-transient path (the test temp dir) is NOT transient.
        self.assertFalse(install_marker._is_transient_build_path(str(self.tmp)))
        # Empty string is not transient (callers may pass empty).
        self.assertFalse(install_marker._is_transient_build_path(""))

    def test_marker_with_deleted_source_treated_as_first_run(self):
        """A marker pointing at a deleted dir is the load-bearing
        symptom of the tarball-install false-positive (v0.4.1):
        pip's `/tmp/pip-req-build-<random>/` is gone the moment the
        install completes, but the marker still points there. The
        check must self-heal — treat the stale marker as "first run,
        no marker yet established" AND rewrite it to the runtime
        source so the next invocation takes the `ok` path.
        """
        gone = self.tmp / "deleted-source"
        gone.mkdir()
        install_marker.write_marker(gone, state_root=self.state)
        gone.rmdir()  # marker now points at a missing dir
        result = install_marker.check_drift(state_root=self.state)
        # The vanished-source path is one of the two transient-build
        # signals; the contract is "no warning, self-heal in place".
        self.assertEqual(result["status"], "no-marker")
        # The marker was rewritten to point at the runtime source.
        rewritten = install_marker.read_marker(state_root=self.state)
        self.assertIsNotNone(rewritten)
        runtime = install_marker._runtime_source_dir().resolve()
        self.assertEqual(rewritten["source_path"], str(runtime))


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
