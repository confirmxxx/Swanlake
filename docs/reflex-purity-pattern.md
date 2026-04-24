# Reflex Purity Pattern

**No LLM in the hot path.**

Applied to: autonomous trading systems, algorithmic control loops, real-time decision systems, any agentic system where an attacker-influenced model output can fire high-cost actions at high frequency.

## The attack it prevents

Academic citations:
- arXiv:2601.13082 "Adversarial News and Lost Profits: Manipulating Headlines in LLM-Driven Algorithmic Trading" — Unicode homoglyph + hidden-text payloads in headlines reduce annual returns by up to 17.7 percentage points.
- arXiv:2512.02261 "TradeTrap: Are LLM-based Trading Agents Truly Reliable and Faithful?" — system-level perturbation of LLM-based trading stacks; attacker-controlled inputs cause unintended trades.
- arXiv:2502.16343 "Exploring Sentiment Manipulation by LLM-Enabled Intelligent Trading Agents" — sentiment-manipulation attacks against LLM-enabled trading.

The attack pattern, in one sentence: **an attacker who can influence the model's inputs — a spoofed headline, a forged SEC filing, a poisoned retrieval corpus — can fire trades across an entire fleet simultaneously.** This is vector #9 in the informal 10-vector taxonomy and a well-attested class across the trading-agent literature.

The same pattern generalizes beyond trading. Any agentic system where:
- A model's output drives a high-cost action
- Actions fire at high frequency (model latency is acceptable in the control loop)
- Inputs are reachable from an outside adversary (news feeds, market data, user uploads, scraped signals)

...has the same shape of vulnerability.

## The principle

Separate reasoning from execution.

- **Brain** (slow, LLM-bearing, strategic): reads the world, reasons over it, proposes intents.
- **Reflex** (fast, deterministic, pure Python or equivalent): executes, reconciles, enforces invariants. No LLM call, no HTTP to LLM endpoints, no dependency that could invoke an LLM. Ever.

The Brain emits advisory intents. The Reflex re-validates those intents against deterministic policy before acting. If a spoofed headline causes the Brain to propose a trade, the Reflex's invariant checks (position limits, staleness, source-consensus requirements, drawdown caps) catch the anomalous intent independent of the Brain's reasoning.

This is not a performance optimization. It is a security primitive. An LLM in the hot path is a remote-controlled action trigger.

## The AST-lint enforcement

Discipline by convention does not survive code review errors, merge mistakes, or "quick fix" patches. Enforce mechanically.

### Scan target

Define hot-path packages as a list of repo-relative paths, e.g. `<your-pkg>/reflex`, `<your-pkg>/detectors`, `<your-pkg>/ingestion`. These are the trees the lint protects. Everything under these paths must be LLM-free.

### What to ban

Three categories of AST findings, any of which fails the build:

**Banned imports** (prefix-matched so `langchain_core` catches alongside `langchain`):
```
anthropic
openai
langchain
llama_index
llamaindex
instructor
litellm
claude_api
anthropic_bedrock
guidance
dspy
```

**Banned constructor calls** (name-matched on function invocations — intentionally conservative; false-positive cost is a rename, false-negative cost is a silent security failure):
```
Anthropic
AsyncAnthropic
AnthropicBedrock
OpenAI
AsyncOpenAI
AzureOpenAI
ChatOpenAI
ChatAnthropic
```

**Banned string markers** — the fingerprint of a hand-rolled HTTP client trying to dodge the import check:
```
api.anthropic.com
api.openai.com
```

If one of these substrings appears in a string literal anywhere in the hot-path tree, the lint fails. The only legitimate reason to name an LLM API endpoint in hot-path code is to call it — which is precisely what we're banning.

### Exempt files

Empty by default. Exemptions must be a conscious decision with justification. The exempt-list file is itself a change that should be reviewed.

## Reference implementation sketch

```python
import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

HOT_PATH_PACKAGES = (
    "your/reflex",
    "your/detectors",
    # add paths
)

BANNED_IMPORT_PREFIXES = (
    "anthropic", "openai", "langchain", "llama_index", "llamaindex",
    "instructor", "litellm", "claude_api", "anthropic_bedrock",
    "guidance", "dspy",
)

BANNED_CALL_NAMES = frozenset({
    "Anthropic", "AsyncAnthropic", "AnthropicBedrock",
    "OpenAI", "AsyncOpenAI", "AzureOpenAI",
    "ChatOpenAI", "ChatAnthropic",
})

BANNED_STRING_MARKERS = ("api.anthropic.com", "api.openai.com")

EXEMPT_FILES = frozenset()


def _is_banned_module(name: str) -> bool:
    head = name.split(".", 1)[0]
    return any(head == p or head.startswith(p) for p in BANNED_IMPORT_PREFIXES)


def scan_file(path: Path) -> list[str]:
    rel = path.relative_to(REPO_ROOT).as_posix()
    if rel in EXEMPT_FILES:
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{rel}:{exc.lineno}: SyntaxError {exc.msg!r}"]
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_banned_module(alias.name):
                    findings.append(f"{rel}:{node.lineno}: banned import {alias.name!r}")
        elif isinstance(node, ast.ImportFrom):
            if _is_banned_module(node.module or ""):
                findings.append(f"{rel}:{node.lineno}: banned from-import {node.module!r}")
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in BANNED_CALL_NAMES:
                findings.append(f"{rel}:{node.lineno}: banned constructor {fn.id!r}")
            elif isinstance(fn, ast.Attribute) and fn.attr in BANNED_CALL_NAMES:
                findings.append(f"{rel}:{node.lineno}: banned constructor .{fn.attr}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for marker in BANNED_STRING_MARKERS:
                if marker in node.value:
                    findings.append(f"{rel}:{node.lineno}: banned endpoint {marker!r}")
    return findings
```

Wire to pytest, pre-commit, or CI as fits your stack. Failing output names the offending file + line + rule.

## CI integration

Minimum: a CI job that runs the scan on every PR and blocks merge on failure. Example GitHub Actions snippet:

```yaml
reflex-purity:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with: { python-version: '3.12' }
    - run: python tools/reflex_purity_lint.py
```

## Brain ↔ Reflex contract

The Brain emits `TradeIntent`-shaped objects (domain-appropriate — for trading, a tuple of `(instrument, direction, setup_id, invalidation_level, narrative_hash, brain_trace_id)`). The Reflex:

1. Re-derives position size from current balance, not from Brain-supplied size
2. Re-derives stop price from stored invariants, not from Brain-supplied stop
3. Rejects any intent whose `narrative_hash` no longer matches the current state (stale intent)
4. Rejects any intent whose underlying invalidation level has been breached (invalid intent)
5. Commits the resulting order with an idempotency key derived from `(idea_id, leg_seq)`, persisted in an outbox before the broker call

If these contracts hold, a spoofed signal that fools the Brain cannot fire a trade — the Reflex's deterministic re-validation catches it.

Adapt the contract shape to your domain. The principle is the same: the Reflex never trusts Brain output as-is. Brain output is advisory data subject to deterministic re-validation.

## When this pattern applies

- Autonomous trading (any asset class)
- Algorithmic bidding (ad auctions, dynamic pricing)
- Real-time control systems with adversarial inputs (fraud scoring, abuse triage)
- Any agent where a manipulated input signal can fire many high-cost actions in parallel

## When it does NOT apply (over-engineering)

- Interactive, human-in-the-loop systems where the human is the Reflex (every action confirmed)
- Systems where the "actions" are cheap and reversible (chat replies, draft text)
- Systems where the LLM's outputs are reviewed before any action fires

In those cases, the Brain alone is fine. Don't add Reflex machinery where it doesn't buy anything.

## Related
- [arXiv:2601.13082](https://arxiv.org/abs/2601.13082) — canonical empirical demonstration of the attack
- [arXiv:2512.02261](https://arxiv.org/abs/2512.02261) — end-to-end trading-agent attack surface
- [arXiv:2502.16343](https://arxiv.org/abs/2502.16343) — sentiment-manipulation variants
