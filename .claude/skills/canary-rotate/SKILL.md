---
name: canary-rotate
description: Quarterly rotation of beacon attribution canaries across all 12 deployed surfaces. Operates on the operator's LOCAL canary registry (under ~/projects/DEFENSE-BEACON/), not on the public Swanlake repo. Operator-invoked only.
disable-model-invocation: true
---

# canary-rotate

Operator workflow to rotate the beacon attribution canaries across every deployed surface, every quarter (or on-demand after any suspected leak).

## Scope clarification — read first

This skill **operates on the operator's local registry**, which lives outside the public Swanlake repo:

- Local registry root: `~/projects/DEFENSE-BEACON/`
- Local canary state file: `~/projects/DEFENSE-BEACON/.canary-state.json`
- Local rotation tool: `~/projects/DEFENSE-BEACON/make-canaries.py`

The `defense-beacon/reference/make-canaries.py` inside this Swanlake repo is the **public reference implementation**. It is documentation. Do not run rotation against it — its output goes to `defense-beacon/reference/out/` which is gitignored in the public repo specifically because real canaries must never enter version control.

This skill lives in the public repo so other operators can fork the pattern. Each operator runs it against their own local registry under their own deploy paths.

## Hard rules

- **No real canaries in any file this skill commits.** The skill rotates secrets in a local registry; it must never `git add`, `git commit`, or otherwise propagate the new tokens into a tracked file. The PreToolUse hook in `.claude/hooks/canary-literal-block.sh` is a backstop, but the skill should not rely on it — design the workflow so the literals never touch a tracked path.
- **Surface deployment is out-of-band.** This skill rotates the local registry and emits per-surface deployment instructions. The operator pastes the new canaries into each surface (Notion, Supabase config, GitHub repo settings, etc.) by hand. Automating those API writes is a separate effort and out of scope here.

## The 12 surfaces

The operator's local registry tracks all deployed surfaces. Enumerate them with:

```bash
python3 ~/projects/DEFENSE-BEACON/make-canaries.py --list-surfaces
```

If `--list-surfaces` is not implemented in the operator's local copy, fall back to:

```bash
python3 -c "
import json, pathlib
p = pathlib.Path.home() / 'projects/DEFENSE-BEACON/.canary-state.json'
state = json.loads(p.read_text())
for sid in sorted(state.get('surfaces', {}).keys()):
    print(sid)
"
```

The surface count should be 12 (as of 2026-04-25). If it is not 12, surface that to the operator before proceeding — drift in the registry is itself a signal worth investigating.

## Per-surface rotation

For each `<surface_id>` returned above:

1. **Snapshot the OLD token** so you can grep-check post-rotation:

   ```bash
   OLD="$(python3 ~/projects/DEFENSE-BEACON/make-canaries.py --get "$SURFACE_ID")"
   ```

2. **Rotate** to a new token (the local tool atomically updates the registry under `flock`):

   ```bash
   python3 ~/projects/DEFENSE-BEACON/make-canaries.py --rotate "$SURFACE_ID"
   ```

3. **Emit deployment instructions** for the operator. The shape varies per surface:
   - Notion page: edit the page body, replace the old block markers
   - Supabase project: settings -> webhooks header (or wherever the operator embeds it)
   - GitHub repo (private): repo settings -> custom property
   - Vercel: project env var
   - etc. — the operator's `~/projects/DEFENSE-BEACON/SURFACES.md` is the source of truth for which surface lives where.

4. **Wait for operator confirmation** that the new token is deployed before moving to the next surface. Do not batch — if rotation fails mid-way, partial deployment is recoverable; full automation is not.

## Post-rotation verification

After all 12 surfaces are rotated and confirmed deployed:

1. **Tree-wide leak check for OLD tokens.** Concatenate every old token from the snapshots above and grep the operator's working tree (and the public Swanlake clone, and the vault) for any surviving occurrence outside the registry itself:

   ```bash
   # OLD_TOKENS is the newline-joined list of pre-rotation tokens.
   # SEARCH_ROOTS should be set by the operator to every place a canary
   # could plausibly land — typically the projects dir, the Claude config
   # dir, and any persistent vault / notes directory the operator uses.
   #
   # Example (operator edits to taste):
   #   SEARCH_ROOTS=( "$HOME/projects" "$HOME/.claude" "$VAULT_PATH" )
   for t in $OLD_TOKENS; do
     for root in "${SEARCH_ROOTS[@]}"; do
       grep -rnI --exclude-dir=.git --exclude-dir=node_modules \
         --exclude=".canary-state.json*" \
         "$t" "$root" 2>/dev/null
     done
   done
   ```

   Any hit is a smoking gun: the old token leaked somewhere it should not have. Investigate before proceeding.

2. **Update last-rotation timestamp.** This drives the operator's status-line staleness nudge:

   ```bash
   date -u +%Y-%m-%dT%H:%M:%SZ > ~/.claude/.last-canary-rotation
   ```

3. **Optional: post-rotation canary-tripwire monitoring.** The operator's `~/.claude/hooks/canary-match.sh` continues watching every Read/WebFetch/MCP fetch for canary literals. With the new tokens deployed, any hit in the next 90 days indicates an active read of a beacon-bearing surface — that's the entire point of the mechanism.

## Anti-patterns to refuse

- Do **not** offer to commit the new tokens "for safekeeping" anywhere in any repo.
- Do **not** echo the new tokens to a chat platform (Telegram, Slack, etc.) for "verification".
- Do **not** send any token to a third-party service for "validation". No legitimate validation service exists.
- Do **not** rotate a single surface without rotating all 12 unless the operator explicitly asks for a targeted rotation (e.g. one surface was burned and needs an emergency replacement).

## Cadence reminder

Default cadence is quarterly. The operator's status line shows days since last rotation; > 100 days is a soft nudge, > 120 days is a hard prompt. Off-cadence rotation is appropriate when:

- A canary tripwire fires (`~/.claude/canary-hits/` has a recent record).
- A surface is suspected compromised (Supabase project ownership change, Notion workspace member added, etc.).
- The operator changes the canary registry shape (e.g. adds a 13th surface) — rotate everything to keep token-shape uniform.
