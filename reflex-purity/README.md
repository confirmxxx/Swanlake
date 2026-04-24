# Reflex Purity

**No LLM in the hot path.** A discipline for agentic systems where attacker-influenced model outputs could fire high-cost actions at high frequency.

## One-paragraph summary

Separate reasoning (Brain — slow, LLM-bearing, advisory) from execution (Reflex — fast, deterministic, pure code). The Brain proposes intents; the Reflex re-validates those intents against deterministic invariants before acting. If a spoofed input causes the Brain to propose a trade, the Reflex's invariant checks catch the anomalous intent independent of the Brain's reasoning. Enforce the separation mechanically with an AST lint that fails the build on any LLM import, constructor call, or REST-endpoint string in the hot-path tree.

## When this matters

- Autonomous trading (any asset class)
- Real-time control loops (fraud scoring, abuse triage, dynamic pricing)
- Algorithmic bidding (ad auctions, RTB)
- Any system where a single manipulated input signal can fire many high-cost actions in parallel

## When it does NOT matter (over-engineering)

- Interactive, human-in-the-loop systems where a human is the gate on every action
- Systems where all actions are cheap and reversible
- Systems where LLM outputs are reviewed before any action fires

Don't add Reflex machinery where it doesn't buy anything.

## What's in this package

- `PAPER.md` — the full pattern with attack citations, the Brain/Reflex contract, the AST-lint sketch, and CI integration.
- No code release. The AST-lint implementation is ~50 lines of stdlib Python and is included as a copy-pasteable sketch in the paper rather than a standalone library. Most teams will need to adapt it to their repo layout and banned-API list; a library would be over-specified.

## References

- [arXiv:2601.13082](https://arxiv.org/abs/2601.13082) — Adversarial News and Lost Profits: headline manipulation against LLM trading, up to 17.7 pp annual-return impact
- [arXiv:2512.02261](https://arxiv.org/abs/2512.02261) — TradeTrap: full trading-agent attack surface
- [arXiv:2502.16343](https://arxiv.org/abs/2502.16343) — sentiment-manipulation variants

See `PAPER.md` for the full treatment.
