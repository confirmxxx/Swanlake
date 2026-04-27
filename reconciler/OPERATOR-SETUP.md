# Operator Setup

Fresh-machine walkthrough for `swanlake-reconciler`. ~10 minutes end to end.
Assumes Linux with `systemd --user`, Python 3.11+, and an existing local
Defense Beacon registry.

## 1. Clone Swanlake + install the reconciler

```bash
git clone https://github.com/confirmxxx/Swanlake.git ~/projects/Swanlake
pip install -e ~/projects/Swanlake/reconciler
```

`pip install -e` registers the `swanlake-reconciler` console script. If
your shell can't find it after install, fall back to
`python3 -m reconciler.cli`.

## 2. Initialize the local canary registry

If you don't already have one, set up `~/projects/DEFENSE-BEACON/` per the
top-level `defense-beacon/README.md`. At minimum, generate one canary
surface so the reconciler has something to attribute against:

```bash
python3 ~/projects/Swanlake/defense-beacon/reference/make-canaries.py --help
```

The reconciler never reads canary tokens directly — it reads the
`deployment-map.json` that `make-canaries.py` produces.

## 3. Run the setup wizard

```bash
swanlake-reconciler --init
```

Prompts (in order):

| Prompt | Typical value |
|---|---|
| deployment-map path | `~/projects/DEFENSE-BEACON/deployment-map.json` |
| vault root | your Obsidian vault dir, or empty if none |
| Notion master page ID | UUID from the master page URL |
| Notion posture page ID | UUID from the posture page URL |
| Swanlake repo path | `~/projects/Swanlake` |
| canon/ dir | empty for `<repo>/canon` |

Writes config to `~/.config/swanlake-reconciler/config.toml` (atomic) and
copies systemd unit files into `~/.config/systemd/user/`.

## 4. Activate the daily timer

```bash
systemctl --user daemon-reload
systemctl --user enable --now swanlake-vault-sync.timer
```

The timer fires daily and runs `swanlake-reconciler --sync` for the vault
surface class. Notion sync runs from the watchdog Routine (step 8).
CLAUDE.md surfaces use `@import` and update at session start.

## 5. Verify

```bash
swanlake-reconciler --status
```

Expected on first run: three surfaces listed (`vault`, `notion`,
`claude_md`), all `missing` until their respective propagation paths run.

## 6. Force the first sync

```bash
swanlake-reconciler --sync
swanlake-reconciler --status
```

Expected: `vault` is now `fresh`. `notion` and `claude_md` may still show
`missing` until the watchdog Routine has fired (step 8) and the per-project
CLAUDE.md migration has happened (step 7).

## 7. CLAUDE.md migration (one-time per project)

For each per-project `CLAUDE.md` that already carries a Defense Beacon
block, replace the inline operating rules (Part A) with an `@import`:

```markdown
## Part A — Operating rules (read before acting)

@~/projects/Swanlake/canon/operating-rules.md
```

Keep Part B (per-surface canary attribution) untouched — those tokens are
unique to each file and must not be shared.

After migration, every Claude Code session that opens that project reads
the latest operating rules at session start. No further hand-syncing.

> **Open question.** `@import` resolution inside Beacon callouts hasn't
> been verified across every CLAUDE.md style in the wild. Migrate one
> low-risk project first. Confirm with a fresh `claude` session that the
> imported rules show up. Then roll out to the remaining files.

## 8. Watchdog Routine extension (one-time)

Edit your existing `swanlake-watchdog` Claude Routine prompt to also
propagate the Notion master page from `canon/notion-template.md`. The
Routine should:

1. Fetch the latest `canon/notion-template.md` from the Swanlake repo
2. Use the Notion MCP `notion-update-page` to write content into the
   master page (page ID = `notion_master_page_id` from your local config)
3. Append an ISO timestamp to `~/.swanlake/last-sync.json` under key
   `notion` (or, more conveniently, run `swanlake reconciler ack notion`
   from the operator's machine after each Routine fire — that writes the
   ack to `~/.swanlake/reconciler-acks.jsonl` and the status reader folds
   it into the freshness calculation). Pre-v0.4.2 installs that wrote to
   `~/.config/swanlake-reconciler/last-sync.json` keep working: the
   status engine migrates the legacy file forward on first read.

After the next Routine fire (Sundays 09:00 UTC by default),
`swanlake-reconciler --status` should show `notion` as `fresh`.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `swanlake-reconciler: command not found` | `pip install -e` didn't refresh PATH. Use `python3 -m reconciler.cli --status`. |
| `--status` shows everything `missing` | First run, no sync yet. Run `--sync` (vault), wait for the next Routine fire (Notion), or finish step 7 (CLAUDE.md). |
| Vault file marked `divergent` unexpectedly | Check the file's frontmatter for `swanlake-divergence: intentional`. Remove the line to opt back in. |
| Timer never fires | Check `systemctl --user status swanlake-vault-sync.timer`. On WSL, ensure `systemd --user` is enabled (`loginctl enable-linger $USER`). |
| `--init` re-run wipes manual edits | Expected — `config.toml` is regenerated atomically. Edit the file by hand or re-run `--init` with the same values. |
