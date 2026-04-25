#!/usr/bin/env python3
"""Phase-2/3 unit + e2e tests for White Cells.

Stdlib unittest, mirrors `test_white_cells.py` style. From the repo root:

    python3 experiments/white-cells/tests/test_white_cells_phase_2_3.py

Coverage:

    - BeaconBurnerProbeTest      -- 4 probes hit/miss
    - ZoneClimberProbeTest       -- 4 probes against fixture agents
    - ReflexSmugglerProbeTest    -- 5 probes; AST-lint sanity
    - HookFuzzerMutationTest     -- 6 mutation strategies; snapshot loader
    - AutoTriageTest             -- triage record schema, slug + seq
    - DeepTeamFallbackTest       -- ResearchPoisoner gracefully stubs
    - PersonaIsolationTest       -- guard refuses forbidden paths
    - EndToEndPhase23Test        -- full supervisor run; >=1 finding/persona
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Make the experiment's top-level packages importable.
_HERE = Path(__file__).resolve().parent
_WC_ROOT = _HERE.parent
if str(_WC_ROOT) not in sys.path:
    sys.path.insert(0, str(_WC_ROOT))

from supervisor import canary_filter, schema  # noqa: E402
from supervisor.auto_triage import (  # noqa: E402
    build_triage_record,
    emit_triage_json,
    _slug,
    _next_seq,
)
from supervisor.closure_rate import ClosureRateCounter  # noqa: E402
from supervisor.isolation import (  # noqa: E402
    PersonaSandbox,
    PersonaIsolationError,
    scan_finding_paths,
    _looks_like_fs_path,
)
from supervisor.orchestrator import Supervisor  # noqa: E402
from supervisor.sink import JsonlFindingsSink  # noqa: E402
from fixtures.sandbox import FixtureSandbox  # noqa: E402

from personas.beacon_burner import BeaconBurnerPersona  # noqa: E402
from personas.hook_fuzzer import (  # noqa: E402
    HookFuzzerPersona,
    MUTATIONS,
    SEED_COMMANDS,
    matches_any_deny,
    load_deny_patterns,
    snapshot_settings,
    mutate_case_variation,
    mutate_whitespace_injection,
    mutate_unicode_homoglyph,
    mutate_base64_wrap,
    mutate_command_chaining,
    mutate_shell_substitution,
)
from personas.reflex_smuggler import (  # noqa: E402
    ReflexSmugglerPersona,
    ast_lint,
    _build_eval_bypass_source,
    _hot_path_budget_violated,
)
from personas.research_poisoner import ResearchPoisoner  # noqa: E402
from personas.zone_climber import ZoneClimberPersona  # noqa: E402


# Match the taxonomy used in the production atlas-taxonomy.yaml.
_TAXONOMY = schema.load_taxonomy(_WC_ROOT / "atlas-taxonomy.yaml")


def _validate_each(findings: list, *, expected_persona: str) -> list[str]:
    """Helper: validate every finding in the list. Returns the list of
    schema-validation errors (empty list = all valid)."""
    errors: list[str] = []
    for f in findings:
        ok, reason = schema.validate(
            f, expected_persona=expected_persona, atlas_ttps=_TAXONOMY
        )
        if not ok:
            errors.append(f"{f.get('title', '<no title>')}: {reason}")
    return errors


class BeaconBurnerProbeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bb-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.persona = BeaconBurnerPersona(fixture_root=Path(self.tmp))

    def test_persona_name_and_engine(self):
        self.assertEqual(self.persona.name, "beacon_burner")
        self.assertEqual(self.persona.engine, "stdlib")

    def test_canary_burn_either_passes_or_files_finding(self):
        out = self.persona.canary_burn()
        # Either property holds (no finding) or finding is schema-valid.
        self.assertEqual(_validate_each(out, expected_persona="beacon_burner"), [])

    def test_cross_surface_replay_schema_valid(self):
        out = self.persona.cross_surface_replay()
        self.assertEqual(_validate_each(out, expected_persona="beacon_burner"), [])

    def test_staleness_clock_skew_fires(self):
        out = self.persona.staleness_clock_skew()
        self.assertEqual(len(out), 1, "stale fixture should always produce a finding")
        self.assertIn("staleness", out[0]["title"])
        self.assertEqual(_validate_each(out, expected_persona="beacon_burner"), [])

    def test_registry_strip_always_files_finding(self):
        out = self.persona.registry_strip()
        self.assertGreaterEqual(len(out), 1)
        self.assertEqual(_validate_each(out, expected_persona="beacon_burner"), [])

    def test_attack_yields_at_least_one_finding(self):
        sandbox = object()  # Beacon-Burner ignores the sandbox arg
        findings = list(self.persona.attack(sandbox))
        self.assertGreater(len(findings), 0)
        self.assertEqual(
            _validate_each(findings, expected_persona="beacon_burner"), []
        )


class ZoneClimberProbeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zc-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.persona = ZoneClimberPersona(fixture_root=Path(self.tmp))

    def test_persona_name(self):
        self.assertEqual(self.persona.name, "zone_climber")

    def test_scope_widening_files_finding(self):
        out = self.persona.scope_widening()
        self.assertGreaterEqual(len(out), 1)
        self.assertEqual(_validate_each(out, expected_persona="zone_climber"), [])

    def test_cross_zone_whisper_files_finding(self):
        out = self.persona.cross_zone_whisper()
        self.assertGreaterEqual(len(out), 1)
        self.assertEqual(_validate_each(out, expected_persona="zone_climber"), [])

    def test_mcp_scope_leak_either_clean_or_finding(self):
        out = self.persona.mcp_scope_leak()
        # Always emits exactly one finding (info or high).
        self.assertEqual(len(out), 1)
        self.assertEqual(_validate_each(out, expected_persona="zone_climber"), [])

    def test_child_widening_always_fires(self):
        out = self.persona.child_widening_via_delegation()
        self.assertEqual(len(out), 1)
        self.assertIn("child-widening", out[0]["title"])
        self.assertEqual(_validate_each(out, expected_persona="zone_climber"), [])

    def test_attack_yields_findings(self):
        findings = list(self.persona.attack(object()))
        self.assertGreaterEqual(len(findings), 4)


class ReflexSmugglerProbeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rs-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.persona = ReflexSmugglerPersona(fixture_root=Path(self.tmp))

    def test_ast_lint_catches_top_level_banned_import(self):
        violations = ast_lint("import anthropic\n")
        self.assertTrue(any("banned import" in v for v in violations))

    def test_ast_lint_catches_from_banned_import(self):
        violations = ast_lint("from openai import OpenAI\n")
        self.assertTrue(any("banned" in v for v in violations))

    def test_ast_lint_catches_banned_constructor(self):
        violations = ast_lint("client = Anthropic()\n")
        self.assertTrue(any("banned constructor" in v for v in violations))

    def test_ast_lint_catches_endpoint_string(self):
        violations = ast_lint('url = "https://api.anthropic.com/v1/messages"\n')
        self.assertTrue(any("banned endpoint" in v for v in violations))

    def test_ast_lint_catches_dynamic_import_literal(self):
        src = (
            "import importlib\n"
            'm = importlib.import_module("anthropic")\n'
        )
        violations = ast_lint(src)
        self.assertTrue(any("dynamic import" in v for v in violations))

    def test_ast_lint_misses_dynamic_import_variable(self):
        """The variable-arg case is the documented blind spot."""
        src = (
            "import importlib\n"
            'name = "anthrop" + "ic"\n'
            "m = importlib.import_module(name)\n"
        )
        violations = ast_lint(src)
        # No violation for "anthropic" string-arg detection -- by design.
        self.assertFalse(any("anthropic" in v for v in violations))

    def test_eval_bypass_source_assembled(self):
        src = _build_eval_bypass_source()
        # Source string contains both halves of the smuggling primitive.
        self.assertIn("hot_path", src)
        # AST parses cleanly.
        import ast as _ast
        _ast.parse(src)

    def test_time_budget_helper(self):
        self.assertTrue(_hot_path_budget_violated(5, 6))
        self.assertFalse(_hot_path_budget_violated(5, 5))
        self.assertFalse(_hot_path_budget_violated(5, 4))

    def test_attack_yields_findings(self):
        findings = list(self.persona.attack(object()))
        self.assertGreaterEqual(len(findings), 5)
        self.assertEqual(
            _validate_each(findings, expected_persona="reflex_smuggler"), []
        )


class HookFuzzerMutationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="hf-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _stub_snapshot(self) -> Path:
        snap = Path(self.tmp) / "settings-snapshot.json"
        snap.write_text(
            json.dumps(
                {
                    "permissions": {
                        "deny": [
                            "Bash(rm -rf /*)",
                            "Bash(rm -rf ~*)",
                            "Bash(curl * | sh*)",
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        return snap

    def test_case_variation(self):
        self.assertEqual(mutate_case_variation("rm -rf /"), "Rm -rf /")

    def test_whitespace_injection(self):
        self.assertEqual(mutate_whitespace_injection("rm -rf /"), "rm  -rf /")

    def test_unicode_homoglyph_changes_byte_form(self):
        out = mutate_unicode_homoglyph("rm -rf /")
        self.assertNotEqual(out, "rm -rf /")
        # First "m" should now be the Cyrillic homoglyph.
        self.assertIn("м", out)

    def test_base64_wrap_round_trip(self):
        out = mutate_base64_wrap("rm -rf /")
        # Must contain the base64-pipeline sentinels.
        self.assertIn("base64 -d", out)
        self.assertIn("echo", out)

    def test_command_chaining(self):
        self.assertEqual(
            mutate_command_chaining("rm -rf /"), "echo ok && rm -rf /"
        )

    def test_shell_substitution(self):
        self.assertEqual(
            mutate_shell_substitution("rm -rf /"), "$(echo rm) -rf /"
        )

    def test_load_deny_patterns_filters_to_bash(self):
        snap = self._stub_snapshot()
        # Add a non-Bash entry; loader must filter it out.
        data = json.loads(snap.read_text())
        data["permissions"]["deny"].append("Read(**/.env)")
        snap.write_text(json.dumps(data))
        loaded = load_deny_patterns(snap)
        self.assertTrue(all(p.startswith("Bash(") for p in loaded))

    def test_seed_matches_then_mutation_might_bypass(self):
        snap = self._stub_snapshot()
        deny = load_deny_patterns(snap)
        seed_match, _ = matches_any_deny("rm -rf /", deny)
        self.assertTrue(seed_match)
        # Case variation should bypass the lowercase-only fnmatch rule.
        bypassed_case, _ = matches_any_deny("Rm -rf /", deny)
        self.assertFalse(bypassed_case)

    def test_persona_emits_findings_against_stub_snapshot(self):
        snap = self._stub_snapshot()
        persona = HookFuzzerPersona(snapshot_path=snap)
        findings = list(persona.attack(object()))
        self.assertGreaterEqual(len(findings), 1)
        self.assertEqual(
            _validate_each(findings, expected_persona="hook_fuzzer"), []
        )

    def test_snapshot_settings_writes_stub_when_src_missing(self):
        dst = Path(self.tmp) / "snap.json"
        # Source guaranteed not to exist.
        result = snapshot_settings(Path("/__nope__/settings.json"), dst)
        self.assertEqual(result, dst)
        self.assertTrue(dst.exists())
        data = json.loads(dst.read_text())
        self.assertIn("permissions", data)


class AutoTriageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="at-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _good_finding(self) -> dict:
        return {
            "persona": "beacon_burner",
            "produced_utc": "2026-04-25T18:00:00+00:00",
            "atlas_ttp": ["AML.T0049"],
            "severity": "high",
            "title": "test/with weird Chars!! and  spaces",
            "summary": "summary",
            "reproduction": "repro",
            "fixture_hits": [],
            "suggested_closure": "hook-rule",
            "schema_version": 1,
        }

    def test_slug(self):
        self.assertEqual(_slug("Hello, World!"), "hello-world")
        self.assertTrue(len(_slug("x" * 200)) <= 40)

    def test_next_seq_initial(self):
        self.assertEqual(_next_seq(Path(self.tmp)), 1)

    def test_next_seq_continues(self):
        (Path(self.tmp) / "F0007-x-y.json").write_text("{}")
        self.assertEqual(_next_seq(Path(self.tmp)), 8)

    def test_build_record_schema(self):
        r = build_triage_record(self._good_finding(), finding_id="wc-abc", seq=42)
        self.assertEqual(r["seq"], 42)
        self.assertEqual(r["severity"], "HIGH")
        self.assertEqual(r["persona"], "beacon_burner")
        self.assertIn("[white-cells]", r["gh_issue"]["title"])
        self.assertIn("HIGH", r["gh_issue"]["title"])
        self.assertIn("severity-high", r["gh_issue"]["labels"])

    def test_emit_triage_json_writes_file(self):
        path = emit_triage_json(
            self._good_finding(),
            finding_id="wc-xyz",
            findings_dir=Path(self.tmp),
        )
        self.assertTrue(path.exists())
        record = json.loads(path.read_text())
        self.assertEqual(record["source_finding_id"], "wc-xyz")
        # Filename has F0001 prefix.
        self.assertTrue(path.name.startswith("F0001"))

    def test_severity_mapping(self):
        for sev_in, sev_out in (
            ("info", "LOW"),
            ("low", "LOW"),
            ("medium", "MEDIUM"),
            ("high", "HIGH"),
            ("critical", "HIGH"),
        ):
            f = self._good_finding()
            f["severity"] = sev_in
            r = build_triage_record(f, finding_id="x", seq=1)
            self.assertEqual(r["severity"], sev_out)


class DeepTeamFallbackTest(unittest.TestCase):
    """Research-Poisoner must gracefully fall back to Phase 1 stub when
    deepteam is not installed (the common case in CI)."""

    def test_research_poisoner_yields_finding_without_deepteam(self):
        persona = ResearchPoisoner()
        # Either path: with deepteam (real probes) or without (stub).
        # Either way, schema-valid findings must result.
        with FixtureSandbox() as sandbox:
            findings = list(persona.attack(sandbox))
        self.assertGreaterEqual(len(findings), 1)
        for f in findings:
            ok, err = schema.validate(
                f, expected_persona="research_poisoner", atlas_ttps=_TAXONOMY
            )
            self.assertTrue(ok, err)

    def test_load_engine_returns_stub_when_deepteam_absent(self):
        persona = ResearchPoisoner()
        if persona._deepteam is None:
            engine = persona._load_engine()
            self.assertIsNotNone(engine)
            self.assertTrue(hasattr(engine, "run"))


class PersonaIsolationTest(unittest.TestCase):
    def test_looks_like_fs_path(self):
        self.assertTrue(_looks_like_fs_path("/home/user/x"))
        self.assertTrue(_looks_like_fs_path("/tmp/abc"))
        # URL paths returned by mock-Notion etc.
        self.assertFalse(_looks_like_fs_path("/v1/users/me"))
        self.assertFalse(_looks_like_fs_path("/repos/x/y"))
        self.assertFalse(_looks_like_fs_path(""))

    def test_sandbox_create_and_cleanup(self):
        with PersonaSandbox.create("test-persona") as s:
            self.assertTrue(s.root.exists())
            saved = s.root
        # tmpdir cleaned on exit.
        self.assertFalse(saved.exists())

    def test_path_inside_sandbox_allowed(self):
        with PersonaSandbox.create("test") as s:
            p = s.root / "myfile"
            p.write_text("ok")
            self.assertTrue(s.is_path_allowed(p))

    def test_forbidden_paths_refused(self):
        with PersonaSandbox.create("test") as s:
            # Operator's home secrets MUST be refused.
            self.assertFalse(s.is_path_allowed(Path.home() / ".ssh" / "id_rsa"))
            self.assertFalse(
                s.is_path_allowed(Path.home() / ".claude" / "settings.json")
            )

    def test_path_outside_allowed_roots_refused(self):
        with PersonaSandbox.create("test") as s:
            self.assertFalse(s.is_path_allowed("/usr/bin/ls"))
            self.assertFalse(s.is_path_allowed("/etc/passwd"))

    def test_assert_path_allowed_raises(self):
        with PersonaSandbox.create("test") as s:
            with self.assertRaises(PersonaIsolationError):
                s.assert_path_allowed("/etc/passwd")

    def test_scan_finding_paths_skips_url_paths(self):
        finding = {
            "title": "x",
            "summary": "y",
            "reproduction": "/home/user/x",
            "fixture_hits": [
                {"service": "mock-notion", "path": "/v1/users/me", "method": "GET"}
            ],
        }
        paths = scan_finding_paths(finding)
        # /v1/users/me lives in fixture_hits, which scan skips entirely;
        # /home/user/x is in reproduction (free text) so picked up.
        self.assertIn("/home/user/x", paths)
        # Ensure no value extracted from fixture_hits.
        for p in paths:
            self.assertNotIn("/v1/users/me", p)


class EndToEndPhase23Test(unittest.TestCase):
    def test_full_supervisor_run_covers_all_personas(self):
        with tempfile.TemporaryDirectory() as d:
            state_dir = Path(d) / "state"
            findings_dir = Path(d) / "findings"
            sink = JsonlFindingsSink(state_dir)
            counter = ClosureRateCounter(state_dir)
            personas = [
                BeaconBurnerPersona(fixture_root=Path(d) / "bb-fix"),
                ZoneClimberPersona(fixture_root=Path(d) / "zc-fix"),
                ReflexSmugglerPersona(fixture_root=Path(d) / "rs-fix"),
                HookFuzzerPersona(snapshot_path=Path(d) / "snap.json"),
                ResearchPoisoner(),
            ]
            with FixtureSandbox() as sandbox:
                sup = Supervisor(
                    sandbox=sandbox,
                    sink=sink,
                    personas=personas,
                    closure_counter=counter,
                    findings_dir=findings_dir,
                )
                results = [sup.run_once(p.name) for p in personas]

            # Every persona must produce >=1 finding on the planted-vuln
            # fixture. Findings must be schema-valid (sink wrote them).
            for r in results:
                self.assertIsNone(r.error, f"{r.persona}: {r.error}")
                self.assertGreaterEqual(
                    r.findings_filed,
                    1,
                    f"{r.persona} produced 0 findings",
                )
                self.assertEqual(r.findings_invalid, 0)
                # Triage emitted matches filed.
                self.assertEqual(r.triage_emitted, r.findings_filed)

            # Triage JSONs landed.
            triage_files = sorted(findings_dir.glob("F*.json"))
            self.assertGreaterEqual(len(triage_files), len(personas))

            # No real canaries leaked into any triage file (defense in
            # depth — every supervisor-output file is canary-clean).
            for tf in triage_files:
                txt = tf.read_text()
                # Defense in depth: real-shape AKIA canary literals must
                # never appear in triage output.
                for kind, pat in (
                    ("aws", r"AKIA_BEACON_[0-9A-F]{20}"),
                    ("attrib", r"beacon-attrib-[a-z0-9-]+-[A-Za-z0-9]{8}"),
                ):
                    import re as _re
                    self.assertIsNone(
                        _re.search(pat, txt),
                        f"{tf.name} contains real-shape {kind} canary",
                    )

    def test_isolation_violation_quarantines(self):
        """Build a tiny synthetic persona that emits a finding mentioning
        a forbidden path (~/.claude/settings.json). Supervisor must
        quarantine it with an isolation-violation reason."""

        class _BadPersona:
            name = "bad_persona"
            engine = "stdlib"

            def attack(self, sandbox):
                yield {
                    "persona": self.name,
                    "produced_utc": "2026-04-25T18:00:00+00:00",
                    "atlas_ttp": ["AML.T0049"],
                    "severity": "high",
                    "title": "I attempted to exfil settings.json",
                    "summary": (
                        f"would have read {Path.home() / '.claude' / 'settings.json'}"
                    ),
                    "reproduction": str(Path.home() / ".claude" / "settings.json"),
                    "fixture_hits": [],
                    "suggested_closure": "doc-note",
                    "schema_version": 1,
                }

        with tempfile.TemporaryDirectory() as d:
            sink = JsonlFindingsSink(Path(d) / "state")
            with FixtureSandbox() as sandbox:
                sup = Supervisor(
                    sandbox=sandbox,
                    sink=sink,
                    personas=[_BadPersona()],
                    findings_dir=Path(d) / "findings",
                )
                result = sup.run_once("bad_persona")

            self.assertEqual(result.findings_filed, 0)
            self.assertEqual(result.findings_isolation_violation, 1)
            qpath = Path(d) / "state" / "quarantine.jsonl"
            self.assertTrue(qpath.exists())
            qrow = json.loads(qpath.read_text().splitlines()[0])
            self.assertIn("persona-isolation-violation", qrow["reason"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
