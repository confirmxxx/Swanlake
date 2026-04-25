#!/usr/bin/env bash
# PostToolUse hook: when an Edit/Write touches a file inside a package's
# reference/ tree, run that package's test scripts and report failures.
#
# Stdin: the Claude Code PostToolUse JSON envelope.
# Stdout: nothing on success (silent).
# Stderr: failing test stderr, only when a test fails.
#
# Exit semantics (PostToolUse):
#   exit 0 -> success / no-op
#   exit 2 -> tests failed; stderr is shown to the model + operator. The
#             file write already happened — this is feedback only, not a block.
#
# Repo root resolution: prefer the env var ${CLAUDE_PROJECT_DIR} that Claude
# Code sets to the project root when hooks are configured at the project
# level. Fall back to walking up from the touched file looking for a .git
# directory, so the hook also works when run by hand.

set -u

payload="$(cat 2>/dev/null || echo '{}')"

file_path="$(printf '%s' "$payload" | python3 -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    ti = d.get('tool_input', {}) or {}
    p = ti.get('file_path') or ''
    print(p)
except Exception:
    pass
" 2>/dev/null)"

[[ -z "$file_path" ]] && exit 0

# Resolve repo root.
if [[ -n "${CLAUDE_PROJECT_DIR:-}" && -d "$CLAUDE_PROJECT_DIR" ]]; then
    repo_root="$CLAUDE_PROJECT_DIR"
else
    cur="$(dirname "$file_path")"
    repo_root=""
    while [[ -n "$cur" && "$cur" != "/" ]]; do
        if [[ -d "$cur/.git" ]]; then
            repo_root="$cur"
            break
        fi
        cur="$(dirname "$cur")"
    done
fi

[[ -z "$repo_root" ]] && exit 0

# Decide which package's tests to run based on the touched path.
case "$file_path" in
    "$repo_root"/defense-beacon/reference/*)
        pkg="defense-beacon"
        ;;
    "$repo_root"/trust-zones/reference/*)
        pkg="trust-zones"
        ;;
    *)
        # Also handle the case where file_path is a relative form by checking
        # the repo-relative path.
        rel="${file_path#$repo_root/}"
        case "$rel" in
            defense-beacon/reference/*) pkg="defense-beacon" ;;
            trust-zones/reference/*)    pkg="trust-zones" ;;
            *) exit 0 ;;
        esac
        ;;
esac

# Don't recursively trigger ourselves: skip if the touched file is one of the
# test scripts themselves AND we're already inside a test invocation.
if [[ "${SWANLAKE_PACKAGE_TESTS_RUNNING:-}" == "1" ]]; then
    exit 0
fi
export SWANLAKE_PACKAGE_TESTS_RUNNING=1

err_log="$(mktemp)"
trap 'rm -f "$err_log"' EXIT

rc=0
case "$pkg" in
    defense-beacon)
        if ! bash "$repo_root/defense-beacon/reference/tests/canary_match_test.sh" >/dev/null 2>"$err_log"; then
            rc=2
        elif ! python3 "$repo_root/defense-beacon/reference/tests/make_canaries_test.py" >/dev/null 2>"$err_log"; then
            rc=2
        fi
        ;;
    trust-zones)
        if ! bash "$repo_root/trust-zones/reference/tests/apply_mcp_scopes_test.sh" >/dev/null 2>"$err_log"; then
            rc=2
        fi
        ;;
esac

if [[ $rc -ne 0 ]]; then
    {
        echo "package tests failed for $pkg after edit to: $file_path"
        echo "----- test stderr -----"
        cat "$err_log"
        echo "-----------------------"
        echo "Note: the file was already written. Fix and re-edit to re-run."
    } >&2
    exit 2
fi

exit 0
