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
export SWANLAKE_POSTURE_PAGE_ID="00000000-0000-0000-0000-000000000000"  # <- replace with your real posture page id
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
| `SWANLAKE_POSTURE_PAGE_ID` | `00000000-0000-0000-0000-000000000000` | `pull` | Notion page id of the Security Posture page. **You must override this** — the shipped default is an obvious all-zeros placeholder and mode `pull` will 404 until you set it to your real page id. |
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
0 9 * * 1  NOTION_TOKEN=secret_... SWANLAKE_POSTURE_PAGE_ID=<your-page-id> $HOME/projects/Swanlake/tools/sync-posture.py pull >> $HOME/.claude/logs/sync-posture.log 2>&1
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
| `🛡canary:1` | 🔴 red | A canary tripwire matched a tool output today (real `hits` array, not just a probe) | Open `~/.claude/canary-hits/$(date -u +%Y-%m-%d).jsonl`. Was it you running `verify-beacons.py`? Benign. Otherwise investigate which surface leaked. |
| `🛡exfil:2` | 🔴 red | Secret-shape payloads flagged by the exfil-monitor hook today at `block` or `warn` severity (`info`-level lines are excluded) | Open `~/.claude/exfil-alerts/…`. Check whether the shape was a false positive (e.g. a legit hex string in your test fixture) or something actually trying to leave. |
| `🛡inject:1` | 🔴 red | Prompt-injection actually flagged in fetched content today (`block: true`, `score > 0`, or non-empty `findings`) — not just a hook fire | Open `~/.claude/content-safety/…`. Usually a scraped page tried to speak in imperatives. |
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
| `🛡canary:N` | N canary-match real hits today (lines with a non-empty `hits` array) |
| `🛡exfil:N` | N exfil-monitor real hits today (`severity` of `block` or `warn`) |
| `🛡inject:N` | N content-safety real hits today (`block: true`, `score > 0`, or findings present) |
| `🛡!stale:8d,canary:1` | Combined — issues listed comma-separated |
| `🛡canary:0/2,exfil:0/0,inject:0/40` | `SWANLAKE_STATUS_VERBOSITY=full` mode — `label:hits/fires`. Always rendered, even when clean. Use it to verify the hooks are actually firing without polluting the default glyph. |

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
| `SWANLAKE_STATUS_VERBOSITY` | `default` | `full` renders each per-dir counter as `label:hits/fires` even at zero. Useful for verifying the hooks are wired correctly when the default mode is intentionally quiet. |

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

### Counting semantics

Each per-dir counter reports **real detections, not hook-fire volume**. A clean day produces zero flags even when the underlying hook fired hundreds of times — for example, `content-safety` inspects every `WebFetch`, so a normal session emits dozens of clean-fire log lines that are intentionally not surfaced.

Per-dir hit predicates (all three additionally require an *interactive session* — see below):

| Dir | Schema field that signals a real hit |
|---|---|
| `~/.claude/canary-hits` | non-empty `hits: [...]` array, `self_edit_noise` not set |
| `~/.claude/exfil-alerts` | `severity in {"block", "warn"}` |
| `~/.claude/content-safety` | `block: true` OR `score > 0` OR non-empty `findings` |

**Interactive-session filter.** Records where `session_id` is *present-but-empty* are excluded from the counters. This is the bench-harness signature: PyRIT/Garak/AB-bench runs invoke the detection hooks against synthetic hostile fixtures by design and would otherwise flood the bar with detections that aren't real-world drift. The Claude Code hook environment populates `session_id` with a non-empty UUID in current versions, so genuine interactive hits keep counting. Records that omit the field entirely (legacy producers, external pipelines) keep their prior counted behavior — no log rewrite required.

The detection itself is unchanged: bench rows are still written to disk, still available for forensics, still re-runnable by the bench harness. The filter only affects what the *counter* surfaces on the bar.

Set `SWANLAKE_STATUS_VERBOSITY=full` to render `label:hits/fires` and see both numbers — useful when you want to confirm the hook is actually firing.

### Limitations

- Reads only today's log files (by local UTC date). Alerts from yesterday still-pending-triage don't appear. By design — status should reflect current state.
- Does not aggregate by severity within the "real hit" set. An `exfil:1` from a false-positive `warn` and an `exfil:1` from a real `block` look identical. Triage via `~/.claude/exfil-alerts/` or the `sec-dash` command that reads the same logs.
- Does not call out to the network; strictly local. Remote posture (Notion Security Posture page freshness) is reflected via the `SWANLAKE_LAST_RUN` file, which the watchdog routine updates on successful writes.

## `loop-closure-metric.py`

Tracks whether your defenses *fire-and-forget* or *fire-and-learn*. Powers the `closure` row of `swanlake status` and the `closure:N%` flag on the status bar.

### What it measures

Two questions look similar but are very different:

- **Q1.** Did the defense layer fire when something hostile showed up? Already answered by `status-segment.py` — `canary:N`, `exfil:N`, `inject:N`.
- **Q2.** When the defense fired, did the operator close the loop with a durable hardening artifact (new hook rule, new deny entry, new test fixture, conventional commit)?

Q2 is the only one that distinguishes a defense that *works* from a defense that *fires-and-forgets*. The metric tracks it as a 7-day rolling ratio:

```
ratio = artifacts_produced / max(events_caught, 1)
```

| Ratio | Meaning |
|---|---|
| `≥ 1.0` | Every event spawned (on average) at least one hardening artifact |
| `0.30 – 1.0` | Healthy follow-through but some events decay un-actioned |
| `< 0.30` | Alerts piling up without follow-up — the defense is becoming theater. The status-bar flag fires. |

### Inputs

**Events caught** (predicates identical to `status-segment.py`, including the bench-harness session-id filter):

| Source | Real-hit condition |
|---|---|
| `~/.claude/canary-hits/<date>.jsonl` | non-empty `hits` AND not `self_edit_noise` AND interactive session |
| `~/.claude/content-safety/<date>.jsonl` | `block: true` OR `score > 0` OR non-empty `findings` AND interactive session |
| `~/.claude/exfil-alerts/<date>.jsonl` | `severity in {block, warn}` AND interactive session |

**Artifacts produced** (counted across the same UTC day):

| Source | Counted as |
|---|---|
| Conventional-commit subjects in configured repos | one artifact per matching commit (`fix\|feat\|chore\|test\|docs\|refactor\|perf\|build\|ci\|style`) |
| New deny-list entries in `~/.claude/settings.json` | one artifact per added entry, diffed against the previous day's snapshot |
| New files under `~/.claude/hooks/` | one artifact per file with mtime in the day window |

The ratio is conservative: removed deny entries don't subtract credit (cleanup is different work), and merge commits are excluded so a release-merge doesn't inflate today.

### Modes

```bash
# 1. Compute today's rollup, write to ~/.claude/loop-closure/<date>.json (default)
python3 tools/loop-closure-metric.py
python3 tools/loop-closure-metric.py --rollup

# 2. Aggregate the last N days from per-day rollups (computes missing days on the fly)
python3 tools/loop-closure-metric.py --report --days 7

# 3. Status-bar flag — emits "closure:NN%" if 7-day ratio is below threshold (default 30%)
#    AND the window has at least 3 events. Always exits 0; status lines must not break.
python3 tools/loop-closure-metric.py --status-flag
```

### Configuration (all env vars)

| Variable | Default | Effect |
|---|---|---|
| `SWANLAKE_CANARY_HITS` | `~/.claude/canary-hits` | Canary-match log directory (shared with `status-segment.py`) |
| `SWANLAKE_CONTENT_HITS` | `~/.claude/content-safety` | Content-safety-check log directory |
| `SWANLAKE_EXFIL_HITS` | `~/.claude/exfil-alerts` | Exfil-monitor log directory |
| `SWANLAKE_ROLLUP_DIR` | `~/.claude/loop-closure` | Where per-day rollups are written |
| `SWANLAKE_HOOKS_DIR` | `~/.claude/hooks` | Watched for new defensive hook files |
| `SWANLAKE_SETTINGS_FILE` | `~/.claude/settings.json` | Source of truth for the deny-list entry count |
| `SWANLAKE_HARDENING_REPOS` | `~/projects/Swanlake,~/projects/DEFENSE-BEACON` | Comma-separated absolute paths of git repos scanned for conventional-commit artifacts |
| `SWANLAKE_CLOSURE_THRESHOLD` | `0.30` | Ratio threshold below which `--status-flag` fires |

### What it does NOT measure

A high closure ratio is not a security claim. The metric counts pattern-match events against actions taken, not attacks against successful blocks. Specifically it does not tell you:

- whether any of the events were real attacks (most are pattern hits on prose — security articles, the operating-rules document, log dumps that quote prior hits)
- whether the artifacts you produced were *related* to the events caught (a conventional commit unrelated to defense still counts as an artifact — by design, since the operator may be hardening orthogonally)
- whether bench coverage is sufficient (the bench dimension answers that)

Use the closure ratio to detect *neglect*, not to certify *defense health*. If you stop responding to fires, the ratio shows it before the alert fatigue compounds.

### Pairs with

- `status-segment.py` — same predicates, same session-id filter, so the bar and the metric tell the same story
- `swanlake status` — folds today's rollup into the `closure` row and the overall verdict
- `swanlake bench --quick` — keeps the events-caught denominator honest by re-firing the detectors on synthetic fixtures whose `session_id=""` makes them invisible to this metric

### Dependencies

Python 3.10+ stdlib only. No `pip install` required.

### Tests

`python3 tools/tests/loop_closure_metric_test.py` runs the full unittest suite (33 tests as of v0.4.3): predicate parity with `status-segment.py`, the empty-`session_id` filter, hardening-artifact counters, rollup composition, window aggregation, and `--status-flag` emission.

## Future additions here

Candidate additions for this directory (PRs welcome): a `canary-triage.py` that lists + clears today's benign canary hits; a `posture-diff.py` that diffs two `last_verified` timestamps to summarize what the watchdog added; a Starship preset with glyph/color mapping; a `loop-closure-metric.py --backfill <date>` flag to recompute a stale rollup without requiring a today-rollup invocation.
