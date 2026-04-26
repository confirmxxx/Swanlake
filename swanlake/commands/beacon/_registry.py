"""Surface-type registry for the v0.3 beacon family.

Single source of truth for the 7-row matrix in the spec (section 4).
Every other beacon module reads from here so adding a new surface type
is one file edit, not a grep across cli.py + sweep + deploy + checklist.

The registry is intentionally a static tuple of dataclasses, not a
dynamic plugin system: the LOCAL/REMOTE split is load-bearing security,
not a configuration choice. Adding a new type requires touching this
file (= visible in code review per spec R9).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Surface-id grammar mirrored from defense-beacon/reference/make-canaries.py
# (SURFACE_ID_RE there). Re-declared here so swanlake never imports the
# script (per D6 / spec A5: subprocess wrappers, never imports).
SURFACE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")


# Method labels are the human-facing strings in the `swanlake beacon list`
# table. They map 1:1 to dispatch decisions in deploy/checklist.
METHOD_LOCAL = "local-write"
METHOD_REMOTE_CHECKLIST = "remote-checklist"
METHOD_PR_CHECKLIST = "pr-checklist"

# Scope buckets used by `swanlake beacon sweep --scope {local,remote,all}`.
SCOPE_LOCAL = "local"
SCOPE_REMOTE = "remote"


@dataclass(frozen=True)
class SurfaceType:
    """One row of the spec's surface-type matrix."""

    type_id: str
    deploy_method: str
    scope: str
    description: str
    examples: tuple[str, ...]

    @property
    def is_local(self) -> bool:
        return self.scope == SCOPE_LOCAL

    @property
    def is_remote(self) -> bool:
        return self.scope == SCOPE_REMOTE


# The 7-row matrix, in the order the spec presents it.
SURFACE_TYPES: tuple[SurfaceType, ...] = (
    SurfaceType(
        type_id="claude-md",
        deploy_method=METHOD_LOCAL,
        scope=SCOPE_LOCAL,
        description="Project CLAUDE.md files (~/projects/*/CLAUDE.md and nested)",
        examples=("cms-project-alpha", "cms-project-beta"),
    ),
    SurfaceType(
        type_id="vault",
        deploy_method=METHOD_LOCAL,
        scope=SCOPE_LOCAL,
        description="Knowledge-vault notes (*.md under operator vault root)",
        examples=("vault-root", "vault-patterns"),
    ),
    SurfaceType(
        type_id="notion",
        deploy_method=METHOD_REMOTE_CHECKLIST,
        scope=SCOPE_REMOTE,
        description="Workspace pages (e.g. operator-managed wiki)",
        examples=("cms-workspace-root",),
    ),
    SurfaceType(
        type_id="supabase-env",
        deploy_method=METHOD_REMOTE_CHECKLIST,
        scope=SCOPE_REMOTE,
        description="Supabase project env vars (existence-only verify)",
        examples=("deploy-project-alpha",),
    ),
    SurfaceType(
        type_id="vercel-env",
        deploy_method=METHOD_REMOTE_CHECKLIST,
        scope=SCOPE_REMOTE,
        description="Vercel project env vars (existence-only verify)",
        examples=("deploy-project-beta",),
    ),
    SurfaceType(
        type_id="github-public",
        deploy_method=METHOD_PR_CHECKLIST,
        scope=SCOPE_REMOTE,
        description="Public-repo README / SECURITY.md / CLAUDE.md (PR-only)",
        examples=("repo-project-alpha",),
    ),
    SurfaceType(
        type_id="claude-routine",
        deploy_method=METHOD_REMOTE_CHECKLIST,
        scope=SCOPE_REMOTE,
        description="Scheduled routine prompts (manual export-then-paste)",
        examples=("routine-intel-weekly",),
    ),
)


# Surface-id-prefix -> type_id mapping. Matches the convention in
# defense-beacon/reference/surfaces.example.yaml. Used as a fallback
# when surfaces.yaml does not carry an explicit `type:` annotation.
PREFIX_TYPE_MAP: dict[str, str] = {
    "cms-": "claude-md",       # project workspace pages and project CLAUDE.md
    "vault-": "vault",
    "deploy-": "supabase-env",  # default to supabase; vercel surfaces opt-in
    "repo-": "github-public",
    "routine-": "claude-routine",
    "agent-": "claude-md",      # agent-harness configs are file-shaped
    "db-": "supabase-env",
}


def infer_type(surface_id: str, explicit_type: str | None = None) -> str:
    """Return the type_id for a surface.

    Precedence: explicit type from surfaces.yaml > prefix-based inference >
    default 'claude-md'. The default is the safest fallback (LOCAL,
    requires confirmation) so an unknown surface cannot trigger a
    REMOTE-checklist by accident.
    """
    if explicit_type:
        if any(t.type_id == explicit_type for t in SURFACE_TYPES):
            return explicit_type
    for prefix, type_id in PREFIX_TYPE_MAP.items():
        if surface_id.startswith(prefix):
            return type_id
    return "claude-md"


def get_type(type_id: str) -> SurfaceType | None:
    """Return the SurfaceType matching `type_id`, or None."""
    for t in SURFACE_TYPES:
        if t.type_id == type_id:
            return t
    return None


def validate_surface_id(surface_id: str) -> bool:
    """Return True iff `surface_id` matches the SURFACE_ID_RE grammar."""
    return bool(SURFACE_ID_RE.match(surface_id))


__all__ = [
    "SurfaceType",
    "SURFACE_TYPES",
    "PREFIX_TYPE_MAP",
    "METHOD_LOCAL",
    "METHOD_REMOTE_CHECKLIST",
    "METHOD_PR_CHECKLIST",
    "SCOPE_LOCAL",
    "SCOPE_REMOTE",
    "SURFACE_ID_RE",
    "infer_type",
    "get_type",
    "validate_surface_id",
]
