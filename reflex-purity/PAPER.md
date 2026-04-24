# Reflex Purity: Preventing Coordinated-Agent Manipulation in Latency-Critical Systems

A short design note on the no-LLM-in-hot-path discipline, its enforcement via AST-level linting, and the Brain ↔ Reflex contract that makes it operational.

## 1. The attack class

Published 2025–2026 research establishes an empirically-attested attack class against LLM-driven decision systems:

- **Adversarial news manipulation against LLM-driven algorithmic trading** (arXiv:2601.13082): Unicode homoglyph substitution + hidden text in headlines caused annual-return degradation of up to 17.7 percentage points across tested trading agents.
- **TradeTrap** (arXiv:2512.02261): end-to-end attack surface against LLM trading agents. The framing is system-level perturbation across multiple trading-stack components; the consequence that matters for this pattern is that attacker-controlled inputs cause trades the operator did not intend.
- **Sentiment manipulation** (arXiv:2502.16343): sentiment-manipulation attacks against LLM-enabled trading, with measurable market-impact consequences.

The common shape: an attacker who can influence the model's inputs — a wire headline, an SEC filing, a retrieval-corpus document, a social-media signal — can cause an entire fleet of agents running the same (or similar) model to produce correlated outputs. Correlated LLM outputs driving autonomous execution means correlated actions: coordinated trades, coordinated market-maker withdrawals, coordinated risk-budget consumption. The attacker need not compromise any single agent; they need only influence the shared input stream.

## 2. Why per-agent defense fails here

Content sanitizers, classifiers, and per-agent safety tuning address the attack surface at the agent level. They are necessary but insufficient:

- Classifiers flag *some* injection attempts, not all. The remaining false negatives — a number small per-agent — multiplied across a fleet of agents consuming the same signal produces a correlated effect.
- Per-agent red-teaming improves the baseline but cannot close the gap in adversarial settings where the attacker has access to the same models for offline testing (standard threat-model assumption).
- Model-layer RL against prompt injection (Opus 4.5 reports ~1% browser-agent attack success rate, per Anthropic 2025-11-24) is a strong baseline, but 1% of a signal-driven fleet is not zero.

The defensive posture that actually closes the gap is **architectural**: never put the LLM on the path from signal to action. The LLM proposes; deterministic code disposes.

## 3. The pattern

### Brain

Slow. LLM-bearing. Reads the world (market data, news feeds, retrieval corpus, instrument fundamentals, research notes). Reasons. Proposes intents.

Key property: the Brain has no authority to fire actions. Everything it produces is advisory.

### Reflex

Fast. Pure deterministic code — Python, Rust, Go, whatever. No LLM call, no HTTP request to LLM endpoints, no dependency that could transitively invoke an LLM. Ever.

Responsibilities:
- Receive Brain-proposed intents
- Re-validate each intent against deterministic invariants (position limits, staleness checks, source-consensus requirements, drawdown caps, kill-switch state)
- Execute valid intents against the broker / market / action surface
- Maintain idempotency, retry behavior, error recovery

The Reflex is the only component that talks to external action surfaces. If the Brain is compromised or misled, the Reflex's invariants catch the anomalous intent before it becomes an action.

### Contract

The Brain emits `Intent` objects with a minimal schema:

```
Intent {
  instrument       # what
  direction        # long / short / close
  setup_id         # which strategy produced this
  invalidation     # deterministic level at which this intent is no longer valid
  narrative_hash   # hash of the supporting narrative at intent-emission time
  brain_trace_id   # audit trail
}
```

Notably absent: `size`, `stop_price`, `take_profit`. The Reflex re-derives these from current portfolio state at action time. An intent carrying stale sizes or stale stops is a vector for manipulation; the Reflex refuses to trust size/stop values from the Brain.

Reflex validation per intent:

1. **Freshness.** `narrative_hash` must match current canonical state. If the supporting narrative has changed since intent emission, intent is stale; reject.
2. **Invalidation.** The underlying invalidation level must not have been breached. If it has, the setup is no longer valid; reject.
3. **Portfolio invariants.** Position limits, exposure caps, drawdown budget, kill-switch state — all deterministic, all checked before the broker call.
4. **Multi-source consensus** (domain-specific). If the intent depends on a price signal that should be corroborated by ≥2 feeds, require consensus before accepting. Mitigation for vector #5 (forged multi-document consensus).
5. **Idempotency.** Commit an outbox row keyed by `(idea_id, leg_seq)` before the broker call. Replay-safe.
6. **Broker clientOrderId.** Derived deterministically from `(idea_id, leg_seq)` so the broker itself refuses duplicate submissions.

If any check fails, the intent is rejected with a logged reason. The Brain does not retry; the operator reviews.

## 4. Mechanical enforcement: the AST-purity lint

Discipline by convention fails. Enforce mechanically.

### Scan scope

The hot-path tree is enumerated as a list of repo-relative packages. Everything under these paths must be LLM-free.

```python
HOT_PATH_PACKAGES = (
    "your/reflex",
    "your/detectors",
    "your/ingestion",
    "your/contracts",
    # add more as needed
)
```

### What to ban

Three categories of finding, any of which fails the build:

**Banned imports** (prefix-matched — `langchain_core` caught alongside `langchain`):

```python
BANNED_IMPORT_PREFIXES = (
    "anthropic",
    "openai",
    "langchain",
    "llama_index", "llamaindex",
    "instructor",
    "litellm",
    "claude_api",
    "anthropic_bedrock",
    "guidance",
    "dspy",
)
```

**Banned constructor calls** (name-matched on function invocations — conservative by design; false-positive cost is a rename):

```python
BANNED_CALL_NAMES = frozenset({
    "Anthropic", "AsyncAnthropic", "AnthropicBedrock",
    "OpenAI", "AsyncOpenAI", "AzureOpenAI",
    "ChatOpenAI", "ChatAnthropic",
})
```

**Banned string markers** (fingerprint of a hand-rolled HTTP client trying to dodge the import check):

```python
BANNED_STRING_MARKERS = (
    "api.anthropic.com",
    "api.openai.com",
)
```

### Reference implementation (pytest-compatible)

```python
import ast
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOT_PATH_PACKAGES = ("your/reflex", "your/detectors",)

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

EXEMPT_FILES: frozenset[str] = frozenset()  # Must stay empty. Exemptions are suspicious.


def _iter_py_files():
    files = []
    for pkg in HOT_PATH_PACKAGES:
        root = REPO_ROOT / pkg
        if not root.exists():
            continue
        files.extend(root.rglob("*.py"))
    return sorted(files)


def _is_banned_module(name: str) -> bool:
    head = name.split(".", 1)[0]
    return any(head == p or head.startswith(p) for p in BANNED_IMPORT_PREFIXES)


def _scan_file(path: Path) -> list[str]:
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
            module = node.module or ""
            if _is_banned_module(module):
                findings.append(f"{rel}:{node.lineno}: banned from-import {module!r}")
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in BANNED_CALL_NAMES:
                findings.append(f"{rel}:{node.lineno}: banned constructor {fn.id!r}")
            elif isinstance(fn, ast.Attribute) and fn.attr in BANNED_CALL_NAMES:
                findings.append(f"{rel}:{node.lineno}: banned constructor .{fn.attr}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            for marker in BANNED_STRING_MARKERS:
                if marker in s:
                    findings.append(f"{rel}:{node.lineno}: banned endpoint {marker!r}")
    return findings


def test_hot_path_tree_exists():
    present = [p for p in HOT_PATH_PACKAGES if (REPO_ROOT / p).exists()]
    assert present, "no hot-path packages found — update HOT_PATH_PACKAGES"


def test_hot_path_has_no_llm_call_sites():
    findings = []
    for path in _iter_py_files():
        findings.extend(_scan_file(path))
    if findings:
        pytest.fail(
            "Reflex-purity violation — LLM call sites in hot-path tree.\n  "
            + "\n  ".join(findings)
        )
```

### CI integration

Run the lint on every PR. Fail the merge on any finding. Example GitHub Actions:

```yaml
reflex-purity:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: actions/setup-python@v5
      with: { python-version: '3.12' }
    - run: python -m pytest tests/test_reflex_purity.py -q
```

Pre-commit hook alternative for teams without CI:

```yaml
repos:
  - repo: local
    hooks:
      - id: reflex-purity
        name: Reflex purity (no LLM in hot path)
        entry: python -m pytest tests/test_reflex_purity.py -q
        language: system
        pass_filenames: false
        stages: [commit, push]
```

## 5. Failure modes named honestly

- **Transitive LLM calls** via a dependency that itself imports an LLM SDK. Mitigation: lock the dep tree (lockfile + SBOM). Audit new dependencies.
- **Dynamic imports** (`importlib.import_module("anthropic")`) bypass the AST scan. Mitigation: add a second scan layer that grep-scans hot-path files for `importlib.import_module` string args.
- **Network calls to LLM endpoints via hardcoded IPs or DNS-over-HTTPS** bypass both import scan and string-marker scan. Mitigation: pair with network-level egress controls (block outbound to known LLM IP ranges at the OS / container level).
- **LLM models served in-process via ONNX / GGUF runtime** bypass all three. Mitigation: if local-model inference is a real risk in your deployment, extend the banned-import list to cover `onnxruntime`, `transformers`, `llama_cpp`, etc.
- **The Brain's output still influences the Reflex.** The Reflex re-validates, but a Brain that produces correlated manipulated intents across the fleet can still waste the Reflex's rejection budget. Mitigation: log Reflex rejection rates; if they spike, halt trading and investigate (kill-switch).

## 6. When this pattern is overkill

- Interactive systems where a human approves each action. The human is the Reflex.
- Low-stakes agents where actions are cheap and reversible (draft emails, chat replies, suggested edits).
- Systems where latency is high enough that an LLM in the loop adds no speed advantage — in which case the Reflex buys you safety at the cost of complexity you don't need.

Decide by asking: *can a single manipulated input produce a high-cost, hard-to-reverse action at high frequency across my fleet?* If yes, Reflex Purity is non-negotiable. If no, it's overhead.

## 7. Summary

- LLMs in the hot path of a high-frequency action loop are a remote-controlled trigger for the attacker.
- Split Brain (LLM, slow, advisory) from Reflex (deterministic, fast, executes).
- Enforce the separation with an AST lint that fails the build on LLM imports, constructor calls, and endpoint strings.
- The Brain/Reflex contract is `Intent` in, validated action out. Sizes, stops, and action identifiers are re-derived by the Reflex, not accepted from the Brain.
- Pair with network-level egress controls and transitive-dependency discipline to close the bypass paths.

Not novel as a principle — this is a restatement of the "control / data plane separation" that's been a security pattern since the 1970s. Novel as an **agent-framework discipline** in 2026, when the default seems to be "LLM in the loop end-to-end." The paper is here because the pattern is under-applied in the fast-moving agentic-trading ecosystem, not because the principle is new.
