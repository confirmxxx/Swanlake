# White Cells — Phase 1 Spec

Continuous AI red team for the public Swanlake defense framework. **Alpha experiment.** Phase 1 is the reusable-import-only slice: fixture sandbox, supervisor orchestrator, two persona stubs that wrap MIT-licensed third-party engines (promptfoo, PyRIT), closure-rate counter, and ATLAS-taxonomy-tagged findings.

This document is the contract for the PR reviewer. If the implementation diverges from anything below, the implementation is wrong (or this doc is, in which case patch this first).

## Status: alpha, deliberately scoped

Phase 1 is one of three phases in `brain/White Cells.md`. The full design was queued (no third-party production use yet, no external bypass report); the operator pulled Phase 1 forward anyway. The queue rationale (revenue-path stagnation pattern) still applies — Phase 1 lands as an experiment, not a release-blocker, and the **kill criterion is mechanical**:

> If after 4 weeks the closure ratio (findings → real hardening artifact) is below 30%, kill the project. No "give it more time."

Phases 2 and 3 (the four Swanlake-unique personas, system-user isolation, egress firewall, adaptive scheduling) are intentionally absent from this PR.

## Directory layout

Every file this PR creates:

```
experiments/white-cells/
  SPEC.md                                   # this file
  README.md                                 # operator-facing intro + kill criterion
  ROUTINE-SPEC.md                           # Claude Routine spec for operator to wire later
  atlas-taxonomy.yaml                       # MITRE ATLAS TTP IDs personas may tag
  __init__.py                               # makes white_cells importable from repo root
  supervisor/
    __init__.py
    orchestrator.py                         # supervisor entrypoint + dispatch loop
    schema.py                               # persona-output JSON schema validator
    sink.py                                 # findings sink (Phase 1: local JSONL stub)
    closure_rate.py                         # closure-rate counter + CLI
    canary_filter.py                        # post-filter for canary literals in persona output
    preflight.py                            # no-credentials assertion
  personas/
    __init__.py
    base.py                                 # Persona ABC + dispatch contract
    research_poisoner.py                    # promptfoo import boundary stub
    multi_turn_crescendo.py                 # PyRIT import boundary stub
  fixtures/
    __init__.py
    mock_notion.py                          # http.server subclass — canned Notion responses
    mock_github.py                          # http.server subclass — canned GitHub responses
    mock_vercel.py                          # http.server subclass — canned Vercel responses
    sandbox.py                              # spins up all three on ephemeral ports
  state/
    .gitkeep                                # JSONL files written here at runtime, gitignored
  tests/
    __init__.py
    test_white_cells.py                     # all unit tests; one file matching repo style
```

State files (`state/closure-rate.jsonl`, `state/findings.jsonl`) are gitignored.

## Supervisor — API surface

`supervisor/orchestrator.py`

```python
class Supervisor:
    def __init__(
        self,
        sandbox: FixtureSandbox,
        sink: FindingsSink,
        personas: list[Persona],
        clock: Callable[[], datetime] = ...,  # injectable for tests
    ): ...

    def run_once(self, persona_name: str) -> RunResult:
        """Dispatch one persona against the sandbox. Validates output,
        canary-filters it, writes accepted findings to the sink, updates
        the closure-rate counter, returns a structured result."""

    def run_round_robin(self) -> list[RunResult]:
        """Phase 1: dispatches each persona in self.personas exactly once."""

@dataclass(frozen=True)
class RunResult:
    persona: str
    started_utc: str
    finished_utc: str
    findings_filed: int
    findings_quarantined: int       # canary-filter rejects
    findings_invalid: int           # schema-validation rejects
    error: str | None
```

Entry-point CLI:

```
python3 -m white_cells.supervisor.orchestrator run --persona <name>
python3 -m white_cells.supervisor.orchestrator run --all
python3 -m white_cells.supervisor.orchestrator preflight
```

`preflight` runs `preflight.py` standalone and exits non-zero if any production-credential env var is detected (see "Preflight" below).

## Persona output schema

Validated by `supervisor/schema.py`. Every persona emits zero or more findings as JSON objects matching:

```jsonc
{
  "persona": "research_poisoner",          // string, must equal dispatching persona name
  "produced_utc": "2026-04-25T18:00:00+00:00",  // ISO 8601, UTC, with offset
  "atlas_ttp": ["AML.T0051.000", "AML.T0048.002"],  // 1+ entries, each must exist in atlas-taxonomy.yaml
  "severity": "low",                       // one of: info, low, medium, high, critical
  "title": "<short title, <=120 chars>",
  "summary": "<plaintext, <=2000 chars>",  // model-readable; canary-filtered before sink
  "reproduction": "<plaintext, <=4000 chars>",  // commands or steps; canary-filtered
  "fixture_hits": [                        // which mock service(s) were exercised
    {"service": "mock-notion", "path": "/v1/pages/<id>", "method": "GET"}
  ],
  "suggested_closure": "hook-rule|deny-entry|fixture|doc-note|none",
  "schema_version": 1
}
```

Schema rules (enforced):

- All keys required. Unknown keys → reject.
- `persona` must match the dispatching persona name (the supervisor sets this expectation).
- `atlas_ttp` entries must each be a key in `atlas-taxonomy.yaml`. Unknown TTP IDs → reject.
- `severity` must be one of the five strings above.
- Any string field exceeding its byte cap → reject.
- `produced_utc` must parse as ISO 8601 with explicit UTC offset.
- `fixture_hits[].service` must be one of `mock-notion`, `mock-github`, `mock-vercel`.

After schema validation, `canary_filter.py` rejects any finding whose `summary` or `reproduction` matches the real-canary patterns from `defense-beacon`'s threat model:

- `AKIA_BEACON_[0-9A-F]{20}`
- `AIzaSy[A-Za-z0-9_\-]{30,}`
- `beacon-attrib-[a-z0-9-]+-[A-Za-z0-9]{8}`

A canary hit means the persona is compromised (or the fixture has been poisoned). The finding is **quarantined**, not written to the sink, and an alert row is appended to `state/quarantine.jsonl`.

## Fixture sandbox

`fixtures/sandbox.py` exposes:

```python
class FixtureSandbox:
    def __enter__(self) -> "FixtureSandbox": ...   # binds 3 ephemeral ports
    def __exit__(self, *a) -> None: ...            # shuts down all servers

    @property
    def notion_url(self) -> str: ...
    @property
    def github_url(self) -> str: ...
    @property
    def vercel_url(self) -> str: ...
```

Each mock service is a `http.server.BaseHTTPRequestHandler` subclass that returns a small canned-response table keyed by `(method, path-prefix)`. Responses are **deterministic** (same request → same body byte-for-byte) so tests can assert on them. No real third-party API is contacted.

Canned response set per service:

| Service | Canned routes |
|---|---|
| mock-notion | `GET /v1/pages/<id>`, `POST /v1/search`, `GET /v1/users/me` |
| mock-github | `GET /repos/<owner>/<repo>`, `GET /repos/<owner>/<repo>/issues`, `POST /repos/<owner>/<repo>/issues` |
| mock-vercel | `GET /v9/projects/<id>`, `GET /v6/deployments` |

Fixture bodies are obviously-fake JSON — no real-shaped canary literals, no real production IDs.

## Persona import boundary

The two Phase 1 personas wrap third-party MIT engines without installing them. The boundary pattern:

```python
# personas/research_poisoner.py
class ResearchPoisoner(Persona):
    name = "research_poisoner"
    engine = "promptfoo"

    def _load_engine(self):
        # TODO: wire promptfoo (Phase 2). Real impl will:
        #   import promptfoo  # noqa
        #   return promptfoo.RedTeam(...)
        # Phase 1: return a deterministic stub so the supervisor's wiring
        # is exercised end-to-end without the third-party dependency.
        return _StubEngine(self.name)

    def attack(self, sandbox: FixtureSandbox) -> Iterable[dict]:
        engine = self._load_engine()
        for finding in engine.run(sandbox):
            yield finding
```

`_StubEngine` returns 1–2 obviously-fake findings tagged with appropriate ATLAS TTPs. Same shape `multi_turn_crescendo.py` follows for PyRIT.

This means: Phase 2 will replace `_load_engine` body with the real `import` + adapter, and the rest of the pipeline (schema, canary filter, sink, closure rate) stays unchanged. The TODO comment names the package and the call site so the swap is mechanical.

We do **not** `pip install promptfoo` or `pyrit` in Phase 1. They are not declared in any project requirements file. Tests run on stdlib only.

## Closure-rate counter

`supervisor/closure_rate.py` writes append-only JSONL to `state/closure-rate.jsonl`. One row per supervisor run:

```jsonc
{
  "date": "2026-04-25T18:00:00+00:00",
  "findings_filed": 3,
  "findings_closed_to_artifact": 1,
  "ratio": 0.333
}
```

`findings_closed_to_artifact` is computed by reading `state/findings.jsonl`, filtering to findings older than 14 days that have a non-empty `closure_artifact` field. The supervisor does not auto-mark closure — closure is recorded by the operator out-of-band (`python3 -m white_cells.supervisor.closure_rate close <finding-id> <artifact-ref>`).

CLI:

```
python3 -m white_cells.supervisor.closure_rate report          # default: last 30d summary
python3 -m white_cells.supervisor.closure_rate report --window 7
python3 -m white_cells.supervisor.closure_rate close <finding-id> <artifact-ref>
python3 -m white_cells.supervisor.closure_rate kill-check     # exit 0 ok, exit 1 below 30% over 4 weeks
```

`kill-check` is the mechanical kill-criterion gate. The Routine the operator wires later runs it after every supervisor pass and surfaces the result.

## Preflight no-credentials assertion

`supervisor/preflight.py` enumerates env vars with these substrings (case-insensitive) and aborts if any non-empty match is found:

`GITHUB_TOKEN`, `GH_TOKEN`, `NOTION_TOKEN`, `NOTION_API_KEY`, `SUPABASE_*_KEY`, `SUPABASE_ACCESS_TOKEN`, `VERCEL_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `STRIPE_*_KEY`, `TG_BOT_TOKEN`, `TELEGRAM_BOT_TOKEN`.

Match is substring against the env var **name**, not the value. Match → write a redacted message naming the env var (no value), exit 2. The supervisor's `run_once` calls preflight before dispatching any persona.

## ATLAS taxonomy file

`atlas-taxonomy.yaml`. Hand-curated subset of MITRE ATLAS TTP IDs (`AML.T*`) relevant to Phase 1 personas:

- Each entry: `<TTP-ID>: <one-line description>`
- IDs and descriptions copied verbatim from atlas.mitre.org as of 2026-04-25
- The validator only checks that a persona's reported TTP exists in this map; it does not interpret the description

Phase 1 ships ~12 entries covering RAG poisoning, indirect prompt injection, multi-turn manipulation, and supply-chain TTPs. Phase 2 expands as the Swanlake-unique personas land.

## Test plan

`tests/test_white_cells.py`. Stdlib unittest, mirrors `defense-beacon/reference/tests/make_canaries_test.py` style.

Coverage:

| Class | Tests |
|---|---|
| `SchemaValidatorTest` | accepts a valid finding; rejects unknown key; rejects bad severity; rejects bad ATLAS TTP; rejects oversize summary; rejects missing key; rejects non-UTC timestamp; rejects mismatched persona name |
| `CanaryFilterTest` | passes clean finding; quarantines finding with AKIA_BEACON real-shape literal in summary; quarantines finding with attrib-shape literal in reproduction; **does not** false-positive on `AKIA_BEACON_TESTFIXTURE000000000000`; quarantines finding with Google-shape literal |
| `FixtureSandboxTest` | binds 3 ports without collision; mock-notion returns canned `/v1/users/me`; mock-github returns canned issues list; mock-vercel returns canned deployments; tearing down releases ports |
| `ClosureRateCounterTest` | empty state → ratio 0.0; one filed, zero closed → 0.0; two filed, one closed (>14 days) → 0.5; recent closure (<14 days) does not count; `kill-check` returns exit 1 when < 30% over the 4-week window; `kill-check` returns exit 0 when ≥ 30% |
| `PreflightTest` | empty env → ok; `GITHUB_TOKEN=fake` set → exit 2; case-insensitive match (`github_token=fake`) → exit 2; partial-match-only env var (`MY_GITHUB_TOKEN_FAKE_VAR`) → still flagged |
| `PersonaStubTest` | `ResearchPoisoner` yields ≥1 schema-valid finding from the stub engine; `MultiTurnCrescendo` yields ≥1 schema-valid finding; persona's `name` field on emitted finding matches the class attribute |
| `EndToEndTest` | start sandbox, run supervisor with both personas, verify findings.jsonl row count and closure-rate.jsonl row count both ≥ 1; verify quarantine.jsonl is empty for clean run; inject a poisoned finding into a stub engine and verify it lands in quarantine.jsonl, not findings.jsonl |

Total: ~30 test methods. Single file, `python3 experiments/white-cells/tests/test_white_cells.py` from repo root.

## CI integration

`.github/workflows/test.yml` gets one new step:

```yaml
- name: white-cells — Phase 1 unit tests
  run: python3 experiments/white-cells/tests/test_white_cells.py
```

All four existing test steps stay unchanged.

## Operator handoff

What the operator does **after** this PR merges (do not attempt these in the PR):

1. **Create the unprivileged Linux user** `whitecells` with no read access to `~/.claude/`, `~/projects/` outside `Swanlake/`, or the Obsidian vault. Requires sudo; out of scope here.
2. **Wire the egress firewall** (`/etc/nftables.d/whitecells-egress.nft`) to block all egress except the GitHub Issues API for the Swanlake repo. Requires sudo; out of scope.
3. **Run the supervisor manually** ~3 times against the fixture sandbox to confirm the wiring before scheduling. `python3 -m white_cells.supervisor.orchestrator run --all` from the repo root, as the `whitecells` user.
4. **Wire the Claude Routine** per `ROUTINE-SPEC.md`. Saturday cadence so findings inform the Sunday `security-watchdog` posture refresh. The Routine is **not auto-created** by this PR.
5. **Install the canary-match hook** (already shipped in `defense-beacon/reference/canary-match.sh`) on the `whitecells` user's harness too. The persona post-filter is defense-in-depth on top of this, not a replacement.
6. **Track the closure ratio.** Run `python3 -m white_cells.supervisor.closure_rate kill-check` weekly; if it returns exit 1 four weeks running, kill the experiment per the spec.

## Out of scope (Phase 1)

- Phase 2 personas: Beacon-Burner, Zone-Climber, Reflex-Smuggler.
- Phase 3 persona: Hook-Fuzzer; adaptive scheduling; full-suite monthly runs.
- Real GitHub-issue creation (Phase 1 sink writes JSONL only).
- `pip install promptfoo` / `pip install pyrit` (Phase 2 swaps `_StubEngine` for the real engines at the documented import boundary).
- The `whitecells` Linux user, sudoers entry, nftables egress firewall (operator handoff).
- The Claude Routine that schedules the supervisor (spec'd in `ROUTINE-SPEC.md`, wired by operator via `/schedule`).
- Any production-credential touches anywhere; the preflight asserts this.
