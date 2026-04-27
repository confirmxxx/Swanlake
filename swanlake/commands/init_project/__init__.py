"""`swanlake init project` -- scaffold a fresh Swanlake-aware project.

Spec: docs/v0.4-enforcement-spec.md punch-list E6 + E7.

Two project types:

  cc   -- Claude Code project. Creates:
            CLAUDE.md (Beacon Part A imported via @canon/operating-rules.md)
            canon/operating-rules.md (canon copy)
            .swanlake-no-beacon.example (rename to activate opt-out)

  cma  -- Claude Managed Agents project. Creates everything in `cc`
          plus:
            cmas/ (empty dir for operator's CMA definitions)
            zones.example.yaml (rename to zones.yaml + customise)

The verb is operator-invoked only. It refuses non-empty target dirs
without --force (R7 mitigation). It refuses opted-out targets
unconditionally (D7 + N8 mitigation).
"""
from __future__ import annotations

from swanlake.exit_codes import USAGE
from swanlake.output import eprint


def run(args) -> int:
    """Dispatch `swanlake init project` to the scaffold handler.

    The CLI parser ensures args.init_op == "project" before reaching
    here; the handler validates --type and the target dir.
    """
    sub = getattr(args, "init_op", None)
    if sub == "project":
        from swanlake.commands.init_project import scaffold
        return scaffold.run(args)
    eprint(
        "swanlake init project: missing or unknown subcommand."
    )
    return USAGE


__all__ = ["run"]
