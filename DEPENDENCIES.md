# Dependencies — what Swanlake relies on, what you get for free

Swanlake sits above a stack of native platform features. Those features do real work and Swanlake does not try to duplicate them. This document lists what Swanlake depends on, what the platform provides natively, and where the boundary is.

As native features evolve, Swanlake's primitives shrink. If a future Claude Code release ships per-surface canary attribution or a spec-level trust-zone system, the affected Swanlake package is deprecated with a migration note.

## Native Claude Code features Swanlake depends on

### Subprocess sandbox
- **Where:** https://code.claude.com/docs/en/sandboxing
- **What:** OS-level filesystem + network isolation for Bash subprocesses. Linux bubblewrap, macOS Seatbelt, WSL2. Shipped 2025-10. Reports 84% permission-prompt reduction in Anthropic's internal usage.
- **Covers:** the 80% bash-safety case — arbitrary file writes outside the project tree, arbitrary network fetches. Command blocklist disables curl/wget by default.
- **Swanlake boundary:** Swanlake does NOT ship a bash firewall. If the native sandbox is enabled, trust it as the primary gate. Add hardening hooks (see the non-goals doc for recommended OSS) only if your threat model requires defense-in-depth on specific shell patterns.

### Isolated-context WebFetch
- **Where:** `code.claude.com/docs/en/security`
- **What:** "Web fetch uses a separate context window to avoid injecting potentially malicious prompts." Native to the Claude Code harness.
- **Covers:** the primary prompt-injection vector for un-sanitized web content.
- **Swanlake boundary:** Swanlake assumes WebFetch uses an isolated context. Defense Beacon advisory rules (§A1 + §A2) compound this with model-facing guidance but do not replace it. For Playwright-MCP or other browser-automation paths that do NOT use isolated context, a separate sanitizer is needed — see NON-GOALS.md for recommendations.

### Hook event surface
Swanlake's reference implementations are Claude Code hooks. Required events:

| Event | Used by |
|---|---|
| `SessionStart` | Reference impls of integrity check, watchdog nudge (user-supplied, not shipped here) |
| `PreToolUse` | Reference impls of content-safety check (user-supplied, not shipped here) |
| `PostToolUse` | `canary-match.sh` reference impl — scans every tool input/output for canary strings |
| `PermissionDenied` | Reference impl of permission-denied logger (user-supplied) |
| `ConfigChange` | Agent-pack integrity (user-supplied on top of native config auditing) |

If you run Claude Code < 2.1.x, not all events may be available. Check `code.claude.com/docs/en/hooks` for your version.

### Permission system
- **Where:** `code.claude.com/docs/en/security#permissions`
- **What:** allow / ask / deny rules. "Fail-closed matching: Unmatched commands default to requiring manual approval."
- **Covers:** tool-level gating. Swanlake trust zones extend this — they determine *which MCPs* an agent loads. Permissions determine *which tools within those MCPs* are approved.
- **Swanlake boundary:** trust zones are a pre-step before the permission system. Zones decide the MCP set; permissions decide the per-tool verdict.

### MCP OAuth 2.1 incremental scope
- **Where:** https://modelcontextprotocol.io/specification/draft/basic/authorization
- **What:** Authorization-server discovery, PKCE required, step-up scope consent via WWW-Authenticate challenges. SEP-835 merged 2025.
- **Covers:** principle-of-least-privilege at the MCP-token level. A token issued for `mcp:tools-basic` cannot mint privileged operations without a fresh consent step.
- **Swanlake boundary:** Swanlake trust zones model *which agents can see* an MCP server. OAuth scopes model *what the agent can do* with that server's tools. Both layers are required; OAuth is native, trust zones are Swanlake.

### Model-layer defenses
- **Where:** Anthropic news 2025-11-24, model card
- **What:** "Training Claude to resist prompt injection" — RL on simulated web content. Opus 4.5 browser-agent attack success reduced to ~1% (from prior 10.8%).
- **Covers:** direct prompt injection at the most common attack surface.
- **Swanlake boundary:** the model-layer defense is the baseline. Swanlake assumes it's on. Swanlake's primitives handle the residual ~1% + the classes that don't get trained out (surface attribution, capability scoping, discipline patterns).

### Classifier scanning of untrusted content
- **Where:** Anthropic news 2025-11-24
- **What:** "scan all untrusted content that enters the model's context window, and flag potential prompt injections."
- **Covers:** additional content-level detection beyond the trained-in defense.
- **Swanlake boundary:** Swanlake does not reimplement a classifier. If you need user-visible content-level scanning (flags in your own logs), install one of the tools in NON-GOALS.md.

### Credential storage
- **What:** "API keys and tokens are encrypted."
- **Swanlake boundary:** Swanlake does not re-encrypt or store credentials. Native storage is trusted.

### `anthropic-experimental/sandbox-runtime` (optional)
- **Where:** https://github.com/anthropic-experimental/sandbox-runtime
- **What:** Reusable sandbox primitive, extracted from Claude Code.
- **Swanlake boundary:** optional dependency for downstream integrations that want to sandbox agent-spawned subprocesses outside of Claude Code. Not required.

## Third-party OSS Swanlake is designed to coexist with

| Project | Role | Why Swanlake doesn't duplicate |
|---|---|---|
| `kenryu42/claude-code-safety-net` | Mature PreToolUse bash semantic parser | Bash hardening is not a Swanlake primitive |
| `slavaspitsyn/claude-code-security-hooks` | 7-layer hook bundle | Some layers (canary files) are covered in a different style by Defense Beacon; the rest should be used alongside |
| `lasso-security/claude-hooks` | PostToolUse prompt-injection defender | Content-safety scanning is not a Swanlake primitive |
| `riseandignite/mcp-shield` | Static scanner for MCP configs | MCP-config static analysis complements Swanlake trust zones |
| `invariantlabs-ai/invariant` | Agent guardrails with policy DSL + MCP proxy | Swanlake is config-shaped, not DSL-shaped; use Invariant where expressive policy matters |
| `protectai/llm-guard` | 35 input/output scanners | Content-level scanning at the app layer |
| `carlrannaberg/claudekit` | File-guard + workflow tools | Complementary workflow primitives |

## Anthropic platform features outside the Claude Code harness

Not direct dependencies, but inform the design:

- **Anthropic Trust Center** — https://trust.anthropic.com — SOC 2 Type 2, ISO 27001, organizational compliance. Swanlake is orthogonal.
- **Claude Managed Agents (CMA)** — per-session VM, network allowlist, scoped credentials, git-push branch restrictions, audit logs. Runtime surface for Swanlake trust zones when agents run under CMA.
- **HackerOne VDP** — https://hackerone.com/anthropic-vdp — path to report a platform-level evasion that bypasses the native layer.

## Version compatibility

Swanlake reference implementations target:
- Claude Code 2.1.x or later (for the full hook event surface)
- MCP specification 2025-11-25 or later (for OAuth 2.1 incremental scope)
- Python 3.10+
- Bash 4.0+

Runtime integrations for Node (`@swanlake/node`) and Python (`swanlake-py`) are on the roadmap and will declare their own compatibility matrix.

## Upstream compatibility policy

Swanlake's contract is:
- If a native Claude Code feature subsumes a Swanlake primitive, that primitive is deprecated with a migration note on the next minor release.
- Swanlake SPEC.md files track the boundary to native features explicitly. When the boundary moves, the SPEC is updated.
- Breaking changes to native features that affect Swanlake are tracked in each package's CHANGELOG.

The point of Swanlake is to disappear upward, not to grow in scope. A smaller Swanlake is a stronger Swanlake.
