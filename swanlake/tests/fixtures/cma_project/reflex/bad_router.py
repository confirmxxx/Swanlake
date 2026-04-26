"""DELIBERATELY-BAD hot-path router for testing reflex-purity.

This file imports `anthropic` at module level and calls it from a
function reached on the hot path. The reflex-purity AST check inside
`swanlake adapt cma` MUST flag this and refuse.

Do not refactor this file to "fix" the issue -- the test depends on
it failing.
"""
from __future__ import annotations

# Intentional violation: an LLM client import in a hot-path module.
# The AST check matches imports whose name matches
# (anthropic|openai|llm|claude|gpt) and reports them.
import anthropic  # noqa: F401  -- import deliberately retained


def hot_path_route(symbol: str) -> str:
    """Calls into anthropic from the hot path. The AST check flags this."""
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=128,
        messages=[{"role": "user", "content": f"route {symbol}"}],
    )
    return msg.content[0].text
