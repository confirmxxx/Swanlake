# tools/

Small utilities that compose with the Swanlake primitives. Each is optional; nothing in the main packages depends on them.

## `sync-posture.py`

Bridges the *remote* posture signal (the `Last verified:` field on the Notion Security Posture page, maintained by the scheduled watchdog routine) to the *local* freshness file that `status-segment.py` reads (`~/.claude/.last-watchdog-run`). Without this bridge, the routine can keep Notion fresh while your terminal's shield still shows `🛡?` — because the local file never got touched.

### Three-mode cheat sheet

| Mode | Network | Requires | What it does |
|---|---|---|---|
| `now` (default) | none | — | Writes the current UTC timestamp to `$SWANLAKE_LAST_RUN`. The "I just reviewed the Notion page by eye, posture is fresh from here" manual confirmation. |
| `pull` | Notion API | `NOTION_TOKEN` env var | Fetches the posture page, finds the `Last verified: <iso>` line, writes that timestamp to `$SWANLAKE_LAST_RUN`. |
| `check` | none | — | Prints `<state> <N_days>` where state is `fresh` / `yellow` / `red` / `unknown`. Same thresholds as `status-segment.py`. |

### Examples

```bash
# Manual confirmation — I just eyeballed the posture page, everything's fresh
./tools/sync-posture.py           # or: ./tools/sync-posture.py now

# Fetch the actual timestamp from Notion (requires read-scoped integration token)
export NOTION_TOKEN="secret_..."
export SWANLAKE_POSTURE_PAGE_ID="34c018ae-d8f8-81ce-8bd1-fbf80defc1e6"
./tools/sync-posture.py pull
# → synced: 2026-04-24T10:15:33Z

# Just check current local state
./tools/sync-posture.py check
# → fresh 0
# → yellow 3
# → red 9
# → unknown -1
```

### Configuration (all env vars)

| Variable | Default | Used by | Effect |
|---|---|---|---|
| `SWANLAKE_LAST_RUN` | `~/.claude/.last-watchdog-run` | all | Path to the ISO-UTC timestamp file. Shared with `status-segment.py`. |
| `SWANLAKE_POSTURE_PAGE_ID` | placeholder UUID | `pull` | Notion page id of the Security Posture page. **Override this** — the shipped default is a placeholder, not a real page. |
| `NOTION_TOKEN` | (unset) | `pull` | Bearer token for a Notion integration with read access to the posture page. **Required** for `pull`. |
| `SWANLAKE_STALE_YELLOW` | `2` | `check` | Days triggering the yellow band. |
| `SWANLAKE_STALE_RED` | `7` | `check` | Days triggering the red band. |

### Exit semantics

| Code | Meaning |
|---|---|
| `0` | Success. |
| `2` | Config missing (e.g. `NOTION_TOKEN` unset in `pull` mode). |
| `3` | HTTP / network error talking to Notion. |
| `4` | Parse error — couldn't find or parse a `Last verified:` timestamp on the page. |

`$SWANLAKE_LAST_RUN` is never overwritten on any error path. Writes are atomic (tempfile + `os.replace`) so the status segment never observes a half-written file.

### Integrations

#### Manual fire (ad-hoc, after eyeballing Notion)

```bash
~/projects/Swanlake/tools/sync-posture.py
```

#### Weekly cron (Monday 09:00 local, pull mode)

```cron
0 9 * * 1  NOTION_TOKEN=secret_... SWANLAKE_POSTURE_PAGE_ID=34c0... $HOME/projects/Swanlake/tools/sync-posture.py pull >> $HOME/.claude/logs/sync-posture.log 2>&1
```

Better: keep the token in a mode-600 env file and source it.

```cron
0 9 * * 1  . $HOME/.config/swanlake/env && $HOME/projects/Swanlake/tools/sync-posture.py pull >> $HOME/.claude/logs/sync-posture.log 2>&1
```

#### systemd user timer

`~/.config/systemd/user/swanlake-sync-posture.service`:

```ini
[Unit]
Description=Swanlake — pull posture freshness from Notion

[Service]
Type=oneshot
EnvironmentFile=%h/.config/swanlake/env
ExecStart=%h/projects/Swanlake/tools/sync-posture.py pull
```

`~/.config/systemd/user/swanlake-sync-posture.timer`:

```ini
[Unit]
Description=Weekly Swanlake posture pull

[Timer]
OnCalendar=Mon 09:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user enable --now swanlake-sync-posture.timer
```

### Credential honesty

Mode `pull` requires a Notion integration token with **read access scoped to the posture page only** — not the whole workspace. Treat the token the way Swanlake asks you to treat all canary/beacon material:

- Local-only. Never commit it. `.env`, `~/.config/swanlake/env`, or your OS keychain — not the repo.
- Treat as burned if it shows up in a PR, a terminal recording, a paste buffer sent to someone, or a log file you can't guarantee is local. Rotate at the Notion integrations page.
- Scope minimally. If you only need the posture page, share only the posture page with the integration.

The offline `now` mode exists precisely so you don't have to stand up a token for casual use — eyeball the Notion page, run `./sync-posture.py`, done.

### Pairs with

`status-segment.py` reads the same `$SWANLAKE_LAST_RUN` file. The pipeline is: remote routine updates Notion → `sync-posture.py` pulls Notion's timestamp to the local file → status segment renders green. Break any link and the shield falls back to `🛡?` or `🛡stale:Nd`.

### Dependencies

Python 3.10+ stdlib only. `urllib.request` for HTTP. No `pip install` required.

## `status-segment.py`

Terse health indicator for a shell status line, Starship/powerlevel segment, or Claude Code status-line hook. Reads local posture state + today's hit logs and emits a single short string.

Think of it as a smoke detector for your agent stack. You glance at your terminal — if the shield is quiet, you're good. If it's shouting, read the flags.

### What the shield means (cheat sheet)

| You see | Vibe | What's happening | What to do |
|---|---|---|---|
| `🛡` | 🟢 green | Clean posture, routine fresh | Nothing — keep working |
| `🛡?` | ⚪ gray | No watchdog has fired yet | Fire the routine once to initialize, OR install the systemd timer, OR `date -u +%Y-%m-%dT%H:%M:%SZ > ~/.claude/.last-watchdog-run` |
| `🛡stale:3d` | 🟡 yellow | Threat posture 2–6 days old | No rush — watchdog will refresh on next scheduled run. Manual fire if you're paranoid. |
| `🛡!stale:9d` | 🔴 red | Posture ≥7 days old — **staleness gate is active** | Fire the routine now. Until you do, Claude Code will refuse new MCP installs / new OAuth scopes / new plugin loads (beacon rule A11). |
| `🛡canary:1` | 🔴 red | A canary tripwire matched a tool output today | Open `~/.claude/canary-hits/$(date -u +%Y-%m-%d).jsonl`. Was it you running `verify-beacons.py`? Benign. Otherwise investigate which surface leaked. |
| `🛡exfil:2` | 🔴 red | Secret-shape payloads flagged by the exfil-monitor hook today | Open `~/.claude/exfil-alerts/…`. Check whether the shape was a false positive (e.g. a legit hex string in your test fixture) or something actually trying to leave. |
| `🛡inject:1` | 🔴 red | Prompt-injection markers in fetched content today | Open `~/.claude/content-safety/…`. Usually a scraped page tried to speak in imperatives. |
| `🛡!stale:8d,canary:1` | 🔴 red | Multiple issues stacked | Triage in order: newest alert first, staleness second (it's informational once you know). |

### Decision flow — "I see X, what now?"

```
See the 🛡 at all?
├── no  → status line doesn't include the segment yet; install per Integrations below
└── yes → anything after it?
          ├── nothing                → all good, keep working
          ├── "?"                    → fire the routine once to bootstrap the last-run file
          ├── "stale:Nd"  (N<7)      → watchdog is watching; no action needed
          ├── "!stale:Nd" (N≥7)      → gate ACTIVE; refresh now, or disable A11 if intentional
          ├── "canary:N"             → a tripwire hit — open canary-hits log
          ├── "exfil:N"              → secret-shape payload blocked — open exfil-alerts log
          ├── "inject:N"             → prompt-injection flagged — open content-safety log
          └── multiple, comma'd      → treat like a stack trace; triage newest first
```

### First-run mental model

Right after you wire the segment:
1. You'll probably see `🛡?` — no watchdog has fired yet.
2. Run the watchdog manually (or wait for its next scheduled run).
3. Shield becomes `🛡` — all green.
4. Walk away for a week: glyph ages `🛡stale:3d` → `🛡!stale:9d`. Your laptop was off, posture drifted, refresh when you return.

### When to panic vs when to chill

- `🛡`                         → don't even look at the status line
- `🛡?` or `🛡stale:Nd`        → todo list, not alarm
- `🛡!stale:Nd`                → actionable but not urgent (the gate is already enforcing for you)
- `🛡canary:N` / `🛡exfil:N`   → drop what you're doing, open the log, triage
- multiple flags stacked      → same — newest first, cheapest to resolve first

### Why a glyph instead of a dashboard

A dashboard requires opening. A shield in your status line is in your peripheral vision every time you type. Friction determines whether you check; glanceability determines whether you notice.

### Troubleshooting

**I see `🛡?` and it won't go away.**
The script can't find a last-run timestamp. Pick one:
- Fire the watchdog routine once manually → it writes `~/.claude/.last-watchdog-run`
- Install the systemd user timer → it writes `~/.claude/.watchdog-tick` on a schedule
- Write the file by hand: `date -u +%Y-%m-%dT%H:%M:%SZ > ~/.claude/.last-watchdog-run`

**I see `🛡canary:1` but I just ran `verify-beacons.py` myself.**
Expected. The verifier reads files containing canary strings, the canary-match hook sees them, logs them. It's a benign self-hit. Clear today's log if it's bothering you: `rm ~/.claude/canary-hits/$(date -u +%Y-%m-%d).jsonl`.

**I want it to stay quiet when clean.**
Set `SWANLAKE_STATUS_STYLE=silent` in your shell env. The segment emits nothing until something's off.

**My status bar is cramped; stacked flags overflow.**
Same env var. Shield disappears when green, appears only when it has something to say.

**What if the posture file is corrupt / unreadable?**
Script exits 0 silently and falls back to `🛡?`. Never breaks your status line.

### Output grammar

| Output | Meaning |
|---|---|
| `🛡` | Clean, fresh posture |
| `🛡?` | No last-run timestamp yet (wire up the routine or manual-fire step) |
| `🛡stale:Nd` | Posture stale, yellow band (default: 2–6 days) |
| `🛡!stale:Nd` | Posture stale, red band (default: ≥7 days) |
| `🛡canary:N` | N canary-match hits today |
| `🛡exfil:N` | N exfil-monitor hits today |
| `🛡inject:N` | N content-safety hits today |
| `🛡!stale:8d,canary:1` | Combined — issues listed comma-separated |

### Configuration (all env vars optional)

| Variable | Default | Effect |
|---|---|---|
| `SWANLAKE_LAST_RUN` | `~/.claude/.last-watchdog-run` | ISO-UTC timestamp file written by the routine / manual-fire step |
| `SWANLAKE_TICK` | `~/.claude/.watchdog-tick` | Fallback timestamp file (systemd user timer writes this) |
| `SWANLAKE_CANARY_HITS` | `~/.claude/canary-hits` | Directory of `YYYY-MM-DD.jsonl` canary-match logs |
| `SWANLAKE_EXFIL_HITS` | `~/.claude/exfil-alerts` | Directory of exfil-monitor logs |
| `SWANLAKE_CONTENT_HITS` | `~/.claude/content-safety` | Directory of content-safety-check logs |
| `SWANLAKE_STALE_YELLOW` | `2` | Days of staleness triggering yellow band |
| `SWANLAKE_STALE_RED` | `7` | Days triggering red band + `!stale` prefix |
| `SWANLAKE_STATUS_STYLE` | `default` | `silent` suppresses output when posture is clean |

### Integrations

#### Claude Code status-line hook

Your `~/.claude/hooks/status-line.sh` likely already composes a line like `cwd  branch  model  effort`. Append the Swanlake segment:

```bash
# near the end, after you've built the parts array:
swanlake=""
if [ -x "$HOME/projects/Swanlake/tools/status-segment.py" ]; then
  swanlake=$("$HOME/projects/Swanlake/tools/status-segment.py" 2>/dev/null || true)
fi
[[ -n "$swanlake" ]] && parts+=("$swanlake")
```

#### Starship segment

Add to `~/.config/starship.toml`:

```toml
[custom.swanlake]
command = "~/projects/Swanlake/tools/status-segment.py"
when = "true"
style = "dimmed yellow"
```

#### PS1 (bash)

```bash
export PS1='\w $(~/projects/Swanlake/tools/status-segment.py) \$ '
```

For a quieter PS1, set `SWANLAKE_STATUS_STYLE=silent` to emit nothing when clean.

### Exit semantics

Exits 0 always. Never breaks the status line. If state files are missing or malformed, the script falls back to `🛡?` or empty output per style.

### Dependencies

Python 3.10+ stdlib only. No `pip install` required.

### Performance

Single stat + at-most-three small file reads per call. Typical wall-clock < 10ms. Safe to invoke on every status-line refresh.

### Limitations

- Reads only today's log files (by local UTC date). Alerts from yesterday still-pending-triage don't appear. By design — status should reflect current state.
- Does not aggregate by severity. An `exfil:1` from a false positive and an `exfil:1` from a real secret-shape look identical. Triage via `~/.claude/exfil-alerts/` or the `sec-dash` command that reads the same logs.
- Does not call out to the network; strictly local. Remote posture (Notion Security Posture page freshness) is reflected via the `SWANLAKE_LAST_RUN` file, which the watchdog routine updates on successful writes.

## Future additions here

Candidate additions for this directory (PRs welcome): a `canary-triage.py` that lists + clears today's benign canary hits; a `posture-diff.py` that diffs two `last_verified` timestamps to summarize what the watchdog added; a Starship preset with glyph/color mapping.
