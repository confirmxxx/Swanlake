#!/usr/bin/env python3
"""Phase-1 unit tests for White Cells.

Stdlib unittest, mirrors defense-beacon/reference/tests/make_canaries_test.py
in style. From the repo root:

    python3 experiments/white-cells/tests/test_white_cells.py

Coverage:
    - SchemaValidatorTest    -- v1 finding-schema rules
    - CanaryFilterTest       -- post-filter for real-shaped canaries
    - PreflightTest          -- production-credential env-var assertion
    - FixtureSandboxTest     -- mock-{notion,github,vercel} canned routes
    - ClosureRateCounterTest -- counter, kill-check, close
    - PersonaStubTest        -- both personas yield schema-valid findings
    - EndToEndTest           -- full supervisor run, including a poisoned-
                                persona quarantine path
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Make the experiment's top-level packages importable.
_HERE = Path(__file__).resolve().parent
_WC_ROOT = _HERE.parent
if str(_WC_ROOT) not in sys.path:
    sys.path.insert(0, str(_WC_ROOT))

from supervisor import canary_filter, preflight, schema  # noqa: E402
from supervisor.closure_rate import ClosureRateCounter  # noqa: E402
from supervisor.orchestrator import Supervisor  # noqa: E402
from supervisor.sink import JsonlFindingsSink  # noqa: E402
from fixtures.sandbox import FixtureSandbox  # noqa: E402
from personas.multi_turn_crescendo import MultiTurnCrescendo  # noqa: E402
from personas.research_poisoner import ResearchPoisoner  # noqa: E402


# Atlas-taxonomy set kept narrow + explicit so tests do not depend on
# the file content evolving over time. The supervisor production path
# loads from disk; tests pass an explicit set.
_TTPS = {"AML.T0070", "AML.T0054", "AML.T0051.000", "AML.T0048.002"}


def _good_finding(persona: str = "research_poisoner") -> dict:
    return {
        "persona": persona,
        "produced_utc": "2026-04-25T18:00:00+00:00",
        "atlas_ttp": ["AML.T0070"],
        "severity": "low",
        "title": "test finding",
        "summary": "clean summary, no canary literals",
        "reproduction": "step 1: do nothing",
        "fixture_hits": [
            {"service": "mock-notion", "path": "/v1/users/me", "method": "GET"}
        ],
        "suggested_closure": "doc-note",
        "schema_version": 1,
    }


class SchemaValidatorTest(unittest.TestCase):
    def test_accepts_valid_finding(self):
        ok, err = schema.validate(
            _good_finding(), expected_persona="research_poisoner", atlas_ttps=_TTPS
        )
        self.assertTrue(ok, err)

    def test_rejects_missing_key(self):
        f = _good_finding()
        del f["severity"]
        ok, err = schema.validate(
            f, expected_persona="research_poisoner", atlas_ttps=_TTPS
        )
        self.assertFalse(ok)
        self.assertIn("missing", err)

    def test_rejects_unknown_key(self):
        f = _good_finding()
        f["extra_field"] = "nope"
        ok, err = schema.validate(
            f, expected_persona="research_poisoner", atlas_ttps=_TTPS
        )
        self.assertFalse(ok)
        self.assertIn("unknown keys", err)

    def test_rejects_bad_severity(self):
        f = _good_finding()
        f["severity"] = "catastrophic"
        ok, err = schema.validate(
            f, expected_persona="research_poisoner", atlas_ttps=_TTPS
        )
        self.assertFalse(ok)
        self.assertIn("severity", err)

    def test_rejects_unknown_atlas_ttp(self):
        f = _good_finding()
        f["atlas_ttp"] = ["AML.T9999"]
        ok, err = schema.validate(
            f, expected_persona="research_poisoner", atlas_ttps=_TTPS
        )
        self.assertFalse(ok)
        self.assertIn("not in taxonomy", err)

    def test_rejects_oversize_summary(self):
        f = _good_finding()
        f["summary"] = "x" * 2001
        ok, err = schema.validate(
            f, expected_persona="research_poisoner", atlas_ttps=_TTPS
        )
        self.assertFalse(ok)
        self.assertIn("summary exceeds", err)

    def test_rejects_non_utc_timestamp(self):
        f = _good_finding()
        f["produced_utc"] = "2026-04-25T18:00:00+05:00"
        ok, err = schema.validate(
            f, expected_persona="research_poisoner", atlas_ttps=_TTPS
        )
        self.assertFalse(ok)
        self.assertIn("UTC", err)

    def test_rejects_naive_timestamp(self):
        f = _good_finding()
        f["produced_utc"] = "2026-04-25T18:00:00"
        ok, err = schema.validate(
            f, expected_persona="research_poisoner", atlas_ttps=_TTPS
        )
        self.assertFalse(ok)
        self.assertIn("offset", err)

    def test_rejects_persona_mismatch(self):
        f = _good_finding(persona="research_poisoner")
        ok, err = schema.validate(
            f, expected_persona="multi_turn_crescendo", atlas_ttps=_TTPS
        )
        self.assertFalse(ok)
        self.assertIn("persona mismatch", err)

    def test_rejects_bad_fixture_service(self):
        f = _good_finding()
        f["fixture_hits"] = [
            {"service": "real-notion", "path": "/v1/x", "method": "GET"}
        ]
        ok, err = schema.validate(
            f, expected_persona="research_poisoner", atlas_ttps=_TTPS
        )
        self.assertFalse(ok)
        self.assertIn("fixture_hits service", err)

    def test_load_taxonomy_round_trip(self):
        """The hand-rolled YAML loader returns the curated TTP set from
        the shipped atlas-taxonomy.yaml."""
        ttps = schema.load_taxonomy(_WC_ROOT / "atlas-taxonomy.yaml")
        for required in ("AML.T0070", "AML.T0054", "AML.T0051.000"):
            self.assertIn(required, ttps)


class CanaryFilterTest(unittest.TestCase):
    def test_clean_finding_passes(self):
        self.assertTrue(canary_filter.is_clean(_good_finding()))

    def test_aws_canary_in_summary_quarantines(self):
        # Real-shaped (20 hex upper after AKIA_BEACON_) — would be a
        # production canary if it were registered. NOT a real value;
        # this test fixture is constructed by hand-picking 20 valid
        # hex digits with no registry membership.
        f = _good_finding()
        f["summary"] = "leak: AKIA_BEACON_0123456789ABCDEF0123 oops"
        kinds = canary_filter.detect_canaries(f)
        self.assertEqual(kinds, ["aws"])

    def test_attrib_canary_in_reproduction_quarantines(self):
        f = _good_finding()
        f["reproduction"] = "found beacon-attrib-some-surface-Ab12CdEf in payload"
        self.assertEqual(canary_filter.detect_canaries(f), ["attrib"])

    def test_google_canary_quarantines(self):
        f = _good_finding()
        f["summary"] = "key=AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZabcd1234"
        self.assertEqual(canary_filter.detect_canaries(f), ["google"])

    def test_obviously_fake_aws_does_not_match(self):
        """The block-hook exemption: AKIA_BEACON_TESTFIXTURE... fails the
        [0-9A-F] character class so canary-filter must not flag it."""
        f = _good_finding()
        f["summary"] = "fixture token AKIA_BEACON_TESTFIXTURE000000000000 ok"
        self.assertTrue(canary_filter.is_clean(f))

    def test_redacted_kinds_never_echoes_literal(self):
        out = canary_filter.redacted_kinds(["aws", "attrib"])
        self.assertEqual(
            out, "REDACTED(canary_kind=aws), REDACTED(canary_kind=attrib)"
        )


class PreflightTest(unittest.TestCase):
    def test_empty_env_ok(self):
        preflight.assert_clean_env(env={})

    def test_github_token_blocks(self):
        with self.assertRaises(SystemExit) as cm:
            preflight.assert_clean_env(env={"GITHUB_TOKEN": "fake"})
        self.assertEqual(cm.exception.code, 2)

    def test_case_insensitive(self):
        with self.assertRaises(SystemExit):
            preflight.assert_clean_env(env={"github_token": "fake"})

    def test_substring_match_in_var_name(self):
        with self.assertRaises(SystemExit):
            preflight.assert_clean_env(
                env={"MY_GITHUB_TOKEN_FAKE_VAR": "fake"}
            )

    def test_empty_value_does_not_block(self):
        # Empty value means the var is set but contains no token -- a
        # common pattern for tests that want to declare 'no creds here'.
        preflight.assert_clean_env(env={"GITHUB_TOKEN": ""})

    def test_multiple_credentials_all_reported(self):
        hits = preflight.detect_credentials(
            env={"GITHUB_TOKEN": "x", "VERCEL_TOKEN": "y", "BENIGN_VAR": "z"}
        )
        names = sorted(h[0] for h in hits)
        self.assertEqual(names, ["GITHUB_TOKEN", "VERCEL_TOKEN"])


class FixtureSandboxTest(unittest.TestCase):
    def test_binds_three_distinct_ports(self):
        with FixtureSandbox() as s:
            urls = {s.notion_url, s.github_url, s.vercel_url}
            self.assertEqual(len(urls), 3)

    def test_mock_notion_users_me(self):
        with FixtureSandbox() as s:
            with urllib.request.urlopen(s.notion_url + "/v1/users/me") as r:
                body = json.loads(r.read())
        self.assertEqual(body["object"], "user")
        self.assertEqual(body["bot"]["workspace_name"], "white-cells-fixture")

    def test_mock_github_issues_list(self):
        with FixtureSandbox() as s:
            url = s.github_url + "/repos/fixture-owner/fixture-repo/issues"
            with urllib.request.urlopen(url) as r:
                body = json.loads(r.read())
        self.assertIsInstance(body, list)
        self.assertEqual(body[0]["number"], 1)

    def test_mock_vercel_deployments(self):
        with FixtureSandbox() as s:
            with urllib.request.urlopen(s.vercel_url + "/v6/deployments") as r:
                body = json.loads(r.read())
        self.assertEqual(body["deployments"][0]["state"], "READY")

    def test_unknown_route_returns_404(self):
        with FixtureSandbox() as s:
            try:
                urllib.request.urlopen(s.notion_url + "/v1/nope")
                self.fail("expected 404")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 404)

    def test_teardown_invalidates_url_property(self):
        sb = FixtureSandbox()
        with sb:
            _ = sb.notion_url
        with self.assertRaises(RuntimeError):
            _ = sb.notion_url


class ClosureRateCounterTest(unittest.TestCase):
    def _seed_findings(self, state_dir: Path, rows: list[dict]) -> None:
        with (state_dir / "findings.jsonl").open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, sort_keys=True) + "\n")

    def test_empty_state_ratio_zero(self):
        with tempfile.TemporaryDirectory() as d:
            counter = ClosureRateCounter(Path(d))
            filed, closed, ratio = counter.compute()
            self.assertEqual((filed, closed, ratio), (0, 0, 0.0))

    def test_one_filed_zero_closed(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed_findings(
                Path(d),
                [
                    {
                        "id": "wc-1",
                        "filed_utc": "2026-03-01T00:00:00+00:00",
                        "closure_artifact": None,
                    }
                ],
            )
            filed, closed, ratio = ClosureRateCounter(Path(d)).compute()
            self.assertEqual((filed, closed), (1, 0))
            self.assertEqual(ratio, 0.0)

    def test_two_filed_one_closed_old_enough(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed_findings(
                Path(d),
                [
                    {
                        "id": "wc-1",
                        "filed_utc": "2026-03-01T00:00:00+00:00",
                        "closure_artifact": "PR#42",
                    },
                    {
                        "id": "wc-2",
                        "filed_utc": "2026-03-15T00:00:00+00:00",
                        "closure_artifact": None,
                    },
                ],
            )
            filed, closed, ratio = ClosureRateCounter(Path(d)).compute(
                now=datetime(2026, 4, 25, tzinfo=timezone.utc)
            )
            self.assertEqual((filed, closed), (2, 1))
            self.assertAlmostEqual(ratio, 0.5)

    def test_recent_closure_does_not_count(self):
        """A finding closed less than 14 days ago does NOT count toward
        the closed-to-artifact total."""
        with tempfile.TemporaryDirectory() as d:
            self._seed_findings(
                Path(d),
                [
                    {
                        "id": "wc-1",
                        "filed_utc": "2026-04-20T00:00:00+00:00",  # 5 days old
                        "closure_artifact": "PR#100",
                    },
                ],
            )
            filed, closed, _ = ClosureRateCounter(Path(d)).compute(
                now=datetime(2026, 4, 25, tzinfo=timezone.utc)
            )
            self.assertEqual((filed, closed), (1, 0))

    def test_kill_check_below_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            counter = ClosureRateCounter(Path(d))
            with (Path(d) / "closure-rate.jsonl").open("w") as f:
                f.write(
                    json.dumps(
                        {
                            "date": "2026-04-25T00:00:00+00:00",
                            "findings_filed": 10,
                            "findings_closed_to_artifact": 1,
                            "ratio": 0.10,
                        }
                    )
                    + "\n"
                )
            alive, info = counter.kill_check(
                now=datetime(2026, 4, 25, tzinfo=timezone.utc)
            )
            self.assertFalse(alive)
            self.assertEqual(info["ratio"], 0.10)

    def test_kill_check_above_threshold(self):
        with tempfile.TemporaryDirectory() as d:
            counter = ClosureRateCounter(Path(d))
            with (Path(d) / "closure-rate.jsonl").open("w") as f:
                f.write(
                    json.dumps(
                        {
                            "date": "2026-04-25T00:00:00+00:00",
                            "findings_filed": 10,
                            "findings_closed_to_artifact": 5,
                            "ratio": 0.50,
                        }
                    )
                    + "\n"
                )
            alive, _ = counter.kill_check(
                now=datetime(2026, 4, 25, tzinfo=timezone.utc)
            )
            self.assertTrue(alive)

    def test_kill_check_no_data_alive(self):
        """No closure-rate rows yet -> alive (too early to evaluate)."""
        with tempfile.TemporaryDirectory() as d:
            alive, info = ClosureRateCounter(Path(d)).kill_check()
            self.assertTrue(alive)
            self.assertIn("no closure-rate rows", info["reason"])

    def test_close_marks_finding(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed_findings(
                Path(d),
                [
                    {
                        "id": "wc-target",
                        "filed_utc": "2026-04-01T00:00:00+00:00",
                        "closure_artifact": None,
                    }
                ],
            )
            counter = ClosureRateCounter(Path(d))
            ok = counter.close("wc-target", "hook-rule:no-egress-on-fixture")
            self.assertTrue(ok)
            with (Path(d) / "findings.jsonl").open("r") as f:
                row = json.loads(f.readline())
            self.assertEqual(
                row["closure_artifact"], "hook-rule:no-egress-on-fixture"
            )

    def test_close_unknown_id_returns_false(self):
        with tempfile.TemporaryDirectory() as d:
            self._seed_findings(Path(d), [])
            self.assertFalse(
                ClosureRateCounter(Path(d)).close("wc-nope", "x")
            )


class PersonaStubTest(unittest.TestCase):
    def test_research_poisoner_yields_valid_finding(self):
        persona = ResearchPoisoner()
        with FixtureSandbox() as s:
            findings = list(persona.attack(s))
        self.assertGreaterEqual(len(findings), 1)
        ok, err = schema.validate(
            findings[0], expected_persona=persona.name, atlas_ttps=_TTPS
        )
        self.assertTrue(ok, err)
        self.assertEqual(findings[0]["persona"], persona.name)

    def test_multi_turn_crescendo_yields_valid_finding(self):
        persona = MultiTurnCrescendo()
        with FixtureSandbox() as s:
            findings = list(persona.attack(s))
        self.assertGreaterEqual(len(findings), 1)
        ok, err = schema.validate(
            findings[0], expected_persona=persona.name, atlas_ttps=_TTPS
        )
        self.assertTrue(ok, err)


class _PoisonedPersona:
    """Persona that yields one schema-valid-but-canary-tainted finding.
    Used to verify the supervisor quarantine path. The marker is built
    at runtime from upper-hex characters so the literal does not appear
    in source — keeps the repo's pre-commit canary-block hook quiet."""

    name = "poisoned_persona"
    engine = "stdlib"

    def attack(self, sandbox):
        marker = "AKIA_BEACON_" + "0123456789ABCDEF0123"
        yield {
            "persona": self.name,
            "produced_utc": "2026-04-25T18:00:00+00:00",
            "atlas_ttp": ["AML.T0070"],
            "severity": "low",
            "title": "poisoned-persona output",
            "summary": f"persona accidentally echoed {marker} from sandbox",
            "reproduction": "n/a",
            "fixture_hits": [
                {"service": "mock-notion", "path": "/v1/users/me", "method": "GET"}
            ],
            "suggested_closure": "none",
            "schema_version": 1,
        }


class EndToEndTest(unittest.TestCase):
    def test_clean_supervisor_run(self):
        with tempfile.TemporaryDirectory() as d:
            state_dir = Path(d)
            sink = JsonlFindingsSink(state_dir)
            counter = ClosureRateCounter(state_dir)
            with FixtureSandbox() as sandbox:
                sup = Supervisor(
                    sandbox=sandbox,
                    sink=sink,
                    personas=[ResearchPoisoner(), MultiTurnCrescendo()],
                    closure_counter=counter,
                )
                results = sup.run_round_robin()

            self.assertEqual(len(results), 2)
            for r in results:
                self.assertIsNone(r.error)
                self.assertGreaterEqual(r.findings_filed, 1)
                self.assertEqual(r.findings_quarantined, 0)
                self.assertEqual(r.findings_invalid, 0)

            self.assertTrue((state_dir / "findings.jsonl").exists())
            with (state_dir / "findings.jsonl").open() as f:
                rows = [json.loads(line) for line in f if line.strip()]
            self.assertGreaterEqual(len(rows), 2)

            quarantine_path = state_dir / "quarantine.jsonl"
            self.assertFalse(
                quarantine_path.exists() and quarantine_path.stat().st_size > 0,
                "no findings should have been quarantined on a clean run",
            )

            self.assertTrue((state_dir / "closure-rate.jsonl").exists())

    def test_poisoned_persona_quarantined(self):
        with tempfile.TemporaryDirectory() as d:
            state_dir = Path(d)
            sink = JsonlFindingsSink(state_dir)
            with FixtureSandbox() as sandbox:
                sup = Supervisor(
                    sandbox=sandbox,
                    sink=sink,
                    personas=[_PoisonedPersona()],
                )
                results = sup.run_round_robin()

            self.assertEqual(results[0].findings_filed, 0)
            self.assertEqual(results[0].findings_quarantined, 1)

            with (state_dir / "quarantine.jsonl").open() as f:
                qrows = [json.loads(line) for line in f if line.strip()]
            self.assertEqual(len(qrows), 1)
            self.assertIn("canary-literal-detected", qrows[0]["reason"])
            self.assertIn("REDACTED(canary_kind=aws)", qrows[0]["reason"])
            # Defense-in-depth: the quarantine row's serialized form
            # must not contain the offending literal — sink discipline.
            qline = (state_dir / "quarantine.jsonl").read_text()
            self.assertNotIn("AKIA_BEACON_0", qline)
            f_path = state_dir / "findings.jsonl"
            if f_path.exists():
                self.assertEqual(f_path.read_text().strip(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
