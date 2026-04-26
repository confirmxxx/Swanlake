"""SDK adapter -- stub. Spec section A8 / A11.

Deferred to v0.3 because no SDK adopters exist today. The contract
inherits from base.Adapter so when a real adopter shows up the
implementation drops in cleanly.
"""
from __future__ import annotations

from typing import Iterable

from swanlake.commands.adapt.base import Adapter, AdapterVerifyResult
from swanlake.exit_codes import NOT_IMPLEMENTED
from swanlake.output import print_line


class SDKAdapter(Adapter):
    name = "sdk"

    def install(self, dry_run: bool = False) -> int:
        return _emit()

    def uninstall(self, dry_run: bool = False) -> int:
        return _emit()

    def verify(self) -> Iterable[AdapterVerifyResult]:
        return iter(())

    def list_surfaces(self) -> Iterable[tuple[str, str]]:
        return iter(())


def _emit() -> int:
    print_line(
        "swanlake adapt sdk: deferred to v0.3 -- no SDK adopters yet",
        quiet=False,
    )
    return NOT_IMPLEMENTED


def run(args) -> int:
    return _emit()


__all__ = ["SDKAdapter", "run"]
