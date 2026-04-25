"""Tests for reconciler CLI entry point."""
import io
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

# Ensure reconciler package importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reconciler import cli


class CLITest(unittest.TestCase):
    def test_help_returns_zero(self):
        """`swanlake-reconciler --help` must exit 0 with usage on stdout."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as cm:
                cli.main(['--help'])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn('swanlake-reconciler', buf.getvalue())

    def test_no_args_shows_usage_and_exits_nonzero(self):
        """Bare invocation should show usage and exit non-zero."""
        buf = io.StringIO()
        with redirect_stderr(buf):
            with self.assertRaises(SystemExit) as cm:
                cli.main([])
        self.assertNotEqual(cm.exception.code, 0)


if __name__ == '__main__':
    unittest.main()
