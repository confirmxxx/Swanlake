#!/usr/bin/env bash
# content-safety-check.sh -- minimal Swanlake template hook for Claude Code.
#
# Scans tool_response stdout for prompt-injection patterns. Records
# matches to ~/.swanlake/content-safety-hits/<YYYY-MM-DD>.jsonl. Exits
# 0 (template stance: log, do not block).
#
# This is a TEMPLATE. The operator's real hook layers in classifier
# tuning, allowlists, and per-tool back-pressure that is not safe to
# ship publicly without per-environment calibration.

set -uo pipefail

HITS_DIR="${SWANLAKE_CONTENT_HITS:-$HOME/.swanlake/content-safety-hits}"
mkdir -p "$HITS_DIR" 2>/dev/null || true
HITS_FILE="$HITS_DIR/$(date -u +%Y-%m-%d).jsonl"

PAYLOAD="$(cat)"

# Coarse-grained injection markers. Real production needs more.
PATTERNS=(
  'ignore (all )?previous instructions'
  'disregard your (rules|system prompt)'
  'you are now (an? )?'
  '<\/?system>'
  'new instructions from'
)

HITS=()
for p in "${PATTERNS[@]}"; do
  if printf '%s' "$PAYLOAD" | grep -Eiq "$p"; then
    HITS+=("$p")
  fi
done

if [[ ${#HITS[@]} -gt 0 ]]; then
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  joined="$(IFS='|'; echo "${HITS[*]}")"
  printf '{"ts":"%s","patterns":"%s","hook":"content-safety-check"}\n' \
    "$ts" "$joined" >> "$HITS_FILE"
fi

exit 0
