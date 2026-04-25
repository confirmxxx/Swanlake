"""Research-Poisoner persona — promptfoo import boundary.

Wraps promptfoo's RAG-poisoning + indirect-prompt-injection plugin
catalog. Phase 2 will replace `_load_engine()` with the real
promptfoo import; Phase 1 keeps the boundary and returns a
deterministic stub finding so the supervisor pipeline runs without
the third-party dep.

This file MUST stay free of `import promptfoo` until Phase 2 — the
project ships nothing that requires `pip install promptfoo` in
Phase 1.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from personas.base import _StubEngine


class ResearchPoisoner:
    name = "research_poisoner"
    engine = "promptfoo"

    def _load_engine(self):
        # TODO(phase-2): wire promptfoo. Real impl will be approximately:
        #
        #   import promptfoo  # noqa: F401
        #   from promptfoo.redteam import RedTeamProvider
        #   return _PromptfooAdapter(
        #       RedTeamProvider(plugins=["rag-poisoning", "indirect-injection"]),
        #       persona_name=self.name,
        #   )
        #
        # The adapter normalizes promptfoo's per-plugin output to v1
        # finding-schema dicts. Until Phase 2 lands, return a stub that
        # exercises the supervisor wiring end-to-end with a single
        # AML.T0070 (RAG Poisoning) tagged finding.
        return _StubEngine(self.name, ttp="AML.T0070")

    def attack(self, sandbox) -> Iterable[dict]:
        engine = self._load_engine()
        produced = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        for finding in engine.run(sandbox, produced_utc=produced):
            yield finding
