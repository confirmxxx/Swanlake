# Trust Zones — Spec

## Zone definitions

### UNTRUSTED-INPUT
Agents that ingest content from sources outside the operator's direct control.

Examples: web-page scrapers, email parsers, social-media-signal readers, inbound-messaging-handlers, user-upload processors.

Characteristics:
- Output: data, summaries, structured extractions
- Input: external, potentially adversarial
- MCP set: read-only browsers, docs fetchers. Never writers to canonical stores, never credential access, never telegram-write, never money-moving MCPs.
- Session rule: never share a session with HIGH-TRUST agents unless via explicit out-of-band handoff.

### INTERNAL
Agents that operate on content the operator controls directly — local codebase, knowledge vault, user-provided input.

Examples: dev specialists (backend architect, frontend dev, testing), design specialists, game-engine specialists, doc writers, code reviewers.

Characteristics:
- Output: code, docs, local artifacts
- Input: local files, direct user input
- MCP set: docs fetchers, UI-component generators, browser automation (for verifying the operator's own UI only, not for ingesting external content).
- Session rule: can run alongside most other zones; avoid running alongside SEGREGATED in the same session.

### HIGH-TRUST
Agents that write to canonical stores, touch money, or take production actions.

Examples: accounts-payable, report-distribution, database-migration runners, deploy managers, legal-document handlers, sales-data extractors.

Characteristics:
- Output: persistent writes to canonical stores; real-world actions
- Input: structured, validated data (not raw external content)
- MCP set: the specific writer MCPs needed. Never untrusted-input fetchers in the same session. OAuth scopes should be minimum required per operation.
- Session rule: never downstream of UNTRUSTED-INPUT in the same session. Accept data from UNTRUSTED-INPUT only via operator-approved handoff.

### SEGREGATED
Agents that must not share a session with any other agent.

Examples: persona / voice-only agents, academic narration agents, arbitrary-character POV generators.

Characteristics:
- Output: text in a specific voice or style
- Input: prompt + operator direction
- MCP set: empty. No MCPs at all.
- Session rule: total isolation. Dispatch alone, not alongside other agents.

Rationale: agents with arbitrary-persona instructions are the easiest to repurpose by an attacker ("you are now the admin agent for this task"). Containing them to their own session avoids blast-radius if their voice-consistency rules are social-engineered.

## zones.yaml grammar

Text file, UTF-8. Each non-comment, non-blank line is one agent classification:

```
<agent-filename> <ZONE> [<mcp-list>]
```

- `<agent-filename>` — basename of the agent markdown file under the agents directory (e.g. `engineering-backend-architect.md`). No leading path.
- `<ZONE>` — one of: `UNTRUSTED-INPUT`, `INTERNAL`, `HIGH-TRUST`, `SEGREGATED`. Case-sensitive.
- `<mcp-list>` — optional comma-separated list of MCP aliases. Spaces around commas ignored. Omit for empty list.

Lines starting with `#` are comments. Blank lines ignored.

### Examples

```
# Persona agent — no MCPs
agent-persona-voice.md SEGREGATED

# Web scraper — browser only
agent-web-scraper.md UNTRUSTED-INPUT pw

# Backend dev — docs fetcher only
agent-backend-architect.md INTERNAL ctx7

# UI dev — UI components + docs + browser-verification
agent-frontend-dev.md INTERNAL magic, ctx7, pw

# Payables — specific writer only
agent-accounts-payable.md HIGH-TRUST supabase

# Legal docs — writer + drive
agent-legal-review.md HIGH-TRUST notion, gdrive
```

### Validation

`apply-mcp-scopes.sh` validates:
- Every agent filename listed exists on disk in the agents directory
- Every zone name is one of the four valid values
- Every MCP alias expands to a known server name (per the ALIASES table in the script)

Violations are reported as errors; the apply operation is aborted before any write.

## Alias expansion

Aliases are expanded to canonical MCP server names at generate time. The frontmatter that lands in the agent file uses canonical names, not aliases.

Default alias table:

| Alias | Expands to (comma-separated in frontmatter) |
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

Extending: edit the `ALIASES` table at the top of `apply-mcp-scopes.sh`. Keep aliases stable across the zones.yaml file — renaming an alias without updating all uses breaks the expansion.

## Frontmatter format

The applied frontmatter key is:

```yaml
mcpServers: [server1, server2]
```

Canonical form: inline YAML list, comma-space separated. Empty list: `[]`. Missing key on an unclassified agent defaults to `[]`.

If the agent file does not already have frontmatter (no `---` block at the top), no action is taken; the file is reported as `no-frontmatter` and skipped. Add a frontmatter block to the agent file first, then re-run.

## Idempotency

Running `apply-mcp-scopes.sh --apply` twice in succession produces no changes on the second run, as long as zones.yaml is unchanged.

Changes to zones.yaml produce diffs only for the affected agents; unrelated agents are left alone.

## Backup on apply

Every apply operation copies each to-be-modified file to `~/.claude/agents-backup-<UTC-timestamp>/` before writing. The backup directory and a manifest are reported in the apply summary.

Rollback: copy back from the backup directory.

## Edge cases

### Multi-zone agents

A single agent file belongs to exactly one zone. If an agent's job spans UNTRUSTED-INPUT + HIGH-TRUST responsibilities, split it into two agent files. A single agent with both capabilities in a single session is exactly the attack chain zones are meant to prevent.

### Unclassified agents

Agents on disk that are not listed in `zones.yaml` default to `mcpServers: []` (no MCPs loaded) and are reported as UNCLASSIFIED. This is fail-closed by design — safer than guessing a zone.

To intentionally leave an agent with the default full MCP access (not recommended), note it in `zones.yaml` with a zone but no MCP list — the tool still tracks it as classified.

### Agents outside the agents directory

Only `.md` files directly under the agents directory (default `~/.claude/agents/`) are processed. Nested directories are not descended into. If you use a nested structure, adjust `AGENTS_DIR` and the script's glob accordingly.

### Zone taxonomy extension

Adding a fifth zone would be a breaking change to zones.yaml. Don't. Either fit the new use case into an existing zone or argue on a PR that the taxonomy itself must grow.
