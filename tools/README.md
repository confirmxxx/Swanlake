# tools/

Small utilities that compose with the Swanlake primitives. Each is optional; nothing in the main packages depends on them.

## `status-segment.py`

Terse health indicator for a shell status line, Starship/powerlevel segment, or Claude Code status-line hook. Reads local posture state + today's hit logs and emits a single short string.

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
