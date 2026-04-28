#!/usr/bin/env python3
"""Tests for loop-closure-metric.py.

Covers the predicates, hardening-artifact counters, rollup composition,
window aggregation, and the --status-flag emission. Stdlib only.

Runs with stdlib unittest. From the repo root:

    python3 tools/tests/loop_closure_metric_test.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE.parent / "loop-closure-metric.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("loop_closure_metric",
                                                  MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Pristine import for unit-style tests that don't need env-injection.
lcm = _load_module()


def _reload_with_env(env_overrides: dict):
    """Reload the module with fresh module-level env reads. Returns the
    re-imported module so the caller sees overridden CANARY_DIR etc."""
    saved = {k: os.environ.get(k) for k in env_overrides}
    os.environ.update({k: str(v) for k, v in env_overrides.items()})
    try:
        spec = importlib.util.spec_from_file_location(
            "loop_closure_metric_reload", MODULE_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _today_jsonl(d: Path) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return d / f"{today}.jsonl"


class PredicateParityTest(unittest.TestCase):
    """The three predicates must match status-segment.py's behavior — if
    they diverge, the metric and the status flag tell different stories."""

    def test_canary_self_edit_noise_excluded(self):
        rec = {"hits": [{"token": "x", "locations": ["x"]}],
               "self_edit_noise": True}
        self.assertFalse(lcm._canary_hit(rec))

    def test_canary_real_hit_counted(self):
        rec = {"hits": [{"token": "x", "locations": ["x"]}]}
        self.assertTrue(lcm._canary_hit(rec))

    def test_content_block_counted(self):
        self.assertTrue(lcm._content_safety_hit(
            {"block": True, "score": 0, "findings": []}))

    def test_content_clean_not_counted(self):
        self.assertFalse(lcm._content_safety_hit(
            {"block": False, "score": 0, "findings": []}))

    def test_exfil_block_counted(self):
        self.assertTrue(lcm._exfil_hit({"severity": "block"}))

    def test_exfil_info_not_counted(self):
        self.assertFalse(lcm._exfil_hit({"severity": "info"}))

    # --- session_id filter: bench/CI harnesses write empty session_id and
    # must not inflate the events-caught denominator. Records that omit the
    # field entirely (legacy/external producers) keep their prior behavior.

    def test_empty_session_id_excludes_content_safety(self):
        rec = {"block": True, "score": 0, "findings": [], "session_id": ""}
        self.assertFalse(lcm._content_safety_hit(rec))

    def test_empty_session_id_excludes_canary(self):
        rec = {"hits": [{"token": "x", "locations": ["x"]}],
               "session_id": ""}
        self.assertFalse(lcm._canary_hit(rec))

    def test_empty_session_id_excludes_exfil(self):
        self.assertFalse(lcm._exfil_hit({"severity": "block",
                                         "session_id": ""}))

    def test_present_session_id_keeps_record(self):
        sid = "b3e7dd3d-8405-4818-b505-f6f2ecb5eb2b"
        self.assertTrue(lcm._content_safety_hit(
            {"block": True, "session_id": sid}))
        self.assertTrue(lcm._canary_hit(
            {"hits": [{"t": 1}], "session_id": sid}))
        self.assertTrue(lcm._exfil_hit(
            {"severity": "warn", "session_id": sid}))

    def test_missing_session_id_field_keeps_legacy_behavior(self):
        # Records that never carried the field stay counted, so existing
        # producers and old log lines continue to register.
        self.assertTrue(lcm._content_safety_hit({"block": True}))
        self.assertTrue(lcm._canary_hit({"hits": [{"t": 1}]}))
        self.assertTrue(lcm._exfil_hit({"severity": "block"}))


class JsonlIterTest(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(list(lcm._iter_jsonl(Path(d) / "no.jsonl")), [])

    def test_skips_blank_and_malformed(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.jsonl"
            f.write_text("\n".join([
                "",
                "   ",
                json.dumps({"ok": 1}),
                "{not json",
                json.dumps([1, 2, 3]),  # valid json, not a dict — skipped
                json.dumps({"ok": 2}),
            ]))
            recs = list(lcm._iter_jsonl(f))
            self.assertEqual(recs, [{"ok": 1}, {"ok": 2}])


class CountRealHitsTest(unittest.TestCase):
    def test_empty_dir_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            today = datetime.now(timezone.utc).date()
            self.assertEqual(
                lcm.count_real_hits(Path(d), lcm._canary_hit, today), 0)

    def test_mixed_hits_partition(self):
        with tempfile.TemporaryDirectory() as d:
            f = _today_jsonl(Path(d))
            f.write_text("\n".join([
                json.dumps({"hits": [{"t": 1}], "self_edit_noise": True}),
                json.dumps({"hits": [{"t": 2}]}),
                json.dumps({"hits": []}),
                json.dumps({"hits": [{"t": 3}], "self_edit_noise": False}),
            ]) + "\n")
            today = datetime.now(timezone.utc).date()
            self.assertEqual(
                lcm.count_real_hits(Path(d), lcm._canary_hit, today), 2)


class GitCommitsTest(unittest.TestCase):
    """Exercise the git-log scrape against a real ephemeral repo."""

    def _init_repo(self, root: Path) -> None:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email",
                        "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name",
                        "Test"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "commit.gpgsign",
                        "false"], check=True)

    def _commit(self, root: Path, msg: str) -> None:
        f = root / "a.txt"
        f.write_text(str(datetime.now().timestamp()))
        subprocess.run(["git", "-C", str(root), "add", "a.txt"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", msg],
                       check=True)

    def test_counts_only_conventional_today(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self._init_repo(root)
            self._commit(root, "feat(hooks): add new check")
            self._commit(root, "fix: tighten regex")
            self._commit(root, "random freeform message")
            self._commit(root, "chore(deps): bump")
            today = datetime.now(timezone.utc).date()
            n = lcm.count_git_commits([root], today)
            self.assertEqual(n, 3, "only conventional-shape commits count")

    def test_missing_repo_is_zero(self):
        with tempfile.TemporaryDirectory() as d:
            today = datetime.now(timezone.utc).date()
            self.assertEqual(
                lcm.count_git_commits([Path(d) / "no-such"], today), 0)

    def test_non_git_dir_is_zero(self):
        with tempfile.TemporaryDirectory() as d:
            today = datetime.now(timezone.utc).date()
            self.assertEqual(lcm.count_git_commits([Path(d)], today), 0)


class HookFilesTest(unittest.TestCase):
    def test_counts_files_with_today_mtime(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "good.sh").write_text("x")
            (root / "old.sh").write_text("x")
            (root / "ignore.bak").write_text("x")
            (root / ".dotfile").write_text("x")
            (root / "snapshot.bak-20260101-000000").write_text("x")
            # Backdate one to yesterday.
            yesterday = (datetime.now(timezone.utc) -
                         timedelta(days=1)).timestamp()
            os.utime(root / "old.sh", (yesterday, yesterday))
            today = datetime.now(timezone.utc).date()
            self.assertEqual(lcm.count_new_hook_files(root, today), 1)

    def test_missing_dir_is_zero(self):
        with tempfile.TemporaryDirectory() as d:
            today = datetime.now(timezone.utc).date()
            self.assertEqual(
                lcm.count_new_hook_files(Path(d) / "no-such", today), 0)


class DenyDeltaTest(unittest.TestCase):
    def test_no_baseline_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            settings.write_text(json.dumps({
                "permissions": {"deny": ["a", "b", "c"]}}))
            today = datetime.now(timezone.utc).date()
            self.assertEqual(
                lcm.deny_delta(settings, Path(d) / "rollups", today), 0)

    def test_positive_delta_counted(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            rollups = Path(d) / "rollups"
            rollups.mkdir()
            yesterday = (datetime.now(timezone.utc).date() -
                         timedelta(days=1)).isoformat()
            (rollups / f"{yesterday}.json").write_text(json.dumps({
                "deny_count_snapshot": 3}))
            settings.write_text(json.dumps({
                "permissions": {"deny": ["a", "b", "c", "d", "e"]}}))
            today = datetime.now(timezone.utc).date()
            self.assertEqual(lcm.deny_delta(settings, rollups, today), 2)

    def test_negative_delta_clamped_to_zero(self):
        # Removing entries shouldn't subtract from the artifact count.
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            rollups = Path(d) / "rollups"
            rollups.mkdir()
            yesterday = (datetime.now(timezone.utc).date() -
                         timedelta(days=1)).isoformat()
            (rollups / f"{yesterday}.json").write_text(json.dumps({
                "deny_count_snapshot": 10}))
            settings.write_text(json.dumps({
                "permissions": {"deny": ["a"]}}))
            today = datetime.now(timezone.utc).date()
            self.assertEqual(lcm.deny_delta(settings, rollups, today), 0)

    def test_malformed_settings_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "settings.json"
            settings.write_text("not json at all")
            today = datetime.now(timezone.utc).date()
            self.assertEqual(
                lcm.deny_delta(settings, Path(d) / "r", today), 0)


class RollupTest(unittest.TestCase):
    """End-to-end: scratch dirs for every input, run compute_rollup,
    assert shape + values."""

    def _scratch(self, d: Path):
        canary = d / "canary"
        content = d / "content"
        exfil = d / "exfil"
        rollup = d / "rollup"
        hooks = d / "hooks"
        repo = d / "repo"
        for p in (canary, content, exfil, rollup, hooks):
            p.mkdir()
        return canary, content, exfil, rollup, hooks, repo

    def test_empty_dirs_clean_rollup(self):
        with tempfile.TemporaryDirectory() as d:
            canary, content, exfil, rollup, hooks, repo = self._scratch(Path(d))
            settings = Path(d) / "settings.json"
            settings.write_text(json.dumps({"permissions": {"deny": []}}))
            mod = _reload_with_env({
                "SWANLAKE_CANARY_HITS": canary,
                "SWANLAKE_CONTENT_HITS": content,
                "SWANLAKE_EXFIL_HITS": exfil,
                "SWANLAKE_ROLLUP_DIR": rollup,
                "SWANLAKE_HOOKS_DIR": hooks,
                "SWANLAKE_SETTINGS_FILE": settings,
                "SWANLAKE_HARDENING_REPOS": str(repo),
            })
            today = datetime.now(timezone.utc).date()
            r = mod.compute_rollup(today)
            self.assertEqual(r["events_caught"], 0)
            self.assertEqual(r["artifacts_produced"], 0)
            # Defensive denominator: ratio is artifacts/max(events, 1) so
            # the empty case must not divide by zero.
            self.assertEqual(r["ratio"], 0.0)

    def test_all_caught_no_artifacts_low_ratio(self):
        with tempfile.TemporaryDirectory() as d:
            canary, content, exfil, rollup, hooks, repo = self._scratch(Path(d))
            settings = Path(d) / "settings.json"
            settings.write_text(json.dumps({"permissions": {"deny": []}}))
            # 5 real exfil blocks today, no commits, no new hooks.
            f = _today_jsonl(exfil)
            f.write_text("\n".join([
                json.dumps({"severity": "block", "reason": "x"})
                for _ in range(5)
            ]) + "\n")
            mod = _reload_with_env({
                "SWANLAKE_CANARY_HITS": canary,
                "SWANLAKE_CONTENT_HITS": content,
                "SWANLAKE_EXFIL_HITS": exfil,
                "SWANLAKE_ROLLUP_DIR": rollup,
                "SWANLAKE_HOOKS_DIR": hooks,
                "SWANLAKE_SETTINGS_FILE": settings,
                "SWANLAKE_HARDENING_REPOS": str(repo),
            })
            today = datetime.now(timezone.utc).date()
            r = mod.compute_rollup(today)
            self.assertEqual(r["events_caught"], 5)
            self.assertEqual(r["artifacts_produced"], 0)
            self.assertEqual(r["ratio"], 0.0)

    def test_high_ratio_when_artifacts_keep_pace(self):
        with tempfile.TemporaryDirectory() as d:
            canary, content, exfil, rollup, hooks, repo = self._scratch(Path(d))
            settings = Path(d) / "settings.json"
            settings.write_text(json.dumps({"permissions": {"deny": []}}))
            # 2 events caught.
            _today_jsonl(canary).write_text(json.dumps({
                "hits": [{"t": "x", "locations": ["x"]}],
            }) + "\n")
            _today_jsonl(content).write_text(json.dumps({
                "block": True, "score": 0, "findings": [],
            }) + "\n")
            # 2 hardening artifacts: a new hook + a real conventional commit.
            (hooks / "new-defense.sh").write_text("x")
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email",
                            "t@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name",
                            "T"], check=True)
            subprocess.run(["git", "-C", str(repo), "config",
                            "commit.gpgsign", "false"], check=True)
            (repo / "x").write_text("a")
            subprocess.run(["git", "-C", str(repo), "add", "x"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m",
                            "fix(canary): tighten classifier"], check=True)
            mod = _reload_with_env({
                "SWANLAKE_CANARY_HITS": canary,
                "SWANLAKE_CONTENT_HITS": content,
                "SWANLAKE_EXFIL_HITS": exfil,
                "SWANLAKE_ROLLUP_DIR": rollup,
                "SWANLAKE_HOOKS_DIR": hooks,
                "SWANLAKE_SETTINGS_FILE": settings,
                "SWANLAKE_HARDENING_REPOS": str(repo),
            })
            today = datetime.now(timezone.utc).date()
            r = mod.compute_rollup(today)
            self.assertEqual(r["events_caught"], 2)
            self.assertEqual(r["artifacts_produced"], 2)
            self.assertEqual(r["ratio"], 1.0)

    def test_bench_harness_records_excluded_from_events(self):
        # Empty session_id is the bench-harness signature — running
        # PYRIT/GARAK/AB benches against the detectors fires every line by
        # design. Those rows must not bleed into events_caught.
        with tempfile.TemporaryDirectory() as d:
            canary, content, exfil, rollup, hooks, repo = self._scratch(Path(d))
            settings = Path(d) / "settings.json"
            settings.write_text(json.dumps({"permissions": {"deny": []}}))
            real_sid = "b3e7dd3d-8405-4818-b505-f6f2ecb5eb2b"
            _today_jsonl(content).write_text("\n".join([
                # 50 bench rows, then 2 real interactive hits.
                *[json.dumps({"block": True, "session_id": ""})
                  for _ in range(50)],
                json.dumps({"block": True, "session_id": real_sid}),
                json.dumps({"score": 1.5, "findings": [{"x": 1}],
                            "session_id": real_sid}),
            ]) + "\n")
            mod = _reload_with_env({
                "SWANLAKE_CANARY_HITS": canary,
                "SWANLAKE_CONTENT_HITS": content,
                "SWANLAKE_EXFIL_HITS": exfil,
                "SWANLAKE_ROLLUP_DIR": rollup,
                "SWANLAKE_HOOKS_DIR": hooks,
                "SWANLAKE_SETTINGS_FILE": settings,
                "SWANLAKE_HARDENING_REPOS": str(repo),
            })
            today = datetime.now(timezone.utc).date()
            r = mod.compute_rollup(today)
            self.assertEqual(r["events_breakdown"]["content_safety"], 2)
            self.assertEqual(r["events_caught"], 2)

    def test_self_edit_noise_does_not_inflate_events(self):
        with tempfile.TemporaryDirectory() as d:
            canary, content, exfil, rollup, hooks, repo = self._scratch(Path(d))
            settings = Path(d) / "settings.json"
            settings.write_text(json.dumps({"permissions": {"deny": []}}))
            _today_jsonl(canary).write_text("\n".join([
                # 10 self-edit-noise rows + 1 real cross-surface hit.
                *[json.dumps({"hits": [{"t": "x", "locations": ["x"]}],
                              "self_edit_noise": True}) for _ in range(10)],
                json.dumps({"hits": [{"t": "y", "locations": ["x"]}]}),
            ]) + "\n")
            mod = _reload_with_env({
                "SWANLAKE_CANARY_HITS": canary,
                "SWANLAKE_CONTENT_HITS": content,
                "SWANLAKE_EXFIL_HITS": exfil,
                "SWANLAKE_ROLLUP_DIR": rollup,
                "SWANLAKE_HOOKS_DIR": hooks,
                "SWANLAKE_SETTINGS_FILE": settings,
                "SWANLAKE_HARDENING_REPOS": str(repo),
            })
            today = datetime.now(timezone.utc).date()
            r = mod.compute_rollup(today)
            # The 10 self-edits must not show up as events.
            self.assertEqual(r["events_breakdown"]["canary"], 1)
            self.assertEqual(r["events_caught"], 1)


class WindowAggregationTest(unittest.TestCase):
    def test_aggregates_existing_rollups(self):
        with tempfile.TemporaryDirectory() as d:
            rollup = Path(d)
            today = datetime.now(timezone.utc).date()
            for offset, (ev, ar) in enumerate([(2, 1), (3, 2), (1, 0)]):
                day = today - timedelta(days=offset)
                (rollup / f"{day.isoformat()}.json").write_text(json.dumps({
                    "date": day.isoformat(),
                    "events_caught": ev,
                    "artifacts_produced": ar,
                }))
            mod = _reload_with_env({
                "SWANLAKE_CANARY_HITS": Path(d) / "no",
                "SWANLAKE_CONTENT_HITS": Path(d) / "no",
                "SWANLAKE_EXFIL_HITS": Path(d) / "no",
                "SWANLAKE_ROLLUP_DIR": rollup,
                "SWANLAKE_HOOKS_DIR": Path(d) / "no",
                "SWANLAKE_SETTINGS_FILE": Path(d) / "no.json",
                "SWANLAKE_HARDENING_REPOS": Path(d) / "no",
            })
            summary = mod.aggregate_window(today, 3)
            self.assertEqual(summary["total_events"], 6)
            self.assertEqual(summary["total_artifacts"], 3)
            self.assertEqual(summary["ratio"], 0.5)
            self.assertEqual(summary["window_days"], 3)
            self.assertEqual(summary["days_with_data"], 3)


class StatusFlagTest(unittest.TestCase):
    """Exercise the --status-flag exit path. Status lines must never break."""

    def _run(self, env_overrides: dict) -> tuple[str, int]:
        env = dict(os.environ)
        env.update({k: str(v) for k, v in env_overrides.items()})
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--status-flag"],
            env=env, capture_output=True, text=True, timeout=10,
        )
        return result.stdout, result.returncode

    def test_silent_when_below_event_floor(self):
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            for sub in ("c", "ct", "x", "ro", "h"):
                (d_path / sub).mkdir()
            settings = d_path / "s.json"
            settings.write_text(json.dumps({"permissions": {"deny": []}}))
            out, rc = self._run({
                "SWANLAKE_CANARY_HITS": d_path / "c",
                "SWANLAKE_CONTENT_HITS": d_path / "ct",
                "SWANLAKE_EXFIL_HITS": d_path / "x",
                "SWANLAKE_ROLLUP_DIR": d_path / "ro",
                "SWANLAKE_HOOKS_DIR": d_path / "h",
                "SWANLAKE_SETTINGS_FILE": settings,
                "SWANLAKE_HARDENING_REPOS": d_path / "no-such",
            })
            self.assertEqual(rc, 0)
            self.assertEqual(out, "")

    def test_emits_flag_when_below_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            for sub in ("c", "ct", "x", "ro", "h"):
                (d_path / sub).mkdir()
            settings = d_path / "s.json"
            settings.write_text(json.dumps({"permissions": {"deny": []}}))
            # 5 real exfil blocks today; zero artifacts -> ratio 0%.
            _today_jsonl(d_path / "x").write_text("\n".join([
                json.dumps({"severity": "block"}) for _ in range(5)
            ]) + "\n")
            out, rc = self._run({
                "SWANLAKE_CANARY_HITS": d_path / "c",
                "SWANLAKE_CONTENT_HITS": d_path / "ct",
                "SWANLAKE_EXFIL_HITS": d_path / "x",
                "SWANLAKE_ROLLUP_DIR": d_path / "ro",
                "SWANLAKE_HOOKS_DIR": d_path / "h",
                "SWANLAKE_SETTINGS_FILE": settings,
                "SWANLAKE_HARDENING_REPOS": d_path / "no-such",
                "SWANLAKE_CLOSURE_THRESHOLD": "0.30",
            })
            self.assertEqual(rc, 0)
            self.assertEqual(out, "closure:0%")

    def test_silent_when_at_or_above_threshold(self):
        # 5 events, 5 artifacts (commits in a fresh repo) -> ratio 100%.
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            for sub in ("c", "ct", "x", "ro", "h"):
                (d_path / sub).mkdir()
            settings = d_path / "s.json"
            settings.write_text(json.dumps({"permissions": {"deny": []}}))
            _today_jsonl(d_path / "x").write_text("\n".join([
                json.dumps({"severity": "block"}) for _ in range(5)
            ]) + "\n")
            repo = d_path / "repo"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email",
                            "t@e.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name",
                            "T"], check=True)
            subprocess.run(["git", "-C", str(repo), "config",
                            "commit.gpgsign", "false"], check=True)
            for i in range(5):
                f = repo / f"f{i}.txt"
                f.write_text(str(i))
                subprocess.run(["git", "-C", str(repo), "add", f.name],
                               check=True)
                subprocess.run(["git", "-C", str(repo), "commit", "-q",
                                "-m", f"fix: thing {i}"], check=True)
            out, rc = self._run({
                "SWANLAKE_CANARY_HITS": d_path / "c",
                "SWANLAKE_CONTENT_HITS": d_path / "ct",
                "SWANLAKE_EXFIL_HITS": d_path / "x",
                "SWANLAKE_ROLLUP_DIR": d_path / "ro",
                "SWANLAKE_HOOKS_DIR": d_path / "h",
                "SWANLAKE_SETTINGS_FILE": settings,
                "SWANLAKE_HARDENING_REPOS": str(repo),
                "SWANLAKE_CLOSURE_THRESHOLD": "0.30",
            })
            self.assertEqual(rc, 0)
            self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
