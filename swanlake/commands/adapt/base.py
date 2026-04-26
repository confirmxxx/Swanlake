"""Adapter ABC -- spec section A8.

Each harness adapter (Claude Code, CMA, SDK) implements this contract.
Two adapters ship in v0.2 (CC + CMA); SDK inherits the contract but
raises NotImplementedError until a real adopter exists.

Methods:
    install(dry_run=False)        -- install Swanlake into the harness
    uninstall()                   -- reverse a prior install via manifest
    verify()                      -- AdapterVerifyResult per surface
    list_surfaces()               -- iterable of (id, type) tuples
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, NamedTuple


class AdapterVerifyResult(NamedTuple):
    """One row of an adapter's verify() output.

    Keep narrow on purpose -- per-adapter detail goes into the
    `detail` string, not new fields. Operators consume this through
    the audit log + table renderer; adding fields breaks both.
    """

    surface_id: str
    status: str   # one of: intact, drifted, missing, unreadable
    detail: str   # human-readable, never includes canary literals


class Adapter(ABC):
    """Harness adapter contract.

    Implementations must be idempotent: install() called twice produces
    the same on-disk state as install() called once. uninstall() is
    safe to call when no install has run (no-op).
    """

    name: str = "<base>"

    @abstractmethod
    def install(self, dry_run: bool = False) -> int:
        """Install Swanlake into the target harness. Return exit code."""

    @abstractmethod
    def uninstall(self, dry_run: bool = False) -> int:
        """Reverse a prior install via the manifest. Return exit code."""

    @abstractmethod
    def verify(self) -> Iterable[AdapterVerifyResult]:
        """Yield one result per managed surface."""

    @abstractmethod
    def list_surfaces(self) -> Iterable[tuple[str, str]]:
        """Yield (surface_id, surface_type) for each managed surface."""


__all__ = ["Adapter", "AdapterVerifyResult"]
