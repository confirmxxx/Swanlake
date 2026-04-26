"""Tests for swanlake.commands.init -- bootstrap + idempotency.

Cases:
  1. fresh-bootstrap creates audit.jsonl + coverage.json under the
     state root; reconciler.init.run_init() called exactly once.
  2. idempotent re-run prints "already initialised" and creates nothing.
  3. preserves existing canary-hits/ byte-identical (R3).
  4. --add-surface adds one row to coverage.json without bootstrap.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from swanlake.commands import init as init_cmd
from swanlake import state as _state


def _ns(**kw) -> Namespace:
    defaults = {
        "json": False,
        "quiet": False,
        "cmd": "init",
        "add_surface": None,
    }
    defaults.update(kw)
    return Namespace(**defaults)


class InitFreshBootstrapTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self._original_root = _state.get_state_root()
        _state.set_state_root(self.tmp)
        # Point the legacy config target at a tmp location so we never
        # touch the operator's real ~/.config/swanlake-reconciler/config.toml.
        self._legacy_dir = self.tmp / "legacy-config"
        self._legacy_path = self._legacy_dir / "config.toml"
        self._legacy_patch = patch.object(
            init_cmd, "LEGACY_CONFIG", self._legacy_path
        )
        self._legacy_patch.start()
        # Also patch the reconciler init's config target so it doesn't
        # write to the operator's real ~/.config dir if invoked.
        from reconciler import init as recon_init
        self._recon_legacy = patch.object(
            recon_init, "CONFIG_DIR", self._legacy_dir
        )
        self._recon_legacy.start()

    def tearDown(self):
        self._recon_legacy.stop()
        self._legacy_patch.stop()
        _state.set_state_root(self._original_root)
        self._tmpdir.cleanup()

    def test_fresh_bootstrap_creates_files(self):
        called = []

        def fake_run_init(skip_systemd=False):
            called.append(skip_systemd)
            # Mimic real reconciler behaviour: write the legacy config
            # so the relocate step has something to copy.
            self._legacy_dir.mkdir(parents=True, exist_ok=True)
            self._legacy_path.write_text(
                'deployment_map_path = "/tmp/dmap.json"\n'
                'vault_root = "/tmp/vault"\n'
                'notion_master_page_id = "x"\n'
                'notion_posture_page_id = "y"\n'
                'swanlake_repo_path = "/tmp/swanlake"\n'
                'canon_dir = "/tmp/swanlake/canon"\n'
            )
            return 0

        with patch("reconciler.init.run_init", side_effect=fake_run_init), \
             patch("sys.stdout", io.StringIO()):
            rc = init_cmd.run(_ns())

        self.assertEqual(rc, 0)
        # reconciler.init.run_init must have been called exactly once
        # with skip_systemd=True (we don't redeploy units on swanlake init).
        self.assertEqual(called, [True])
        # Bootstrap created all three files in the unified state root.
        self.assertTrue(_state.state_path("audit.jsonl").exists())
        self.assertTrue(_state.state_path("coverage.json").exists())
        self.assertTrue(_state.state_path("config.toml").exists())
        # coverage.json schema check.
        cov = json.loads(_state.state_path("coverage.json").read_text())
        self.assertEqual(cov.get("schema"), 1)
        self.assertEqual(cov.get("surfaces"), {})

    def test_idempotent_rerun_creates_nothing(self):
        # Pre-populate everything as if a prior init had run.
        cfg = _state.state_path("config.toml")
        cfg.write_text("# already there\n")
        audit = _state.state_path("audit.jsonl")
        audit.write_text("")
        cov = _state.state_path("coverage.json")
        cov.write_text(json.dumps(init_cmd.EMPTY_COVERAGE) + "\n")

        cfg_mtime = cfg.stat().st_mtime
        audit_mtime = audit.stat().st_mtime
        cov_mtime = cov.stat().st_mtime

        called = []

        def fake_run_init(skip_systemd=False):
            called.append(skip_systemd)
            return 0

        captured = io.StringIO()
        with patch("reconciler.init.run_init", side_effect=fake_run_init), \
             patch("sys.stdout", captured):
            rc = init_cmd.run(_ns())

        self.assertEqual(rc, 0)
        # reconciler.init.run_init must NOT have been called -- nothing to do.
        self.assertEqual(called, [])
        self.assertIn("already initialised", captured.getvalue())
        # mtimes are unchanged -- no file was rewritten.
        self.assertEqual(cfg_mtime, cfg.stat().st_mtime)
        self.assertEqual(audit_mtime, audit.stat().st_mtime)
        self.assertEqual(cov_mtime, cov.stat().st_mtime)

    def test_preserves_existing_canary_hits_byte_identical(self):
        """R3 mitigation: canary-hits/ is never touched by init."""
        canary_dir = self.tmp / "canary-hits"
        canary_dir.mkdir()
        sample_file = canary_dir / "2026-04-26.jsonl"
        sample_payload = (
            '{"ts": "2026-04-26T12:00:00Z", "kind": "AKIA",'
            ' "where": "tool_response", "value": "AKIA_BEACON_TESTFIXTURE000000000000"}\n'
        )
        sample_file.write_bytes(sample_payload.encode("utf-8"))
        before = sample_file.read_bytes()
        before_mtime = sample_file.stat().st_mtime
        before_strings = self.tmp / "canary-strings.txt"
        before_strings.write_bytes(b"AKIA_BEACON_TESTFIXTURE000000000000\n")
        strings_before = before_strings.read_bytes()
        strings_mtime = before_strings.stat().st_mtime

        # Pre-create a config so init doesn't try to prompt.
        _state.state_path("config.toml").write_text("# test\n")

        with patch("reconciler.init.run_init", side_effect=lambda **_: 0), \
             patch("sys.stdout", io.StringIO()):
            init_cmd.run(_ns())

        after = sample_file.read_bytes()
        self.assertEqual(before, after, "canary-hits file must be byte-identical")
        self.assertEqual(before_mtime, sample_file.stat().st_mtime)
        self.assertEqual(strings_before, before_strings.read_bytes())
        self.assertEqual(strings_mtime, before_strings.stat().st_mtime)

    def test_add_surface_adds_one_row(self):
        # Pre-create coverage.json so the add-surface flow exercises merge,
        # not the empty-file create.
        cov = _state.state_path("coverage.json")
        cov.write_text(
            json.dumps({"schema": 1, "surfaces": {"existing": {"source": "scanned"}}})
            + "\n"
        )

        with patch("sys.stdout", io.StringIO()):
            rc = init_cmd.run(_ns(add_surface="local-claude-md-global"))

        self.assertEqual(rc, 0)
        data = json.loads(cov.read_text())
        # Pre-existing entry preserved.
        self.assertIn("existing", data["surfaces"])
        # New entry registered.
        self.assertIn("local-claude-md-global", data["surfaces"])
        self.assertEqual(
            data["surfaces"]["local-claude-md-global"]["source"], "manual"
        )


if __name__ == "__main__":
    unittest.main()
