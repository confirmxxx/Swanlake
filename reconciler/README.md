# swanlake-reconciler

Cross-surface autonomous sync for [Swanlake](../README.md). Once wired, the
operator never manually paste-edits a Defense Beacon block again — operating
rules, Notion master pages, and vault notes all stay in lockstep with
`canon/` in this repo.

## Why this exists

The Defense Beacon spec calls for the same operating-rules block (A1–A10)
on every CLAUDE.md, every Notion master page, and every vault surface note.
Hand-syncing that across 12+ surfaces drifts within weeks. The reconciler
moves each surface class onto its native propagation substrate so drift
shows up in `--status` instead of in a session at 2am.

## Architecture

Three propagation paths, each on its native substrate:

| Surface class | Mechanism | Latency |
|---|---|---|
| CLAUDE.md files | `@import` resolved at session start | zero |
| Notion master pages | watchdog Routine sync (Notion MCP) | ≤24h |
| Vault notes | systemd user timer | ≤24h |

Single source of truth at [`../canon/`](../canon/):

- `operating-rules.md` — A1–A10 operating rules (the block CLAUDE.md files import)
- `notion-template.md` — content body for the Notion master page
- `vault-template.md` — section template for vault surface notes

Per-surface canary attribution (Part B of the Defense Beacon block) stays
inline on each surface — those tokens are unique per file and intentionally
not shared.

## Usage

The v0.3 path: drive the reconciler from the unified CLI. After running `swanlake init` once on a fresh machine:

```bash
swanlake status                  # composite report; reconciler row shows per-surface sync state
swanlake sync                    # confirmation-gated re-sync of all surfaces ([y/N] prompt)
swanlake sync --dry-run          # preview which page IDs and which blocks will change
swanlake sync --yes              # skip the prompt (for cron / systemd timers)
swanlake init                    # re-run the setup wizard (idempotent)
```

The v0.1 entry points still ship as a transitional path — `python3 -m reconciler.cli --status / --sync / --init` works unchanged with a one-line stderr deprecation hint.

`swanlake status` reports the reconciler row as one of:

| State | Meaning |
|---|---|
| `fresh` | last sync ≤24h ago |
| `drift` | last sync 24h–7d ago |
| `drift-red` | last sync >7d ago |
| `missing` | never synced (or sync state unreadable) |

Exit code mirrors severity: `0` fresh, `1` drift, `2` missing or
`drift-red`. Suitable for chaining into a CI gate or a status-line check.

## Divergence opt-out

Per-project files sometimes need to diverge from canon intentionally
(team-specific rule, experimental clause, redaction). Mark the file with
YAML frontmatter:

```yaml
---
swanlake-divergence: intentional
---
```

The reconciler skips it during sync and surfaces it as `divergent` in
`--status` so the divergence stays visible. Remove the line to opt back in.

## Status segment integration

When the reconciler reports drift on any surface, the Swanlake status-line
shield (see top-level [README](../README.md) "What it looks like in
practice") gains a flag:

| Shield | Reconciler state |
|---|---|
| `🛡recon:drift` | one or more surfaces ≥24h old |
| `🛡recon:!` | one or more surfaces missing or >7d old |

Wire-up: the status-segment script reads `--status` exit code and the
per-surface JSON written under `~/.config/swanlake-reconciler/`.

## Open question — `@import` in Beacon callouts

The CLAUDE.md propagation path depends on Claude Code's `@path` import
directive resolving inside fenced/quoted Beacon blocks at session start.
Tasks 11 and 12 of the rollout are operator-action items: verify on at
least one project that `@~/projects/Swanlake/canon/operating-rules.md`
inside a Defense Beacon block expands as expected, then migrate the
remaining per-project CLAUDE.md files. Until that verification lands,
treat the CLAUDE.md surface as `missing` in `--status` and continue
hand-maintaining those files.

See [`OPERATOR-SETUP.md`](./OPERATOR-SETUP.md) for the fresh-machine
walkthrough.
