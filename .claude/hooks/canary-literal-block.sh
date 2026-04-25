#!/usr/bin/env bash
# PreToolUse hook: refuse Edit/Write/MultiEdit that would commit a real-shaped
# canary literal to a file in this repo.
#
# Real canaries live only in the operator's local registry under
# defense-beacon/reference/out/ (gitignored). They must never appear in
# tracked source, fixtures, docs, or examples. Test fixtures must use
# obviously fake placeholders such as AKIA_BEACON_TESTFIXTURE000000000000.
#
# Stdin: the Claude Code PreToolUse JSON envelope.
# Stdout: nothing (we use exit codes).
# Stderr: human-readable block message when we deny.
#
# Exit semantics (PreToolUse):
#   exit 0 -> allow the tool call
#   exit 2 -> deny; stderr is shown to the model + operator
#
# IMPORTANT: never echo the matched literal back. We redact to
#   REDACTED(canary_kind=<aws|google|attrib>)
# so the hook's own stderr cannot leak the token to logs or transcripts.

set -u

payload="$(cat 2>/dev/null || echo '{}')"
TMP_PAYLOAD="$(mktemp)"
trap 'rm -f "$TMP_PAYLOAD"' EXIT
printf '%s' "$payload" > "$TMP_PAYLOAD"

python3 - "$TMP_PAYLOAD" <<'PY'
import json
import os
import re
import sys

payload_file = sys.argv[1]
try:
    with open(payload_file, "r", encoding="utf-8", errors="replace") as f:
        ev = json.load(f)
except Exception:
    # Malformed payload — do not block, do not crash. Allow.
    sys.exit(0)

tool_name = ev.get("tool_name") or ev.get("tool") or ""
if tool_name not in ("Edit", "Write", "MultiEdit"):
    sys.exit(0)

tool_input = ev.get("tool_input") or {}
file_path = tool_input.get("file_path") or ""

# Allowlist: the operator's local canary registry directory. The full path
# from a contributor checkout will be <repo>/defense-beacon/reference/out/...
# We test for the segment regardless of where the repo is cloned.
norm = os.path.normpath(file_path) if file_path else ""
parts = norm.split(os.sep)
try:
    i = parts.index("defense-beacon")
    if parts[i : i + 3] == ["defense-beacon", "reference", "out"]:
        sys.exit(0)
except ValueError:
    pass

# Build the candidate content. Write -> `content`. Edit -> `new_string`.
# MultiEdit -> each edit's `new_string`. Concatenate so any one match fires.
candidates = []
for k in ("content", "new_string"):
    v = tool_input.get(k)
    if isinstance(v, str):
        candidates.append(v)
edits = tool_input.get("edits")
if isinstance(edits, list):
    for e in edits:
        if isinstance(e, dict):
            v = e.get("new_string")
            if isinstance(v, str):
                candidates.append(v)

if not candidates:
    sys.exit(0)

haystack = "\n".join(candidates)

# Real-canary literal patterns. Length-bounded so fake fixtures of obviously
# different shape (e.g. AKIA_BEACON_TESTFIXTURE...) do not match.
PATTERNS = [
    ("aws",    re.compile(r"AKIA_BEACON_[0-9A-F]{20}")),
    ("google", re.compile(r"AIzaSy[A-Za-z0-9_\-]{30,}")),
    ("attrib", re.compile(r"beacon-attrib-[a-z0-9-]+-[A-Za-z0-9]{8}")),
]

hits = []
for kind, pat in PATTERNS:
    if pat.search(haystack):
        hits.append(kind)

if not hits:
    sys.exit(0)

seen = set()
unique = []
for k in hits:
    if k not in seen:
        seen.add(k)
        unique.append(k)

msg = [
    "BLOCKED: real-shaped canary literal in candidate content.",
    f"file_path: {file_path or '<unknown>'}",
    "matched_kinds: " + ", ".join(f"REDACTED(canary_kind={k})" for k in unique),
    "",
    "Real canaries belong only in the operator's local registry under",
    "defense-beacon/reference/out/ (gitignored). Tests and fixtures must use",
    "obviously fake placeholders, e.g. AKIA_BEACON_TESTFIXTURE000000000000.",
    "If this is a fixture, change the literal to a clearly fake form.",
]
print("\n".join(msg), file=sys.stderr)
sys.exit(2)
PY
