# Full-repo pre-publish audit — 2026-04-27

Auditor: `swanlake-pre-publish` contract, expanded to seven categories per operator-issued full-repo brief.
Scope: entire tree at `origin/main` (commit `4e8b776`), not diff-vs-tag.

## TL;DR

`publication-clean` — no actionable findings in any of the seven scanned categories.
No fixes applied. No deferred items requiring substantive review.
No BLOCKER-severity items.

## Method

Seven parallel scans run from repo root. Excluded: `.git/`, `**/__pycache__/`, `**/node_modules/`, `defense-beacon/reference/out/`.

| # | Category | Tool | Findings | Action |
|---|---|---|---|---|
| 1 | AI-attribution drift (commits + tracked content) | `grep -rnIE` + `git log --all` | 0 actionable | none |
| 2 | Real-shaped canary literals | 5 regex patterns | 0 | none |
| 3 | Host / path / private-email leaks | `grep -rnIE` for `/home/<op>`, `/mnt/c/Users/<initials>`, UUID page IDs, OAuth client IDs | 0 actionable | none |
| 4 | Operator-private project names | 13 patterns, word-boundary on `\bATLAS\b` and exclusions for contract-allowed generic CMA fixture names | 0 actionable | none |
| 5 | Stale `last_verified` markers (>60 days) | date-format grep, comparing against today (2026-04-26) | 0 stale | none |
| 6 | Broken internal cross-references | Python AST-style markdown link walker over 48 `*.md` files | 0 broken | none |
| 7 | Empty placeholders left in published artifacts | `<TODO>`, `<placeholder>`, `<TBD>`, `XXX`, `FIXME` | 0 actionable | none |

## Per-category notes

### 1 — AI-attribution drift

Five literal hits for the strict-drift patterns (`Co-Authored-By: Claude`, `🤖 Generated`, `made with Opus`, `AI-assisted`, `AI-generated`, `LLM-generated`). All five are **meta-references inside policy / contract documents** that quote the banned strings to define them as banned:

- `CONTRIBUTING.md:66` — DCO / no-AI-attribution policy section
- `CLAUDE.md:7` — project-level hard rule
- `.claude/agents/swanlake-pre-publish.md:39` — the auditor contract itself
- `.claude/skills/swanlake-release/SKILL.md:13` — release-skill's no-attribution discipline note
- `defense-beacon/examples/synthetic-saas/README.md:8` — synthetic SaaS scenario describes Claude Code as "AI-assisted coding harness"; product-category descriptor, not a generation marker

None are actual attribution drift. The auditor contract itself notes that meta-references in policy docs are acceptable: the rule targets propagation of fingerprints into artifacts, not erasure of the very vocabulary needed to enforce the rule.

References to the platform name `Claude Code` and the vendor `Anthropic` (in `DEPENDENCIES.md`, `THREAT-MODEL.md`, `NOTICE`, `README.md`, etc.) are structurally required: Swanlake is a defense framework that sits above the Claude Code harness and depends on Anthropic-shipped primitives. Not drift.

Git-history scan across `--all`: zero hits for `Co-Authored-By: Claude`, `🤖`, `Generated with Claude`, `Made with Opus`, etc., in any commit subject or body. Author/committer identities are operator's GitHub-noreply address and one operator-controlled iCloud relay (long-established commit identity for the public repo, not the operator's primary handle).

### 2 — Real-shaped canary literals

Five regex patterns scanned: `AKIA_BEACON_[0-9A-F]{20}`, `ghp_beacon_[0-9a-f]{40}`, `beacon-attrib-[a-z0-9-]+-[A-Za-z0-9]{8}`, `sk_live_beacon_[0-9a-f]{24}`, `AIzaSy[A-Za-z0-9_-]{30,}`. **Zero hits anywhere in tracked source.**

The repo's `.gitignore` correctly excludes `defense-beacon/reference/out/` (operator's local registry). Test fixtures use the documented `AKIA_BEACON_TESTFIXTURE000000000000` placeholder shape, which contains non-hex characters and does not match the real-canary regex by design.

### 3 — Host / path / private-email leaks

- `/home/banana` and `/mnt/c/Users/HP`: zero hits in tracked content. (One transient hit appears in `.git` worktree-pointer file `.git:1`, which is not tracked and not committed.)
- `.local/share/`: 3 hits, all inside `.claude/agents/swanlake-pre-publish.md` documenting the patterns the agent scans for. Meta, intentional.
- Real email addresses (excluding GitHub-noreply and `*.example.{com,org,invalid}`): zero hits in tracked content. Operator's primary email (`descant.hires7m@icloud.com`) is not present anywhere in the repo.
- UUID-shaped Notion page IDs: only the all-zeros placeholder `00000000-0000-0000-0000-000000000000` appears, used as documented "you must override this" defaults in `tools/sync-posture.py` and `tools/README.md`. No real page IDs.
- OAuth client IDs: zero hits.

### 4 — Operator-private project names

Patterns scanned: `Verso`, `Verso Prime`, `verso-crm`, `alfardzip`, `Passway`, `Tristar`, `Tristar AGS`, `Helena`, `Marstec`, `Olympus Mons`, `ingestion-worker`, `listing-composer`, `chart-vision`, `journalist`, and word-boundary `\bATLAS\b`.

- `Verso`, `verso-crm`, `alfardzip`, `Passway`, `Tristar`, `Helena`, `Marstec`, `Olympus Mons`, `ingestion-worker`, `listing-composer`, `chart-vision`, `journalist`: **zero hits**.
- `\bATLAS\b`: 12 hits, all under `experiments/white-cells/` referencing the public **MITRE ATLAS** threat-knowledge-base (`atlas-taxonomy.yaml`, ATLAS TTP IDs, `atlas.mitre.org`). Explicitly contract-allowed.
- Generic CMA-fixture names `orchestrator`, `data-extractor`, `report-writer` exist under `swanlake/tests/fixtures/cma_project/cmas/` and are explicitly contract-allowed.

### 5 — Stale `last_verified` markers

Four `as of YYYY-MM-DD` lines found, all dated within today minus 60 days (today: 2026-04-26):

| File | Date | Age |
|---|---|---|
| `.claude/skills/canary-rotate/SKILL.md:48` | 2026-04-25 | 1 day |
| `defense-beacon/README.md:114` | 2026-04-24 | 2 days |
| `trust-zones/README.md:110` | 2026-04-24 | 2 days |
| `experiments/white-cells/SPEC.md:234` | 2026-04-25 | 1 day |

All fresh. No fixes required.

### 6 — Broken internal cross-references

Walked all 48 markdown files for relative `[text](path)` links, resolved each against the source file's directory and the repo root. **Zero broken links.**

### 7 — Empty placeholders

Single hit: `defense-beacon/SPEC.md:72`, which is the security-note paragraph itself explaining that `<placeholder>` notation is the correct convention. Intentional, meta. Not actionable.

## Conclusion

The repo at `origin/main` (`4e8b776`) is in a publication-clean state across all seven audit categories. No fixes were applied because no leftover artifacts were identified that match the "obvious 1-line fix" criterion — every hit traced back to either (a) a meta-reference in a policy/contract document that legitimately quotes the banned vocabulary in order to enforce the ban, (b) a structurally required platform-name reference, or (c) a contract-explicit allowlist (MITRE ATLAS, generic CMA fixture names, all-zeros placeholder UUIDs).

This audit doc is itself the only artifact produced by this branch.

## Next-tag readiness

Belt-and-suspenders: the `swanlake-pre-publish` subagent's three core scans (attribution / canary / host-paths) plus the four extended scans (private-names / staleness / xrefs / placeholders) all return clean. A release skill invoked tomorrow morning would emit `publication-clean` and the operator can cut a tag from `origin/main` without scrubbing.
