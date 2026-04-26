"""Tests for the `swanlake beacon` argparse wiring.

The full subcommand bodies are exercised in test_beacon_*.py modules
that mirror each subcommand. This file tests only:
  - argparse builds the beacon subtree
  - dispatch routes to each subcommand handler
  - missing subcommand returns USAGE
"""
from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake import cli
from swanlake.exit_codes import NOT_IMPLEMENTED, USAGE


class BeaconArgparseTest(unittest.TestCase):
    def test_help_lists_beacon(self):
        parser = cli.build_parser()
        # The metavar listing in the top-level help should include
        # 'beacon' as one of the subcommand choices.
        buf = io.StringIO()
        parser.print_help(buf)
        self.assertIn("beacon", buf.getvalue())

    def test_beacon_help_lists_five_ops(self):
        parser = cli.build_parser()
        # parse with --help would call sys.exit; instead build the parser
        # and inspect the subparsers action directly.
        for op in ("list", "sweep", "deploy", "checklist", "verify"):
            args = parser.parse_args(["beacon", op] + (
                ["dummy-surface"] if op == "deploy" else []
            ))
            self.assertEqual(args.cmd, "beacon")
            self.assertEqual(args.beacon_op, op)

    def test_no_subcommand_returns_usage(self):
        # `swanlake beacon` with no subcommand should print to stderr and
        # exit USAGE. The dispatcher routes the no-op case via the beacon
        # __init__'s run().
        with patch("sys.stderr", io.StringIO()) as captured_err:
            rc = cli.main(["beacon"])
        self.assertEqual(rc, USAGE)
        self.assertIn("missing subcommand", captured_err.getvalue())


class BeaconStubDispatchTest(unittest.TestCase):
    """Stubs not yet replaced return NOT_IMPLEMENTED; verify dispatch reaches them.

    `list`, `sweep`, and `verify` are implemented; their own test modules
    cover behavior. `deploy` and `checklist` are still stubs at this point
    in the build sequence and will be replaced commit-by-commit.
    """

    def test_deploy_stub_returns_not_implemented(self):
        with patch("sys.stderr", io.StringIO()):
            rc = cli.main(["beacon", "deploy", "cms-test"])
        self.assertEqual(rc, NOT_IMPLEMENTED)

    def test_checklist_stub_returns_not_implemented(self):
        with patch("sys.stderr", io.StringIO()):
            rc = cli.main(["beacon", "checklist"])
        self.assertEqual(rc, NOT_IMPLEMENTED)


class BeaconRegistryTest(unittest.TestCase):
    """Surface-type registry sanity."""

    def test_seven_surface_types(self):
        from swanlake.commands.beacon import _registry as reg
        self.assertEqual(len(reg.SURFACE_TYPES), 7)

    def test_local_remote_split(self):
        from swanlake.commands.beacon import _registry as reg
        local = [t for t in reg.SURFACE_TYPES if t.is_local]
        remote = [t for t in reg.SURFACE_TYPES if t.is_remote]
        # Spec: rows 1-2 are LOCAL (claude-md, vault); rows 3-7 are REMOTE.
        self.assertEqual(len(local), 2)
        self.assertEqual(len(remote), 5)

    def test_infer_type_by_prefix(self):
        from swanlake.commands.beacon import _registry as reg
        self.assertEqual(reg.infer_type("cms-project-alpha"), "claude-md")
        self.assertEqual(reg.infer_type("vault-root"), "vault")
        self.assertEqual(reg.infer_type("repo-foo"), "github-public")
        self.assertEqual(reg.infer_type("routine-x"), "claude-routine")
        # Unknown prefix -> safest fallback.
        self.assertEqual(reg.infer_type("unknown-xyz"), "claude-md")

    def test_explicit_type_overrides_prefix(self):
        from swanlake.commands.beacon import _registry as reg
        # An operator-supplied explicit type wins.
        self.assertEqual(
            reg.infer_type("repo-foo", explicit_type="vercel-env"),
            "vercel-env",
        )
        # An unknown explicit type falls through to the prefix-based default.
        self.assertEqual(
            reg.infer_type("repo-foo", explicit_type="bogus"),
            "github-public",
        )

    def test_validate_surface_id(self):
        from swanlake.commands.beacon import _registry as reg
        self.assertTrue(reg.validate_surface_id("cms-project-alpha"))
        self.assertTrue(reg.validate_surface_id("a1"))
        self.assertFalse(reg.validate_surface_id("Bad-Caps"))
        self.assertFalse(reg.validate_surface_id("-leading-hyphen"))
        self.assertFalse(reg.validate_surface_id("trailing-hyphen-"))
        self.assertFalse(reg.validate_surface_id(""))
        self.assertFalse(reg.validate_surface_id(".."))


if __name__ == "__main__":
    unittest.main()
