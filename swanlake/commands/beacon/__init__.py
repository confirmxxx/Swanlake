"""`swanlake beacon` -- sweep + deploy verb family.

Spec: docs/v0.3-beacon-deploy-spec.md.

Five subcommands:
    swanlake beacon list         show all known surface types
    swanlake beacon sweep        find unbeaconed surfaces; emit a plan
    swanlake beacon deploy       deploy to one surface (LOCAL only)
    swanlake beacon checklist    emit paste-checklist for REMOTE surfaces
    swanlake beacon verify       thin wrapper over `swanlake verify`

The hard architectural split: LOCAL surfaces (project CLAUDE.md files,
vault notes) are auto-deployable behind a confirmation gate; REMOTE
surfaces (workspace pages, env vars, public-repo READMEs, scheduled
routine prompts) are checklist-only. See spec section 1.
"""
from __future__ import annotations

from swanlake.exit_codes import USAGE


def run(args) -> int:
    """Dispatch to the per-subcommand handler.

    Argparse routes to one of {list, sweep, deploy, checklist, verify}
    via args.beacon_op. A None subcommand prints usage and exits 2.
    """
    op = getattr(args, "beacon_op", None)
    if op == "list":
        from swanlake.commands.beacon import list as _list_cmd
        return _list_cmd.run(args)
    if op == "sweep":
        from swanlake.commands.beacon import sweep as _sweep_cmd
        return _sweep_cmd.run(args)
    if op == "deploy":
        from swanlake.commands.beacon import deploy as _deploy_cmd
        return _deploy_cmd.run(args)
    if op == "checklist":
        from swanlake.commands.beacon import checklist as _checklist_cmd
        return _checklist_cmd.run(args)
    if op == "verify":
        from swanlake.commands.beacon import verify as _verify_cmd
        return _verify_cmd.run(args)

    # No subcommand provided.
    from swanlake.output import eprint
    eprint(
        "swanlake beacon: missing subcommand "
        "(one of: list, sweep, deploy, checklist, verify)"
    )
    return USAGE


__all__ = ["run"]
