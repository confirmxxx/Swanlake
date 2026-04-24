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

mkdir -p "$LOG_DIR" 2>/dev/null || exit 0

REGISTRY="$REGISTRY" LOG_DIR="$LOG_DIR" \
python3 - <<'PY' || true
import json, os, re, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

# Payload arrives on stdin; argv would hit ARG_MAX (~2 MiB Linux, ~256 KiB macOS)
# and silently no-op on exactly the large tool responses where canaries most matter.
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
log_file = log_dir / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"
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

exit 0
