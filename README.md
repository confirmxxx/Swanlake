# Swanlake

**Swanlake** is a defense-in-depth security framework for AI agents — whether those agents run inside Claude Code on your developer machine or as autonomous agents inside production applications. Swanlake is the **Subroutine Layer**: a set of small, composable primitives that sit beneath your agent. You turn on the primitives that address your specific threats; each one addresses a specific attack class. None promise perfect defense.

> Defense-in-depth primitives for Claude Code and agentic applications. Zero-trust surface beacons with per-surface canary attribution, agent trust-zone scoping, hardened research-dispatch discipline, reflex-purity enforcement. Composable. Advisory rules paired with hard controls.

---

## Why this exists

Autonomous AI agents in 2026 live in a hostile environment. Recent academic work is unambiguous:

- A **single poisoned email** can cause an agent to exfiltrate SSH keys with >80% success in a multi-agent workflow (arXiv:2601.07072).
- **0.0005%** of a corpus — five documents in millions — is enough to flip retrieval-augmented generation to the attacker's narrative (PoisonedRAG, arXiv:2402.07867).
- Adaptive attacks achieve **>85% success** against state-of-the-art defenses for agentic coding assistants (arXiv:2601.17548).
- MCP **tool descriptions** loaded at session start are a first-class injection surface (arXiv:2603.22489, 2506.02040).
- Agent skill files are treated as trusted system prompts — and a modified skill bypasses every web-content defense downstream (arXiv:2510.26328).

Native platform features (Claude Code sandboxing, isolated-context WebFetch, MCP OAuth 2.1 incremental scope, model-layer RL against prompt injection) are strong baselines and cut the attack surface materially. They do not eliminate the residual risk. Swanlake is the layer that sits on top: composable primitives that add surface-specific attribution, trust-zone scoping, research-dispatch discipline, and fail-closed staleness gates — without reinventing what the platform already does.

## What's in the box

Each package is a primitive. Adopt one, many, or all.

| Package | What it is | Claude Code | Agentic apps |
|---|---|---|---|
| [`defense-beacon/`](./defense-beacon/) | Zero-trust surface markers with per-surface canary attribution + local registry discipline + staleness gate | ✅ reference impl | Spec applies; runtime integration on the roadmap |
| [`trust-zones/`](./trust-zones/) | Four-zone taxonomy (UNTRUSTED-INPUT / INTERNAL / HIGH-TRUST / SEGREGATED) for scoping MCP / tool access per agent | ✅ reference impl | Spec applies; runtime integration on the roadmap |
| [`reflex-purity/`](./reflex-purity/) | Pattern + AST-lint sketch for "no LLM in the hot path" — prevents coordinated-agent manipulation in latency-critical systems | Paper + pattern | Paper + pattern |

Supporting documentation:

| Document | Covers |
|---|---|
| [`THREAT-MODEL.md`](./THREAT-MODEL.md) | Vectors Swanlake defends against, vectors it does not, the honesty problem |
| [`DEPENDENCIES.md`](./DEPENDENCIES.md) | Native platform features Swanlake depends on (Claude Code sandbox, MCP OAuth, etc.) — what you get for free |
| [`NON-GOALS.md`](./NON-GOALS.md) | What Swanlake is explicitly NOT |
| [`docs/adversarial-research-pattern.md`](./docs/adversarial-research-pattern.md) | Hardened research-dispatch discipline for defending agents that research security topics themselves |
| [`docs/reflex-purity-pattern.md`](./docs/reflex-purity-pattern.md) | The reflex-purity principle in depth |
| [`docs/how-this-fits-above-native-claude-code.md`](./docs/how-this-fits-above-native-claude-code.md) | The layering — what native does, what Swanlake adds |

## Design principles

1. **Partial automation only.** Primitives produce evidence. They never silently apply config, hook code, deny rules, or MCP changes. Humans decide.
2. **Fail-closed staleness.** If the threat posture is older than N days (default 7), refuse surface-expansion actions (new MCP installs, new tool grants, risk-boundary changes) until refreshed.
3. **Advisory + hard controls.** Rules the model reads are *advisory* (compliance depends on the model). Attribution tripwires + OS-level isolation are *hard* (independent of model behavior). Every Swanlake primitive pairs both.
4. **Don't reinvent the platform.** Native Claude Code already ships a subprocess sandbox, isolated-context WebFetch, a classifier layer, and hook events (SessionStart, PreToolUse, PostToolUse, PermissionDenied, ConfigChange). Swanlake depends on these, doesn't replace them. See `DEPENDENCIES.md`.
5. **Stdlib first.** Reference implementations use Python + bash stdlib where possible. Zero pip installs for the baseline.
6. **Local registry for attribution.** Canary tokens are local secrets. Swanlake ships the schema and generator, never the tokens.

## What it looks like in practice

Once wired, the Swanlake status segment becomes a quiet shield in your terminal:

```
~/projects/myapp  main  opus-4.7  xhigh  🛡
```

Clean shield = clean posture. When something needs attention, flags appear next to it:

| You see | In English |
|---|---|
| `🛡` | All green. Keep working. |
| `🛡?` | No watchdog fired yet. Run it once. |
| `🛡stale:3d` | Posture 3 days old. Fine, but getting dusty. |
| `🛡!stale:9d` | **Stale gate active.** No new MCPs / OAuth grants until refreshed. |
| `🛡canary:1` | Tripwire fired today. Check `~/.claude/canary-hits/`. |
| `🛡exfil:2` | Secret-shape payloads blocked. Check `~/.claude/exfil-alerts/`. |
| `🛡!stale:8d,canary:1` | Multiple issues. Triage newest first. |

Because a dashboard you have to open. A shield in your status line you see every time you glance at your terminal. Full cheat sheet + integration in [`tools/README.md`](./tools/README.md).

## Getting started — Claude Code

```bash
# clone
git clone https://github.com/confirmxxx/Swanlake.git
cd Swanlake

# generate beacon outputs for your surfaces
python3 defense-beacon/reference/make-canaries.py --help

# scope your agent pack by trust zones
cp trust-zones/reference/zones.example.yaml trust-zones/reference/zones.yaml
# edit zones.yaml to list your agents + their zones
bash trust-zones/reference/apply-mcp-scopes.sh --dry-run

# install the canary-match hook in your ~/.claude/settings.json
#   see defense-beacon/README.md for the snippet
```

Each package's README has the full walkthrough.

## Getting started — agentic apps (Node / Python)

Interface contracts are specified in each package's `SPEC.md`. Runtime wrappers for Node (`@swanlake/node`) and Python (`swanlake-py`) are on the v1.1 roadmap. Early adopters can implement against the spec and file issues when gaps appear.

## Compatibility with native Claude Code

Swanlake expects Claude Code 2.1.x+ and the following native features, documented in `DEPENDENCIES.md`:

- Hook events: `SessionStart`, `PreToolUse`, `PostToolUse`, `PermissionDenied`, `ConfigChange`
- Subprocess sandbox (Linux bubblewrap / macOS Seatbelt / WSL2)
- Isolated-context `WebFetch`
- MCP OAuth 2.1 incremental scope

None of these are replaced by Swanlake. They're the substrate.

## Not a competitor to

Swanlake is explicitly NOT trying to replace:

- [`kenryu42/claude-code-safety-net`](https://github.com/kenryu42/claude-code-safety-net) — has a more mature semantic bash parser
- [`slavaspitsyn/claude-code-security-hooks`](https://github.com/slavaspitsyn/claude-code-security-hooks) — 7-layer hook bundle with canary-file precedent
- [`lasso-security/claude-hooks`](https://github.com/lasso-security/claude-hooks) — PostToolUse prompt-injection scanner
- [`invariantlabs-ai/invariant`](https://github.com/invariantlabs-ai/invariant) — MCP proxy with policy-rule DSL

Use them alongside Swanlake where they fit. Swanlake contributes the primitives their stacks don't cover: per-surface canary attribution, trust-zone taxonomy, reflex-purity pattern, adversarial-research discipline. See `NON-GOALS.md`.

## Contributing

See `CONTRIBUTING.md`. Developer Certificate of Origin sign-off required on all commits.

## License

Apache 2.0. Copyright (c) 2026 confirmxxx. See `LICENSE` and `NOTICE`.

## Status

Pre-1.0. Spec-stable for the primitives documented here. Runtime wrappers for Node and Python on the roadmap. Use in production at your discretion — the reference implementations are straightforward but have not yet accumulated battle-testing outside the maintainer's own deployments.
