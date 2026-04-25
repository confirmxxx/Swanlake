"""Supervisor orchestrator — dispatches personas, validates output, sinks findings.

Run from the repo root:

    python3 experiments/white-cells/supervisor/orchestrator.py preflight
    python3 experiments/white-cells/supervisor/orchestrator.py run --persona research_poisoner
    python3 experiments/white-cells/supervisor/orchestrator.py run --all
    python3 experiments/white-cells/supervisor/orchestrator.py run --all-phase-2
    python3 experiments/white-cells/supervisor/orchestrator.py run --all-phase-3

The directory `experiments/white-cells/` is intentionally hyphenated to
match the project's naming convention. Imports use a sys.path shim so
`supervisor`, `personas`, and `fixtures` resolve as top-level packages
without requiring an importable `white_cells` package name.

Phase 2/3 dispatches the four Swanlake-unique personas + the upgraded
Research-Poisoner (DeepTeam-backed when installed, Phase 1 stub when
not) + the Phase 1 Multi-Turn Crescendo stub. Each finding is:
  1. schema-validated
  2. canary-post-filtered
  3. persona-isolation-guarded (path references must lie inside the
     persona's allowed roots)
  4. emitted to the auto-triage findings/ directory
  5. written to the sink

Closure-rate counter is updated after every run.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# Resolve experiments/white-cells/ as a package root for top-level imports.
_WC_ROOT = Path(__file__).resolve().parent.parent
if str(_WC_ROOT) not in sys.path:
    sys.path.insert(0, str(_WC_ROOT))

from supervisor import canary_filter, preflight, schema  # noqa: E402
from supervisor.auto_triage import emit_triage_json  # noqa: E402
from supervisor.closure_rate import ClosureRateCounter  # noqa: E402
from supervisor.isolation import (  # noqa: E402
    PersonaSandbox,
    scan_finding_paths,
)
from supervisor.sink import FindingsSink, JsonlFindingsSink  # noqa: E402
from fixtures.sandbox import FixtureSandbox  # noqa: E402
from personas.base import Persona  # noqa: E402
from personas.beacon_burner import BeaconBurnerPersona  # noqa: E402
from personas.hook_fuzzer import HookFuzzerPersona  # noqa: E402
from personas.multi_turn_crescendo import MultiTurnCrescendo  # noqa: E402
from personas.reflex_smuggler import ReflexSmugglerPersona  # noqa: E402
from personas.research_poisoner import ResearchPoisoner  # noqa: E402
from personas.zone_climber import ZoneClimberPersona  # noqa: E402

_TAXONOMY_PATH = _WC_ROOT / "atlas-taxonomy.yaml"
_DEFAULT_STATE_DIR = _WC_ROOT / "state"
_DEFAULT_FINDINGS_DIR = _WC_ROOT / "findings"


# Persona phase classification — used by --all-phase-N flags.
_PHASE_2_PERSONAS = ("beacon_burner", "zone_climber", "reflex_smuggler")
_PHASE_3_PERSONAS = ("hook_fuzzer", "research_poisoner")
_PHASE_1_STUBS = ("multi_turn_crescendo",)
_ALL_PERSONAS = _PHASE_2_PERSONAS + _PHASE_3_PERSONAS + _PHASE_1_STUBS


@dataclass(frozen=True)
class RunResult:
    persona: str
    started_utc: str
    finished_utc: str
    findings_filed: int
    findings_quarantined: int
    findings_invalid: int
    findings_isolation_violation: int = 0
    triage_emitted: int = 0
    error: str | None = None


def _now_utc_iso(clock: Callable[[], datetime]) -> str:
    return clock().strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _build_default_personas() -> list:
    """Construct one of each persona. Order matters for run_round_robin
    stability (tests + closure-rate counter rely on it)."""
    return [
        BeaconBurnerPersona(),
        ZoneClimberPersona(),
        ReflexSmugglerPersona(),
        HookFuzzerPersona(),
        ResearchPoisoner(),
        MultiTurnCrescendo(),
    ]


class Supervisor:
    def __init__(
        self,
        sandbox: FixtureSandbox,
        sink: FindingsSink,
        personas: list,
        *,
        taxonomy_path: Path = _TAXONOMY_PATH,
        clock: Callable[[], datetime] = _utcnow,
        closure_counter: ClosureRateCounter | None = None,
        findings_dir: Path | None = None,
        emit_triage: bool = True,
    ):
        self.sandbox = sandbox
        self.sink = sink
        self.personas = {p.name: p for p in personas}
        self.taxonomy = schema.load_taxonomy(taxonomy_path)
        self.clock = clock
        self._closure_counter = closure_counter
        self._findings_dir = findings_dir or _DEFAULT_FINDINGS_DIR
        self._emit_triage = emit_triage

    def run_once(self, persona_name: str) -> RunResult:
        if persona_name not in self.personas:
            return RunResult(
                persona=persona_name,
                started_utc=_now_utc_iso(self.clock),
                finished_utc=_now_utc_iso(self.clock),
                findings_filed=0,
                findings_quarantined=0,
                findings_invalid=0,
                error=f"unknown persona {persona_name!r}",
            )

        persona = self.personas[persona_name]
        started = _now_utc_iso(self.clock)

        filed = quarantined = invalid = isolation_viol = triage_count = 0
        err: str | None = None

        # Build a per-persona isolation sandbox so we can reason about
        # what paths are legal for THIS persona to name.
        with PersonaSandbox.create(persona.name) as iso:
            try:
                for finding in persona.attack(self.sandbox):
                    ok, reason = schema.validate(
                        finding,
                        expected_persona=persona.name,
                        atlas_ttps=self.taxonomy,
                    )
                    if not ok:
                        invalid += 1
                        self.sink.invalid(finding, reason)
                        continue

                    if not canary_filter.is_clean(finding):
                        kinds = canary_filter.detect_canaries(finding)
                        quarantined += 1
                        self.sink.quarantine(
                            finding,
                            reason="canary-literal-detected: "
                            + canary_filter.redacted_kinds(kinds),
                        )
                        continue

                    # Persona-isolation guard: every absolute path the
                    # finding mentions must lie inside the persona's
                    # allowed roots (its tmpdir + the experiment's
                    # fixture tree). A path under ~/.claude/, ~/projects/
                    # outside Swanlake/, or the operator's vault is a
                    # quarantine-worthy isolation violation.
                    iso_violation = self._check_isolation(finding, iso)
                    if iso_violation is not None:
                        isolation_viol += 1
                        self.sink.quarantine(
                            finding,
                            reason=f"persona-isolation-violation: {iso_violation}",
                        )
                        continue

                    fid = self.sink.write(finding)
                    filed += 1

                    if self._emit_triage:
                        emit_triage_json(
                            finding,
                            finding_id=fid,
                            findings_dir=self._findings_dir,
                        )
                        triage_count += 1
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"

        finished = _now_utc_iso(self.clock)
        result = RunResult(
            persona=persona.name,
            started_utc=started,
            finished_utc=finished,
            findings_filed=filed,
            findings_quarantined=quarantined,
            findings_invalid=invalid,
            findings_isolation_violation=isolation_viol,
            triage_emitted=triage_count,
            error=err,
        )
        if self._closure_counter is not None:
            self._closure_counter.record_run(self.clock())
        return result

    def run_round_robin(self) -> list[RunResult]:
        return [self.run_once(name) for name in self.personas]

    @staticmethod
    def _check_isolation(finding: dict, iso: PersonaSandbox) -> str | None:
        """Return None if every absolute-looking path in the finding
        lies inside the persona's allowed roots; else return a short
        violation summary suitable for the quarantine reason."""
        for candidate in scan_finding_paths(finding):
            if not iso.is_path_allowed(candidate):
                return f"path={candidate!r} outside persona allowed roots"
        return None


# ----- CLI plumbing -----------------------------------------------------


def _build_default(state_dir: Path, findings_dir: Path | None = None) -> tuple[Supervisor, FixtureSandbox]:
    sandbox = FixtureSandbox()
    sink = JsonlFindingsSink(state_dir)
    counter = ClosureRateCounter(state_dir)
    sup = Supervisor(
        sandbox=sandbox,
        sink=sink,
        personas=_build_default_personas(),
        closure_counter=counter,
        findings_dir=findings_dir or _DEFAULT_FINDINGS_DIR,
    )
    return sup, sandbox


def _resolve_dispatch(args, available: dict) -> list[str]:
    """Translate CLI flags into the ordered list of persona names to
    dispatch. Order: explicit --persona; then --all-phase-2 / --all-phase-3
    / --all. Mutually exclusive."""
    if args.persona:
        return [args.persona]
    if getattr(args, "all_phase_2", False):
        return [n for n in _PHASE_2_PERSONAS if n in available]
    if getattr(args, "all_phase_3", False):
        return [n for n in _PHASE_3_PERSONAS if n in available]
    if args.all:
        # Phase 2 + Phase 3 + Phase 1 stubs, in canonical order.
        return [n for n in _ALL_PERSONAS if n in available]
    raise SystemExit("must pass --persona or --all or --all-phase-2/3")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="white-cells-supervisor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("preflight", help="assert no production-credential env vars set")

    p_run = sub.add_parser("run", help="dispatch one or more persona(s)")
    g = p_run.add_mutually_exclusive_group(required=True)
    g.add_argument("--persona", help="persona name to dispatch (single)")
    g.add_argument("--all", action="store_true", help="all personas (Phase 1+2+3)")
    g.add_argument(
        "--all-phase-2",
        action="store_true",
        dest="all_phase_2",
        help="Phase 2 personas only (beacon_burner, zone_climber, reflex_smuggler)",
    )
    g.add_argument(
        "--all-phase-3",
        action="store_true",
        dest="all_phase_3",
        help="Phase 3 personas only (hook_fuzzer, research_poisoner DeepTeam upgrade)",
    )
    p_run.add_argument("--state-dir", default=str(_DEFAULT_STATE_DIR))
    p_run.add_argument("--findings-dir", default=str(_DEFAULT_FINDINGS_DIR))
    p_run.add_argument(
        "--no-triage",
        action="store_true",
        help="Skip auto-triage JSON emission (testing aid).",
    )

    args = parser.parse_args(argv)

    if args.cmd == "preflight":
        preflight.assert_clean_env()
        print("preflight: ok (no credential env vars detected)")
        return 0

    if args.cmd == "run":
        preflight.assert_clean_env()
        state_dir = Path(args.state_dir)
        findings_dir = Path(args.findings_dir)
        sup, sandbox = _build_default(state_dir, findings_dir)
        if args.no_triage:
            sup._emit_triage = False  # internal toggle; CLI escape hatch
        names = _resolve_dispatch(args, sup.personas)
        with sandbox:
            results = [sup.run_once(n) for n in names]
        for r in results:
            print(
                f"persona={r.persona} filed={r.findings_filed} "
                f"quarantined={r.findings_quarantined} "
                f"isolation_violation={r.findings_isolation_violation} "
                f"invalid={r.findings_invalid} "
                f"triage_emitted={r.triage_emitted} "
                f"error={r.error or '-'}"
            )
        return 1 if any(r.error for r in results) else 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
