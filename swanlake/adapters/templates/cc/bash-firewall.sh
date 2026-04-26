#!/usr/bin/env bash
# bash-firewall.sh -- minimal Swanlake template hook for Claude Code.
#
# PreToolUse hook for the Bash tool. Refuses obviously dangerous
# commands (rm -rf /, curl|sh patterns, etc.) by exiting 2. Records
# every refusal to ~/.swanlake/bash-firewall-hits/<YYYY-MM-DD>.jsonl.
#
# This is a TEMPLATE. The operator's real hook adds environment-specific
# allowlists, account-id checks, and team-policy enforcement.

set -uo pipefail

HITS_DIR="${SWANLAKE_BASH_HITS:-$HOME/.swanlake/bash-firewall-hits}"
mkdir -p "$HITS_DIR" 2>/dev/null || true
HITS_FILE="$HITS_DIR/$(date -u +%Y-%m-%d).jsonl"

PAYLOAD="$(cat)"

# Pull tool_input.command out of the payload via python (jq optional).
CMD="$(python3 -c '
import json, sys
try:
    p = json.load(sys.stdin)
    print((p.get("tool_input") or {}).get("command", ""))
except Exception:
    print("")
' <<< "$PAYLOAD")"

# Refuse patterns. Each is intentionally conservative.
DENY=(
  'rm[[:space:]]+-rf?[[:space:]]+/'
  'curl[[:space:]]+[^|]+\|[[:space:]]*(ba)?sh'
  'wget[[:space:]]+[^|]+\|[[:space:]]*(ba)?sh'
  ':\(\)\{[[:space:]]*:\|:&[[:space:]]*\};:'   # fork bomb
  'mkfs\.'
  'dd[[:space:]]+.*of=/dev/'
)

for pat in "${DENY[@]}"; do
  if printf '%s' "$CMD" | grep -Eq "$pat"; then
    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    # We log the *pattern that matched*, not the raw command -- the
    # raw command is still in the payload Claude Code retains.
    printf '{"ts":"%s","matched":"%s","hook":"bash-firewall"}\n' \
      "$ts" "$pat" >> "$HITS_FILE"
    echo "swanlake bash-firewall: refused command matching ${pat}" >&2
    exit 2
  fi
done

exit 0
