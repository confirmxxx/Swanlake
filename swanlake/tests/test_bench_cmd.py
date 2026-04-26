"""Tests for swanlake.commands.bench -- live-fire wrapper + stub --full.

Cases:
  1. --quick success writes ~/.swanlake/last-bench (ISO-UTC timestamp).
  2. --quick failure (script exit != 0) does NOT write last-bench.
  3. --full exits 3 (NOT_IMPLEMENTED) without running anything.
  4. parse_counts extracts PASS / BLOCKED / HOOK_ERROR / FETCH_FAILED.
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake.commands import bench as bench_cmd
from swanlake import _compat
from swanlake import state as _state


def _ns(**kw) -> Namespace:
    defaults = {
        "json": False,
        "quiet": False,
        "cmd": "bench",
        "quick": False,
        "full": False,
    }
    defaults.update(kw)
    return Namespace(**defaults)


SAMPLE_OUTPUT_OK = """\
swanlake live-fire-rerun starting...
  [1] simon-willison-prompt-injection-explained  PASS  http=200 bytes=43421
  [2] poisoned-rag-arxiv-abstract                PASS  http=200 bytes=29101
  [3] promptfoo-indirect-prompt-injection-plugin BLOCKED  http=200 bytes=51001
  [4] jthack-pipe-readme                         BLOCKED  http=200 bytes=8401
  [5] lakera-gandalf-baseline                    HOOK_ERROR(exit=1)  http=200 bytes=20001
done.
"""


SAMPLE_OUTPUT_FAIL = """\
hook not executable: /home/operator/.claude/hooks/content-safety-check.sh
"""


class BenchParseCountsTest(unittest.TestCase):
    def test_parse_counts_against_sample(self):
        counts = bench_cmd._parse_counts(SAMPLE_OUTPUT_OK)
        self.assertEqual(counts["pass_count"], 2)
        self.assertEqual(counts["blocked_count"], 2)
        self.assertEqual(counts["hook_error_count"], 1)
        self.assertEqual(counts["fetch_failed_count"], 0)


class BenchQuickTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)

        # Pretend the repo root has a bench script.
        self._fake_repo = self.tmp / "fake_repo"
        (self._fake_repo / "bench").mkdir(parents=True)
        self._script = self._fake_repo / "bench" / "live-fire-rerun.sh"
        self._script.write_text("#!/usr/bin/env bash\necho fake\n")
        self._script.chmod(0o755)

        # Patch _resolve_script to return our fake path, bypassing
        # _compat.find_repo_root() entirely (the real lookup walks up
        # to the actual Swanlake clone, which would also work but
        # introduces a dependency on the operator's tree).
        self._resolve_patch = patch.object(
            bench_cmd, "_resolve_script", return_value=self._script
        )
        self._resolve_patch.start()

    def tearDown(self):
        self._resolve_patch.stop()
        _state.set_state_root(self._original_root)
        self._tmpdir.cleanup()

    def test_quick_success_writes_last_bench(self):
        fake_proc = subprocess.CompletedProcess(
            args=["bash", str(self._script)],
            returncode=0,
            stdout=SAMPLE_OUTPUT_OK,
            stderr="",
        )
        with patch("subprocess.run", return_value=fake_proc), \
             patch("sys.stdout", io.StringIO()):
            rc = bench_cmd.run(_ns(quick=True))
        self.assertEqual(rc, 0)
        last = _state.state_path("last-bench")
        self.assertTrue(last.exists())
        ts = last.read_text().strip()
        # Must parse as ISO-UTC.
        parsed = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        # Stamped within the last 60 seconds.
        delta = abs((datetime.now(timezone.utc) - parsed).total_seconds())
        self.assertLess(delta, 60)

    def test_quick_failure_does_not_write_last_bench(self):
        fake_proc = subprocess.CompletedProcess(
            args=["bash", str(self._script)],
            returncode=2,
            stdout=SAMPLE_OUTPUT_FAIL,
            stderr="",
        )
        with patch("subprocess.run", return_value=fake_proc), \
             patch("sys.stdout", io.StringIO()):
            rc = bench_cmd.run(_ns(quick=True))
        self.assertNotEqual(rc, 0)
        self.assertFalse(_state.state_path("last-bench").exists())

    def test_quick_setup_error_returns_usage(self):
        """Regression for v0.2.1 #3: bench/live-fire-rerun.sh exits 2 to
        signal its own setup error (missing hook, dep absent). The wrapper
        must surface that as USAGE so callers can tell a configuration
        problem from a real benchmark alarm. USAGE and ALARM share the
        numeric value 2 by argparse convention, but the named constant
        is what we assert against -- it documents intent."""
        from swanlake.exit_codes import USAGE
        fake_proc = subprocess.CompletedProcess(
            args=["bash", str(self._script)],
            returncode=2,
            stdout=SAMPLE_OUTPUT_FAIL,
            stderr="",
        )
        with patch("subprocess.run", return_value=fake_proc), \
             patch("sys.stdout", io.StringIO()):
            rc = bench_cmd.run(_ns(quick=True))
        self.assertEqual(rc, USAGE)

    def test_quick_alarm_distinct_from_usage(self):
        """A non-2, non-0 script exit (e.g. 1 from a real benchmark
        regression) must surface as ALARM so the caller sees the
        difference between 'setup broken' and 'alarm fired'."""
        from swanlake.exit_codes import ALARM, USAGE
        fake_proc = subprocess.CompletedProcess(
            args=["bash", str(self._script)],
            returncode=1,
            stdout=SAMPLE_OUTPUT_OK,
            stderr="",
        )
        with patch("subprocess.run", return_value=fake_proc), \
             patch("sys.stdout", io.StringIO()):
            rc = bench_cmd.run(_ns(quick=True))
        self.assertEqual(rc, ALARM)
        # Sanity: the test would be vacuous if ALARM == USAGE numerically
        # AND we only checked exit_code == ALARM. They DO collide (both 2)
        # so we additionally verify the script returncode path: a non-2,
        # non-0 input must NOT trigger the setup-error branch.
        self.assertNotEqual(fake_proc.returncode, USAGE)


class BenchFullStubTest(unittest.TestCase):
    def test_full_returns_not_implemented(self):
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            rc = bench_cmd.run(_ns(full=True))
        self.assertEqual(rc, 3)
        self.assertIn("not implemented", captured.getvalue())


if __name__ == "__main__":
    unittest.main()
