# Non-Goals

Swanlake is intentionally narrow. Here is what it is NOT.

## Not a bash-firewall

We do not ship a PreToolUse Bash-command interceptor. That territory is covered well by:
- [`kenryu42/claude-code-safety-net`](https://github.com/kenryu42/claude-code-safety-net) — mature semantic parser for `bash -c` wrappers, interpreter one-liners, destructive verbs
- The Claude Code native subprocess sandbox (Linux bubblewrap, macOS Seatbelt, WSL2) — OS-level filesystem + network isolation
- The Claude Code native command blocklist (curl/wget blocked by default)

If you run a Claude Code agent that executes shell, use safety-net + native sandbox. Do not ask Swanlake to do this.

## Not a prompt-injection scanner

Content-level injection scanning (hidden-text detection, zero-width unicode stripping, HTML-comment smuggling) is covered by:
- [`lasso-security/claude-hooks`](https://github.com/lasso-security/claude-hooks) — 5-category PostToolUse scanner
- [`protectai/llm-guard`](https://github.com/protectai/llm-guard) — 35 scanners including PromptInjection, Secrets, Anonymize
- Claude Code native isolated-context WebFetch (renders scraped content in a separate context window)
- Anthropic's model-level training against prompt injection (Opus 4.5, ~1% attack success for browser agents)

Swanlake does not reimplement these. If you need per-tool-output content scanning, install one of the above.

## Not an MCP scanner

Static analysis of installed MCP configs for known bad patterns is covered by:
- [`riseandignite/mcp-shield`](https://github.com/riseandignite/mcp-shield) — scans for tool poisoning, exfil channels, cross-origin escalations, hidden instructions

Swanlake's trust-zones package operates at a different layer: it says *which MCPs are allowed per agent*. Pair with mcp-shield to *also* scan those MCPs for bad content.

## Not a policy DSL

[`invariantlabs-ai/invariant`](https://github.com/invariantlabs-ai/invariant) provides a rule-engine DSL for constraining agent traces with an MCP proxy. Swanlake does not try to be a policy language. The trust-zones taxonomy is opinionated config, not a DSL — four zones, a YAML mapping, done.

If your use case requires expressive per-trace policy, use Invariant. You can layer Swanlake beacons and trust zones on top without conflict.

## Not a red-team toolkit

Offensive tooling — adversarial prompt generation, attack-corpus runners, evasion testing — is covered by:
- [`NVIDIA/garak`](https://github.com/NVIDIA/garak)
- [`microsoft/PyRIT`](https://github.com/microsoft/PyRIT)

Swanlake is defensive. Red-team tools belong alongside, not inside.

## Not an auto-deploy mechanism for remote surfaces

`swanlake beacon` does not auto-deploy canaries to Notion, Supabase env vars, Vercel env vars, GitHub repos, or Claude Routines. By SPEC: a deploy credential (Notion token, Supabase service-role key, Vercel API token, GitHub PAT, Routines token) is a single point of compromise that lets an attacker silently rotate canaries to attacker-known values, after which every exfiltration of a "canary" passes detection. The cheapest defense is to never have such a credential.

LOCAL surfaces (project `CLAUDE.md`, vault notes) are auto-deployable behind a 12-step safety machine because the blast radius is bounded by `git diff` and reversible by `git checkout`. REMOTE surfaces stay checklist-only; `swanlake beacon checklist` emits paste-ready markdown the operator pastes by hand. See [`docs/v0.3-beacon-deploy-spec.md`](./docs/v0.3-beacon-deploy-spec.md) §10 for the full hard-NO list.

## Not a managed service

There is no hosted Swanlake. There is no dashboard service. The reference implementations run on your machine, and the canary registry lives on your machine. This is a design decision, not an oversight: a cloud-hosted canary registry is a centralized attack surface that defeats the local-attribution guarantee.

If you operate a fleet of agent deployments and need aggregated telemetry, build your own aggregator that reads individual registries via authenticated pull — never push the registry to cloud.

## Not a compliance tool

Swanlake does not produce SOC 2, ISO 27001, HIPAA, or PCI-DSS evidence. It is a defense framework, not an audit tool. If you need compliance evidence of agent security, refer to the native Anthropic Trust Center (trust.anthropic.com) and ordinary evidence-collection tools.

## Not a replacement for good judgment

Swanlake raises the cost of accidental compliance with injected content. It attributes reads after the fact. It scopes capability. It does not make decisions for you. An operator who merges an attacker-controlled config because the PR description was persuasive is a failure mode Swanlake cannot prevent.

Use Swanlake as one layer of a defense-in-depth posture. If Swanlake is the whole defense, the defense is theater.

## Not an excuse to skip updates

If Claude Code native features obsolete a Swanlake primitive — for example, if a future release ships per-surface canary attribution as a platform feature — the right move is to migrate off Swanlake and cite the native feature. The maintainer will document the migration path.
