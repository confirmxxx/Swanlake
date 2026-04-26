---
name: swanlake-status
description: Use when the operator asks about Swanlake defense posture, drift, alarms, what's broken in their security stack, "how's swanlake doing", "is everything OK", or any quick "what's the state" question. Read-only composite report across 7 dimensions (reconciler / canary / inject / exfil / closure / coverage / bench); exits non-zero on drift.
disable-model-invocation: false
---

# /swanlake-status

Run `swanlake status` and surface the table to the operator. Read-only.

## Behavior

Shell out to: `swanlake status` (or `swanlake status --json` if the operator asks for machine-readable).

Report the 7-dimension table verbatim. Highlight any row where status is not `clean` or `ok`. Note the overall verdict (`CLEAN`, `DRIFT`, or `ALARM`) and the exit code.

## Hard rules

- Never paraphrase canary/inject hit detail in a way that would expose token tails. The CLI redacts; do not undo that.
- Do not modify `~/.swanlake/`; the CLI is the only canonical writer.
