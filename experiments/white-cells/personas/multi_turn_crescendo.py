"""Multi-Turn Crescendo persona — PyRIT import boundary.

Wraps Microsoft PyRIT's `CrescendoOrchestrator`. Phase 2 will replace
`_load_engine()` with the real PyRIT import; Phase 1 keeps the
boundary and returns a deterministic stub finding so the supervisor
pipeline runs without the third-party dep.

This file MUST stay free of `import pyrit` until Phase 2.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from personas.base import _StubEngine


class MultiTurnCrescendo:
    name = "multi_turn_crescendo"
    engine = "pyrit"

    def _load_engine(self):
        # TODO(phase-4): wire PyRIT.
        #   Class to instantiate: pyrit.orchestrator.CrescendoOrchestrator
        #   PyRIT version pinned at: pyrit>=0.5,<0.6 (verified Apr 2026)
        # Real impl will be approximately:
        #
        #   from pyrit.orchestrator import CrescendoOrchestrator
        #   from pyrit.prompt_target import AnthropicChatTarget
        #   target = AnthropicChatTarget(...)  # configured for fixture
        #   return _PyritAdapter(
        #       CrescendoOrchestrator(prompt_target=target, ...),
        #       persona_name=self.name,
        #   )
        #
        # The adapter normalizes PyRIT's score/conversation output to v1
        # finding-schema dicts. Phase 2/3 leaves this stub in place;
        # Phase 4 will swap in the real engine. Tagged with AML.T0054
        # (LLM Jailbreak), the closest ATLAS TTP for multi-turn crescendo.
        return _StubEngine(self.name, ttp="AML.T0054")

    def attack(self, sandbox) -> Iterable[dict]:
        engine = self._load_engine()
        produced = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        for finding in engine.run(sandbox, produced_utc=produced):
            yield finding
