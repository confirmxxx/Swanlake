"""Divergence frontmatter detector.

A file marked with `swanlake-divergence: intentional` in YAML frontmatter
is opted out of reconciliation - sync engines skip it. Surfaced in
--status as informational (not error).
"""
from __future__ import annotations

import re
from pathlib import Path


# Frontmatter delimited by --- on its own line at start of file.
# Tolerate both LF and CRLF line endings; tolerate optional trailing newline
# after the closing --- (spec-legal Markdown).
_FRONTMATTER_RE = re.compile(
    r'^---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)',
    re.DOTALL,
)
_DIVERGENCE_LINE_RE = re.compile(
    r'^\s*swanlake-divergence:\s*intentional\s*$',
    re.MULTILINE,
)


def is_divergent(file_path: Path) -> bool:
    """Return True iff file has YAML frontmatter with `swanlake-divergence: intentional`."""
    try:
        text = file_path.read_text(encoding='utf-8')
    except OSError:
        # FileNotFoundError, IsADirectoryError, PermissionError, NotADirectoryError,
        # ELOOP, ENAMETOOLONG - any read failure means we cannot determine
        # divergence; fail closed (treat as non-divergent so the sync engine
        # surfaces a real error if the file is actually expected).
        return False
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        return False
    frontmatter = m.group(1)
    return bool(_DIVERGENCE_LINE_RE.search(frontmatter))
