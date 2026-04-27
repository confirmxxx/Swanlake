"""Tests for swanlake.__version__ -- pin the package self-report.

A previous release (v0.2.1) shipped without bumping `__version__` from
its v0.2.0 value, so the installed CLI reported the wrong version
through both `swanlake --version` and the audit log's
`swanlake_version` field. This test guards against the same drift in
future releases by pinning the constant explicitly. When cutting a new
release, bump the constant here in the same commit as the bump in
swanlake/__init__.py and pyproject.toml.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Ensure the package under test is importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import swanlake


EXPECTED_VERSION = "0.4.2"


class VersionPinTest(unittest.TestCase):
    def test_dunder_version_matches_release_tag(self) -> None:
        self.assertEqual(swanlake.__version__, EXPECTED_VERSION)


if __name__ == "__main__":
    unittest.main()
