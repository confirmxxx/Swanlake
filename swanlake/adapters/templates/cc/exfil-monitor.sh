#!/usr/bin/env bash
# exfil-monitor.sh -- minimal Swanlake template hook for Claude Code.
#
# PostToolUse hook that scans the tool_response payload for likely
# exfiltration patterns: outbound POST requests carrying file blobs,
# base64-encoded chunks of certain shapes, etc. Records hits to
# ~/.swanlake/exfil-hits/<YYYY-MM-DD>.jsonl. Exits 0.
#
# This is a TEMPLATE. The operator's real hook layers in tool-specific
# heuristics, target-domain allowlists, and rate-limited alerting.

set -uo pipefail

HITS_DIR="${SWANLAKE_EXFIL_HITS:-$HOME/.swanlake/exfil-hits}"
mkdir -p "$HITS_DIR" 2>/dev/null || true
HITS_FILE="$HITS_DIR/$(date -u +%Y-%m-%d).jsonl"

PAYLOAD="$(cat)"

PATTERNS=(
  # Outbound HTTP method + body argument
  '"method":[[:space:]]*"POST"'
  # Webhook-shaped URLs to known catch-alls
  'webhook\.site|requestbin|pipedream\.net'
  # Long base64 blobs (>=512 chars) inside tool_response
  '[A-Za-z0-9+/]{512,}'
)

HITS=()
for p in "${PATTERNS[@]}"; do
  if printf '%s' "$PAYLOAD" | grep -Eq "$p"; then
    HITS+=("$p")
  fi
done

if [[ ${#HITS[@]} -gt 0 ]]; then
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  joined="$(IFS='|'; echo "${HITS[*]}")"
  printf '{"ts":"%s","patterns":"%s","hook":"exfil-monitor"}\n' \
    "$ts" "$joined" >> "$HITS_FILE"
fi

exit 0
