#!/usr/bin/env bash
# swanlake-session-nudge.sh -- v0.4 L2 SessionStart advisory hook.
#
# Wired into ~/.claude/settings.json hooks.SessionStart bucket by
# `swanlake adapt cc --enable-session-nudge`. Reads the SessionStart
# event payload (JSON on stdin OR $CLAUDE_PROJECT_DIR env var) and
# prints exactly one stderr nudge line if the project meets these
# conditions:
#
#   1. project root is under $SWANLAKE_NUDGE_SCOPE (default ~/projects)
#   2. no .swanlake-no-beacon marker at or above the project root
#   3. CLAUDE.md exists at the project root
#   4. CLAUDE.md does NOT contain the Defense Beacon v1 header sentinel
#
# ALWAYS exits 0. A non-zero exit would block the session start --
# nudge means nudge, not block. Internal errors write to stderr but
# never propagate. Performance budget: < 50ms warm-cache wall-clock.
#
# Spec: docs/v0.4-enforcement-spec.md section 4 (SessionStart hook
# contract).

# Note: NOT setting `set -e` -- this hook must never exit non-zero
# on internal errors. Defensive `|| true` on the noisy steps; the
# trap below catches anything else.
set -u

# Trap any unhandled error and exit 0 anyway. Belt-and-braces with
# the per-command `|| true` guards.
trap 'exit 0' ERR
trap 'exit 0' EXIT

NUDGE_SCOPE="${SWANLAKE_NUDGE_SCOPE:-$HOME/projects}"
SENTINEL='<!-- DEFENSE BEACON v'
OPTOUT_NAME='.swanlake-no-beacon'
# Cap the upward walk depth at 32 levels (mirrors _optout.find_marker()
# in the Python codebase). Defense against pathological symlink loops.
WALK_LIMIT=32

# --- Resolve project root --------------------------------------------------
# Prefer $CLAUDE_PROJECT_DIR set by the harness. Fall back to reading
# the SessionStart JSON payload's "cwd" field; fall back further to
# walking the actual cwd upward looking for .git/HEAD.

PROJECT_ROOT=""

if [[ -n "${CLAUDE_PROJECT_DIR:-}" && -d "${CLAUDE_PROJECT_DIR:-}" ]]; then
  PROJECT_ROOT="$CLAUDE_PROJECT_DIR"
fi

if [[ -z "$PROJECT_ROOT" ]]; then
  # Read up to 8 KiB of stdin (the SessionStart payload is small).
  # `read -t 0` would skip if no data; we prefer a tiny timeout so
  # operators running the hook by hand outside CC don't hang it.
  PAYLOAD="$(timeout 1 cat 2>/dev/null || true)"
  if [[ -n "$PAYLOAD" ]]; then
    # Tiny grep-based JSON extractor -- avoid jq dependency. Matches
    # `"cwd":"<value>"` (single-quoted bash literal, no escaping
    # acrobatics needed for the common case).
    CWD_FIELD="$(printf '%s' "$PAYLOAD" \
      | grep -oE '"cwd"[[:space:]]*:[[:space:]]*"[^"]+"' \
      | head -n1 \
      | sed -E 's/.*"cwd"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/' \
      || true)"
    if [[ -n "$CWD_FIELD" && -d "$CWD_FIELD" ]]; then
      PROJECT_ROOT="$CWD_FIELD"
    fi
  fi
fi

if [[ -z "$PROJECT_ROOT" ]]; then
  PROJECT_ROOT="$PWD"
fi

# Walk upward from PROJECT_ROOT looking for .git/HEAD; if found, use
# the parent dir as the project root. Bounded to WALK_LIMIT levels.
walk_to_repo_root() {
  local cur="$1"
  local i=0
  while (( i < WALK_LIMIT )); do
    if [[ -f "$cur/.git/HEAD" ]]; then
      printf '%s\n' "$cur"
      return 0
    fi
    local parent
    parent="$(dirname -- "$cur")"
    if [[ "$parent" == "$cur" ]]; then
      break
    fi
    cur="$parent"
    i=$((i + 1))
  done
  printf '%s\n' "$1"  # fallback: original
  return 0
}

PROJECT_ROOT="$(walk_to_repo_root "$PROJECT_ROOT")"

# --- Scope check -----------------------------------------------------------
# Only nudge for projects under $SWANLAKE_NUDGE_SCOPE. Out-of-scope
# projects (system dirs, tmp dirs, vendored copies on /mnt) get silent
# exit 0. Resolve symlinks for both before comparing.
abs_path() {
  # readlink -f works on Linux; on macOS the BSD readlink lacks -f. Try
  # `realpath` first which is portable enough; fall back to plain echo.
  if command -v realpath >/dev/null 2>&1; then
    realpath "$1" 2>/dev/null || printf '%s\n' "$1"
  elif command -v readlink >/dev/null 2>&1; then
    readlink -f "$1" 2>/dev/null || printf '%s\n' "$1"
  else
    printf '%s\n' "$1"
  fi
}

PROJECT_ROOT_ABS="$(abs_path "$PROJECT_ROOT")"
NUDGE_SCOPE_ABS="$(abs_path "$NUDGE_SCOPE")"

case "$PROJECT_ROOT_ABS" in
  "$NUDGE_SCOPE_ABS"|"$NUDGE_SCOPE_ABS"/*)
    : # in scope, continue
    ;;
  *)
    exit 0
    ;;
esac

# --- Opt-out check ---------------------------------------------------------
# Walk up from PROJECT_ROOT looking for a .swanlake-no-beacon marker.
# Stops at NUDGE_SCOPE (inclusive) or filesystem root.
walk_for_optout() {
  local cur="$1"
  local ceiling="$2"
  local i=0
  while (( i < WALK_LIMIT )); do
    if [[ -f "$cur/$OPTOUT_NAME" ]]; then
      return 0  # marker found -> opted out
    fi
    if [[ "$cur" == "$ceiling" ]]; then
      return 1
    fi
    local parent
    parent="$(dirname -- "$cur")"
    if [[ "$parent" == "$cur" ]]; then
      return 1
    fi
    cur="$parent"
    i=$((i + 1))
  done
  return 1
}

if walk_for_optout "$PROJECT_ROOT_ABS" "$NUDGE_SCOPE_ABS"; then
  exit 0
fi

# --- Beacon check ----------------------------------------------------------
# If no CLAUDE.md, exit silently. The hook only nudges projects that
# already use Claude Code agent context.
CLAUDE_MD="$PROJECT_ROOT_ABS/CLAUDE.md"
if [[ ! -f "$CLAUDE_MD" ]]; then
  exit 0
fi

# Grep CLAUDE.md for the v1 beacon header sentinel. Substring match
# only -- per-surface canary tail is never read into a variable.
if grep -qF -- "$SENTINEL" "$CLAUDE_MD" 2>/dev/null; then
  exit 0
fi

# --- Nudge -----------------------------------------------------------------
# One stderr line. ~180 chars. Carries both remediation paths so the
# operator never has to look up the silence command.
printf 'swanlake: project at %s has CLAUDE.md but no beacon attribution. Run '\''swanlake init project --type cc'\'' to scaffold, or '\''touch %s/%s'\'' to silence.\n' \
  "$PROJECT_ROOT_ABS" "$PROJECT_ROOT_ABS" "$OPTOUT_NAME" >&2

exit 0
