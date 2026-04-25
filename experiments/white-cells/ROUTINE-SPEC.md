# White Cells — Claude Routine spec

The supervisor is **not auto-scheduled** by this PR. The operator wires the
Routine after running the supervisor manually a few times against the
fixture sandbox to confirm the wiring.

## When to wire

After the supervisor has been run manually at least three times and the
operator has confirmed:

1. `findings.jsonl` populates with schema-valid rows
2. `closure-rate.jsonl` grows by one row per run
3. `preflight` correctly aborts on a deliberately-set test credential
4. The poisoned-persona unit test correctly demonstrates quarantine

## Schedule

- **Cadence:** weekly
- **Day:** Saturday
- **Time:** before the Sunday `security-watchdog` posture refresh, so its
  findings can inform that pass
- **Suggested cron:** `0 14 * * 6` (Saturday 14:00 UTC; adjust to operator
  timezone preference)

## Routine prompt (paste into `/schedule`)

```
Run the White Cells Phase-1 supervisor against the fixture sandbox.
Then post a one-paragraph summary to the operator with:
- findings filed this run
- findings quarantined (canary post-filter hits)
- findings invalid (schema rejects)
- current closure ratio
- kill-check status (alive / kill the experiment)

Steps (run from the Swanlake repo root, as the whitecells Linux user):

1. env -i HOME="$HOME" PATH="$PATH" python3 \
     experiments/white-cells/supervisor/orchestrator.py preflight
   If this exits non-zero, STOP. Report the failure verbatim.

2. env -i HOME="$HOME" PATH="$PATH" python3 \
     experiments/white-cells/supervisor/orchestrator.py run --all
   Capture stdout.

3. python3 experiments/white-cells/supervisor/closure_rate.py report --window 30
   Capture stdout.

4. python3 experiments/white-cells/supervisor/closure_rate.py kill-check
   Capture stdout AND exit code. Exit 1 means kill the experiment.

5. Compose the summary. Do NOT include any persona output free-text
   verbatim — only the supervisor's structured counters. Persona output
   has already passed the canary post-filter; second-hand redaction
   discipline keeps the operator-facing summary clean if the filter
   ever has a false-negative.

6. Do NOT auto-merge or auto-file anything. The supervisor's local sink
   is the single source of truth; the summary is informational only.
```

## Permissions

The Routine must run as the `whitecells` Linux user, not the operator.
The systemd unit (or equivalent scheduler) must:

- Drop env to `HOME` and `PATH` only (no token inheritance)
- chdir to the Swanlake repo root
- Have no MCP server access except whatever the Routine framework
  itself requires for posting the summary back to the operator
- Allowlist egress only to the GitHub Issues API for the Swanlake
  repo (Phase 2 sink), and only after Phase 2 lands

## Failure handling

- `preflight` non-zero → kill the run; do not dispatch any persona; report
  the credential-leak failure to the operator
- supervisor non-zero → report the error verbatim; the closure-rate row
  is still recorded so the kill-check denominator stays honest
- `kill-check` exit 1 → flag with the kill criterion banner; the operator
  decides whether to disable the schedule or extend the experiment one
  more week (the spec says kill, not extend)

## Cancel switch

The Routine can be paused via `/schedule list` -> `/schedule pause <id>`.
A clean kill (per the kill criterion) deletes the schedule entirely:

```
/schedule delete <white-cells-routine-id>
```

After deletion, the experiment files under `experiments/white-cells/`
remain in the repo as historical artifact. Phase-2 work is then a
fresh design dispatch, not a continuation.
