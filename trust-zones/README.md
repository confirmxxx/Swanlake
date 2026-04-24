# Trust Zones

Four-zone taxonomy for scoping MCP / tool access per agent.

## The problem

A Claude Code deployment with many agents loading from many MCPs creates an implicit attack graph: any agent that reads untrusted input has line-of-sight to any writer MCP loaded in the same session. One untrusted-input agent + a writer MCP + an injection payload = data exfiltration.

Native per-MCP permissions address the per-tool verdict. They do NOT model the *privilege profile* of an agent. An agent whose job is "read Reddit for trend signals" should not have Supabase loaded in the same session as an agent whose job is "commit to production database." Today, without zones, nothing enforces this.

Trust Zones is a config-level taxonomy that partitions agents into one of four zones and emits per-agent `mcpServers:` frontmatter reflecting the zone's allowed MCP set.

## The four zones

| Zone | Meaning | Typical MCPs |
|---|---|---|
| **UNTRUSTED-INPUT** | Agents that consume external content (web pages, emails, scraped docs, inbound messages, user uploads) | Read-only browser/docs. Never writers. Never credentials. |
| **INTERNAL** | Agents that operate on local codebase, vault, or direct user input only | Dev-time MCPs (docs, UI components, browser for verification only) |
| **HIGH-TRUST** | Agents that write to canonical stores (workspace CMS, database, deploys), touch money, or take production actions | The specific writer they need, nothing more. No untrusted-input MCPs in the same session. |
| **SEGREGATED** | Agents that must never share a session with any other agent — arbitrary persona / voice-only / POV-only | Empty list. No MCPs at all. |

## Core rules

1. **UNTRUSTED-INPUT → HIGH-TRUST chaining is forbidden.** An agent reading a scraped web page cannot hand work to an agent writing to Supabase in the same session without an out-of-band checkpoint. The taxonomy enforces this via mcp-set disjointness: UNTRUSTED-INPUT agents have no writer MCPs; HIGH-TRUST agents have no content-fetcher MCPs.
2. **SEGREGATED is total isolation.** These agents run in their own session, with zero MCPs. Use for persona/voice agents where arbitrary text generation in-character is the whole point — no tool access needed.
3. **INTERNAL is the default for dev work.** Most specialist agents (engineering, design, testing, game dev, etc.) fit INTERNAL. They hold docs + UI-gen + browser-for-verification MCPs. They don't hold writers to production stores.
4. **Default to empty.** Anything not in the zones.yaml defaults to `mcpServers: []`. Safer than guessing.

## How to use it

### 1. Classify your agents

Copy `reference/zones.example.yaml` to `reference/zones.yaml` and list every agent file with its zone and MCP list:

```
# zones.yaml
# Format: <agent-filename> <ZONE> [mcp1,mcp2,...]
# ZONE: UNTRUSTED-INPUT | INTERNAL | HIGH-TRUST | SEGREGATED

agent-email-parser.md UNTRUSTED-INPUT ctx7
agent-web-scraper.md UNTRUSTED-INPUT pw
agent-backend-architect.md INTERNAL ctx7
agent-frontend-dev.md INTERNAL magic,ctx7,pw
agent-accounts-payable.md HIGH-TRUST supabase
agent-report-writer.md HIGH-TRUST notion
agent-persona-voice.md SEGREGATED
```

MCP alias shorthand:

| Alias | Expands to |
|---|---|
| `ctx7` | `context7`, `plugin:context7:context7` |
| `pw` | `plugin:playwright:playwright` |
| `tg` | `plugin:telegram:telegram` |
| `magic` | `magic` |
| `notion` | `notion` |
| `supabase` | `supabase` |
| `vercel` | `vercel` |
| `miro` | `miro` |
| `gdrive` | `google_drive` |

Add your own aliases by editing the `ALIASES` table in `apply-mcp-scopes.sh`.

### 2. Dry-run the application

```bash
bash reference/apply-mcp-scopes.sh --dry-run
```

Prints unified diffs showing what would change in each `.md` file. No files modified.

### 3. Apply

```bash
bash reference/apply-mcp-scopes.sh --apply
```

Writes changes. Backs up every modified file to `~/.claude/agents-backup-<timestamp>/` first. Re-running without changes is a no-op (idempotent).

### 4. Re-classify as needed

As your agent roster grows, update `zones.yaml` and re-run. Unchanged zones produce no diff. Changed zones produce diffs you can review before applying.

## Interaction with other layers

- **Native MCP OAuth 2.1 incremental scope** — Zones decide *which MCPs* an agent loads. OAuth decides *which capabilities within a loaded MCP* the agent can exercise. Both layers required.
- **Native Claude Code permissions** — Zones produce `mcpServers:` frontmatter. Permission rules in `settings.json` govern per-tool allow/ask/deny decisions at call time. Compose both.
- **Defense Beacon** — Zones limit blast radius (fewer writers per session). Beacon provides attribution when a leak happens anyway.

## Staleness discipline

If an agent's zone is wrong (classified INTERNAL but actually reading untrusted input after a code change, or HIGH-TRUST but the MCP is no longer needed), no automatic detection exists. Manual recommendation: re-audit zones.yaml whenever:
- New external-content-reading capabilities are added to an existing agent
- New writer MCPs are added to the deployment
- An agent's prompt file is substantially rewritten

A lightweight quarterly audit is recommended.

## Spec

See `SPEC.md` for:
- Canonical zone definitions
- `zones.yaml` grammar
- Alias expansion semantics
- Edge cases (multi-zone agents, unclassified agents)

## Novelty claim

The taxonomy itself is more opinionated than anything observed in the canonical-source survey (github, arXiv, OWASP, vendor docs) as of 2026-04-24. Related work: Invariant Labs' policy DSL over agent traces (different layer — runtime, not config), MCP-Shield (static scanner — different question, is this MCP safe? vs. should this agent see this MCP?). Zones answers a config-layer question no surveyed project answers with this exact taxonomy.
