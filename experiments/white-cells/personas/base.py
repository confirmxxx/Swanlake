"""Persona base contract.

Every White Cells persona implements:

  - `name` (class attribute) — short snake_case identifier; used as
    finding.persona and CLI dispatch key
  - `engine` (class attribute) — short label naming the upstream engine
    wrapped, or "stdlib" for hand-built personas
  - `attack(sandbox)` — generator yielding zero or more dict findings
    matching the v1 schema

Phase-1 personas are *stubs* around third-party engines (promptfoo /
DeepTeam, PyRIT). The `_load_engine()` method is the import boundary;
Phase 2/3 swap the stub for the real `import` + adapter without
touching the supervisor or the rest of the persona class.

Phase-2 personas (Beacon-Burner, Zone-Climber, Reflex-Smuggler) and
the Phase-3 Hook-Fuzzer use the `build_finding` helper to assemble
schema-valid v1 finding dicts without each persona re-implementing
the boilerplate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Protocol


class Persona(Protocol):
    name: str
    engine: str

    def attack(self, sandbox) -> Iterable[dict]:
        ...


def build_finding(
    *,
    persona: str,
    atlas_ttp: list[str],
    severity: str,
    title: str,
    summary: str,
    reproduction: str,
    fixture_hits: list[dict] | None = None,
    suggested_closure: str = "none",
    produced_utc: str | None = None,
) -> dict:
    """Assemble a v1-schema-shaped finding dict.

    Cuts every Phase 2/3 persona's boilerplate. Schema cap enforcement
    happens later in `supervisor.schema.validate`; this helper does NOT
    truncate strings — exceeding a cap is a bug in the persona.
    """
    if produced_utc is None:
        produced_utc = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S+00:00"
        )
    return {
        "persona": persona,
        "produced_utc": produced_utc,
        "atlas_ttp": list(atlas_ttp),
        "severity": severity,
        "title": title,
        "summary": summary,
        "reproduction": reproduction,
        "fixture_hits": list(fixture_hits or []),
        "suggested_closure": suggested_closure,
        "schema_version": 1,
    }


class _StubEngine:
    """Generic stub. Returns 1-2 deterministic, schema-valid findings.

    The real promptfoo / DeepTeam / PyRIT adapter will: (1) configure
    the engine with the persona's prompts/probes, (2) target the fixture
    sandbox URLs, (3) collect each engine's raw findings, (4) translate
    to the v1 schema. Phase 1 skipped (1)-(3) and emitted canned (4)-shape
    rows so the rest of the supervisor pipeline runs end-to-end.
    """

    def __init__(self, persona_name: str, ttp: str):
        self.persona_name = persona_name
        self.ttp = ttp

    def run(self, sandbox, *, produced_utc: str) -> list[dict]:
        # The persona uses sandbox URLs purely so the fixture_hits field
        # can name the path the persona "would have" hit. The stub does
        # not perform the HTTP call — that's reserved for the real engine
        # in Phase 2 (and verified end-to-end in EndToEndTest by exercising
        # the sandbox separately).
        _ = sandbox  # contract; unused in stub
        return [
            {
                "persona": self.persona_name,
                "produced_utc": produced_utc,
                "atlas_ttp": [self.ttp],
                "severity": "info",
                "title": f"stub finding from {self.persona_name}",
                "summary": (
                    f"Phase-1 stub finding. Real engine wiring is a Phase-2 "
                    f"deliverable; this row exercises the supervisor "
                    f"validation + sink pipeline end-to-end."
                ),
                "reproduction": (
                    "n/a — stub persona. Phase 2 reproduction will be the "
                    "engine's emitted attack trace."
                ),
                "fixture_hits": [
                    {"service": "mock-notion", "path": "/v1/users/me", "method": "GET"},
                ],
                "suggested_closure": "none",
                "schema_version": 1,
            }
        ]
