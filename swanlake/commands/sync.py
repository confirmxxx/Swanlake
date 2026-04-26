"""`swanlake sync` -- reconcile canon to managed surfaces.

Spec section A7: sync prompts `[y/N]` summarizing what will be touched
unless `--yes` or `SWANLAKE_NONINTERACTIVE=1` bypasses. Non-TTY without
either bypass exits 2 (USAGE) with a clear error.

The actual sync work is delegated to `reconciler.sync_vault.run_sync_all()`
which handles vault file propagation + Notion master page touch. We do
not re-implement any of that here -- this command is a thin safety
wrapper that records `prompted` / `confirmed` to the audit row.

`--dry-run` short-circuits before any reconciler dispatch and prints a
preview built from the same config + deployment-map the real sync would
read. Fail-soft: any read error during preview surfaces as a single
"could not preview because X" line and the command still exits 0 (same
shape as `swanlake status` per dimension), so cron / `swanlake-upd`
flows that probe for the flag's existence don't trip on a stale config.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from swanlake.exit_codes import USAGE
from swanlake.output import eprint, print_json, print_line
from swanlake.safety import confirm, is_noninteractive


def _summary_lines() -> list[str]:
    """Build a brief preview of what `sync` will touch.

    Kept terse on purpose -- the operator sees this every sync invocation
    and a wall of text trains them to ignore the prompt. Per-file
    propagation detail is printed by run_sync_all() during the run.
    """
    return [
        "swanlake sync will:",
        "  - propagate canon -> vault files referenced in deployment-map",
        "  - touch the Notion master page sync timestamp",
        "Existing files are atomic-write replaced; divergent files are skipped.",
    ]


def _is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _now_iso() -> str:
    """ISO-8601 UTC timestamp for the dry-run banner. Stdlib only."""
    return datetime.now(timezone.utc).isoformat()


def _short_id(page_id: str, n: int = 8) -> str:
    """Return a short, safe prefix of a Notion page ID for preview output.

    Page IDs themselves are not secrets, but printing only a prefix keeps
    the preview line scannable and avoids dumping a full UUID into shared
    terminal scrollback.
    """
    if not isinstance(page_id, str):
        return "<invalid>"
    return (page_id[:n] + "...") if len(page_id) > n else page_id


def _preview_payload() -> dict[str, Any]:
    """Build the dry-run preview without touching the reconciler.

    Returns a dict with one of two shapes:

      success:
        {
          "ok": True,
          "canon_targets": [
              {"src": str, "dst": str, "action": "FRESH WRITE" | "REPLACE" | "UNCHANGED"},
              ...
          ],
          "notion": {"master_page_id": str, "last_sync": str | None},
        }

      degraded (any read failure along the way):
        {"ok": False, "reason": str}

    Never raises -- a broken config or missing canon should yield a
    clear preview line, not a stack trace. Mirrors the fail-soft shape
    of `swanlake status` per dimension.
    """
    try:
        from reconciler import config as _config
        from reconciler import status as _status
    except Exception as e:  # defensive: import-time failure
        return {"ok": False, "reason": f"reconciler import failed: {e}"}

    try:
        cfg = _config.load()
    except _config.ConfigMissing as e:
        return {"ok": False, "reason": str(e)}
    except Exception as e:
        return {"ok": False, "reason": f"could not read config: {e}"}

    template = cfg.canon_dir / "vault-template.md"
    if not template.exists():
        return {
            "ok": False,
            "reason": (
                f"canon template missing at {template} (canon_dir set but "
                f"vault-template.md not present)"
            ),
        }

    try:
        dmap_raw = cfg.deployment_map_path.read_text(encoding="utf-8")
        dmap = json.loads(dmap_raw)
    except OSError as e:
        return {"ok": False, "reason": f"deployment-map unreadable: {e}"}
    except json.JSONDecodeError as e:
        return {"ok": False, "reason": f"deployment-map is not valid JSON: {e}"}

    template_text = ""
    try:
        template_text = template.read_text(encoding="utf-8")
    except OSError as e:
        # Template exists() said yes but read failed -- race or perms.
        return {"ok": False, "reason": f"canon template unreadable: {e}"}

    targets: list[dict[str, str]] = []
    for surface_id, paths in (dmap.get("surfaces") or {}).items():
        if not isinstance(surface_id, str) or not surface_id.startswith("vault-"):
            continue
        if not isinstance(paths, list):
            continue
        for path_str in paths:
            if not isinstance(path_str, str):
                continue
            dst = Path(path_str)
            if not dst.exists():
                action = "FRESH WRITE -- file does not exist"
            else:
                try:
                    current = dst.read_text(encoding="utf-8")
                except OSError as e:
                    action = f"UNREADABLE -- {e}"
                    targets.append({
                        "src": str(template),
                        "dst": str(dst),
                        "action": action,
                    })
                    continue
                # Cheap: replicate sync_vault's section-substitution check
                # without atomic-writing anything. Compare current vs.
                # what would be written, ignoring section-marker absence
                # (we only flag REPLACE if the substring would actually
                # change).
                if template_text and template_text in current:
                    action = "UNCHANGED"
                else:
                    action = "REPLACE -- content differs"
            targets.append({
                "src": str(template),
                "dst": str(dst),
                "action": action,
            })

    notion_info: dict[str, Any] = {
        "master_page_id": cfg.notion_master_page_id,
        "last_sync": None,
    }
    try:
        report = _status.compute_report()
        last = (
            report.get("surfaces", {})
            .get("vault", {})
            .get("last_sync_utc")
        )
        if last:
            notion_info["last_sync"] = last
    except Exception:
        # Status helpers are best-effort by design; ignore.
        pass

    return {"ok": True, "canon_targets": targets, "notion": notion_info}


def _print_preview(payload: dict[str, Any], quiet: bool) -> None:
    """Render the dry-run preview as the spec'd human-readable block."""
    if quiet:
        return

    print_line(f"swanlake sync --dry-run -- {_now_iso()}", quiet=False)
    print_line("", quiet=False)

    if not payload.get("ok"):
        reason = payload.get("reason", "unknown error")
        print_line(f"could not preview because {reason}", quiet=False)
        print_line("exit: 0", quiet=False)
        return

    targets = payload.get("canon_targets", [])
    if targets:
        print_line("would touch (canon -> vault):", quiet=False)
        for t in targets:
            print_line(f"  {t['src']} -> {t['dst']} ({t['action']})", quiet=False)
    else:
        print_line(
            "would touch (canon -> vault): nothing -- no vault-* surfaces in deployment-map",
            quiet=False,
        )
    print_line("", quiet=False)

    notion = payload.get("notion") or {}
    page_id = notion.get("master_page_id")
    last = notion.get("last_sync") or "(never)"
    if page_id:
        print_line("would touch (Notion master page):", quiet=False)
        print_line(
            f"  page {_short_id(page_id)} -- last sync {last} -- would replace body",
            quiet=False,
        )
    print_line("", quiet=False)
    print_line(
        "re-run without --dry-run (or with --yes) to apply.",
        quiet=False,
    )
    print_line("exit: 0", quiet=False)


def _run_dry_run(args) -> int:
    """Dry-run handler: build preview, print, exit 0.

    Critically: never imports `reconciler.sync_vault` and never calls
    `run_sync_all()`. The audit row naturally records the operator's
    intent through `args` (which contains `--dry-run`) and the absence
    of `--yes` -- no extra fields are injected into the audit schema.
    """
    quiet: bool = bool(getattr(args, "quiet", False))
    json_out: bool = bool(getattr(args, "json", False))
    payload = _preview_payload()
    if json_out:
        print_json(
            {
                "sync": "dry-run",
                "dry_run": True,
                "prompted": False,
                "confirmed": False,
                "ok": bool(payload.get("ok")),
                "preview": payload,
            },
            quiet=quiet,
        )
    else:
        _print_preview(payload, quiet=quiet)
    return 0


def run(args) -> int:
    """CLI entry. `args` is the argparse Namespace from swanlake.cli.

    Exit codes:
      0 on successful sync (or operator-aborted prompt)
      1 if reconciler reported per-file errors
      2 on USAGE (non-TTY without --yes / NONINTERACTIVE)
      whatever reconciler returns for config-missing-shaped errors

    Audit-side effects: this command does NOT itself touch the audit
    row. The CLI's AuditRecord context manager records exit_code via
    set_exit() and noninteractive via the env var. To distinguish
    `prompted` vs `confirmed` we rely on the noninteractive flag plus
    the args.yes flag; both surface in the audit args list.
    """
    yes: bool = bool(getattr(args, "yes", False))
    quiet: bool = bool(getattr(args, "quiet", False))
    json_out: bool = bool(getattr(args, "json", False))
    dry_run: bool = bool(getattr(args, "dry_run", False))

    # --dry-run short-circuits every safety gate AND every reconciler
    # dispatch path. It is read-only by construction and exits 0 always
    # (preview-failure surfaces as a single explanatory line, not as an
    # error code) so cron / `swanlake-upd` can probe for the flag's
    # existence without tripping a non-zero exit.
    if dry_run:
        return _run_dry_run(args)

    bypass = yes or is_noninteractive()
    tty = _is_tty()

    if not bypass and not tty:
        # Non-TTY without explicit bypass -> refuse with a clear error.
        eprint(
            "swanlake sync: no TTY and no --yes / SWANLAKE_NONINTERACTIVE=1; "
            "refusing to proceed without operator confirmation."
        )
        return USAGE

    # Show preview unless the operator suppressed it. Even with --yes the
    # preview is useful in scrollback for after-the-fact review.
    if not quiet:
        for line in _summary_lines():
            print_line(line, quiet=False)

    prompted = not bypass
    confirmed = confirm("Proceed with sync?", yes=yes)

    # Aborted at the prompt -> not an error, exit 0. The audit row will
    # carry exit_code=0 and the args list shows --yes was absent.
    if not confirmed:
        if json_out:
            print_json(
                {"sync": "aborted", "prompted": prompted, "confirmed": False},
                quiet=quiet,
            )
        elif not quiet:
            print_line("aborted by operator (no sync run).", quiet=False)
        return 0

    # Confirmed -> dispatch to the reconciler. We import inline so the
    # test suite can monkey-patch sync.run_sync_all without dragging the
    # whole reconciler import graph into module-load time.
    from reconciler import sync_vault as _sync_vault

    rc = _sync_vault.run_sync_all()
    if json_out:
        print_json(
            {
                "sync": "ran",
                "prompted": prompted,
                "confirmed": True,
                "exit_code": int(rc),
            },
            quiet=quiet,
        )
    return int(rc)


__all__ = ["run"]
