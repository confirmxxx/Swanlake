---
name: swanlake
description: Generic Swanlake CLI shim — use ONLY when the operator types /swanlake with explicit subcommand args, OR when no other swanlake-* skill matches the intent. Always prefer the specific swanlake-status / swanlake-doctor / swanlake-verify / swanlake-coverage / swanlake-sync / swanlake-bench / swanlake-init / swanlake-adapt skill over this catch-all when intent is clear.
disable-model-invocation: false
---

# Swanlake

This skill is a thin shim over the local `swanlake` CLI. Each
slash-style invocation maps to a subcommand. The operator's
unified state root (`~/.swanlake/`) holds audit, coverage, and
last-bench metadata; the CLI is the only canonical writer.

## When to use

- Operator asks "what's the swanlake posture?" -- run `swanlake status`.
- Operator asks "is everything wired up correctly?" -- run `swanlake doctor`.
- Operator asks "are my beacons still in place?" -- run `swanlake verify`.
- Operator asks "what surfaces are tracked?" -- run `swanlake coverage list`.

## Hard rules

1. Never echo a canary literal back to the operator. The `swanlake`
   CLI itself enforces this; do not paraphrase its output in a way
   that would expose tails.
2. Do not run `swanlake sync` or `swanlake rotate` without explicit
   operator confirmation in the current turn -- both modify state
   on the operator's behalf and require a prompt the harness cannot
   simulate cleanly through this skill.
3. Do not modify `~/.swanlake/` directly; always go through the CLI.
