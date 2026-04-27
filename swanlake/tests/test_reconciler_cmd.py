"""Tests for ``swanlake reconciler ack`` -- argparse + dispatch + side effects."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reconciler import acks as _acks
from reconciler import status as recon_status
from swanlake import cli as swan_cli
from swanlake import state as _state
from swanlake.exit_codes import CLEAN, USAGE


class _IsolatedRoot(unittest.TestCase):
    """Per-test SWANLAKE_STATE_ROOT so acks land in a tempdir."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self._prior_env = os.environ.get("SWANLAKE_STATE_ROOT")
        os.environ["SWANLAKE_STATE_ROOT"] = str(self.root)
        # Reset module-level state-root cache too.
        _state.set_state_root(self.root)

    def tearDown(self):
        if self._prior_env is None:
            os.environ.pop("SWANLAKE_STATE_ROOT", None)
        else:
            os.environ["SWANLAKE_STATE_ROOT"] = self._prior_env
        self._tmpdir.cleanup()

    def _run(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = swan_cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()


class AckSubcommandTest(_IsolatedRoot):
    def test_ack_known_surface_writes_record(self):
        rc, out, _err = self._run(
            "reconciler", "ack", "notion", "--state-root", str(self.root)
        )
        self.assertEqual(rc, CLEAN)
        self.assertIn("acked notion", out)
        path = _acks.acks_path(self.root)
        self.assertTrue(path.exists())
        records = [json.loads(line) for line in path.read_text().splitlines() if line]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["surface"], "notion")

    def test_ack_with_explicit_since(self):
        ts = "2026-04-26T23:30:00Z"
        rc, _out, _err = self._run(
            "reconciler", "ack", "notion",
            "--since", ts,
            "--state-root", str(self.root),
        )
        self.assertEqual(rc, CLEAN)
        latest = _acks.latest_acks(state_root=self.root)
        self.assertEqual(
            latest["notion"].synced_at,
            datetime(2026, 4, 26, 23, 30, tzinfo=timezone.utc),
        )

    def test_ack_unknown_surface_errors_cleanly(self):
        rc, _out, err = self._run(
            "reconciler", "ack", "notin",
            "--state-root", str(self.root),
        )
        self.assertEqual(rc, USAGE)
        self.assertIn("unknown surface", err)
        # No file written for the bad surface.
        self.assertFalse(_acks.acks_path(self.root).exists())

    def test_ack_invalid_since_errors_cleanly(self):
        rc, _out, err = self._run(
            "reconciler", "ack", "notion",
            "--since", "yesterday",
            "--state-root", str(self.root),
        )
        self.assertEqual(rc, USAGE)
        self.assertIn("invalid --since", err)

    def test_ack_all_remote_acks_every_remote_surface(self):
        # Override the surface map so we have two remote surfaces.
        cfg = self.root / "config.toml"
        cfg.write_text("[surfaces]\nnotion = \"remote\"\nsupabase = \"cloud\"\n")
        rc, out, _err = self._run(
            "reconciler", "ack", "--all-remote",
            "--state-root", str(self.root),
        )
        self.assertEqual(rc, CLEAN)
        self.assertIn("notion", out)
        self.assertIn("supabase", out)
        latest = _acks.latest_acks(state_root=self.root)
        self.assertIn("notion", latest)
        self.assertIn("supabase", latest)

    def test_ack_all_remote_with_only_default_classification(self):
        # Default config-less classification: notion is the only remote.
        rc, _out, _err = self._run(
            "reconciler", "ack", "--all-remote",
            "--state-root", str(self.root),
        )
        self.assertEqual(rc, CLEAN)
        latest = _acks.latest_acks(state_root=self.root)
        self.assertEqual(set(latest), {"notion"})

    def test_ack_surface_and_all_remote_mutually_exclusive(self):
        rc, _out, err = self._run(
            "reconciler", "ack", "notion", "--all-remote",
            "--state-root", str(self.root),
        )
        self.assertEqual(rc, USAGE)
        self.assertIn("not both", err)

    def test_ack_neither_surface_nor_all_remote_errors(self):
        rc, _out, err = self._run(
            "reconciler", "ack",
            "--state-root", str(self.root),
        )
        self.assertEqual(rc, USAGE)
        self.assertIn("required", err)

    def test_ack_records_optional_note(self):
        rc, _out, _err = self._run(
            "reconciler", "ack", "notion",
            "--note", "manual fire after watchdog routine",
            "--state-root", str(self.root),
        )
        self.assertEqual(rc, CLEAN)
        latest = _acks.latest_acks(state_root=self.root)
        self.assertEqual(latest["notion"].note, "manual fire after watchdog routine")

    def test_ack_json_emits_machine_payload(self):
        rc, out, _err = self._run(
            "reconciler", "ack", "notion", "--json",
            "--state-root", str(self.root),
        )
        self.assertEqual(rc, CLEAN)
        payload = json.loads(out)
        self.assertEqual(len(payload["acked"]), 1)
        self.assertEqual(payload["acked"][0]["surface"], "notion")


class StatusFoldsAckEndToEndTest(_IsolatedRoot):
    """After ack, compute_report shows the surface as fresh/ack."""

    def test_ack_then_status_shows_fresh_via_ack(self):
        # Run the ack via the CLI surface (end-to-end path).
        rc, _out, _err = self._run(
            "reconciler", "ack", "notion",
            "--state-root", str(self.root),
        )
        self.assertEqual(rc, CLEAN)

        # Empty last-sync.json -> would normally be missing for notion.
        state_path = self.root / "last-sync.json"
        report = recon_status.compute_report(
            state_path=state_path,
            acks_state_root=self.root,
        )
        self.assertEqual(report["surfaces"]["notion"]["status"], "fresh")
        self.assertEqual(report["surfaces"]["notion"]["synced_via"], "ack")


if __name__ == "__main__":
    unittest.main()
