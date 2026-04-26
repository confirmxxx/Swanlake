#!/usr/bin/env bash
# canary-match.sh -- minimal Swanlake template hook for Claude Code.
#
# Reads a Claude Code hook payload from stdin, scans tool_response /
# tool_input bytes for real-shaped canary literals, appends a JSONL
# record to ~/.swanlake/canary-hits/<YYYY-MM-DD>.jsonl on a hit, and
# exits 0 either way (block-or-pass behaviour is operator-tunable).
#
# This is a TEMPLATE shipped by `swanlake adapt cc`. It demonstrates
# the contract. Real production hooks (the ones the operator runs in
# their own ~/.claude/hooks/) implement this same shape with extra
# noise classification, allowlists, and back-pressure logic that is
# not appropriate to ship in a public template.

set -uo pipefail

HITS_DIR="${SWANLAKE_CANARY_HITS:-$HOME/.swanlake/canary-hits}"
mkdir -p "$HITS_DIR" 2>/dev/null || true
HITS_FILE="$HITS_DIR/$(date -u +%Y-%m-%d).jsonl"

# Read the entire stdin payload. Claude Code hook envelopes are JSON;
# we treat the whole payload as bytes for the regex pass.
PAYLOAD="$(cat)"

# Regex patterns mirror the in-process detector in defense-beacon and
# in swanlake/audit.py::_CANARY_PATTERNS. Keep these synchronised.
KINDS=()
if printf '%s' "$PAYLOAD" | grep -Eq 'AKIA_BEACON_[0-9A-Fa-f]{20}'; then
  KINDS+=("aws")
fi
if printf '%s' "$PAYLOAD" | grep -Eq 'AIzaSy[A-Za-z0-9_\-]{30,}'; then
  KINDS+=("google")
fi
if printf '%s' "$PAYLOAD" | grep -Eq 'beacon-attrib-[a-z0-9-]+-[A-Za-z0-9]{8}'; then
  KINDS+=("attrib")
fi
if printf '%s' "$PAYLOAD" | grep -Eq 'ghp_beacon_[0-9a-fA-F]{40}'; then
  KINDS+=("ghp")
fi

if [[ ${#KINDS[@]} -gt 0 ]]; then
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  joined="$(IFS=,; echo "${KINDS[*]}")"
  # Record kind only -- never log the matched literal.
  printf '{"ts":"%s","kinds":"%s","hook":"canary-match"}\n' \
    "$ts" "$joined" >> "$HITS_FILE"
fi

exit 0
