#!/usr/bin/env bash
# Tests for canary-match.sh covering the P1 patch: large payloads (>~256 KiB,
# which hits macOS ARG_MAX and stresses Linux) must be matched, not silently
# dropped. Also verifies that non-JSON payloads don't crash the hook and that
# a canary string in a tool response produces a jsonl record.
#
# Run: bash defense-beacon/reference/tests/canary_match_test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$HERE/../canary-match.sh"

if [[ ! -x "$HOOK" ]]; then
  chmod +x "$HOOK"
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

REGISTRY="$TMP/registry.txt"
HITS_DIR="$TMP/hits"
export SWANLAKE_REGISTRY="$REGISTRY"
export SWANLAKE_HITS_DIR="$HITS_DIR"

CANARY="AKIA_BEACON_TESTTOKEN1234567890"
cat > "$REGISTRY" <<EOF
# test registry
$CANARY
EOF

pass() { printf "  [PASS] %s\n" "$1"; }
fail() { printf "  [FAIL] %s\n" "$1"; exit 1; }

echo "T1: small payload containing canary produces a hit"
small_payload=$(python3 -c "
import json
print(json.dumps({'tool_name':'Read','tool_response':{'content':'file contains $CANARY in body'}}))
")
rm -rf "$HITS_DIR"
printf '%s' "$small_payload" | "$HOOK" 2>/dev/null
today=$(date -u +%Y-%m-%d)
log_file="$HITS_DIR/$today.jsonl"
if [[ -f "$log_file" ]] && grep -q "$CANARY" "$log_file"; then
  pass "hit logged for small payload"
else
  fail "no hit logged for small payload (file: $log_file)"
fi

echo "T2: large payload (~1 MiB) containing canary still produces a hit"
# Build a ~1 MiB payload that would exceed macOS ARG_MAX (256 KiB) and stress
# Linux. The old `python3 - "$payload"` form would fail E2BIG and silently
# no-op via `|| true`. The env-var path accepts it.
rm -rf "$HITS_DIR"
large_payload=$(python3 -c "
import json
padding = 'x' * (1024 * 1024)
print(json.dumps({'tool_name':'WebFetch','tool_response':{'content': padding + '$CANARY' + padding}}))
")
printf '%s' "$large_payload" | "$HOOK" 2>/dev/null
if [[ -f "$log_file" ]] && grep -q "$CANARY" "$log_file"; then
  pass "hit logged for ~2 MiB payload"
else
  fail "no hit logged for large payload — ARG_MAX regression (file: $log_file)"
fi

echo "T3: payload without canary produces no hit"
rm -rf "$HITS_DIR"
no_hit_payload='{"tool_name":"Read","tool_response":{"content":"nothing interesting here"}}'
printf '%s' "$no_hit_payload" | "$HOOK" 2>/dev/null
if [[ -f "$log_file" ]]; then
  fail "unexpected hit for canary-free payload"
else
  pass "no hit for canary-free payload"
fi

echo "T4: malformed JSON does not crash the hook"
rm -rf "$HITS_DIR"
printf 'not-json-at-all' | "$HOOK" 2>/dev/null
pass "malformed JSON exits cleanly"

echo "T5: empty registry produces no hit and no crash"
rm -rf "$HITS_DIR"
echo "# comments only" > "$REGISTRY"
printf '%s' "$small_payload" | "$HOOK" 2>/dev/null
if [[ -f "$log_file" ]]; then
  fail "unexpected hit with empty registry"
else
  pass "empty registry handled"
fi

echo
echo "all canary-match tests passed."
