#!/usr/bin/env bash
# PostToolUse hook: scan every tool input+response for canary tripwire strings.
#
# Canaries live in $SWANLAKE_REGISTRY (default ~/.swanlake/canary-strings.txt).
# A hit means a doc containing a tripwire string was read by an agent, or
# a tripwire string is appearing in an outbound tool call.
#
# Logs hits to $SWANLAKE_HITS_DIR (default ~/.swanlake/canary-hits/YYYY-MM-DD.jsonl).
# Prints a loud stderr warning and fires a desktop notification when notify-send
# is available. Never blocks (post-hook). Stdlib Python only.

set -u

REGISTRY="${SWANLAKE_REGISTRY:-$HOME/.swanlake/canary-strings.txt}"
LOG_DIR="${SWANLAKE_HITS_DIR:-$HOME/.swanlake/canary-hits}"

mkdir -p "$LOG_DIR" 2>/dev/null || {
  echo "canary-match: cannot create log dir $LOG_DIR" >&2
  exit 0
}

# The Claude Code tool event payload arrives on this script's stdin. We must
# forward it to the embedded python as stdin — not argv, not env — because:
#   * argv (`python3 - "$payload"`) hits ARG_MAX (~2 MiB Linux, ~256 KiB
#     macOS). execve fails E2BIG and the payload is lost.
#   * envp shares the same ARG_MAX budget with argv; a ~2 MiB PAYLOAD env var
#     also triggers E2BIG on Linux.
#   * stdin is a pipe and has no ARG_MAX ceiling — only the pipe buffer, which
#     python drains as it reads. Multi-MB payloads stream through cleanly.
#
# To keep stdin free for the payload we write the python program to a tempfile
# and pass its path as argv[1]. The tempfile is deleted immediately after the
# interpreter exits.
#
# We also drop the original `|| true` — a failing python hook should surface
# the failure on stderr so the operator notices their tripwire is broken.
# PostToolUse hooks do not block the tool call; a non-zero exit here only
# affects the hook subshell.

PROG="$(mktemp "${TMPDIR:-/tmp}/canary-match.XXXXXX.py")"
trap 'rm -f "$PROG"' EXIT

cat > "$PROG" <<'PY'
import json, os, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

raw = sys.stdin.read() or '{}'
try:
    ev = json.loads(raw)
except Exception:
    sys.exit(0)

canary_file = Path(os.environ['REGISTRY'])
if not canary_file.exists():
    sys.exit(0)

canaries = []
for line in canary_file.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith('#'):
        canaries.append(line)
if not canaries:
    sys.exit(0)

tool_name = ev.get('tool_name') or ev.get('tool') or ''
tool_input = ev.get('tool_input') or {}
tool_response = ev.get('tool_response') or {}
session_id = ev.get('session_id') or ev.get('sessionId') or ''

def flatten(obj):
    if obj is None:
        return ''
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (int, float, bool)):
        return str(obj)
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return str(obj)

haystack_input = flatten(tool_input)
haystack_response = flatten(tool_response)

hits = []
for tok in canaries:
    where = []
    if tok in haystack_input:
        where.append('tool_input')
    if tok in haystack_response:
        where.append('tool_response')
    if where:
        hits.append({'token': tok, 'locations': where})

if not hits:
    sys.exit(0)

rec = {
    'ts': datetime.now(timezone.utc).isoformat(),
    'session_id': session_id,
    'tool_name': tool_name,
    'hits': hits,
    'input_sample': haystack_input[:240],
    'response_sample': haystack_response[:240],
}

log_dir = Path(os.environ['LOG_DIR'])
log_dir.mkdir(parents=True, exist_ok=True)
# UTC date so the file matches what status-segment.py and the test harness
# both look up (`date -u +%Y-%m-%d`). Local time would put hits into the
# wrong bucket whenever local TZ differs from UTC -- broke the canary_match
# test harness on EDT machines after midnight UTC. (E38 in the 2026-04-27
# edge-case audit.)
log_file = log_dir / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
with open(log_file, 'a') as f:
    f.write(json.dumps(rec, ensure_ascii=False) + '\n')

banner = (
    '\n' + '!' * 68 + '\n'
    '!!! CANARY TRIPWIRE HIT !!!\n'
    f'Tool: {tool_name}\n'
    f'Locations: {[h["locations"] for h in hits]}\n'
    f'Tokens: {[h["token"][:40] + "..." for h in hits]}\n'
    'A canary string was seen in tool i/o. Something read a tripwire doc.\n'
    f'Check {log_dir}/ for full context.\n'
    + '!' * 68 + '\n'
)
print(banner, file=sys.stderr)

nt = shutil.which('notify-send')
if nt:
    try:
        subprocess.run(
            [nt, '-u', 'critical', '-t', '12000',
             'CANARY TRIPWIRE HIT',
             f'{tool_name} surfaced a canary string.'],
            timeout=2, check=False,
        )
    except Exception:
        pass
PY

# Run the program with the payload on stdin. No `|| true` — if python3 fails,
# let the non-zero exit propagate so the operator can see the hook is broken.
REGISTRY="$REGISTRY" LOG_DIR="$LOG_DIR" python3 "$PROG"

exit 0
