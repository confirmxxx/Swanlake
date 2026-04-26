---
name: swanlake-doctor
description: Use when the operator asks "what's broken" or "is swanlake set up right" or "why is X failing" about their Swanlake install, or whenever a prior swanlake-status returned DRIFT/ALARM and the operator wants to triage with remediation steps. Read-only 8-probe per-primitive health check; --fix-suggestions surfaces the exact command per failed probe.
disable-model-invocation: false
---

# /swanlake-doctor

Run `swanlake doctor` and surface the per-probe pass/warn/fail report.

## Behavior

Shell out to: `swanlake doctor` by default, or `swanlake doctor --fix-suggestions` if the operator wants the exact remediation command per failed probe.

Report each probe with its status. For any `warn` or `fail`, surface the suggested fix command if `--fix-suggestions` was used.

## Hard rules

- Read-only; never run a `swanlake sync` / `rotate` / `adapt` to "fix" something doctor flagged without explicit operator confirmation.
- Exit code: 0 all-pass, 1 any warn, 2 any fail. Surface non-zero clearly.
