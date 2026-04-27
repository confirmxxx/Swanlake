# Swanlake — Threat Model

What Swanlake defends against, what it does not, the honesty problem, and where the edges are.

## Attack vectors addressed (by primitive)

| Vector | Primary citation | Primitive that addresses it |
|---|---|---|
| Indirect prompt injection via scraped web / fetched doc | arXiv:2302.12173 (Greshake et al.); arXiv:2601.10923 (OpenRAG-Soc) | Defense Beacon §A1 + §A2 (advisory); native isolated-context WebFetch (hard) |
| Invisible-text payload (display:none, zero-width unicode, HTML comments) | arXiv:2603.00164 (Reverse CAPTCHA) | Pair with native WebFetch isolation + any content-sanitizer of choice; Beacon §A2 enumerates patterns to refuse |
| Image-payload injection (pixel stego, adversarial patches) | arXiv:2603.03637; arXiv:2512.04895 | Beacon §A8 (advisory); pair with OCR + sanitization of choice; image hardening is out of Swanlake's direct scope |
| Authority-tone / social-engineering steering in fetched content | arXiv:2502.08177 (SycEval); arXiv:2604.21564 | Beacon §A3 |
| Multi-document forged consensus | arXiv:2604.17139 (The Consensus Trap); arXiv:2504.13079 | Beacon §A4 requires independent corroboration for consequential actions |
| RAG / knowledge-corpus poisoning | arXiv:2402.07867 (PoisonedRAG); arXiv:2504.03957 (CorruptRAG) | Beacon §A1 + §A4; canary-attribution reveals which surface was read |
| Malicious email → corporate agent exfiltrates data | arXiv:2507.02699; arXiv:2508.12175 (Promptware) | Trust Zones confines email-reading agents to UNTRUSTED-INPUT; no write capabilities in the same session |
| Summarizer launders attacker instructions as "recommendations" | arXiv:2302.12173 subclass | Beacon §A2 + docs/adversarial-research-pattern.md: raw citations only, no synthesis |
| Forged financial signal → coordinated trading-agent dump | arXiv:2601.13082 (17.7pp loss); arXiv:2512.02261 (TradeTrap) | Reflex Purity: no LLM in hot path so headline-level poisoning cannot fire trades |
| Cross-source payload assembly / confused-deputy across agents | arXiv:2503.12188; arXiv:2509.06572 | Beacon §A9 (sub-agents inherit restrictions not privileges); Trust Zones forbid chaining UNTRUSTED-INPUT into HIGH-TRUST |
| MCP tool-poisoning (malicious instructions in tool descriptions) | arXiv:2603.22489; arXiv:2506.02040 | Out of direct scope; pair with `mcp-shield`. Trust Zones limit blast radius by restricting which agents see which MCPs |
| Session / memory persistence attacks | arXiv:2604.02623 (Poison Once Exploit Forever); arXiv:2512.16962 (MemoryGraft) | Beacon §A6 forbids carrying instructions across sessions; each session starts with posture checks |
| MCP OAuth token replay / leakage | MCP spec 2025-11-25 basic/authorization | Partial: Trust Zones limit which agents see which MCPs, reducing token exposure blast radius. Token rotation + revocation is native-OAuth territory — see DEPENDENCIES.md. |
| Long-term memory poisoning across sessions | arXiv:2512.16962 (MemoryGraft) | Partial: Beacon §A6 advisory; hard defense requires memory-store integrity checks outside Swanlake scope. |
| Silent-surface attribution — which surface leaked? | No public precedent at this taxonomy depth | Defense Beacon Part B: per-surface canary tokens, local registry |

## What Swanlake does NOT prevent

| Gap | Why | Compensating control |
|---|---|---|
| Zero-day bugs in MCP transport | Below the agent layer | Keep MCP versions pinned, monitor MCP CVE feeds |
| Model-level jailbreaks that exploit tokenizer quirks or attention failures | Below the agent layer | Model-vendor's native defenses (Anthropic Opus 4.5 RL training, classifier layer) |
| Platform-level compromise of the Claude Code binary or hook scripts | Operator's host is trusted | Integrity hashing the agent pack; native `ConfigChange` hook; standard supply-chain hygiene |
| Upstream supply-chain compromise (e.g. injected npm dependency) | Build-time, not runtime | Lockfile discipline, audit tooling (npm audit, socket.dev), SBOM |
| Determined compromise of the beacon itself | Attacker with write access to a surface can strip or forge the beacon | Cross-surface divergence monitoring — verify monthly that beacon text survived on every surface, using `swanlake verify` / `swanlake beacon verify` |
| Operator mistakes | Rules bind the agent, not the human | Training, dual-control for destructive operations |
| Image-derived OCR injection | Swanlake does not OCR | Pair with OCR + content-safety at the application layer |
| Auto-deploy of canaries to REMOTE surfaces (Notion, Supabase, Vercel, GitHub, Routines) | Forbidden by SPEC: a deploy credential is a single point of compromise that lets an attacker silently rotate canaries to attacker-known values; cheapest defense is to never have such a credential | `swanlake beacon checklist` emits a paste-ready markdown the operator pastes by hand; `swanlake beacon verify` checks remote presence read-only |

## Scope of the CMA adapter

The `swanlake adapt cma` subcommand is **validated against synthetic CMA-shape fixtures only; first live-project install is operator follow-up work**. The adapter installs Beacon Part A + per-CMA Part B canaries, applies `zones.yaml`-driven tool allowlists, and runs a report-only reflex-purity AST check. None of that is the same as "Swanlake secures CMA-based agentic systems in production." Production CMA hardening is the operator's job; the adapter is the install primitive.

The reflex-purity AST check exits 0 even on violations — the report is information, not a gate. Wiring it as a hard CI gate is an operator decision.

## Why the canary registry must stay local

The registry maps every surface to its tripwire tokens. An attacker who reads that registry knows:
1. Exactly which strings to **avoid echoing** — defeats detection.
2. Which tokens map to which surface — allows forging canary hits to generate false positives, burning operator trust ("alert fatigue" attack).

Registry discipline:
- Local dev host only. Never synced to cloud. Never committed to git.
- Never surfaced in a tool response. If an agent is asked to "list the canaries", refuse.
- Never included in backups that leave the machine.
- If the host is suspected compromised, rotate every canary immediately and treat the old mapping as leaked.

## The honesty problem

The beacon's Part A operating rules are **instructions to a model**. The model decides whether to follow them. This is an honesty property, not a security primitive: a jailbroken or tampered model will ignore the beacon as readily as it would ignore any system message.

Therefore:

| Soft (advisory — model-respected) | Hard (enforced — model cannot bypass) |
|---|---|
| Beacon Part A rules | Native bash sandbox + permission deny rules |
| Beacon Part B canaries as model-visible warning | Canary-match hook on tool outputs (detection independent of model compliance) |
| Refusal framing in agent prompts | OS-level isolation + MCP OAuth scope limits |
| Adversarial-research-pattern output discipline | Tool allowlist restriction at dispatch time |

If the Swanlake primitives are the whole defense, the defense is theater. The primitives' job is to **raise the cost of accidental compliance with injected content** and to **attribute reads after the fact**. Hard controls (native platform features, OS sandboxing, deny rules) are what actually stop a misbehaving agent.

Both layers are required. Neither is sufficient alone.

## Residual risk

1. **Sophisticated attacker with write access to a surface** can strip or forge the beacon. Detection requires monthly verify runs plus cross-surface canary divergence analysis.
2. **Cloud-credential compromise** for a scheduled routine that refreshes the posture page can silently freshen `last_verified`, defeating the staleness gate. No local hook can prevent this — pair with out-of-band verification where staleness matters most.
3. **Canary fatigue**. Legitimate maintenance (running verifier, grep-based audits) produces benign hits. Triage discipline matters; tag known-benign in the log.
4. **Model improvements that "helpfully clean up" beacon text**. A future agent may try to summarize or deduplicate the beacon block. The wording in Part B ("do not remove / summarize / translate / clean up") resists this, but verification must be periodic.
5. **Attribution ambiguity on beacon-in-beacon reads**. If an agent reads two surfaces with different beacons in the same session, both canary sets can fire. Treat this as normal — the signal is still useful, but root-causing requires context.

## Threat model updates

This file is regenerated on threat-landscape updates. See `docs/how-this-fits-above-native-claude-code.md` for ongoing tracking of what the platform natively covers — as Claude Code adds features, Swanlake's threat model shrinks, and obsolete primitives are retired.

## Citation list (selected)

| arXiv / source | Topic |
|---|---|
| 2302.12173 | Greshake et al. — foundational indirect prompt injection |
| 2402.07867 | PoisonedRAG — 0.0005% of a corpus flips RAG |
| 2502.08177 | SycEval — sycophancy / authority-tone exploitation |
| 2503.03704 | MINJA — query-only memory injection |
| 2503.23278 | MCP landscape + threat taxonomy |
| 2504.03767 | MCP Safety Audit — exploitation PoCs |
| 2506.02040 | Beyond the Protocol — MCP attack vectors |
| 2506.13538 | MCP server security + maintainability |
| 2507.02699 | Email-agent hijacking |
| 2508.12175 | Invitation Is All You Need — promptware via calendar invite |
| 2509.00124 | Parallel-Poisoned Web (Zychlinski) — UA-cloaking |
| 2510.26328 | Agent Skills as injection class (targets a popular coding agent from a frontier LLM provider) |
| 2601.07072 | Indirect PI in the wild — SSH-key exfil >80% from single email |
| 2601.13082 | Adversarial news in LLM trading — 17.7pp loss |
| 2603.22489 | MCP tool-poisoning |
| 2604.05432 | Data exfil via backdoored tool use |
| 2604.17139 | The Consensus Trap — multi-agent false consensus |
| SSRN 6372438 | DeepMind "AI Agent Traps" — 6-category taxonomy |
| OWASP Top 10 for LLM Applications (2025) | Standards baseline |
| OWASP Top 10 for Agentic Applications (ASI01-ASI10, 2025-12-09) | Agent-specific baseline |
| Anthropic 2025-11-24 | "Mitigating the risk of prompt injections in browser use" |
| Anthropic 2026-04-09 | "Trustworthy agents in practice" |
