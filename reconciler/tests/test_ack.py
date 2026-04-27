"""Tests for reconciler.acks — operator acks for remote-only sync surfaces."""
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure the reconciler package is importable from a clean checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from reconciler import acks, status


class _IsolatedStateRoot(unittest.TestCase):
    """Base class: gives each test a fresh tempdir as the state root.

    Routes the env var so the bare ``acks_path()`` call uses the tempdir
    too (covers the contract that ``SWANLAKE_STATE_ROOT`` flows through
    every public read/write helper, not just the ones that take an
    explicit ``state_root=`` argument).
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self._prior_env = os.environ.get("SWANLAKE_STATE_ROOT")
        os.environ["SWANLAKE_STATE_ROOT"] = str(self.root)

    def tearDown(self):
        if self._prior_env is None:
            os.environ.pop("SWANLAKE_STATE_ROOT", None)
        else:
            os.environ["SWANLAKE_STATE_ROOT"] = self._prior_env
        self._tmpdir.cleanup()


class WriteAckTest(_IsolatedStateRoot):
    """write_ack persists a record and round-trips through latest_acks."""

    def test_write_ack_records_synced_at_default_now(self):
        ack = acks.write_ack("notion", state_root=self.root)
        self.assertEqual(ack.surface, "notion")
        # Default synced_at is "now" (within a tiny tolerance).
        self.assertLessEqual(
            (datetime.now(timezone.utc) - ack.synced_at).total_seconds(),
            5,
        )

    def test_write_ack_persists_to_jsonl(self):
        ts = datetime(2026, 4, 26, 23, 30, tzinfo=timezone.utc)
        acks.write_ack("notion", synced_at=ts, state_root=self.root)
        path = acks.acks_path(self.root)
        self.assertTrue(path.exists())
        records = [json.loads(line) for line in path.read_text().splitlines() if line]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["surface"], "notion")
        self.assertEqual(records[0]["synced_at"], ts.isoformat())

    def test_write_ack_explicit_synced_at_naive_assumes_utc(self):
        naive = datetime(2026, 4, 26, 23, 30)
        ack = acks.write_ack("notion", synced_at=naive, state_root=self.root)
        self.assertEqual(ack.synced_at.tzinfo, timezone.utc)

    def test_write_ack_unknown_surface_raises(self):
        with self.assertRaises(acks.UnknownSurface):
            acks.write_ack("notin", state_root=self.root)

    def test_write_ack_appends_not_overwrites(self):
        acks.write_ack("notion", state_root=self.root)
        acks.write_ack("notion", state_root=self.root)
        path = acks.acks_path(self.root)
        lines = [line for line in path.read_text().splitlines() if line]
        self.assertEqual(len(lines), 2)


class LatestAcksTest(_IsolatedStateRoot):
    """latest_acks returns the freshest ack per surface."""

    def test_empty_when_file_missing(self):
        self.assertEqual(acks.latest_acks(state_root=self.root), {})

    def test_returns_only_freshest_per_surface(self):
        older = datetime(2026, 4, 25, tzinfo=timezone.utc)
        newer = datetime(2026, 4, 26, tzinfo=timezone.utc)
        acks.write_ack("notion", synced_at=older, state_root=self.root)
        acks.write_ack("notion", synced_at=newer, state_root=self.root)
        latest = acks.latest_acks(state_root=self.root)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest["notion"].synced_at, newer)

    def test_skips_corrupt_lines(self):
        # Force one good ack, then corrupt the file with a torn write.
        acks.write_ack("notion", state_root=self.root)
        path = acks.acks_path(self.root)
        with open(path, "a") as f:
            f.write("{not valid json\n")
        # Reader still sees the good ack.
        latest = acks.latest_acks(state_root=self.root)
        self.assertIn("notion", latest)


class TimestampParseTest(unittest.TestCase):
    """parse_timestamp accepts the common ISO + Z forms."""

    def test_z_suffix(self):
        dt = acks.parse_timestamp("2026-04-26T23:30:00Z")
        self.assertEqual(dt, datetime(2026, 4, 26, 23, 30, tzinfo=timezone.utc))

    def test_explicit_offset(self):
        dt = acks.parse_timestamp("2026-04-26T23:30:00+00:00")
        self.assertEqual(dt, datetime(2026, 4, 26, 23, 30, tzinfo=timezone.utc))

    def test_naive_assumes_utc(self):
        dt = acks.parse_timestamp("2026-04-26T23:30:00")
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            acks.parse_timestamp("")

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            acks.parse_timestamp("not-a-timestamp")


class SurfaceClassificationTest(_IsolatedStateRoot):
    """Surface classification reads config + falls back to defaults."""

    def test_default_classes_when_no_config(self):
        classes = acks.load_surface_classes(state_root=self.root)
        self.assertEqual(classes["notion"], "remote")
        self.assertEqual(classes["vault"], "local")
        self.assertEqual(classes["claude_md"], "local")

    def test_remote_surfaces_default_set(self):
        self.assertEqual(acks.remote_surfaces(state_root=self.root), ("notion",))

    def test_config_overrides_default(self):
        cfg = self.root / "config.toml"
        cfg.write_text(
            "deployment_map_path = \"/x\"\n"
            "vault_root = \"/y\"\n"
            "notion_master_page_id = \"abc\"\n"
            "notion_posture_page_id = \"def\"\n"
            "swanlake_repo_path = \"/z\"\n"
            "canon_dir = \"/z/canon\"\n"
            "\n"
            "[surfaces]\n"
            "notion = \"remote\"\n"
            "supabase = \"cloud\"\n"  # alias should resolve to remote
            "vault = \"local\"\n"
        )
        classes = acks.load_surface_classes(state_root=self.root)
        self.assertEqual(classes["supabase"], "remote")
        # ``cloud`` is an accepted alias for ``remote``.
        self.assertIn("supabase", acks.remote_surfaces(state_root=self.root))

    def test_invalid_class_value_falls_back_to_default(self):
        cfg = self.root / "config.toml"
        cfg.write_text("[surfaces]\nnotion = \"banana\"\n")
        classes = acks.load_surface_classes(state_root=self.root)
        # ``banana`` is invalid; default for notion is still ``remote``.
        self.assertEqual(classes["notion"], "remote")


class StatusFoldsAcksTest(unittest.TestCase):
    """compute_report folds the most recent ack into freshness."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.state = self.root / "last-sync.json"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_ack_makes_otherwise_missing_surface_fresh(self):
        # No local sync record at all; ack-only.
        acks.write_ack("notion", state_root=self.root)
        report = status.compute_report(
            state_path=self.state,
            acks_state_root=self.root,
        )
        self.assertEqual(report["surfaces"]["notion"]["status"], "fresh")
        self.assertEqual(report["surfaces"]["notion"]["synced_via"], "ack")
        self.assertIsNotNone(report["surfaces"]["notion"]["last_ack_utc"])

    def test_old_ack_decays_to_drift_red(self):
        # Ack from 10 days ago should NOT permanently mute the alarm.
        old = datetime.now(timezone.utc) - timedelta(days=10)
        acks.write_ack("notion", synced_at=old, state_root=self.root)
        report = status.compute_report(
            state_path=self.state,
            acks_state_root=self.root,
        )
        self.assertEqual(report["surfaces"]["notion"]["status"], "drift-red")

    def test_fresher_sync_wins_over_older_ack(self):
        old_ack = datetime.now(timezone.utc) - timedelta(days=5)
        recent_sync = datetime.now(timezone.utc) - timedelta(hours=1)
        acks.write_ack("notion", synced_at=old_ack, state_root=self.root)
        self.state.write_text(json.dumps({"notion": recent_sync.isoformat()}))
        report = status.compute_report(
            state_path=self.state,
            acks_state_root=self.root,
        )
        self.assertEqual(report["surfaces"]["notion"]["synced_via"], "sync")
        self.assertEqual(report["surfaces"]["notion"]["status"], "fresh")

    def test_fresher_ack_wins_over_older_sync(self):
        old_sync = datetime.now(timezone.utc) - timedelta(days=3)
        recent_ack = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.state.write_text(json.dumps({"notion": old_sync.isoformat()}))
        acks.write_ack("notion", synced_at=recent_ack, state_root=self.root)
        report = status.compute_report(
            state_path=self.state,
            acks_state_root=self.root,
        )
        self.assertEqual(report["surfaces"]["notion"]["synced_via"], "ack")
        self.assertEqual(report["surfaces"]["notion"]["status"], "fresh")

    def test_no_ack_path_unchanged_for_local_surfaces(self):
        # Vault has no ack; existing local-sync flow must keep working.
        now = datetime.now(timezone.utc)
        self.state.write_text(json.dumps({"vault": now.isoformat()}))
        report = status.compute_report(
            state_path=self.state,
            acks_state_root=self.root,
        )
        self.assertEqual(report["surfaces"]["vault"]["status"], "fresh")
        self.assertEqual(report["surfaces"]["vault"]["synced_via"], "sync")


class ConcurrentWriteTest(_IsolatedStateRoot):
    """Two parallel ack writes both land — fcntl lock holds."""

    def test_concurrent_writes_no_torn_lines(self):
        def write():
            acks.write_ack("notion", state_root=self.root)

        threads = [threading.Thread(target=write) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        path = acks.acks_path(self.root)
        lines = [line for line in path.read_text().splitlines() if line]
        self.assertEqual(len(lines), 8)
        # Every line must parse — no torn writes.
        for line in lines:
            json.loads(line)


if __name__ == "__main__":
    unittest.main()
