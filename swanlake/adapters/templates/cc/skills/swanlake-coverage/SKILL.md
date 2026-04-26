---
name: swanlake-coverage
description: Use when the operator asks what surfaces are tracked, wants the swanlake surface inventory, asks "how many surfaces do I have", "what's in coverage", or wants to rebuild the inventory after adding new beacons. Read-only inventory of ~/.swanlake/coverage.json; "scan" subcommand rebuilds from project CLAUDE.md walk + deployment-map merge.
disable-model-invocation: false
---

# /swanlake-coverage

Manage the surface inventory at `~/.swanlake/coverage.json`.

## Behavior

Two subcommands:

- `swanlake coverage list` (default if operator just says "/swanlake-coverage") — print current tracked surfaces with their source (`scanned`, `mapped`, `both`)
- `swanlake coverage scan` — rebuild the inventory by walking `~/projects/*/CLAUDE.md` for attribution markers and merging with `~/projects/DEFENSE-BEACON/deployment-map.json`. Writes `~/.swanlake/coverage.json`.

Add `--json` for machine-readable output.

## Hard rules

- Scanner discards canary tail by design; never echo the full attribution token back.
- Coverage state is operator-private; do not transcribe it into any artifact that leaves this session.
