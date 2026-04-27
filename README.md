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
| [`defense-beacon/`](./defense-beacon/) | Zero-trust surface markers with per-surface canary attribution + local registry discipline + staleness gate | ✅ reference impl | ✅ via `swanlake adapt cma` (CMA adapter installs Beacon Part A + per-CMA Part B canaries) |
| [`trust-zones/`](./trust-zones/) | Four-zone taxonomy (UNTRUSTED-INPUT / INTERNAL / HIGH-TRUST / SEGREGATED) for scoping MCP / tool access per agent | ✅ reference impl | ✅ via `swanlake adapt cma` (CMA adapter applies `zones.yaml`-driven tool allowlists) |
| [`reflex-purity/`](./reflex-purity/) | Pattern + AST-lint sketch for "no LLM in the hot path" — prevents coordinated-agent manipulation in latency-critical systems | Paper + pattern | 🟡 AST check via `swanlake adapt cma` (report-only; exits 0 even with violations — operator decides whether to wire as a CI gate) |
| [`reconciler/`](./reconciler/) | Cross-surface autonomous sync. Single canon source propagates via `@import` to CLAUDE.md (zero-latency), watchdog Routine to Notion, systemd timer to vault. Drift detection + portable `--init` wizard. Invoked via `swanlake sync`. | ✅ Phase 1 shipped | Spec applies; primitive is operator-grade today |
| [`experiments/white-cells/`](./experiments/white-cells/) | Continuous AI red team. 6 attack-class personas (Beacon-Burner / Zone-Climber / Reflex-Smuggler / Research-Poisoner / Hook-Fuzzer / Multi-Turn Crescendo). Closure-rate metric with explicit kill criterion. | 🟡 Phase 1+2+3 alpha | Same — runs against fixtures, not production |

Supporting documentation:

| Document | Covers |
|---|---|
| [`THREAT-MODEL.md`](./THREAT-MODEL.md) | Vectors Swanlake defends against, vectors it does not, the honesty problem |
| [`DEPENDENCIES.md`](./DEPENDENCIES.md) | Native platform features Swanlake depends on (Claude Code sandbox, MCP OAuth, etc.) — what you get for free |
| [`NON-GOALS.md`](./NON-GOALS.md) | What Swanlake is explicitly NOT |
| [`docs/adversarial-research-pattern.md`](./docs/adversarial-research-pattern.md) | Hardened research-dispatch discipline for defending agents that research security topics themselves |
| [`docs/reflex-purity-pattern.md`](./docs/reflex-purity-pattern.md) | The reflex-purity principle in depth |
| [`docs/how-this-fits-above-native-claude-code.md`](./docs/how-this-fits-above-native-claude-code.md) | The layering — what native does, what Swanlake adds |
| [`reconciler/README.md`](./reconciler/README.md) | Reconciler architecture overview + usage commands + divergence opt-out + status segment integration |
| [`reconciler/OPERATOR-SETUP.md`](./reconciler/OPERATOR-SETUP.md) | Fresh-machine setup walkthrough (~10 min): clone, init canary registry, wizard, systemd timer, verify |

## What's new in v0.4 (current)

- **Enforcement layer** — `swanlake scan` (per-project audit), SessionStart advisory nudge (opt-in via `swanlake adapt cc --enable-session-nudge`), and `swanlake init project --type {cc,cma}` for scaffolding fresh Swanlake-aware projects. Hard rule preserved: NOTHING auto-installs without explicit operator confirmation. See [`docs/v0.4-enforcement-spec.md`](./docs/v0.4-enforcement-spec.md).
- **Worktree-install isolation** — install-marker at `~/.swanlake/.install-marker` self-detects + warns when `pip install -e .` from a sibling worktree captures the global install pointer. v0.4.1 self-heals on tarball-install transient build paths. New `swanlake doctor` 10th probe verifies marker matches runtime path. See [`docs/v0.3.x-worktree-install-isolation-spec.md`](./docs/v0.3.x-worktree-install-isolation-spec.md).
- **Reconciler `ack` subcommand** — `swanlake reconciler ack <surface>` records that a remote-only sync (e.g. Notion via the watchdog routine) happened, clearing the persistent local-state ALARM that was architecturally correct but cosmetically noisy. v0.4.2 migrates `STATE_PATH` to `~/.swanlake/last-sync.json` so the ack-clears-status flow works end-to-end (legacy XDG path auto-migrates on first run).
- **`swanlake beacon` family** (shipped v0.3) — `list`, `sweep`, `deploy`, `checklist`, `verify` with HARD LOCAL/REMOTE split (LOCAL under 12-step safety machine; REMOTE stays checklist-only by SPEC). See [`docs/v0.3-beacon-deploy-spec.md`](./docs/v0.3-beacon-deploy-spec.md).
- **`.swanlake-no-beacon` opt-out marker** for excluding directories from sweep/deploy/scan. See [`docs/swanlake-no-beacon.md`](./docs/swanlake-no-beacon.md).
- **39-finding edge-case audit** — permanent backlog at [`docs/edge-case-audit-2026-04-27.md`](./docs/edge-case-audit-2026-04-27.md). 7 quick-fixes already shipped including the canary-match UTC-date timezone bug.

All v0.3.x entry points and subcommands remain unchanged. Backward compatible upgrade.

## Where Swanlake fits in the agent immune system

Swanlake covers the **input path**: it stops hostile content from steering an agent at ingestion time. It does not enforce policy on agent actions. For the output path — deterministic policy enforcement on every action before execution — pair Swanlake with [Microsoft Agent Governance Toolkit (AGT)](https://github.com/microsoft/agent-governance-toolkit) or equivalent policy middleware.

| Layer | Cuts which arrow | Substrate |
|---|---|---|
| Swanlake (this repo) | malicious input → agent | Per-surface canary attribution + trust-zone scoping + reflex purity. Operator-grade primitives. |
| AGT | compromised agent → harmful action | Deterministic policy enforcement, zero-trust identity, execution rings, kill switch. Enterprise infrastructure. |

Neither alone is sufficient. AGT stops a compromised agent from executing a bad action; Swanlake stops the agent from getting compromised in the first place. The OWASP Agentic Top 10 spans both halves (ASI-01 through ASI-10).

**Deployment cost is asymmetric**: AGT requires containers, a DevSecOps team, and per-language SDK integration; Swanlake is one Claude Code subscription plus a repo install. Same operator who runs AGT in production still benefits from Swanlake — different budget, different team, different deployment surface.

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
| `🛡inject:5` | Content-safety hook caught 5 prompt-injection attempts today. Check `~/.swanlake/content-safety/`. |
| `🛡exfil:2` | Secret-shape payloads blocked. Check `~/.claude/exfil-alerts/`. |
| `🛡!stale:8d,canary:1` | Multiple issues. Triage newest first. |

Sync the shield's freshness signal from the Notion posture page via `tools/sync-posture.py`.

Because a dashboard you have to open. A shield in your status line you see every time you glance at your terminal. Full cheat sheet + integration in [`tools/README.md`](./tools/README.md).

## Operating Swanlake (v0.4 CLI)

### Install

```bash
# Recommended — pipx for full isolation (no worktree-install pollution)
pipx install git+https://github.com/confirmxxx/Swanlake.git@v0.4.2

# Or frozen tarball
pip install --break-system-packages https://github.com/confirmxxx/Swanlake/archive/refs/tags/v0.4.2.tar.gz

# From source — development install only. With multiple git worktrees,
# `pip install -e .` from a sibling worktree captures the global install
# pointer; prefer pipx or the tarball above for non-dev use. The
# install-marker check at CLI startup will warn if drift is detected.
git clone https://github.com/confirmxxx/Swanlake.git
cd Swanlake
pip install -e .
```

`swanlake --version` prints `0.4.2`.

> **Worktree-isolation note.** Editable installs share one global pointer per Python interpreter. If a background agent (or a parallel `git worktree`) runs `pip install -e .` against its own checkout, the operator's `swanlake` binary silently starts importing from that worktree. Swanlake v0.3.x+ records the install source under `~/.swanlake/.install-marker` and prints a one-line stderr warning the next time the CLI runs from a different source; v0.4.1 self-heals on transient pip-build paths. `swanlake doctor`'s 10th probe flags the same drift as a fail row. The hard fix is `pipx install swanlake-cli` — pipx puts the tool in its own venv, so any agent's `pip install -e .` inside a worktree affects only that agent's interpreter, never the operator's CLI. Full design rationale: [`docs/v0.3.x-worktree-install-isolation-spec.md`](./docs/v0.3.x-worktree-install-isolation-spec.md).

### The six workflows

**1. Posture check.** `swanlake status` aggregates 7 dimensions (reconciler / canary / inject / exfil / closure / coverage / bench) and exits non-zero on drift.

```
$ swanlake status
swanlake status -- 2026-04-26T14:23Z

dimension       status     detail
--------------  ---------  ----------------------------------------
reconciler      clean      notion: fresh, claude_md: 2h, vault: fresh
canary          clean      0 hits / 4 fires (24h)
inject          clean      0 hits / 14 fires (24h)
exfil           clean      0 hits / 0 fires (24h)
closure         ok         0.94 ratio (7d window)
coverage        ok         3d old (12 surfaces tracked)
bench           ok         3d since last quick run

overall: CLEAN  [exit 0]
```

When a surface drifts, the row goes red and the exit code follows:

```
reconciler      drift      notion: missing, claude_md: 26h, vault: fresh
coverage        stale      8d since last verify (8 of 25 surfaces tracked)
overall: DRIFT  [exit 1]
```

**2. Canon sync.** Reconcile the canon source to managed surfaces. Always preview first:

```bash
swanlake sync --dry-run    # prints which page IDs and which blocks will change
swanlake sync              # [y/N] prompt showing the same diff
swanlake sync --yes        # skips the prompt (for cron / systemd timers)
```

**3. Onboard a new surface.**

```bash
swanlake init --add-surface NAME
```

Registers `NAME` in `~/.swanlake/coverage.json` without re-running the bootstrap wizard.

**4. Adversarial smoke test.**

```bash
swanlake bench --quick     # ~1 min fixture-based smoke; writes ISO-UTC to ~/.swanlake/last-bench
swanlake bench --full      # v0.4+ stub; PyRIT + Garak harness deferred. Currently exits 0 with a manual-fallback hint pointing at /tmp/swanlake-pyrit-garak-bench-*/run.sh.
```

**5. Beacon deploy (LOCAL surfaces) + checklist (REMOTE surfaces).**

```bash
swanlake beacon list                 # 7 surface types + scope (local/remote) + deploy method
swanlake beacon sweep                # find unbeaconed/partial surfaces; honors .swanlake-no-beacon opt-out
swanlake beacon deploy <surface-id>  # 12-step LOCAL safety machine (clean-tree, backup, atomic write, post-status)
swanlake beacon checklist            # paste-ready markdown for REMOTE surfaces; default stdout (no on-disk live-canary registry)
swanlake beacon verify <surface-id>  # thin wrapper + 5-type REMOTE dispatch
```

Auto-deploy to REMOTE surfaces (Notion, Supabase, Vercel, GitHub, Routines) is forbidden by the threat model — the checklist is the deployment artifact. Drop a `.swanlake-no-beacon` file in any directory to exclude it from sweep/deploy; full semantics in [`docs/swanlake-no-beacon.md`](./docs/swanlake-no-beacon.md).

**6. New machine.**

```bash
pipx install git+https://github.com/confirmxxx/Swanlake.git@v0.4.2 \
  && swanlake init \
  && swanlake adapt cc
```

Three commands from zero to wired.

**7. Audit existing projects + scaffold new ones.**

```bash
swanlake scan                                # walk ~/projects/*; classify each project
swanlake init project --type cc <path>       # scaffold a fresh CC-aware project
swanlake init project --type cma <path>      # scaffold a fresh CMA-aware project
swanlake adapt cc --enable-session-nudge     # opt in to per-session unprotected-project nudge
```

`scan` is read-only; never installs anything. The SessionStart nudge is advisory only — operator decides per project.

### Auto-wiring (set-and-forget)

Three optional wires push the framework toward "exists, doing its work, you forget about it." All advisory or local-only; none auto-modify operator surfaces.

```bash
# 1. SessionStart nudge — every CC session, one stderr line if the current
#    project lacks Swanlake. Silent otherwise.
swanlake adapt cc --enable-session-nudge

# 2. Daily bench --quick via systemd user timer — keeps the bench freshness
#    dim green; logs to ~/.swanlake/bench-daily.log.
cat > ~/.config/systemd/user/swanlake-bench-daily.service <<'EOF'
[Unit]
Description=Swanlake daily bench --quick
After=network-online.target

[Service]
Type=oneshot
ExecStart=%h/.local/bin/swanlake bench --quick
StandardOutput=append:%h/.swanlake/bench-daily.log
StandardError=append:%h/.swanlake/bench-daily.log
EOF

cat > ~/.config/systemd/user/swanlake-bench-daily.timer <<'EOF'
[Unit]
Description=Run swanlake bench --quick daily

[Timer]
OnCalendar=*-*-* 07:00:00
RandomizedDelaySec=900
Persistent=true
Unit=swanlake-bench-daily.service

[Install]
WantedBy=timers.target
EOF

# 3. Weekly reconciler ack — fires after the security-watchdog Routine cron
#    so the local reconciler dim clears automatically.
cat > ~/.config/systemd/user/swanlake-reconciler-ack-weekly.service <<'EOF'
[Unit]
Description=Swanlake weekly reconciler-ack notion
After=network-online.target

[Service]
Type=oneshot
ExecStart=%h/.local/bin/swanlake reconciler ack notion --note "weekly auto-ack"
StandardOutput=append:%h/.swanlake/reconciler-ack-weekly.log
StandardError=append:%h/.swanlake/reconciler-ack-weekly.log
EOF

cat > ~/.config/systemd/user/swanlake-reconciler-ack-weekly.timer <<'EOF'
[Unit]
Description=Weekly reconciler ack (post security-watchdog Routine)

[Timer]
OnCalendar=Sun *-*-* 11:00:00 UTC
RandomizedDelaySec=600
Persistent=true
Unit=swanlake-reconciler-ack-weekly.service

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now swanlake-bench-daily.timer swanlake-reconciler-ack-weekly.timer
```

After this, the only manual ops left are `swanlake adapt cma --project <X>` once per real project (deliberately operator-confirmed because it modifies project files) and glancing at the status-line shield when curious.

### Manual install / customization (fallback)

Operators who don't want to install the CLI can still drive each primitive directly:

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

## Operating Swanlake — agentic apps

### CMA (Claude Managed Agents)

```bash
swanlake adapt cma --project PATH
```

Validated against synthetic CMA-shape fixtures only; **first live-project install is operator follow-up work**. The adapter operates against any project that has a `cmas/` or `agents/` directory of per-CMA markdown/yaml files (configurable via `--cma-glob`). For each CMA the adapter:

- Injects Beacon Part A operating rules into the CMA's system prompt
- Generates per-CMA Part B canaries on first install (preserved on re-run)
- Reads the project's `zones.yaml` (or seeds one classifying every CMA as `INTERNAL`) and applies the zone's tool-allowlist semantics to each CMA's tool config
- Runs a reflex-purity AST check on configurable hot-path globs (`--reflex-glob`, default `**/reflex*.py:**/hot_path*.py`). The check is report-only: violations land in the per-project manifest and on stderr, but `swanlake adapt cma` exits 0 regardless. Operator decides whether to wire the report into CI as a hard gate.
- Registers each CMA as a surface in `~/.swanlake/coverage.json` with `type=cma, project=<name>, cma_id=<id>`

Uninstall via `swanlake adapt cma --project PATH --uninstall` reverses everything from the per-project manifest at `~/.swanlake/cma-adapter-manifest-<project>.json`.

Do not read this as "Swanlake secures CMA-based agentic systems in production." The CMA adapter is the install primitive; production hardening is the operator's job.

### Anthropic SDK

```bash
swanlake adapt sdk
```

Stub in v0.4; gated on a real SDK adopter. Exits 3 with a deferred-stub message.

### Node

Out of scope for v0.4. Each package's `SPEC.md` is implementation-language-agnostic; Node operators can implement against the spec and file issues when gaps appear.

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

Pre-1.0, no third-party adopters yet. v0.4 ships the enforcement layer (scan + SessionStart nudge + init project), worktree-install isolation, reconciler ack, and 7 edge-case patches on top of the v0.3 beacon family + v0.2 unified CLI. The SDK adapter is a deferred stub; the `--full` PyRIT/Garak bench harness is a v0.5+ stub. Spec-stable for the primitives documented here.

Reference implementations are straightforward but have not accumulated production exposure outside the maintainer's own deployments. Hook templates installed by `swanlake adapt cc` are minimal demos (~30-60 LOC each) that exercise the contract — they are not drop-in replacements for a hardened production hook stack. Treat them as starting skeletons and harden per your own threat model. Reflex-purity is a report-only AST check; wiring it as a CI gate is an operator decision.

## Honesty audit log

Last reviewed against the shipped CLI surface and behavior on **2026-04-27** for **v0.4.2**. Docs touched in that pass:

- `README.md`, `THREAT-MODEL.md`, `NON-GOALS.md`, `DEPENDENCIES.md`, `CONTRIBUTING.md`
- `docs/v0.2-unified-cli-spec.md`, `docs/v0.3-beacon-deploy-spec.md`, `docs/swanlake-no-beacon.md`, `docs/how-this-fits-above-native-claude-code.md`, `docs/adversarial-research-pattern.md`, `docs/reflex-purity-pattern.md`
- `defense-beacon/README.md`, `trust-zones/README.md`, `reflex-purity/README.md`, `reconciler/README.md`, `experiments/white-cells/README.md`, `tools/README.md`, `defense-beacon/examples/synthetic-saas/README.md`
