"""Auto-triage: emit GH-issue-ready Finding JSONs.

Phase 3 dev-workflow integration. When the supervisor's run_once
accepts a finding (passes schema + canary post-filter), it routes
the finding through `emit_triage_json` here. The JSON is written to:

  experiments/white-cells/findings/F<N>-<persona>-<short-slug>.json

`<N>` is a monotonic counter (zero-padded, 4 digits). `<short-slug>`
is derived from the finding's title (lowercased, alnum+hyphens, max 40 chars).

Each JSON contains everything needed to file a GH issue:

  - title:    "[white-cells] <severity>: <persona> — <title>"
  - body:     full markdown body (summary, repro, ATLAS TTPs, mitigation)
  - severity: HIGH / MEDIUM / LOW (mapped from finding.severity)
  - persona:  finding.persona
  - atlas_ttp: list[str]
  - source_finding_id: <wc-...> hash
  - filed_utc: timestamp

`file_findings` CLI:

  python3 -m white_cells.supervisor.file_findings --dry-run
  python3 -m white_cells.supervisor.file_findings --commit

Dry-run prints what would be filed. Commit calls `gh issue create`.
The supervisor never auto-commits — the operator must pass --commit.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_WC_ROOT = _HERE.parent
_DEFAULT_FINDINGS_DIR = _WC_ROOT / "findings"


_SEVERITY_MAP = {
    "info": "LOW",
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
    "critical": "HIGH",
}


def _slug(s: str, max_len: int = 40) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    if len(out) > max_len:
        out = out[:max_len].rstrip("-")
    return out or "finding"


def _next_seq(findings_dir: Path) -> int:
    findings_dir.mkdir(parents=True, exist_ok=True)
    nums: list[int] = []
    for p in findings_dir.glob("F*.json"):
        m = re.match(r"^F(\d{4,})-", p.stem)
        if m:
            try:
                nums.append(int(m.group(1)))
            except ValueError:
                continue
    return (max(nums) + 1) if nums else 1


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def build_triage_record(
    finding: dict,
    *,
    finding_id: str | None = None,
    seq: int | None = None,
) -> dict:
    """Translate a v1 finding dict into a GH-issue-shaped record. Pure
    function; does not write to disk."""
    sev = _SEVERITY_MAP.get(finding["severity"], "LOW")
    persona = finding["persona"]
    title_short = finding["title"]
    body = _build_issue_body(finding, finding_id=finding_id)

    return {
        "schema_version": 1,
        "seq": seq,
        "filed_utc": _now_utc_iso(),
        "source_finding_id": finding_id,
        "gh_issue": {
            "title": f"[white-cells] {sev}: {persona} — {title_short}",
            "body": body,
            "labels": [
                "white-cells",
                f"severity-{sev.lower()}",
                f"persona-{persona.replace('_', '-')}",
            ],
        },
        "severity": sev,
        "persona": persona,
        "atlas_ttp": list(finding.get("atlas_ttp", [])),
        "suggested_closure": finding.get("suggested_closure", "none"),
    }


def _build_issue_body(finding: dict, *, finding_id: str | None) -> str:
    ttps = ", ".join(finding.get("atlas_ttp", [])) or "none"
    fixture_hits = finding.get("fixture_hits", []) or []
    fixture_lines = (
        "\n".join(
            f"- `{h.get('service','?')}` `{h.get('method','?')} {h.get('path','?')}`"
            for h in fixture_hits
        )
        if fixture_hits
        else "_none_"
    )
    return (
        f"## Summary\n\n{finding['summary']}\n\n"
        f"## Persona\n\n`{finding['persona']}`\n\n"
        f"## Severity\n\n{finding['severity']}\n\n"
        f"## ATLAS TTP(s)\n\n{ttps}\n\n"
        f"## Reproduction\n\n```\n{finding['reproduction']}\n```\n\n"
        f"## Fixture hits\n\n{fixture_lines}\n\n"
        f"## Suggested closure\n\n`{finding.get('suggested_closure', 'none')}`\n\n"
        f"---\n\n"
        f"_Source finding-id: `{finding_id or '(unknown)'}`. Filed by White Cells "
        f"supervisor auto-triage. Operator must run "
        f"`python3 -m white_cells.supervisor.file_findings --commit` to actually "
        f"create the GH issue._"
    )


def emit_triage_json(
    finding: dict,
    *,
    finding_id: str | None = None,
    findings_dir: Path = _DEFAULT_FINDINGS_DIR,
) -> Path:
    """Write the triage record to disk and return its path. Sequence
    number is determined per-call from the existing F*.json files in
    findings_dir."""
    findings_dir.mkdir(parents=True, exist_ok=True)
    seq = _next_seq(findings_dir)
    record = build_triage_record(finding, finding_id=finding_id, seq=seq)
    slug = _slug(finding["title"])
    persona_slug = finding["persona"].replace("_", "-")
    path = findings_dir / f"F{seq:04d}-{persona_slug}-{slug}.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    return path


# ---- file_findings CLI -------------------------------------------------


def _gh_create_issue(record: dict, *, repo: str | None = None) -> tuple[int, str]:
    """Invoke `gh issue create`. Returns (rc, stdout|stderr). Will not
    run if `gh` is missing — caller's responsibility to check."""
    args = ["gh", "issue", "create",
            "--title", record["gh_issue"]["title"],
            "--body", record["gh_issue"]["body"]]
    for label in record["gh_issue"]["labels"]:
        args += ["--label", label]
    if repo:
        args += ["--repo", repo]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return 127, "gh CLI not installed"
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="white-cells-file-findings")
    parser.add_argument(
        "--findings-dir",
        default=str(_DEFAULT_FINDINGS_DIR),
        help="Directory of F*.json triage records (default: experiments/white-cells/findings/)",
    )
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true",
                   help="Print what would be filed; do not call gh.")
    g.add_argument("--commit", action="store_true",
                   help="Actually file via gh issue create.")
    parser.add_argument("--repo", help="Override target repo (owner/name).")
    args = parser.parse_args(argv)

    findings_dir = Path(args.findings_dir)
    if not findings_dir.exists():
        print(f"findings dir not found: {findings_dir}", file=sys.stderr)
        return 1

    triage_files = sorted(findings_dir.glob("F*.json"))
    if not triage_files:
        print(f"no triage records in {findings_dir}")
        return 0

    filed = 0
    failed = 0
    for tp in triage_files:
        try:
            record = json.loads(tp.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"SKIP {tp.name}: {exc}", file=sys.stderr)
            failed += 1
            continue

        if args.dry_run:
            print("=" * 60)
            print(f"FILE:  {tp.name}")
            print(f"TITLE: {record['gh_issue']['title']}")
            print(f"LABELS: {record['gh_issue']['labels']}")
            print(f"BODY (first 240 chars):")
            print("  " + record['gh_issue']['body'][:240].replace("\n", "\n  "))
            filed += 1
            continue

        # --commit path
        rc, out = _gh_create_issue(record, repo=args.repo)
        if rc == 0:
            print(f"FILED  {tp.name} -> {out.strip()}")
            filed += 1
        else:
            print(f"FAILED {tp.name}: rc={rc} {out}", file=sys.stderr)
            failed += 1

    print(f"\nsummary: filed={filed} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
