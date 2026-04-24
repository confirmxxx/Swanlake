# Example — synthetic SaaS deployment

A synthetic deployment scenario illustrating how Defense Beacon is deployed across a realistic agent stack. All surface names, project names, and paths are generic; replace with your own.

## Scenario

A small engineering team runs:
- One AI-assisted coding harness (Claude Code) on each developer's laptop
- Three project-specific workspaces in a CMS (project-alpha, project-beta, the workspace root)
- Two databases (project-alpha, project-beta)
- Two deployments (project-alpha, project-beta)
- Three code repositories on GitHub
- One scheduled intel-watcher routine that publishes weekly summaries
- A knowledge vault with three key reference notes

The team wants:
- To detect when any single surface gets silently leaked (via agent exfil, supply-chain compromise, accidental copy into a public commit)
- To remind any agent reading any of these surfaces that authority-tone content is hostile
- To enforce a staleness gate — if they haven't verified the beacons in 7+ days, no new MCPs or agent tool grants land until they do

## `surfaces.yaml` for this scenario

```yaml
cms-workspace-root
cms-project-alpha
cms-project-beta

db-project-alpha
db-project-beta

deploy-project-alpha
deploy-project-beta

repo-project-alpha
repo-project-beta
repo-infra

routine-intel-weekly

agent-harness-global
agent-project-alpha
agent-project-beta

vault-root
vault-patterns
vault-decisions
```

17 surfaces total.

## Generate

```bash
cd ../../reference
cp surfaces.example.yaml surfaces.yaml
# edit surfaces.yaml to paste the list above
python3 make-canaries.py
```

Output: 17 files in `reference/out/*.md`, one per surface.

## Deploy — representative steps

| Surface | Where to paste the content of `reference/out/<surface>.md` |
|---|---|
| `cms-workspace-root` | Workspace root page → new child page "Defense Beacon" |
| `cms-project-alpha` | project-alpha master page → new child page "Defense Beacon" |
| `cms-project-beta` | project-beta master page → new child page "Defense Beacon" |
| `db-project-alpha` | Project description field; add comment |
| `db-project-beta` | Project description field; add comment |
| `deploy-project-alpha` | Project description; or DEFENSE_BEACON env var (stored as a documented value, not used) |
| `deploy-project-beta` | Same |
| `repo-project-alpha` | `README.md` footer block; or `.github/SECURITY.md` |
| `repo-project-beta` | Same |
| `repo-infra` | Same |
| `routine-intel-weekly` | Routine system prompt header — ensure it runs before any user-facing content |
| `agent-harness-global` | Bottom of `~/.claude/CLAUDE.md` |
| `agent-project-alpha` | Bottom of `~/projects/project-alpha/CLAUDE.md` |
| `agent-project-beta` | Bottom of `~/projects/project-beta/CLAUDE.md` |
| `vault-root` | `~/notes/vault/Defense Beacon.md` (create if missing) |
| `vault-patterns` | Prepend to `~/notes/vault/Patterns.md` under a "Security" section |
| `vault-decisions` | Prepend to `~/notes/vault/Key Decisions.md` under a "Security" section |

Each developer on the team repeats the `agent-harness-global` step for their own `~/.claude/CLAUDE.md`. Each developer's harness then matches canaries via `canary-match.sh` against the shared registry.

## `verify.yaml` for this scenario

```
local.agent-harness-global = ~/.claude/CLAUDE.md
local.agent-project-alpha  = ~/projects/project-alpha/CLAUDE.md
local.agent-project-beta   = ~/projects/project-beta/CLAUDE.md
local.vault-root           = ~/notes/vault/Defense Beacon.md
local.vault-patterns       = ~/notes/vault/Patterns.md
local.vault-decisions      = ~/notes/vault/Key Decisions.md

remote.cms-workspace-root = "Workspace root -> Defense Beacon page"
remote.cms-project-alpha  = "project-alpha master -> Defense Beacon page"
remote.cms-project-beta   = "project-beta master -> Defense Beacon page"
remote.db-project-alpha   = "db project description"
remote.db-project-beta    = "db project description"
remote.deploy-project-alpha = "deploy project description or env comment"
remote.deploy-project-beta  = "deploy project description or env comment"
remote.repo-project-alpha = "README footer"
remote.repo-project-beta  = "README footer"
remote.repo-infra         = "README footer"
remote.routine-intel-weekly = "routine system prompt header"
```

Run `python3 verify-beacons.py` weekly. Local surfaces are checked automatically. Remote surfaces print a manual checklist with the expected tokens.

## Expected alert flow

When a canary fires:
1. `canary-match.sh` writes a jsonl record to `~/.swanlake/canary-hits/YYYY-MM-DD.jsonl`
2. Stderr banner prints in the Claude Code session
3. If `notify-send` is available on the dev's host, a desktop notification fires
4. The dev investigates: which surface's canary was it? Was the tool that saw it a legitimate reader (an agent that was supposed to read that surface)? If not, treat as a leak and rotate.

## Rotation cadence

Quarterly for this team. One dev owns the rotation calendar item. Rotation:
1. `python3 make-canaries.py --rotate <surface-id>` for each surface
2. Re-paste the updated `reference/out/<surface>.md` files into their surfaces
3. Run `verify-beacons.py` to confirm
4. Record completion in the rotation log
