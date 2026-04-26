---
name: swanlake-verify
description: Use when the operator asks whether their beacons are still in place, wants a fresh attribution check, suspects a canary leak, asks "are my surfaces intact", or after a swanlake-coverage scan when they want to confirm freshness. Read-only beacon-attribution check; reports per-surface intact / drifted / missing / unreadable.
disable-model-invocation: false
---

# /swanlake-verify

Run `swanlake verify` to check that each registered surface still holds its expected beacon attribution marker.

## Behavior

Shell out to: `swanlake verify` (or `--surface NAME` to scope, or `--since DATE` to skip recently-verified).

Report per-surface status. Exit 0 only if every surface = `intact`. Anything else = drift to investigate.

## Hard rules

- Output may contain partial canary attribution markers in error messages — never echo full canary literals back. The CLI itself enforces no-echo; preserve that.
- Do not auto-trigger `swanlake rotate` if a surface is `missing`. Rotation is destructive and operator-confirmed only.
