# How Swanlake fits above native Claude Code

Native Claude Code ships strong security defaults. Swanlake is the thin layer above — not a replacement. This doc draws the boundary explicitly.

## Layering diagram

```
┌────────────────────────────────────────────────────────────────────┐
│                    SWANLAKE (this repo)                             │
│                                                                     │
│  Defense Beacon  ·  Trust Zones  ·  Reflex Purity  ·  Adversarial   │
│  surface         ·  per-agent    ·  no-LLM in      ·  research      │
│  attribution     ·  MCP scoping  ·  hot-path       ·  dispatch      │
│  + canaries      ·  + taxonomy   ·  AST lint       ·  discipline    │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼  hooks into / depends on
┌────────────────────────────────────────────────────────────────────┐
│             NATIVE CLAUDE CODE (trust it; don't replace)            │
│                                                                     │
│  Permission system (allow/ask/deny)                                 │
│  Subprocess sandbox (bubblewrap / Seatbelt / WSL2)                  │
│  Isolated-context WebFetch                                          │
│  Command blocklist (curl/wget by default)                           │
│  Hook events: SessionStart · PreToolUse · PostToolUse ·             │
│              PermissionDenied · ConfigChange · PreCompact · ...     │
│  Trust verification for first-time codebases + new MCPs             │
│  Credential storage (encrypted)                                     │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼  relies on
┌────────────────────────────────────────────────────────────────────┐
│                  ANTHROPIC PLATFORM + MCP                           │
│                                                                     │
│  Model-layer RL against prompt injection (Opus 4.5 — ~1% ASR)       │
│  Classifier scanning of untrusted content                           │
│  MCP OAuth 2.1 incremental scope + PKCE                             │
│  Claude Managed Agents isolation (cloud sessions)                   │
│  Trust Center (SOC 2, ISO 27001)                                    │
└────────────────────────────────────────────────────────────────────┘
```

## Per-primitive boundary

### Defense Beacon

| Who does what | Native | Swanlake |
|---|---|---|
| Isolate untrusted web content from the main context window | ✅ WebFetch isolated context | — |
| Detect prompt-injection patterns in scraped content | Partial: classifier layer (invisible to operator) | Advisory Part A rules model-facing; canary attribution operator-facing |
| Attribute a leak to a specific surface | — | ✅ Part B per-surface canary tokens + local registry |
| Enforce staleness gates on threat posture | — | ✅ Rule A11 optional extension (fail-closed at 7 days) |
| Hard-stop on content-level injection | Partial: native classifiers | — (beacon is advisory; pair with hard controls) |

### Trust Zones

| Who does what | Native | Swanlake |
|---|---|---|
| Deny specific tools to specific commands | ✅ Permission system (allow/ask/deny) | — |
| Load only approved MCPs per Claude Code session | Partial: per-session MCP config | ✅ Per-agent `mcpServers:` frontmatter taxonomy with 4 zones |
| OAuth scope per MCP capability | ✅ MCP OAuth 2.1 incremental scope + PKCE | — |
| Prevent chaining UNTRUSTED-INPUT → HIGH-TRUST agents in one session | — | ✅ Zone semantics |
| Classify 100+ agents into coherent privilege groups | — | ✅ 4-zone taxonomy |

### Reflex Purity

| Who does what | Native | Swanlake |
|---|---|---|
| Sandbox agent-spawned subprocesses | ✅ Native sandbox | — |
| Prevent LLM calls from firing trades | — | ✅ AST lint + Brain/Reflex contract pattern |
| Contract for advisory-output → deterministic-validation | — | ✅ Pattern doc |

### Adversarial-Research Dispatch

| Who does what | Native | Swanlake |
|---|---|---|
| Restrict an agent's tool surface at dispatch | ✅ Agent config allows tool allowlisting | — |
| Enforce raw-citation output discipline | — | ✅ Dispatch prompt template |
| Refuse chain-fetches from fetched pages | — | ✅ Discipline rule (advisory) |
| Source-allowlist enforcement | Partial: via tool allowlist + domain restrictions | ✅ Explicit allowlist in template |

## What the native layer has that Swanlake uses (not replaces)

- `SessionStart` hook — Swanlake reference impls attach here for posture checks.
- `PreToolUse` hook + `PostToolUse` hook — Swanlake reference impls attach for canary-match, content-safety routing.
- `PermissionDenied` hook with `retry: true` JSON response — Swanlake's pattern suggests NEVER retrying for high-trust agents; consumes the native event.
- `ConfigChange` hook — Swanlake's agent-pack integrity pattern layers on top.
- Subprocess sandbox — Swanlake assumes this is enabled. If it's not, Swanlake primitives are weaker but still useful; the operator should enable the native sandbox first.
- Isolated-context WebFetch — Swanlake primitives do not reimplement context isolation.

## What the native layer does that Swanlake chose not to touch

- **Bash-command interception.** Covered by the native sandbox + OSS projects like `kenryu42/claude-code-safety-net`. Swanlake explicitly does not ship a bash firewall.
- **Content-level prompt-injection scanning.** Covered by Anthropic's classifier layer natively + OSS projects like `lasso-security/claude-hooks` and `protectai/llm-guard`. Swanlake does not reimplement content scanners.
- **MCP config static analysis.** Covered by `riseandignite/mcp-shield`. Swanlake's trust-zones is about scoping access, not scanning the servers themselves.

See `NON-GOALS.md` for the full list.

## What future native features would obsolete in Swanlake

Some Swanlake primitives may become redundant as native features evolve:

| If Claude Code ships... | Then Swanlake primitive becomes... |
|---|---|
| Per-surface canary attribution as a platform feature | Defense Beacon Part B → deprecated, migration to native |
| A first-class trust-zone taxonomy in agent config | Trust Zones → deprecated |
| Hot-path purity enforcement as a platform lint | Reflex Purity → deprecated (unlikely; this is domain-specific) |
| Automatic rejection of chain-fetches in WebFetch | Research dispatch rule 6 → covered natively |

The maintenance policy is to track these explicitly in each package's SPEC.md and deprecate with migration notes when native catches up. Swanlake's health is measured by how small it is, not how large.

## Guidance: when NOT to install Swanlake

- You have an ordinary Claude Code setup with a few trusted MCPs, no multi-agent orchestration, no production AI agents. Native defaults are enough.
- You already run `kenryu42/claude-code-safety-net` and don't have a specific need Swanlake addresses.
- You have a policy DSL / guardrail framework (Invariant Labs, NeMo Guardrails) and are happy with the coverage.

Swanlake's fit is when you're orchestrating many agents, reading untrusted surfaces, deploying agents that touch money or sensitive data, and need surface-specific attribution + taxonomy discipline that the per-tool OSS ecosystem doesn't provide.

## Guidance: when Swanlake is most valuable

- Multiple MCPs with different trust profiles (Notion + Supabase + Playwright + Telegram + ...)
- Many custom agents (10+) with overlapping but not identical tool needs
- Production AI agents that take external input (web, email, messaging) and produce external effects (DB writes, messages out, money movement)
- Trading / control loops where attacker-influenced inputs could fire high-cost actions
- Research workflows that themselves fetch adversarial content (security research, threat intel)
