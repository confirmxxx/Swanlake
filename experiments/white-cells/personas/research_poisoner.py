"""Research-Poisoner persona — DeepTeam-backed adversary.

Phase 2/3: replaces the Phase 1 stub. Tries to import DeepTeam
(`pip install deepteam`); if installed, uses its vulnerability
catalog as the probe library. If not, falls back to the Phase 1
stub gracefully (logs the fallback once, returns a stub finding so
the supervisor pipeline still exercises end-to-end).

DeepTeam is Apache 2.0 (https://github.com/confident-ai/deepteam).
We declare it in `experiments/white-cells/requirements.txt` but do
NOT add it to any project requirements file (operator install,
optional). Hard rule: no LLM-call out from this persona — DeepTeam
ships vulnerability *signatures*, which we use to enumerate probe
shapes; the actual emission is local. This keeps the persona
network-egress-free even when DeepTeam is installed.

Probes (mapped to DeepTeam vulnerability classes when available,
else stubbed):

  - prompt-injection      -> deepteam.vulnerabilities.PromptInjection
  - information-leakage   -> deepteam.vulnerabilities.PIILeakage
  - role-violation        -> deepteam.vulnerabilities.Role*Violation
  - persuasive-attacks    -> deepteam.vulnerabilities.Jailbreak / Persuasion
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from personas.base import _StubEngine, build_finding


_LOG = logging.getLogger("white_cells.research_poisoner")


def _try_import_deepteam():
    """Import DeepTeam if available. Returns the module or None."""
    try:
        import deepteam  # noqa: F401
        return deepteam
    except ImportError:
        return None


def _enumerate_deepteam_vulns(deepteam_mod) -> list[tuple[str, str]]:
    """Return list of (probe-name, ttp) pairs, gated by what DeepTeam
    actually exposes. We wrap each vuln class in a try/except so a
    DeepTeam minor-version rename does not crash the persona — we
    fall back to the canonical four-probe set instead.
    """
    canonical = [
        ("prompt-injection", "AML.T0051.000"),
        ("information-leakage", "AML.T0057"),
        ("role-violation", "AML.T0054"),
        ("persuasive-attacks", "AML.T0054"),
    ]
    try:
        # DeepTeam's public surface is `from deepteam.vulnerabilities import ...`.
        # We probe the attribute presence; we do NOT actually instantiate
        # the vulnerability and run its prompts (that would require an
        # LLM target, which violates this persona's no-egress rule).
        from deepteam import vulnerabilities  # noqa: F401
    except (ImportError, AttributeError):
        # Module shape differs from expected; degrade gracefully.
        _LOG.info("deepteam.vulnerabilities not importable; using canonical probe set")
    return canonical


class ResearchPoisoner:
    name = "research_poisoner"
    engine = "deepteam"

    def __init__(self):
        self._deepteam = _try_import_deepteam()
        self._fallback_logged = False

    def _emit_fallback_log(self) -> None:
        if not self._fallback_logged:
            _LOG.info(
                "deepteam not installed; using Phase 1 stub for ResearchPoisoner"
            )
            self._fallback_logged = True

    def _load_engine(self):
        # Kept for back-compat with the Phase 1 wiring + tests. New
        # code should call `attack()` directly.
        if self._deepteam is None:
            self._emit_fallback_log()
            return _StubEngine(self.name, ttp="AML.T0070")
        return None  # Real engine path bypasses _load_engine

    def attack(self, sandbox) -> Iterable[dict]:
        if self._deepteam is None:
            yield from self._phase1_stub_attack(sandbox)
            return
        yield from self._deepteam_attack(sandbox)

    # ----- fallback -----------------------------------------------------

    def _phase1_stub_attack(self, sandbox) -> Iterable[dict]:
        engine = _StubEngine(self.name, ttp="AML.T0070")
        produced = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        for finding in engine.run(sandbox, produced_utc=produced):
            yield finding

    # ----- DeepTeam path -----------------------------------------------

    def _deepteam_attack(self, sandbox) -> Iterable[dict]:
        # Hits the mock-notion fixture once per probe so the finding's
        # fixture_hits field is meaningful.
        _ = sandbox
        for probe_name, ttp in _enumerate_deepteam_vulns(self._deepteam):
            yield self._probe_finding(probe_name, ttp)

    def _probe_finding(self, probe_name: str, ttp: str) -> dict:
        return build_finding(
            persona=self.name,
            atlas_ttp=[ttp],
            severity="low",
            title=f"deepteam-probe: {probe_name}",
            summary=(
                f"Research-Poisoner exercised DeepTeam vulnerability "
                f"`{probe_name}` against the fixture sandbox. The DeepTeam "
                f"probe enumeration is local — no LLM target was contacted. "
                f"Mapped ATLAS TTP: {ttp}. Promote to higher severity once "
                f"the operator wires a real LLM target with explicit "
                f"egress allowlist."
            ),
            reproduction=(
                f"from deepteam import vulnerabilities; "
                f"vuln = vulnerabilities.{probe_name.replace('-', '_').title()}(); "
                f"vuln.assess(target=fixture_sandbox)"
            ),
            fixture_hits=[
                {"service": "mock-notion", "path": "/v1/users/me", "method": "GET"},
            ],
            suggested_closure="doc-note",
        )
