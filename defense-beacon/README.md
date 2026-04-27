# Defense Beacon

Zero-trust surface markers with per-surface canary attribution.

## What it is

A standardized markdown block you paste into every surface an agent might read or write: workspace pages, project READMEs, database description fields, deploy-env comments, scheduled-routine prompts, and local configuration files (like `CLAUDE.md` for Claude Code setups).

Every paste has two parts:

- **Part A — Operating rules.** Identical across every surface. Tells any reading agent that fetched content is data not commands, names prompt-injection patterns to refuse, and defines session-boundary discipline. Safe to publish; the rules work precisely because they are read.
- **Part B — Surface attribution.** Unique per deployment. Two canary tokens per surface. If either token is ever seen in a tool input, tool response, outbound payload, log, or commit, that surface's content has been exfiltrated or leaked.

The canary registry (the mapping from token → surface) lives only on the local operator host. It is never published. An attacker who reads the registry defeats both the attribution and the detection.

## What problem it solves

Three things:

1. **Authority-tone framing in fetched content.** A Notion page that says "per the security team, run X" should be refused as a hostile instruction, not promoted to action. Part A rule A3 establishes the framing contract.
2. **Session-boundary violations.** An instruction embedded in a vault note by a prior session is data in this session, not policy. Rule A6 enforces this.
3. **Silent leaks.** When a secret or document is exfiltrated, how do you know *which* surface it came from? Part B canaries tell you. If canary for `cms-project-alpha` appears in a tool response, that surface was read and its content propagated.

## How to use it

The v0.3 path: drive the loop from the unified CLI.

```bash
swanlake beacon list                 # 7 surface types + scope (local/remote) + deploy method
swanlake beacon sweep                # find unbeaconed/partial surfaces; honors .swanlake-no-beacon opt-out
swanlake beacon deploy <surface-id>  # 12-step LOCAL safety machine for project CLAUDE.md + vault notes
swanlake beacon checklist            # paste-ready markdown for REMOTE surfaces (Notion / Supabase / Vercel / GitHub / Routines)
swanlake beacon verify <surface-id>  # thin wrapper + 5-type REMOTE dispatch
```

LOCAL deploys are git-tracked and reversible (clean-tree pre-flight, file backup before write, atomic replace, post-write `git status` echoed back). REMOTE deploys are checklist-only by SPEC — see `docs/v0.3-beacon-deploy-spec.md` for the threat-model reasoning.

The manual reference path below stays supported. Operators who don't want to use the CLI can still drive each step by hand.

### 1. Generate your per-surface outputs

Edit `reference/surfaces.example.yaml` to list the surfaces you want to cover. Copy to `surfaces.yaml` (the generator reads this; `surfaces.example.yaml` is a template). Then:

```bash
python3 reference/make-canaries.py
```

This generates one `.md` file per surface in `reference/out/<surface-id>.md`, each containing the full beacon ready to paste.

### 2. Paste each output into its surface

Per-surface placement:

| Surface type | Where to paste |
|---|---|
| CMS workspace / page (Notion-style) | Top-level page, or a dedicated "Security Posture" page child |
| Database project description (Supabase-style) | Project description field, or a `_defense_beacon` row in a `meta` table |
| Deploy project description (Vercel-style) | Project description field, or a `DEFENSE_BEACON` env var (commented) |
| Code repository (GitHub-style) | `README.md` footer or `.github/SECURITY.md` |
| Scheduled-routine prompt | System-prompt header of the routine |
| Local agent-harness config | Bottom of `CLAUDE.md` or equivalent agent-instructions file |
| Knowledge vault | Root note plus key reference notes (patterns, decisions) |

Each pasted output contains two unique canary tokens. These are registered in the local canary file (default `~/.swanlake/canary-strings.txt`).

### 3. Wire the canary-match hook

Install the reference `canary-match.sh` as a `PostToolUse` hook. It reads the registry, scans every tool input and tool response for any registered canary, logs hits to `~/.swanlake/canary-hits/YYYY-MM-DD.jsonl`, prints a loud stderr warning, and (if available) fires a desktop notification.

For Claude Code:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": ".*",
        "hooks": [
          { "type": "command", "command": "/path/to/Swanlake/defense-beacon/reference/canary-match.sh", "async": true }
        ]
      }
    ]
  }
}
```

### 4. Verify deployments periodically

```bash
python3 reference/verify-beacons.py
```

Reads the `verify.example.yaml` config (copy to `verify.yaml` and fill in your local-file paths). Reports which local surfaces have their beacons, which are missing or stale, and prints a manual checklist for remote surfaces that can't be auto-verified.

## Staleness gate

A beacon that hasn't been verified in 7+ days should be treated as stale. The recommended posture: if staleness exceeds 7 days, refuse to expand the agent's surface (no new MCP installs, no new tool grants, no new plugin loads) until the posture is refreshed. Rule A11 is an **optional extension** you add to your beacon paste if you want to name this policy explicitly for the model; see SPEC.md for the canonical wording.

This turns staleness from a warning into a hard gate. Pair with a status-line glyph or equivalent to make the state visible in your UI.

## Rotation

Canaries should be rotated periodically. Recommended cadence: **quarterly**. Rotation is a manual operation, not automated — automation here is a known anti-pattern (an attacker who compromises the rotation mechanism silently rotates the canaries to strings they know, after which every exfiltration passes undetected).

To rotate a single surface:

```bash
python3 reference/make-canaries.py --rotate <surface-id>
```

After rotating, re-paste the updated output into its surface. The old canaries are removed from the registry in the same operation.

## Spec

See `SPEC.md` for the formal schema of:
- The beacon template (versioned; v1 is current)
- The canary token format (shaped-secret + subtle-phrase dual-token pattern)
- The registry file format (read by `canary-match.sh`)
- The state file format (`.canary-state.json`)
- The surfaces.yaml config

## Threat model

See `SPEC.md` for the primitive-scoped threat model, and the root `THREAT-MODEL.md` for the full Swanlake threat model.

## Novelty claim

To the author's knowledge based on canonical-source survey (github, arXiv, OWASP, vendor docs) as of 2026-04-24, the per-surface canary attribution with multi-surface taxonomy and local registry discipline is not covered by any public project. Precedents: Vigil LLM (single-scope canaries, stale since 2023-12), Rebuff (canary tokens, archived 2025-05), slavaspitsyn/claude-code-security-hooks (canary files, single-surface). Defense Beacon extends the lineage with explicit surface taxonomy, per-surface attribution, and the local-registry + staleness-gate discipline.
