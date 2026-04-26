---
name: swanlake-adapt
description: Use ONLY when the operator explicitly asks to install Swanlake into Claude Code or a CMA project. MUTATIVE — replaces hooks unless --skill-only. ALWAYS dry-runs first AND checks `wc -l ~/.claude/hooks/*.sh` before any non-skill-only install on the operator's CC dir; if existing hooks exceed 100 LOC the bundled templates would be a regression and --skill-only must be used instead. Never auto-fire.
disable-model-invocation: false
---

# /swanlake-adapt

Install Swanlake into a specific agent harness. Three target harnesses: `cc`, `cma`, `sdk`.

## Behavior

ALWAYS start with `--dry-run` to surface what would change before any writes.

- `swanlake adapt cc --dry-run` — shows hook scripts + settings.json patches + skill install. **WARNING: full install REPLACES existing `~/.claude/hooks/*.sh` with bundled templates.** If the operator already has production hooks, use `--skill-only` (v0.2.1+) to install only the slash-command skill.
- `swanlake adapt cma --project PATH --dry-run` — shows per-CMA file changes (Beacon Part A injection, Part B canary generation, zones.yaml seeding, tool-config writes, reflex-purity AST report)
- `swanlake adapt sdk` — v0.3 stub; exits 3

After dry-run, only proceed to real install with explicit operator confirmation in this turn.

## Hard rules

- NEVER run `swanlake adapt cc` (without `--skill-only`) on the operator's machine without first checking `wc -l ~/.claude/hooks/*.sh` — if existing hooks are >100 LOC, the bundled templates would be a regression.
- `--uninstall` reverses via per-adapter manifest at `~/.swanlake/{cc,cma}-adapter-manifest*.json`. Without the manifest, refuse to auto-uninstall.
- CMA install touches files inside the operator's project. Always show the dry-run diff per-CMA before proceeding.
