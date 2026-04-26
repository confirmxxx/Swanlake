# Migrating from Swanlake v0.1 to v0.2

## What changed

v0.1 shipped seven entry points across two repos: three reconciler subcommands, two `tools/` scripts, two `DEFENSE-BEACON/` scripts, four hook shell files, and one ad-hoc bench script under `/tmp/`. Operators kept the paths in muscle memory or in a personal cheat sheet. v0.2 introduces one CLI — `swanlake` — that wraps all of it. The underlying scripts still exist and still work; the CLI is a typed harness over them.

Nothing breaks. v0.1 entry points keep functioning in v0.2 with a one-line stderr deprecation hint. They are removed in v0.3.

## Command translation

| v0.1 | v0.2 | Removed in v0.3? |
|---|---|---|
| `python3 -m reconciler.cli --status` | `swanlake status` | yes |
| `python3 -m reconciler.cli --sync` | `swanlake sync` | yes |
| `python3 -m reconciler.cli --init` | `swanlake init` | yes |
| `python3 tools/status-segment.py` | invoked by status-line; `swanlake status` for full view | yes (kept as library shim) |
| `python3 tools/loop-closure-metric.py` | folded into `swanlake status` (closure row); standalone via `swanlake status --json` | yes (kept as library shim) |
| `python3 DEFENSE-BEACON/make-canaries.py` | `swanlake rotate` | the script stays; CLI wraps it |
| `python3 DEFENSE-BEACON/verify-beacons.py` | `swanlake verify` | the script stays; CLI wraps it |
| `~/.claude/hooks/canary-match.sh` (manual install) | installed by `swanlake adapt cc` | no — still bash |
| `~/.claude/hooks/content-safety-check.sh` (manual install) | installed by `swanlake adapt cc` | no — still bash |
| `~/.claude/hooks/bash-firewall.sh` (manual install) | installed by `swanlake adapt cc` | no — still bash |
| `~/.claude/hooks/exfil-monitor.sh` (manual install) | installed by `swanlake adapt cc` | no — still bash |
| `/tmp/swanlake-ab-bench-*/run.sh` | `swanlake bench --quick` | yes |
| `/tmp/swanlake-pyrit-garak-bench-*/run.sh` | `swanlake bench --full` (v0.2.x) | yes |

## Backwards compatibility

Every v0.1 entry point still runs in v0.2 unmodified. The reconciler module prints a single deprecation line to stderr at the top of each invocation:

```
$ python3 -m reconciler.cli --status
[deprecation] Direct reconciler.cli invocation is deprecated in Swanlake v0.2 and
will be removed in v0.3. Use `swanlake status`. See docs/migrating-from-v0.1.md.

reconciler — canon @ 2026-04-26T11:02Z, 2 surfaces in sync
[...]
```

The deprecation hint goes to stderr only, so existing pipes and cron jobs that consume stdout are unaffected. Suppress the hint with `SWANLAKE_NO_DEPRECATION=1` in the environment if you want a clean log during the transition.

## Installing the CLI

```bash
pip install swanlake-cli
```

On Debian/Ubuntu and other PEP 668 systems, the system Python rejects `pip install` with an `externally-managed-environment` error. Use a venv: `python3 -m venv .venv && source .venv/bin/activate && pip install -e .`. Or `pipx install swanlake-cli` if you prefer isolated tool installs.

## State migration

`swanlake init` (or first invocation of any subcommand on a v0.1 machine) creates these files. None of them touch existing v0.1 state.

| File | Created? | Purpose |
|---|---|---|
| `~/.swanlake/` | created if absent | state root |
| `~/.swanlake/canary-hits/` | preserved if exists | canary trip log (v0.1 location, unchanged) |
| `~/.swanlake/canary-strings.txt` | preserved if exists | canary registry (unchanged) |
| `~/.swanlake/config.toml` | created from reconciler config | unified operator config (supersedes `~/.config/swanlake-reconciler/config.toml`) |
| `~/.swanlake/audit.jsonl` | created empty | one row per CLI invocation |
| `~/.swanlake/coverage.json` | created empty `{"schema":1,"surfaces":{}}` | populated by `swanlake verify` and `swanlake coverage scan` |
| `~/.swanlake/last-bench` | not created | written by first `swanlake bench --quick` |

Verify migration succeeded:

```bash
swanlake doctor
```

Healthy output ends with `posture: ok` and exit code 0. If `coverage.json` is empty (fresh machine), `doctor` flags it under `coverage` with the exact `swanlake init --add-surface NAME` command needed to populate it.

## Rollback

To uninstall v0.2 and return to v0.1 entry points:

```bash
# 1. Uninstall the CLI
pip uninstall swanlake-cli

# 2. Optionally, revert the Claude Code adapter
swanlake adapt cc --uninstall   # restores settings.json from timestamped backup
# or manually:
mv ~/.claude/settings.json.bak-swanlake-<timestamp> ~/.claude/settings.json

# 3. v0.1 state is untouched. The reconciler module, tools/, and DEFENSE-BEACON/
#    scripts are unchanged on disk. Continue invoking them directly.
```

`~/.swanlake/audit.jsonl`, `coverage.json`, and `last-bench` remain after uninstall. They are inert without the CLI; delete by hand if undesired.

## FAQ

**Will `swanlake sync` overwrite my Notion edits?**
Only on the two reconciler-managed pages declared in your `~/.swanlake/config.toml` (`notion_master_page_id` and `notion_posture_page_id`). Every other Notion page in your workspace is invisible to Swanlake. The `--dry-run` flag prints exactly which page IDs and which blocks will change before any write.

**Do I have to install the CLI to keep using v0.1?**
No. v0.1 entry points keep working unmodified through v0.2. The CLI is a convenience wrapper, not a dependency. If you skip the install, the only thing you lose is the consolidated `swanlake status` view; every individual script remains usable.

**What about my existing systemd timer?**
The v0.1 timer at `~/.config/systemd/user/swanlake-vault-sync.timer` keeps working — it invokes `python3 -m reconciler.cli --sync`, which still exists. If you want to migrate, the v0.2 equivalent unit calls `swanlake sync --yes` instead:

```ini
# ~/.config/systemd/user/swanlake-vault-sync.service (v0.2 equivalent)
[Unit]
Description=Swanlake watchdog — sync canon to managed surfaces
After=network-online.target

[Service]
Type=oneshot
ExecStart=%h/.local/bin/swanlake sync --yes
StandardOutput=append:%h/.swanlake/timer.log
StandardError=append:%h/.swanlake/timer.log
```

Reload with `systemctl --user daemon-reload && systemctl --user restart swanlake-vault-sync.timer`. The schedule (`OnCalendar=` in the `.timer` unit) does not change. Audit rows from the timer-driven sync land in `~/.swanlake/audit.jsonl` with `tty: false` and `noninteractive: true`.

**Will the CLI install touch any of my existing v0.1 files?**
No. `swanlake init` creates new files in `~/.swanlake/` if absent and leaves anything that already exists untouched. `swanlake adapt cc` writes to `~/.claude/settings.json` after writing a timestamped backup of the original. No v0.1 script under `tools/`, `reconciler/`, or `DEFENSE-BEACON/` is modified.

**My CI calls `python3 -m reconciler.cli --sync`. Does it break?**
No. It keeps working through v0.2 with the stderr deprecation hint. Migrate at your convenience by replacing the call with `swanlake sync --yes` and adding `pip install swanlake-cli` to the CI image. Plan to migrate before v0.3 cuts (target: Q3 2026).

**Where does `swanlake` look for the canon source?**
The `canon_dir` field in `~/.swanlake/config.toml`, defaulting to `<swanlake_repo_path>/canon/`. Both paths are written by `swanlake init`. Override at runtime with `--state-root PATH` or by editing the config file.

**Why did my reconciler config move from `~/.config/swanlake-reconciler/` to `~/.swanlake/`?**
The reconciler was an independent tool in v0.1 and used XDG-style `~/.config/` paths. v0.2 unifies operator state under `~/.swanlake/`. The loader reads the new location first and falls back to the old one with a one-line stderr deprecation hint. Move at your convenience by running `swanlake init` (it copies forward without deleting the old file).

**Can I run `swanlake adapt cc` more than once?**
Yes — it is idempotent. The adapter checks `~/.claude/settings.json` for existing hook entries before appending and skips entries that match by `command` field. Re-running upgrades hook script content if the templates in the repo have changed; otherwise it is a no-op. The settings backup is only written on the first install.
