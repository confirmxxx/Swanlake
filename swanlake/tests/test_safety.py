"""Tests for swanlake.safety.confirm()."""
from __future__ import annotations

import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake import safety


class ConfirmFlagBypassTest(unittest.TestCase):
    def test_yes_flag_skips_prompt(self):
        with patch("builtins.input") as mock_input:
            result = safety.confirm("delete all things?", yes=True)
        self.assertTrue(result)
        mock_input.assert_not_called()

    def test_yes_flag_prints_auto_confirmed(self):
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            safety.confirm("rotate canaries?", yes=True)
        self.assertIn("[auto-confirmed]", captured.getvalue())
        self.assertIn("rotate canaries?", captured.getvalue())


class ConfirmEnvBypassTest(unittest.TestCase):
    def test_noninteractive_env_skips_prompt(self):
        with patch.dict(os.environ, {"SWANLAKE_NONINTERACTIVE": "1"}):
            with patch("builtins.input") as mock_input:
                result = safety.confirm("sync now?")
        self.assertTrue(result)
        mock_input.assert_not_called()

    def test_noninteractive_env_unset_does_not_bypass(self):
        env = {k: v for k, v in os.environ.items()
               if k != "SWANLAKE_NONINTERACTIVE"}
        with patch.dict(os.environ, env, clear=True):
            # Force non-TTY so we exercise the no-bypass + no-TTY -> False path
            # without actually reading from stdin.
            with patch.object(sys.stdin, "isatty", return_value=False):
                result = safety.confirm("sync now?")
        self.assertFalse(result)


class ConfirmInteractiveTest(unittest.TestCase):
    def test_no_answer_returns_false(self):
        env = {k: v for k, v in os.environ.items()
               if k != "SWANLAKE_NONINTERACTIVE"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(sys.stdin, "isatty", return_value=True):
                with patch("builtins.input", return_value="n"):
                    result = safety.confirm("sync now?")
        self.assertFalse(result)

    def test_yes_answer_returns_true(self):
        env = {k: v for k, v in os.environ.items()
               if k != "SWANLAKE_NONINTERACTIVE"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(sys.stdin, "isatty", return_value=True):
                with patch("builtins.input", return_value="yes"):
                    result = safety.confirm("sync now?")
        self.assertTrue(result)

    def test_eof_at_prompt_returns_false(self):
        env = {k: v for k, v in os.environ.items()
               if k != "SWANLAKE_NONINTERACTIVE"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(sys.stdin, "isatty", return_value=True):
                with patch("builtins.input", side_effect=EOFError):
                    result = safety.confirm("sync now?")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
