#!/usr/bin/env bash
# apply-mcp-scopes.sh
#
# Walks every *.md in the agents directory and writes a trust-zone-scoped
# `mcpServers:` YAML key into each file's frontmatter based on zones.yaml.
#
# Usage:
#   ./apply-mcp-scopes.sh                   # dry-run (default) — prints diffs, writes nothing
#   ./apply-mcp-scopes.sh --dry-run         # same as default
#   ./apply-mcp-scopes.sh --apply           # writes changes; backs up originals first
#   ./apply-mcp-scopes.sh --apply --only=<filename.md>  # single file
#   ./apply-mcp-scopes.sh --apply --force   # proceed even if preflight finds orphans
#   ./apply-mcp-scopes.sh --help
#
# Config:
#   AGENTS_DIR       — directory of *.md agent files (default ~/.claude/agents)
#   ZONES_FILE       — path to zones.yaml (default: ./zones.yaml next to this script)
#
# Preflight validation:
#   At startup, the filenames listed in zones.yaml are diffed against actual
#   *.md files under AGENTS_DIR. Orphans (listed but missing on disk) are
#   reported as warnings and abort unless --force is passed — silent
#   fall-back-to-UNCLASSIFIED on a typo is a footgun. Files on disk but not
#   in zones.yaml are reported as UNCLASSIFIED (non-fatal, fail-closed).
#
# Backup location (apply mode only): ~/.claude/agents-backup-YYYYMMDD-HHMMSS/
#
# Implementation notes:
#   * Pure Python stdlib (no PyYAML). zones.yaml is parsed line-by-line.
#   * Idempotent: re-running with the same zones.yaml produces zero changes.
#   * Fail-closed: anything not in zones.yaml defaults to `mcpServers: []`
#     (no MCPs loaded) and is reported as UNCLASSIFIED.
#   * Preserves the rest of each agent's frontmatter exactly as written.

set -euo pipefail

AGENTS_DIR="${AGENTS_DIR:-$HOME/.claude/agents}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ZONES_FILE="${ZONES_FILE:-$SCRIPT_DIR/zones.yaml}"
MODE="dry-run"
ONLY=""
FORCE="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)   MODE="apply"; shift ;;
    --dry-run) MODE="dry-run"; shift ;;
    --only=*)  ONLY="${1#*=}"; shift ;;
    --force)   FORCE="1"; shift ;;
    --help|-h)
      sed -n '2,38p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ ! -d "$AGENTS_DIR" ]]; then
  echo "agents dir not found: $AGENTS_DIR" >&2
  exit 1
fi

if [[ ! -f "$ZONES_FILE" ]]; then
  echo "zones.yaml not found: $ZONES_FILE" >&2
  echo "copy zones.example.yaml -> zones.yaml and classify your agents." >&2
  exit 1
fi

BACKUP_DIR=""
if [[ "$MODE" == "apply" ]]; then
  BACKUP_DIR="$HOME/.claude/agents-backup-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$BACKUP_DIR"
  echo "backup dir: $BACKUP_DIR"
fi

export AGENTS_DIR ZONES_FILE MODE ONLY BACKUP_DIR FORCE

python3 - <<'PYEOF'
import os
import sys
import shutil
import difflib
from pathlib import Path

AGENTS_DIR = Path(os.environ["AGENTS_DIR"])
ZONES_FILE = Path(os.environ["ZONES_FILE"])
MODE = os.environ["MODE"]
ONLY = os.environ.get("ONLY", "")
BACKUP_DIR = os.environ.get("BACKUP_DIR", "")
FORCE = os.environ.get("FORCE", "0") == "1"

# MCP alias table. Edit to add new aliases. Order inside each list is
# deliberate — keep it stable for diff sanity.
ALIASES = {
    "ctx7":     ["context7", "plugin:context7:context7"],
    "pw":       ["plugin:playwright:playwright"],
    "tg":       ["plugin:telegram:telegram"],
    "notion":   ["notion"],
    "supabase": ["supabase"],
    "vercel":   ["vercel"],
    "miro":     ["miro"],
    "gdrive":   ["google_drive"],
    "magic":    ["magic"],
}

VALID_ZONES = {"UNTRUSTED-INPUT", "INTERNAL", "HIGH-TRUST", "SEGREGATED"}


def expand(aliases):
    out = []
    seen = set()
    for a in aliases:
        if a not in ALIASES:
            raise SystemExit(f"unknown MCP alias: {a!r}. Known: {sorted(ALIASES.keys())}")
        for name in ALIASES[a]:
            if name not in seen:
                seen.add(name)
                out.append(name)
    return out


def load_zones(path):
    """Parse zones.yaml. Format per line:
        <agent-filename> <ZONE> [<mcp1,mcp2,...>]
    Comments start with #. Blank lines ignored.

    Returns (classify, orphans). Orphans is the list of `{path}:{lineno}: {name}`
    strings for filenames listed in zones.yaml that do not exist under
    AGENTS_DIR. Caller decides whether to abort (default) or proceed (--force).
    """
    classify = {}
    orphans = []
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            raise SystemExit(f"{path}:{lineno}: expected at least <filename> <zone>, got {raw!r}")
        filename = parts[0]
        zone = parts[1]
        mcp_list_str = parts[2] if len(parts) >= 3 else ""
        if zone not in VALID_ZONES:
            raise SystemExit(
                f"{path}:{lineno}: invalid zone {zone!r}. Valid: {sorted(VALID_ZONES)}"
            )
        # Path-traversal / non-.md guard — still a hard fail; a malformed
        # filename cannot be --force'd past, it is a syntax error.
        if "/" in filename or "\\" in filename or not filename.endswith(".md"):
            raise SystemExit(
                f"{path}:{lineno}: filename must be a plain *.md basename, got {filename!r}"
            )
        if not (AGENTS_DIR / filename).exists():
            orphans.append(f"{path}:{lineno}: {filename}")
        mcp_aliases = [a.strip() for a in mcp_list_str.split(",") if a.strip()]
        classify[filename] = (zone, mcp_aliases)
    return classify, orphans


def preflight(classify, orphans, on_disk):
    """Report orphans + unclassified before processing. Aborts on orphans
    unless --force was passed. UNCLASSIFIED (on-disk but not listed) is only
    a warning — fail-closed default (mcpServers: []) already limits blast
    radius; loud reporting is the remediation."""
    listed = set(classify.keys())
    disk_names = {f.name for f in on_disk}
    unlisted = sorted(disk_names - listed)

    if orphans:
        sys.stderr.write(
            "preflight: filenames in zones.yaml do not exist under AGENTS_DIR "
            f"({AGENTS_DIR}):\n  " + "\n  ".join(orphans) + "\n"
        )
        if not FORCE:
            sys.stderr.write(
                "aborting — typo'd entries would silently fall back to "
                "UNCLASSIFIED without this check. Pass --force to proceed "
                "anyway, or fix the filenames in zones.yaml.\n"
            )
            sys.exit(1)
        sys.stderr.write("--force: proceeding despite orphans.\n")

    if unlisted:
        sys.stderr.write(
            "preflight: agent files on disk are not listed in zones.yaml "
            "(will default to UNCLASSIFIED / mcpServers: []):\n  "
            + "\n  ".join(unlisted) + "\n"
        )


def split_frontmatter(text):
    """Return (fm_lines, body) where fm_lines is the list of YAML lines
    between the two `---` markers (exclusive), or (None, text) if none."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\n") != "---":
        return None, text
    for i in range(1, len(lines)):
        if lines[i].rstrip("\n") == "---":
            return lines[1:i], "".join(lines[i+1:])
    return None, text


def render_mcp_line(servers):
    """Canonical form: mcpServers: [a, b, c] or mcpServers: []"""
    if not servers:
        return "mcpServers: []\n"
    return "mcpServers: [" + ", ".join(servers) + "]\n"


def update_frontmatter(fm_lines, servers):
    """Replace any existing mcpServers: line with the target, or append one
    at the end of the frontmatter if missing. Returns new fm_lines."""
    target_line = render_mcp_line(servers)
    out = []
    replaced = False
    skip_block = False
    for line in fm_lines:
        if skip_block:
            if line.startswith(("  ", "\t", "- ")) or line.strip() == "":
                continue
            skip_block = False
        if line.startswith("mcpServers:"):
            rest = line[len("mcpServers:"):].strip()
            if rest == "" or rest == "[]" or rest.startswith("["):
                if rest == "" or (rest and not rest.endswith("]") and not rest == "[]"):
                    skip_block = True
                out.append(target_line)
                replaced = True
                continue
            else:
                out.append(target_line)
                replaced = True
                continue
        out.append(line)
    if not replaced:
        if out and not out[-1].endswith("\n"):
            out[-1] = out[-1] + "\n"
        out.append(target_line)
    return out


def process_file(path, classify):
    text = path.read_text(encoding="utf-8")
    fm, body = split_frontmatter(text)
    if fm is None:
        return ("no-frontmatter", text, text, None, None)

    name = path.name
    if name in classify:
        zone, aliases = classify[name]
    else:
        zone, aliases = ("UNCLASSIFIED", [])

    servers = expand(aliases) if aliases else []
    new_fm = update_frontmatter(list(fm), servers)
    new_text = "---\n" + "".join(new_fm) + "---\n" + body

    if new_text == text:
        return ("unchanged", text, new_text, zone, servers)
    return ("changed", text, new_text, zone, servers)


def main():
    classify, orphans = load_zones(ZONES_FILE)
    files = sorted(AGENTS_DIR.glob("*.md"))
    # Preflight runs against the full on-disk set, not the --only subset,
    # so typos are caught even when operating on a single file.
    preflight(classify, orphans, files)
    if ONLY:
        files = [f for f in files if f.name == ONLY]
        if not files:
            print(f"no match for --only={ONLY}", file=sys.stderr)
            sys.exit(3)

    tallies = {"changed": 0, "unchanged": 0, "no-frontmatter": 0}
    by_zone = {}
    unclassified = []
    changed_files = []

    for f in files:
        status, old, new, zone, servers = process_file(f, classify)
        tallies[status] = tallies.get(status, 0) + 1
        if zone:
            by_zone[zone] = by_zone.get(zone, 0) + 1
        if zone == "UNCLASSIFIED":
            unclassified.append(f.name)

        if status == "changed":
            changed_files.append(f)
            if MODE == "dry-run":
                diff = difflib.unified_diff(
                    old.splitlines(keepends=True),
                    new.splitlines(keepends=True),
                    fromfile=f"a/{f.name}",
                    tofile=f"b/{f.name}",
                    n=2,
                )
                sys.stdout.writelines(diff)
                sys.stdout.write("\n")

    if MODE == "apply":
        for f in changed_files:
            shutil.copy2(f, Path(BACKUP_DIR) / f.name)
            status, _, new, _, _ = process_file(f, classify)
            f.write_text(new, encoding="utf-8")

    # Summary always printed.
    print("=" * 60)
    print(f"mode:        {MODE}")
    print(f"agents dir:  {AGENTS_DIR}")
    print(f"zones file:  {ZONES_FILE}")
    print(f"files seen:  {len(files)}")
    print(f"changed:     {tallies.get('changed', 0)}")
    print(f"unchanged:   {tallies.get('unchanged', 0)}")
    print(f"no-fm:       {tallies.get('no-frontmatter', 0)}")
    print("zones:")
    for z, n in sorted(by_zone.items()):
        print(f"  {z:<18} {n}")
    if unclassified:
        print("UNCLASSIFIED (defaulted to mcpServers: []):")
        for name in unclassified:
            print(f"  {name}")
    if MODE == "apply":
        print(f"backup:      {BACKUP_DIR}")

main()
PYEOF
