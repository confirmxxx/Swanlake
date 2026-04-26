---
name: swanlake-init
description: Use ONLY when the operator explicitly asks for first-run swanlake setup, fresh-machine bootstrap, or to register a new surface via --add-surface. Idempotent; never touches existing canary-hits/ or canary-strings.txt. Never auto-fire on a missing-config error — surface the error and ask the operator first.
disable-model-invocation: false
---

# /swanlake-init

Run `swanlake init` to bootstrap the Swanlake state root at `~/.swanlake/`. Idempotent — safe to re-run; existing files are preserved byte-identical.

## Behavior

- Default: `swanlake init` — full bootstrap (creates audit.jsonl, coverage.json, config copies)
- `--add-surface NAME` — register a single new surface in the canary registry + coverage map without re-running the full bootstrap

Re-runs print `already initialised — nothing to do` and exit 0.

## Hard rules

- NEVER touches `~/.swanlake/canary-hits/` or `~/.swanlake/canary-strings.txt` if either exists. These are operator-state with attribution tokens; preserving them is non-negotiable.
- `--add-surface` writes to the canary registry; the new surface ID is operator-private. Confirm spelling before invoking.
