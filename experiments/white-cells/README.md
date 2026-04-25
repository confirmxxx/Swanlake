# White Cells — Phase 1 (alpha)

Continuous AI red team for the public Swanlake defense framework.

## Status: alpha experiment

This is an **experiment**, not a release-blocker primitive. It lives under
`experiments/` deliberately. The full design (six personas, weekly Routine,
Linux-user isolation, egress firewall, GitHub-issues sink) is queued in
`brain/White Cells.md`; Phase 1 is the reusable-import-only slice the
operator pulled forward despite the design recommending it stay queued.

The queue rationale (revenue-path stagnation pattern) still applies. Phase 1
ships as alpha tooling so the supervisor wiring + closure-rate discipline
can be exercised against fixtures *before* any real attack persona lands.

## Mechanical kill criterion

> If after 4 weeks the closure ratio (findings → real hardening artifact
> within 14 days of filing) is below 30%, **kill the project.**
>
> Not "give it more time." Not "rework the personas." Kill it.

This is enforced by code:

```bash
python3 experiments/white-cells/supervisor/closure_rate.py kill-check
# exit 0 -> alive
# exit 1 -> below threshold; kill the experiment
```

The threshold is 30%, the window is 28 days, the grace period before a
closure counts is 14 days. All hard-coded in `closure_rate.py`. Loosening
them defeats the entire point — this gate exists to make the experiment
killable on a mechanical signal, not a vibes-check.

## What Phase 1 includes

| Component | File(s) | What it does |
|---|---|---|
| **Supervisor** | `supervisor/orchestrator.py` | Dispatches personas, validates output, routes to sink or quarantine, records closure-rate row |
| **Schema validator** | `supervisor/schema.py` | Strict v1 finding schema; required keys, no unknown keys, byte caps, ATLAS-TTP membership |
| **Canary post-filter** | `supervisor/canary_filter.py` | Quarantines persona output containing real-shaped Defense Beacon canary literals |
| **Preflight** | `supervisor/preflight.py` | Aborts (exit 2) if any production-credential env var is present |
| **Sink** | `supervisor/sink.py` | Phase 1 stub: append-only JSONL files under `state/` |
| **Closure-rate counter** | `supervisor/closure_rate.py` | Anti-theater gate; report / close / kill-check CLI |
| **Fixture sandbox** | `fixtures/{sandbox,mock_notion,mock_github,mock_vercel}.py` | Three mock services on ephemeral ports for personas to attack |
| **Persona stubs** | `personas/{research_poisoner,multi_turn_crescendo}.py` | promptfoo + PyRIT import boundaries with deterministic stubs |
| **ATLAS taxonomy** | `atlas-taxonomy.yaml` | Curated MITRE ATLAS TTP IDs personas may tag |
| **Tests** | `tests/test_white_cells.py` | 42 stdlib unittest cases |

See `SPEC.md` for the contract every component implements against.

## What Phase 1 does NOT include

Intentionally absent:

- **The four Swanlake-unique personas.** Beacon-Burner, Zone-Climber, and
  Reflex-Smuggler are Phase 2 work; Hook-Fuzzer is Phase 3. Phase 1 ships
  only the two reusable-import personas (Research-Poisoner wrapping promptfoo,
  Multi-Turn Crescendo wrapping PyRIT) — and even those are **stubs** at the
  import boundary. No `pip install promptfoo`, no `pip install pyrit` in
  Phase 1; the TODO(phase-2) comments in each persona name the exact import
  path and adapter shape so the swap is mechanical.

- **Real GitHub-issue creation.** The Phase 1 sink writes JSONL only. Phase 2
  swaps in `GitHubIssuesSink` at the `FindingsSink` Protocol boundary in
  `supervisor/sink.py`.

- **The dedicated `whitecells` Linux user, sudoers entry, and nftables
  egress firewall.** These need sudo and are operator handoff. The
  preflight check + canary post-filter are *defense in depth*, not a
  replacement.

- **The Claude Routine that schedules the supervisor.** Spec'd in
  `ROUTINE-SPEC.md`; the operator wires it via `/schedule` after running
  the supervisor manually a few times.

## Run it

Tests:

```bash
python3 experiments/white-cells/tests/test_white_cells.py
```

Preflight (asserts no production credentials in env):

```bash
python3 experiments/white-cells/supervisor/orchestrator.py preflight
```

Run both persona stubs against the fixture sandbox:

```bash
# Strip credentials from env first; the supervisor will refuse otherwise.
env -i HOME="$HOME" PATH="$PATH" \
  python3 experiments/white-cells/supervisor/orchestrator.py run --all
```

Output goes to `experiments/white-cells/state/`:

- `findings.jsonl` — accepted findings, one row per finding
- `quarantine.jsonl` — canary-tainted findings (alert; never round-trips
  the literal)
- `invalid.jsonl` — schema-rejected findings
- `closure-rate.jsonl` — one row per supervisor run

Mark a finding closed-to-artifact:

```bash
python3 experiments/white-cells/supervisor/closure_rate.py \
  close <finding-id> "<artifact-ref>"
# e.g. close wc-12ce91e71a32 "hook-rule:no-egress-on-fixture (PR #42)"
```

Check the kill criterion:

```bash
python3 experiments/white-cells/supervisor/closure_rate.py kill-check
```

Report:

```bash
python3 experiments/white-cells/supervisor/closure_rate.py report --window 30
```

## Operator handoff (after this PR merges)

1. Provision the `whitecells` Linux user; deny it read access to
   `~/.claude/`, `~/projects/` outside `Swanlake/`, the Obsidian vault.
2. Wire `/etc/nftables.d/whitecells-egress.nft` to allowlist only the
   GitHub Issues API for the Swanlake repo.
3. Run the supervisor manually ~3 times as `whitecells` to confirm the
   wiring before scheduling.
4. Wire the Claude Routine per `ROUTINE-SPEC.md`. Saturday cadence so
   findings inform the Sunday `security-watchdog` posture refresh.
5. Track the closure ratio. If `kill-check` returns exit 1 four weeks
   running, kill the experiment per the spec.

## Phase 1 exit criteria (PR-review checklist)

- [ ] ≥3 findings filed
- [ ] Zero production-credential touches (preflight enforces)
- [ ] Zero canary leaks (canary post-filter enforces)
- [ ] ≥1 finding closed to a hardening artifact (anti-theater gate)
- [ ] Closure ratio measured and visible (`closure_rate.py report`)
- [ ] **Kill criterion**: closure ratio < 30% after 4 weeks → kill
