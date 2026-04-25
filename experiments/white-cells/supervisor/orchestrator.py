"""Supervisor orchestrator — dispatches personas, validates output, sinks findings.

Run from the repo root:

    python3 experiments/white-cells/supervisor/orchestrator.py preflight
    python3 experiments/white-cells/supervisor/orchestrator.py run --persona research_poisoner
    python3 experiments/white-cells/supervisor/orchestrator.py run --all

The directory `experiments/white-cells/` is intentionally hyphenated to
match the project's naming convention. Imports use a sys.path shim so
`supervisor`, `personas`, and `fixtures` resolve as top-level packages
without requiring an importable `white_cells` package name.

Phase 1 dispatches the two reusable-import persona stubs against the
in-process FixtureSandbox. Each finding is schema-validated, then
canary-post-filtered, then either sunk or quarantined. The closure-rate
counter is updated after every run.
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
from supervisor.closure_rate import ClosureRateCounter  # noqa: E402
from supervisor.sink import FindingsSink, JsonlFindingsSink  # noqa: E402
from fixtures.sandbox import FixtureSandbox  # noqa: E402
from personas.base import Persona  # noqa: E402
from personas.research_poisoner import ResearchPoisoner  # noqa: E402
from personas.multi_turn_crescendo import MultiTurnCrescendo  # noqa: E402

_TAXONOMY_PATH = _WC_ROOT / "atlas-taxonomy.yaml"
_DEFAULT_STATE_DIR = _WC_ROOT / "state"


@dataclass(frozen=True)
class RunResult:
    persona: str
    started_utc: str
    finished_utc: str
    findings_filed: int
    findings_quarantined: int
    findings_invalid: int
    error: str | None = None


def _now_utc_iso(clock: Callable[[], datetime]) -> str:
    return clock().strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Supervisor:
    def __init__(
        self,
        sandbox: FixtureSandbox,
        sink: FindingsSink,
        personas: list[Persona],
        *,
        taxonomy_path: Path = _TAXONOMY_PATH,
        clock: Callable[[], datetime] = _utcnow,
        closure_counter: ClosureRateCounter | None = None,
    ):
        self.sandbox = sandbox
        self.sink = sink
        self.personas = {p.name: p for p in personas}
        self.taxonomy = schema.load_taxonomy(taxonomy_path)
        self.clock = clock
        self._closure_counter = closure_counter

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

        filed = quarantined = invalid = 0
        err: str | None = None

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

                self.sink.write(finding)
                filed += 1
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
            error=err,
        )
        if self._closure_counter is not None:
            self._closure_counter.record_run(self.clock())
        return result

    def run_round_robin(self) -> list[RunResult]:
        return [self.run_once(name) for name in self.personas]


def _build_default(state_dir: Path) -> tuple[Supervisor, FixtureSandbox]:
    sandbox = FixtureSandbox()
    sink = JsonlFindingsSink(state_dir)
    counter = ClosureRateCounter(state_dir)
    sup = Supervisor(
        sandbox=sandbox,
        sink=sink,
        personas=[ResearchPoisoner(), MultiTurnCrescendo()],
        closure_counter=counter,
    )
    return sup, sandbox


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="white-cells-supervisor")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("preflight", help="assert no production-credential env vars set")

    p_run = sub.add_parser("run", help="dispatch one or all persona(s)")
    g = p_run.add_mutually_exclusive_group(required=True)
    g.add_argument("--persona", help="persona name to dispatch")
    g.add_argument("--all", action="store_true", help="round-robin all personas")
    p_run.add_argument("--state-dir", default=str(_DEFAULT_STATE_DIR))

    args = parser.parse_args(argv)

    if args.cmd == "preflight":
        preflight.assert_clean_env()
        print("preflight: ok (no credential env vars detected)")
        return 0

    if args.cmd == "run":
        preflight.assert_clean_env()
        state_dir = Path(args.state_dir)
        sup, sandbox = _build_default(state_dir)
        with sandbox:
            results = (
                sup.run_round_robin()
                if args.all
                else [sup.run_once(args.persona)]
            )
        for r in results:
            print(
                f"persona={r.persona} filed={r.findings_filed} "
                f"quarantined={r.findings_quarantined} invalid={r.findings_invalid} "
                f"error={r.error or '-'}"
            )
        return 1 if any(r.error for r in results) else 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
