#!/usr/bin/env python3
"""Tests for status-segment.py.

Covers the per-dir hit predicates (`_content_safety_hit`, `_canary_hit`,
`_exfil_hit`) and the `count_today` (hits, fires) tuple they feed. Also
covers end-to-end statusline rendering via subprocess so the verbosity
toggle and the integrated `build_flags()` path stay honest.

Runs with stdlib unittest; no external deps. From the repo root:

    python3 tools/tests/status_segment_test.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE.parent / "status-segment.py"


def _load_module():
    """Import status-segment.py by path — filename has a hyphen, so the
    usual import syntax does not work."""
    spec = importlib.util.spec_from_file_location("status_segment", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ss = _load_module()


def _today_path(d: Path) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return d / f"{today}.jsonl"


class ContentSafetyPredicateTest(unittest.TestCase):
    """Real hit = block True OR score > 0 OR non-empty findings."""

    def test_clean_fire_is_not_a_hit(self):
        rec = {"block": False, "score": 0, "findings": []}
        self.assertFalse(ss._content_safety_hit(rec))

    def test_block_true_is_a_hit(self):
        rec = {"block": True, "score": 0, "findings": []}
        self.assertTrue(ss._content_safety_hit(rec))

    def test_positive_score_is_a_hit(self):
        rec = {"block": False, "score": 3, "findings": []}
        self.assertTrue(ss._content_safety_hit(rec))

    def test_findings_present_is_a_hit(self):
        rec = {"block": False, "score": 0,
               "findings": [{"category": "imperative"}]}
        self.assertTrue(ss._content_safety_hit(rec))

    def test_missing_fields_is_not_a_hit(self):
        # A record with no detection fields at all is not a hit. Defensive
        # default — better to under-report than to fabricate signal.
        self.assertFalse(ss._content_safety_hit({}))

    def test_score_must_be_numeric(self):
        # If a writer mis-encodes score as a string, do not coerce — treat
        # as missing rather than truthy.
        self.assertFalse(ss._content_safety_hit({"score": "3"}))


class CanaryPredicateTest(unittest.TestCase):
    """Real hit = non-empty `hits` array. The canary-match hook only writes
    when it sees something, so this is mostly a shape-validation test."""

    def test_non_empty_hits_is_a_hit(self):
        rec = {"hits": [{"token": "AKIA_BEACON_TESTFIXTURE000000000000",
                         "locations": ["tool_response"]}]}
        self.assertTrue(ss._canary_hit(rec))

    def test_empty_hits_array_is_not_a_hit(self):
        # Defensive: if a future writer logs probe lines with empty hits,
        # don't count them as real detections.
        self.assertFalse(ss._canary_hit({"hits": []}))

    def test_missing_hits_field_is_not_a_hit(self):
        self.assertFalse(ss._canary_hit({}))

    def test_hits_must_be_list(self):
        # A scalar in the hits field is malformed — not a detection.
        self.assertFalse(ss._canary_hit({"hits": "yes"}))

    def test_self_edit_noise_row_is_not_a_hit(self):
        # The producer hook (canary-match.sh) tags Edit/Write payloads on
        # known beacon-deployed surfaces with self_edit_noise=true. Those
        # rows are true positives (the canary literally appeared) but
        # operationally meaningless — routine edits of beacon-bearing
        # files. The status-line must not count them or the counter goes
        # to permanent noise the moment the operator touches CLAUDE.md.
        rec = {
            "hits": [{"token": "AKIA_BEACON_TESTFIXTURE000000000000",
                      "locations": ["tool_input"]}],
            "self_edit_noise": True,
            "self_edit_reason": "deployed-beacon-file",
        }
        self.assertFalse(ss._canary_hit(rec))

    def test_self_edit_noise_false_still_counts(self):
        # Belt-and-suspenders: explicit false must still count.
        rec = {
            "hits": [{"token": "AKIA_BEACON_TESTFIXTURE000000000000",
                      "locations": ["tool_response"]}],
            "self_edit_noise": False,
        }
        self.assertTrue(ss._canary_hit(rec))

    def test_missing_self_edit_noise_field_still_counts(self):
        # Backwards compatibility — log rows written before the producer
        # added the field are real hits unless proven otherwise.
        rec = {"hits": [{"token": "AKIA_BEACON_TESTFIXTURE000000000000",
                         "locations": ["tool_input"]}]}
        self.assertTrue(ss._canary_hit(rec))

    def test_truthy_non_bool_self_edit_noise_does_not_suppress(self):
        # Defensive: only the boolean True suppresses. A string "true" or
        # 1 would mean a buggy producer; we don't silently drop real
        # detections on malformed flags.
        for val in ("true", 1, "yes", [True]):
            with self.subTest(val=val):
                rec = {"hits": [{"token": "x", "locations": ["x"]}],
                       "self_edit_noise": val}
                self.assertTrue(ss._canary_hit(rec))


class ExfilPredicateTest(unittest.TestCase):
    """Real hit = severity in {'block', 'warn'}; 'info' is noise."""

    def test_block_severity_is_a_hit(self):
        self.assertTrue(ss._exfil_hit({"severity": "block"}))

    def test_warn_severity_is_a_hit(self):
        self.assertTrue(ss._exfil_hit({"severity": "warn"}))

    def test_info_severity_is_not_a_hit(self):
        self.assertFalse(ss._exfil_hit({"severity": "info"}))

    def test_missing_severity_is_not_a_hit(self):
        self.assertFalse(ss._exfil_hit({}))

    def test_unknown_severity_is_not_a_hit(self):
        # Future-proofing: an unrecognised severity value should not be
        # silently promoted to a hit.
        self.assertFalse(ss._exfil_hit({"severity": "potato"}))


class CountTodayFileShapesTest(unittest.TestCase):
    """count_today must handle missing / empty / mixed / malformed files
    without raising and without over- or under-counting."""

    def test_missing_file_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            self.assertEqual(ss.count_today(d_path, ss._content_safety_hit),
                             (0, 0))

    def test_empty_file_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            _today_path(d_path).write_text("")
            self.assertEqual(ss.count_today(d_path, ss._content_safety_hit),
                             (0, 0))

    def test_blank_lines_are_not_fires(self):
        # Whitespace-only lines must not inflate the fire count.
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            _today_path(d_path).write_text("\n\n   \n\n")
            self.assertEqual(ss.count_today(d_path, ss._content_safety_hit),
                             (0, 0))

    def test_all_noise_file_zero_hits(self):
        # The bug case from production: 40 fires of the content-safety hook
        # all with block=false, score=0, findings=[]. The old code reported
        # inject:40; the new code must report (0 hits, 40 fires).
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            lines = []
            for _ in range(40):
                lines.append(json.dumps({
                    "block": False, "score": 0, "findings": [],
                    "tool_name": "WebFetch",
                }))
            _today_path(d_path).write_text("\n".join(lines) + "\n")
            hits, fires = ss.count_today(d_path, ss._content_safety_hit)
            self.assertEqual(hits, 0)
            self.assertEqual(fires, 40)

    def test_all_real_hits_file(self):
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            lines = [
                json.dumps({"block": True, "score": 0, "findings": []}),
                json.dumps({"block": False, "score": 5, "findings": []}),
                json.dumps({"block": False, "score": 0,
                            "findings": [{"category": "imperative"}]}),
            ]
            _today_path(d_path).write_text("\n".join(lines) + "\n")
            hits, fires = ss.count_today(d_path, ss._content_safety_hit)
            self.assertEqual(hits, 3)
            self.assertEqual(fires, 3)

    def test_mixed_file_partitions_correctly(self):
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            lines = []
            for _ in range(7):
                lines.append(json.dumps({"block": False, "score": 0,
                                         "findings": []}))
            for _ in range(3):
                lines.append(json.dumps({"block": True, "score": 9,
                                         "findings": [{"x": 1}]}))
            _today_path(d_path).write_text("\n".join(lines) + "\n")
            hits, fires = ss.count_today(d_path, ss._content_safety_hit)
            self.assertEqual(hits, 3)
            self.assertEqual(fires, 10)

    def test_malformed_json_does_not_crash(self):
        # A truncated or otherwise malformed JSONL line counts as a fire
        # (the hook did invoke and emit something) but never as a hit.
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            text = "\n".join([
                json.dumps({"block": True, "score": 0, "findings": []}),
                "{not json at all",
                json.dumps({"block": False, "score": 0, "findings": []}),
                "",
                "{\"truncated\": ",
            ]) + "\n"
            _today_path(d_path).write_text(text)
            hits, fires = ss.count_today(d_path, ss._content_safety_hit)
            self.assertEqual(hits, 1)
            # 4 non-empty lines: 2 valid JSON + 2 malformed.
            self.assertEqual(fires, 4)

    def test_non_dict_json_is_not_a_hit(self):
        # A bare list or string is valid JSON but not a record shape;
        # treat as malformed for predicate purposes.
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            text = "\n".join([
                json.dumps([1, 2, 3]),
                json.dumps("hello"),
                json.dumps({"block": True}),
            ]) + "\n"
            _today_path(d_path).write_text(text)
            hits, fires = ss.count_today(d_path, ss._content_safety_hit)
            self.assertEqual(hits, 1)
            self.assertEqual(fires, 3)

    def test_buggy_predicate_does_not_break_count(self):
        # If a predicate raises on a particular record, that record is not
        # a hit — the status line must not crash.
        def explode(rec):
            raise RuntimeError("boom")
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            _today_path(d_path).write_text(json.dumps({"x": 1}) + "\n")
            hits, fires = ss.count_today(d_path, explode)
            self.assertEqual(hits, 0)
            self.assertEqual(fires, 1)

    def test_no_predicate_is_legacy_behavior(self):
        # When predicate=None, every non-empty line is a hit (matches the
        # pre-fix behavior, kept for callers that genuinely want fire count).
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            _today_path(d_path).write_text("a\nb\nc\n")
            hits, fires = ss.count_today(d_path, None)
            self.assertEqual(hits, 3)
            self.assertEqual(fires, 3)


class StatuslineRenderTest(unittest.TestCase):
    """End-to-end: invoke the script as a subprocess against scratch dirs
    and assert the rendered glyph string. This is the operator-facing
    contract — a misleading flag here is the bug we fixed."""

    def _run(self, env_overrides: dict) -> str:
        env = dict(os.environ)
        env.update(env_overrides)
        # Force the staleness check to "fresh" so it does not leak into
        # the assertion. The watchdog tick file is fresh-by-construction.
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH)],
            env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0,
                         f"non-zero exit: {result.stderr}")
        return result.stdout

    def _scratch_dirs(self, d: Path):
        canary = d / "canary"
        exfil = d / "exfil"
        content = d / "content"
        for sub in (canary, exfil, content):
            sub.mkdir()
        # Fresh tick file so staleness flags do not appear.
        tick = d / "tick"
        tick.write_text(datetime.now(timezone.utc).isoformat())
        return canary, exfil, content, tick

    def _base_env(self, canary, exfil, content, tick) -> dict:
        return {
            "SWANLAKE_LAST_RUN": str(d_unused := (canary.parent / "no-such")),
            "SWANLAKE_TICK": str(tick),
            "SWANLAKE_CANARY_HITS": str(canary),
            "SWANLAKE_EXFIL_HITS": str(exfil),
            "SWANLAKE_CONTENT_HITS": str(content),
        }

    def test_all_clean_renders_bare_glyph(self):
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            canary, exfil, content, tick = self._scratch_dirs(d_path)
            env = self._base_env(canary, exfil, content, tick)
            out = self._run(env)
            # Bare shield, no flags. Defensive against future glyph
            # changes: just assert no per-dir flags appeared.
            self.assertNotIn("canary:", out)
            self.assertNotIn("exfil:", out)
            self.assertNotIn("inject:", out)

    def test_noise_only_content_safety_does_not_show_inject(self):
        # The exact bug report: 40 fires, 0 detections, must NOT render
        # "inject:40".
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            canary, exfil, content, tick = self._scratch_dirs(d_path)
            lines = [json.dumps({"block": False, "score": 0,
                                 "findings": []}) for _ in range(40)]
            _today_path(content).write_text("\n".join(lines) + "\n")
            env = self._base_env(canary, exfil, content, tick)
            out = self._run(env)
            self.assertNotIn("inject:", out,
                             f"noise must not render an inject flag: {out!r}")

    def test_real_inject_hit_renders_inject_flag(self):
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            canary, exfil, content, tick = self._scratch_dirs(d_path)
            lines = [
                json.dumps({"block": True, "score": 7,
                            "findings": [{"category": "imperative"}]}),
                json.dumps({"block": False, "score": 0, "findings": []}),
                json.dumps({"block": False, "score": 0, "findings": []}),
            ]
            _today_path(content).write_text("\n".join(lines) + "\n")
            env = self._base_env(canary, exfil, content, tick)
            out = self._run(env)
            self.assertIn("inject:1", out)

    def test_canary_hit_renders_canary_flag(self):
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            canary, exfil, content, tick = self._scratch_dirs(d_path)
            line = json.dumps({
                "ts": "2026-04-25T00:00:00+00:00",
                "tool_name": "Edit",
                # Obvious test-fixture token — never a real-shaped canary.
                "hits": [{"token": "AKIA_BEACON_TESTFIXTURE000000000000",
                          "locations": ["tool_response"]}],
            })
            _today_path(canary).write_text(line + "\n")
            env = self._base_env(canary, exfil, content, tick)
            out = self._run(env)
            self.assertIn("canary:1", out)

    def test_exfil_block_and_warn_count_info_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            canary, exfil, content, tick = self._scratch_dirs(d_path)
            lines = [
                json.dumps({"severity": "block", "reason": "exfil-monitor"}),
                json.dumps({"severity": "warn", "reason": "exfil-monitor"}),
                json.dumps({"severity": "info", "reason": "exfil-monitor"}),
            ]
            _today_path(exfil).write_text("\n".join(lines) + "\n")
            env = self._base_env(canary, exfil, content, tick)
            out = self._run(env)
            self.assertIn("exfil:2", out)

    def test_full_verbosity_shows_hits_over_fires(self):
        with tempfile.TemporaryDirectory() as d:
            d_path = Path(d)
            canary, exfil, content, tick = self._scratch_dirs(d_path)
            lines = [json.dumps({"block": False, "score": 0,
                                 "findings": []}) for _ in range(40)]
            _today_path(content).write_text("\n".join(lines) + "\n")
            env = self._base_env(canary, exfil, content, tick)
            env["SWANLAKE_STATUS_VERBOSITY"] = "full"
            out = self._run(env)
            self.assertIn("inject:0/40", out)
            # Other dirs are empty so they render as 0/0.
            self.assertIn("canary:0/0", out)
            self.assertIn("exfil:0/0", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
