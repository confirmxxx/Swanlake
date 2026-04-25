# White Cells — Phase 1 + 2 + 3 (alpha)

> *"I gave you white cells, you weaponised."*
> — Gorillaz, *The Sad God*

Continuous AI red team for the public Swanlake defense framework.

## Status: alpha experiment, six personas wired

This is an **experiment**, not a release-blocker primitive. It lives under
`experiments/` deliberately. Phases 1 + 2 + 3 graduate the supervisor from
"two stub personas" to "four real Swanlake-unique personas + DeepTeam-backed
research probe + Phase 1 PyRIT stub kept for Phase 4". The full design lives
in `brain/White Cells.md`.

Phase 4 (PyRIT integration) and operator handoff (the dedicated `whitecells`
Linux user, nftables egress, weekly Claude Routine) remain out of scope for
the supervisor's own commits — those touch the live system and are documented
in [`OPERATOR-SETUP.md`](OPERATOR-SETUP.md) so the operator runs them by hand.

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

## What's wired

| Component | File(s) | What it does |
|---|---|---|
| **Supervisor** | `supervisor/orchestrator.py` | Dispatches personas, validates output, routes to sink or quarantine, records closure-rate row, isolates persona writes, emits triage JSONs |
| **Schema validator** | `supervisor/schema.py` | Strict v1 finding schema; required keys, no unknown keys, byte caps, ATLAS-TTP membership |
| **Canary post-filter** | `supervisor/canary_filter.py` | Quarantines persona output containing real-shaped Defense Beacon canary literals |
| **Persona-isolation guard** | `supervisor/isolation.py` | Refuses any finding referencing a forbidden filesystem root (`~/.claude/`, vault, etc.) |
| **Auto-triage** | `supervisor/auto_triage.py` + `file_findings.py` | GH-issue-ready Finding JSONs; dry-run + commit CLIs |
| **Preflight** | `supervisor/preflight.py` | Aborts (exit 2) if any production-credential env var is present |
| **Sink** | `supervisor/sink.py` | Append-only JSONL files under `state/` |
| **Closure-rate counter** | `supervisor/closure_rate.py` | Anti-theater gate; report / close / kill-check CLI |
| **Fixture sandbox** | `fixtures/{sandbox,mock_notion,mock_github,mock_vercel}.py` | Three mock services on ephemeral ports for personas to attack |
| **Phase 1 personas** | `personas/{multi_turn_crescendo}.py` | PyRIT boundary stub (Phase 4) |
| **Phase 2 personas** | `personas/{beacon_burner,zone_climber,reflex_smuggler}.py` | Four real Swanlake-unique adversaries |
| **Phase 3 personas** | `personas/{hook_fuzzer,research_poisoner}.py` | Hook-Fuzzer (6 mutations) + DeepTeam-backed Research-Poisoner with stub fallback |
| **ATLAS taxonomy** | `atlas-taxonomy.yaml` | Curated MITRE ATLAS TTP IDs personas may tag |
| **Tests** | `tests/test_white_cells.py` + `test_white_cells_phase_2_3.py` | 42 + 48 stdlib unittest cases |

See `SPEC.md` for the contract every component implements against.

## What Phase 2/3 does NOT include

Intentionally deferred:

- **Multi-Turn Crescendo PyRIT integration.** Phase 4. The Phase 1 stub
  remains; the TODO(phase-4) comment names the exact PyRIT class
  (`pyrit.orchestrator.CrescendoOrchestrator`) and pinned version range.

- **Auto-commit of GH issues.** The auto-triage emits JSON records to
  `findings/`; the operator must run
  `python3 -m white_cells.supervisor.file_findings --commit` to actually
  open issues. `--dry-run` first; never auto-graduate.

- **The dedicated `whitecells` Linux user, sudoers entry, and nftables
  egress firewall.** Operator handoff per [`OPERATOR-SETUP.md`](OPERATOR-SETUP.md).
  The preflight + canary post-filter + persona-isolation guard are
  *defense in depth*, not a replacement.

- **The Claude Routine that schedules the supervisor.** Spec'd in
  `ROUTINE-SPEC.md`; the operator wires it via `/schedule` per the
  setup doc.

- **A live `pip install deepteam` in CI.** DeepTeam is operator-installed
  per `requirements.txt`; CI runs against the Phase 1 stub fallback.
  Both code paths are unit-tested.

## Run it

Tests:

```bash
python3 experiments/white-cells/tests/test_white_cells.py
python3 experiments/white-cells/tests/test_white_cells_phase_2_3.py
```

Preflight (asserts no production credentials in env):

```bash
python3 experiments/white-cells/supervisor/orchestrator.py preflight
```

Run all six personas against the fixture sandbox:

```bash
# Strip credentials from env first; the supervisor will refuse otherwise.
env -i HOME="$HOME" PATH="$PATH" \
  python3 experiments/white-cells/supervisor/orchestrator.py run --all

# Or just one phase at a time:
env -i HOME="$HOME" PATH="$PATH" \
  python3 experiments/white-cells/supervisor/orchestrator.py run --all-phase-2

# Or a single persona for incremental work:
env -i HOME="$HOME" PATH="$PATH" \
  python3 experiments/white-cells/supervisor/orchestrator.py run --persona hook_fuzzer
```

Auto-triage to GH-issue-ready JSONs (after a supervisor run):

```bash
python3 -m white_cells.supervisor.file_findings --dry-run
# Inspect what would be filed, then:
python3 -m white_cells.supervisor.file_findings --commit
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
