# `.swanlake-no-beacon` opt-out marker

A zero-byte (or YAML-frontmatter-bearing) file at any directory's root tells
`swanlake beacon` to skip that directory and all descendants from sweep and
deploy. Spec §5 step 4, §9 R3, N4.

## When to use it

Drop a `.swanlake-no-beacon` file in:

- A scratchpad or throwaway project where a `CLAUDE.md` is just notes.
- A vendored / submodule directory whose `CLAUDE.md` belongs to a third
  party — you don't want sweep to keep flagging it as unbeaconed.
- A subtree of an otherwise beaconed project that's intentionally
  attribution-free (e.g. a `fixtures/` or `experiments/` folder).

Without the marker, `swanlake beacon sweep` will keep reporting these as
`unbeaconed` forever, which is the operator-confusion mode this marker
exists to prevent.

## Format

Two flavors:

### 1. Empty file — exclude everything below

```bash
touch .swanlake-no-beacon
```

This excludes the directory and every descendant from both sweep and
deploy. Sweep records the surfaces in the `skipped_by_optout` bucket;
deploy refuses with a clear error pointing back at the marker file.

### 2. Surface-id list — exclude only specific IDs

```yaml
surfaces: [cms-scratch-foo, cms-scratch-bar]
```

This is a single line of YAML-shaped frontmatter. Only the listed
surface IDs are excluded; other surfaces in the same subtree are still
swept and deployable. The surface-id grammar matches the rest of
Swanlake: `[a-z0-9][a-z0-9-]{0,62}[a-z0-9]`.

If the file is malformed (the parser cannot find a `surfaces:` line in
the recognised shape), Swanlake falls back to the empty-file semantics
and excludes everything below — fail-closed.

## Walk semantics

Sweep and deploy walk up from the target file looking for the nearest
ancestor `.swanlake-no-beacon`. The walk is bounded to 32 levels (a
defense against pathological symlink loops). The first marker found is
the one that applies; markers in further-up ancestors are ignored.

```
~/projects/foo/                 (no marker; subject to sweep)
├── .swanlake-no-beacon         <- marker A: excludes everything below
├── pkg-a/CLAUDE.md             <- skipped (marker A)
├── pkg-b/
│   ├── .swanlake-no-beacon     <- marker B: overrides A for this subtree
│   └── CLAUDE.md               <- skipped (marker B)
└── pkg-c/CLAUDE.md             <- skipped (marker A)
```

## What it does NOT do

- Does not delete any beacon block from the target file (use
  `git checkout` or hand-edit).
- Does not affect the `~/.swanlake/canary-strings.txt` registry — opt-out
  is about LOCAL deploy/sweep, not about registry contents.
- Does not affect REMOTE-surface checklists. A REMOTE surface listed in
  `surfaces.yaml` whose target identifier (Notion page URL, env var key)
  happens to live "under" an opt-out directory is still surfaced in the
  checklist; the marker only governs LOCAL file-tree decisions.
