# Swanlake Notion Master Page Template

> Notion-flavored markdown for the Swanlake master page sections. The
> security-watchdog Routine reads this file (resolving any @import
> directives) and writes it to the Swanlake master page on Notion.
> Edit here, not on Notion directly.

---

<callout icon="🟢" color="green_bg">
	**PUBLIC OSS — v0.1 LIVE** — Defense-in-depth primitives for Claude Code and agentic applications. Apache 2.0. Branch-protected, signed-commits-only.
	Repo: [github.com/confirmxxx/Swanlake](https://github.com/confirmxxx/Swanlake) · License: Apache-2.0 · Maintained by Tristar AGS
</callout>

Swanlake is the **Subroutine Layer**: a set of small, composable primitives that sit beneath an AI agent (whether the agent runs inside Claude Code on a developer machine or as an autonomous agent inside a production application). Turn on the primitives that address your specific threats; each addresses a specific attack class. None promise perfect defense.

This page is a **read-only source of truth** for what Swanlake is, what's shipped, and what's queued. It is automatically propagated from `canon/notion-template.md` in the repo via the security-watchdog Routine — edit there, not here.

---

# Why this exists

Autonomous AI agents in 2026 live in a hostile environment. Recent academic work is unambiguous:

- A **single poisoned email** can cause an agent to exfiltrate SSH keys with >80% success in a multi-agent workflow (arXiv:2601.07072).
- **0.0005%** of a corpus — five documents in millions — is enough to flip retrieval-augmented generation to the attacker's narrative (PoisonedRAG, arXiv:2402.07867).
- Adaptive attacks achieve **>85% success** against state-of-the-art defenses for agentic coding assistants (arXiv:2601.17548).
- MCP **tool descriptions** loaded at session start are a first-class injection surface (arXiv:2603.22489, 2506.02040).
- Agent skill files are treated as trusted system prompts — and a modified skill bypasses every web-content defense downstream (arXiv:2510.26328).

Native platform features (Claude Code sandboxing, isolated-context WebFetch, MCP OAuth 2.1 incremental scope, model-layer RL against prompt injection) are strong baselines and cut the attack surface materially. They do not eliminate the residual risk. **Swanlake is the layer that sits on top.**

---

# What's in the box

| Package | What it is | Defends against | Status |
|---|---|---|---|
| **Defense Beacon** | Zero-trust surface markers with per-surface canary attribution, local-only registry, fail-closed staleness gate (rule A11) | Surface-content propagation · undetected exfiltration via fetched content · stale-posture drift | 🟢 Reference impl shipped |
| **Trust Zones** | 4-class agent taxonomy (UNTRUSTED-INPUT / INTERNAL / HIGH-TRUST / SEGREGATED) with per-agent MCP-server scoping | Privilege escalation via delegation · cross-agent compromise · over-broad MCP grants | 🟢 Reference impl shipped |
| **Reflex Purity** | AST-level lint enforcing "no LLM in the latency-critical hot path" | Coordinated-agent manipulation in trading/control loops · LLM-induced non-determinism in reflex paths | 🟢 Paper + pattern + reference impl shipped |
| **Reconciler** | Cross-surface autonomous sync. Single canon source propagates via @import to CLAUDE.md, watchdog to Notion, systemd timer to vault. | Surface drift · cross-session manual sync overhead · inconsistent rule deployment | 🟢 Phase 1 shipped |
| **White Cells** | Continuous AI red team. 6 attack-class personas. Closure-rate metric with kill criterion. | Defense-stack drift · undetected coverage gaps · theatre-vs-real distinction | 🟡 Phase 1+2+3 alpha shipped |

Source: [public repo](https://github.com/confirmxxx/Swanlake) — full reference implementations under `defense-beacon/`, `trust-zones/`, `reflex-purity/`, `reconciler/`, `experiments/white-cells/`.

## Supporting documentation

All docs live in the public repo:

- `THREAT-MODEL.md` — vectors Swanlake defends against, vectors it does not, the honesty problem
- `DEPENDENCIES.md` — native platform features Swanlake depends on
- `NON-GOALS.md` — what Swanlake is explicitly NOT
- `docs/adversarial-research-pattern.md` — hardened research-dispatch discipline
- `docs/reflex-purity-pattern.md` — the reflex-purity principle in depth
- `docs/how-this-fits-above-native-claude-code.md` — the layering relative to native platform features
- `reconciler/README.md` + `reconciler/OPERATOR-SETUP.md` — Reconciler usage + setup

---

# Where Swanlake fits in the agent immune system

Swanlake covers the **input path**: it stops hostile content from steering an agent at ingestion time. It does not enforce policy on agent actions. For the output path — deterministic policy enforcement on every action before execution — pair Swanlake with Microsoft Agent Governance Toolkit (AGT) or equivalent policy middleware.

| Layer | Cuts which arrow | Substrate |
|---|---|---|
| Swanlake (this project) | malicious input → agent | Per-surface canary attribution + trust-zone scoping + reflex purity. Operator-grade primitives. |
| AGT | compromised agent → harmful action | Deterministic policy enforcement, zero-trust identity, execution rings, kill switch. Enterprise infrastructure. |

Neither alone is sufficient. AGT stops a compromised agent from executing a bad action; Swanlake stops the agent from getting compromised in the first place. The OWASP Agentic Top 10 spans both halves (ASI-01 through ASI-10).

**Deployment cost is asymmetric:** AGT requires containers, a DevSecOps team, and per-language SDK integration; Swanlake is one Claude Code subscription plus a repo install. Same operator who runs AGT in production still benefits from Swanlake — different budget, different team, different deployment surface.

---

# Hard rules

@~/projects/Swanlake/canon/operating-rules.md

---

# Live links

- **Repo:** [github.com/confirmxxx/Swanlake](https://github.com/confirmxxx/Swanlake)
- **License:** [Apache-2.0](https://github.com/confirmxxx/Swanlake/blob/main/LICENSE)
- **Issues:** [github.com/confirmxxx/Swanlake/issues](https://github.com/confirmxxx/Swanlake/issues)
- **Pull requests:** [github.com/confirmxxx/Swanlake/pulls](https://github.com/confirmxxx/Swanlake/pulls)
- **CI status:** [Actions tab](https://github.com/confirmxxx/Swanlake/actions)

---

# Roadmap — what's next

## Near-term (queued, full design specs in vault)

- **White Cells full activation** — Phase 1+2+3 alpha is shipped. Full activation = wire the supervising Claude Routine + dedicated unprivileged Linux user + nftables egress allowlist + scheduled weekly persona swarm runs against a fixture sandbox. Queued behind passive triggers (see below).
- **Reconciler Phase 2 — Backend abstraction** — make Reconciler stack-agnostic. One Backend protocol, N swappable implementations: Notion / Obsidian / SQLite RAG / Pinecone / Confluence / GitHub wiki / plain markdown dir. Queued behind a real second-backend need.
- **Continuous-Eval CI** — wire promptfoo + PyRIT + Garak into CI with a regression budget. Every PR auto-proven against published attack corpora; merge fails if detection drops past tolerance. Queued behind ≥30 days of loop-closure baseline data.

## Trigger conditions (any one pulls a queued item forward)

- ≥1 third-party adopting Swanlake in production (track GH stars / forks / issues)
- External user reports a defense regression
- A sibling project under the same operator umbrella needs a non-Notion backend
- Loop-closure metric accumulates ≥30 days of clean data → real baseline for regression budget
- A specific Swanlake primitive needs a feature one of the queued items already proved out

## Out of scope (deliberately)

- **Output-path policy enforcement.** Use [Microsoft AGT](https://github.com/microsoft/agent-governance-toolkit) or equivalent. Swanlake covers the input path only — see "Where Swanlake fits in the agent immune system" above.
- **Per-tenant multi-customer scaling.** Single-operator design today; multi-tenant is a different architectural pattern.
- **Replacing native Claude Code platform features.** Swanlake layers ABOVE the native sandbox / OAuth scoping / model-layer RL — does not replace them.
- **Cloud-hosted SaaS.** Operator-installed only; secrets stay local.

## Honest gaps (publicly tracked)

- **One-maintainer artifact at v0.1.0.** Bus factor of 1. Contributor flow has the discipline baked in (pre-publish scans, signed commits, DCO) but no second human reviewer yet.
- **Notion sync attestation gap.** The watchdog Routine writes Notion in the cloud; the local status engine can't directly attest those writes. Future: Notion-side `modified_at` polling.
- **White Cells personas are scaffolds.** Phase 2/3 shipped real personas + auto-triage, but the attack-library payloads are stubs; full DeepTeam / PyRIT / promptfoo integration is post-trigger.
- **No external integrations or community CI yet.** Awesome-list discovery PR pending merge externally.

## Loop-closure metric (the meta-defense)

Every proposed change is measured: did it produce a real hardening artifact within 14 days? 7-day rolling ratio currently 0.70 — well above the 30% theatre threshold and above the 50% legit threshold. Kill criterion: if the ratio drops below 30% for 4 weeks, the project (or the relevant primitive) is retired.

---

*Maintained by Tristar AGS. Auto-propagated from `canon/notion-template.md` via the security-watchdog Routine.*
