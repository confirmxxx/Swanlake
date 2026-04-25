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

    def test_status_flag_dispatches_to_run_status(self):
        """`--status` must call reconciler.status.run_status."""
        from unittest.mock import patch
        with patch('reconciler.status.run_status', return_value=0) as mock:
            rc = cli.main(['--status'])
        self.assertEqual(rc, 0)
        mock.assert_called_once()

    def test_sync_flag_dispatches_to_run_sync_all(self):
        from unittest.mock import patch
        with patch('reconciler.sync_vault.run_sync_all', return_value=0) as mock:
            rc = cli.main(['--sync'])
        self.assertEqual(rc, 0)
        mock.assert_called_once()

    def test_init_flag_dispatches_to_run_init(self):
        from unittest.mock import patch
        with patch('reconciler.init.run_init', return_value=0) as mock:
            rc = cli.main(['--init'])
        self.assertEqual(rc, 0)
        mock.assert_called_once()

    def test_mutually_exclusive_flags(self):
        """Cannot specify both --status and --sync."""
        with self.assertRaises(SystemExit):
            cli.main(['--status', '--sync'])


if __name__ == '__main__':
    unittest.main()
