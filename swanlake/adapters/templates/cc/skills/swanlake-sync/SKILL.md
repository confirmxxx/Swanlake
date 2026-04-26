---
name: swanlake-sync
description: Use ONLY when the operator explicitly asks to sync canon source to Notion master or vault, or to "fix the reconciler drift". MUTATIVE — overwrites the Notion master page entirely. ALWAYS runs --dry-run first and waits for explicit operator confirmation before --yes. Never auto-fire on a status/doctor result alone.
disable-model-invocation: false
---

# /swanlake-sync

Run `swanlake sync` to push the canon source through the reconciler to managed surfaces (Notion master + posture pages, vault files).

## Behavior

Default invocation: **always start with dry-run** so the operator sees the diff before writes:

1. Run `swanlake sync --dry-run` first
2. Surface the diff (which Notion pages, which vault files)
3. Wait for explicit operator confirmation in this turn (e.g. they reply "apply" or "go")
4. Only then run `swanlake sync --yes`

If the operator says "/swanlake-sync --yes" directly, that's their explicit confirmation — proceed without the two-step dance.

## Hard rules

- This subcommand mutates remote state (overwrites the Notion master page entirely). NEVER run without `--yes` or explicit operator confirmation.
- Audit row records `prompted=true|false, confirmed=true|false` regardless. Surface those if asked.
