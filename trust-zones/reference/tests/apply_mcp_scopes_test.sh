#!/usr/bin/env bash
# Tests for apply-mcp-scopes.sh preflight (P4).
#
# Behaviour under test:
#   1. Orphan filenames in zones.yaml (listed but not on disk) abort by default
#      with a clear error naming the missing files.
#   2. --force proceeds past orphans with a warning.
#   3. On-disk files not listed in zones.yaml report as UNCLASSIFIED and get
#      `mcpServers: []` (non-fatal fail-closed).
#
# Run: bash trust-zones/reference/tests/apply_mcp_scopes_test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../apply-mcp-scopes.sh"
chmod +x "$SCRIPT"

pass() { printf "  [PASS] %s\n" "$1"; }
fail() { printf "  [FAIL] %s\n" "$1"; exit 1; }

scratch() {
  local d; d="$(mktemp -d)"
  echo "$d"
}

mk_agent() {
  local dir="$1" name="$2"
  cat > "$dir/$name" <<'EOF'
---
name: test
---

body
EOF
}

echo "T1: orphan in zones.yaml aborts (default, no --force)"
D="$(scratch)"; AG="$D/agents"; Z="$D/zones.yaml"
mkdir -p "$AG"
mk_agent "$AG" "real.md"
cat > "$Z" <<EOF
real.md        INTERNAL  ctx7
typo-agent.md  INTERNAL  ctx7
EOF
set +e
AGENTS_DIR="$AG" ZONES_FILE="$Z" "$SCRIPT" --dry-run >/dev/null 2>"$D/err"
rc=$?
set -e
if [[ $rc -ne 0 ]] && grep -q "typo-agent.md" "$D/err"; then
  pass "aborts on orphan, names it in error"
else
  fail "did not abort on orphan (rc=$rc, stderr=$(cat "$D/err"))"
fi
rm -rf "$D"

echo "T2: --force proceeds past orphan with warning"
D="$(scratch)"; AG="$D/agents"; Z="$D/zones.yaml"
mkdir -p "$AG"
mk_agent "$AG" "real.md"
cat > "$Z" <<EOF
real.md        INTERNAL  ctx7
typo-agent.md  INTERNAL  ctx7
EOF
set +e
AGENTS_DIR="$AG" ZONES_FILE="$Z" "$SCRIPT" --dry-run --force >/dev/null 2>"$D/err"
rc=$?
set -e
if [[ $rc -eq 0 ]] && grep -q "proceeding despite orphans" "$D/err"; then
  pass "--force proceeds with warning"
else
  fail "--force did not proceed (rc=$rc, stderr=$(cat "$D/err"))"
fi
rm -rf "$D"

echo "T3: on-disk but not listed -> UNCLASSIFIED report"
D="$(scratch)"; AG="$D/agents"; Z="$D/zones.yaml"
mkdir -p "$AG"
mk_agent "$AG" "listed.md"
mk_agent "$AG" "unlisted.md"
cat > "$Z" <<EOF
listed.md   INTERNAL  ctx7
EOF
set +e
AGENTS_DIR="$AG" ZONES_FILE="$Z" "$SCRIPT" --dry-run >"$D/out" 2>"$D/err"
rc=$?
set -e
if [[ $rc -eq 0 ]] && grep -q "unlisted.md" "$D/err" && grep -q "UNCLASSIFIED" "$D/out"; then
  pass "unlisted file flagged UNCLASSIFIED"
else
  fail "unlisted file not flagged (rc=$rc, stderr=$(cat "$D/err"), stdout=$(cat "$D/out"))"
fi
rm -rf "$D"

echo "T4: clean setup (no orphans, no unlisted) runs silently on stderr"
D="$(scratch)"; AG="$D/agents"; Z="$D/zones.yaml"
mkdir -p "$AG"
mk_agent "$AG" "only.md"
cat > "$Z" <<EOF
only.md  INTERNAL  ctx7
EOF
set +e
AGENTS_DIR="$AG" ZONES_FILE="$Z" "$SCRIPT" --dry-run >/dev/null 2>"$D/err"
rc=$?
set -e
# Stderr should be empty or only contain informational lines; no "preflight:" warnings.
if [[ $rc -eq 0 ]] && ! grep -q "preflight:" "$D/err"; then
  pass "clean setup runs with no preflight warnings"
else
  fail "clean setup emitted preflight warnings (rc=$rc, stderr=$(cat "$D/err"))"
fi
rm -rf "$D"

echo
echo "all apply-mcp-scopes tests passed."
