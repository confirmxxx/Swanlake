"""Vault sync engine — propagates canon templates into vault notes.

Each vault note may have one or more `swanlake-section-start: <name>` /
`swanlake-section-end: <name>` marker pairs. The sync engine extracts
the corresponding section from a template file and writes it into the
vault file between the markers (or appends the section if markers
absent). Files marked `swanlake-divergence: intentional` are skipped.

Writes are atomic (tempfile + os.replace) to survive mid-write crashes.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Literal

from reconciler import divergence


SyncResult = Literal['inserted', 'updated', 'unchanged', 'skipped-divergent']

DEFAULT_SECTION = 'defense-beacon-rules'


def _section_re(name: str) -> re.Pattern[str]:
    start = re.escape(f'<!-- swanlake-section-start: {name} -->')
    end = re.escape(f'<!-- swanlake-section-end: {name} -->')
    return re.compile(f'{start}.*?{end}\n?', re.DOTALL)


def _extract_section(template_text: str, name: str) -> str:
    rx = _section_re(name)
    m = rx.search(template_text)
    if not m:
        raise ValueError(f'Section "{name}" not found in template')
    return m.group(0)


def _atomic_write(path: Path, text: str) -> None:
    """Write text to path atomically: tempfile in same dir + os.replace."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + '.', suffix='.tmp', dir=str(parent),
    )
    try:
        with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def sync_file(vault_file: Path, template_file: Path, section_name: str) -> SyncResult:
    """Replace or insert a section in vault_file from template_file."""
    if divergence.is_divergent(vault_file):
        return 'skipped-divergent'

    template_text = template_file.read_text(encoding='utf-8')
    section = _extract_section(template_text, section_name)

    try:
        vault_text = vault_file.read_text(encoding='utf-8')
    except OSError:
        # File missing or unreadable — treat as new file with just the section.
        _atomic_write(vault_file, section)
        return 'inserted'

    rx = _section_re(section_name)
    if rx.search(vault_text):
        new_text = rx.sub(section, vault_text)
        if new_text == vault_text:
            return 'unchanged'
        _atomic_write(vault_file, new_text)
        return 'updated'

    # No markers present — append.
    if not vault_text.endswith('\n'):
        vault_text += '\n'
    _atomic_write(vault_file, vault_text + section)
    return 'inserted'


def run_sync_all() -> int:
    """CLI entry: read config, walk every vault target, sync each.

    Returns:
        0 if all attempted syncs succeeded AND at least one file was processed
        1 if any per-file error occurred
        2 if config or deployment-map could not be read
    """
    from reconciler import config, status
    try:
        cfg = config.load()
    except config.ConfigMissing as e:
        print(f'error: {e}', flush=True)
        return 2

    template = cfg.canon_dir / 'vault-template.md'
    try:
        dmap = json.loads(cfg.deployment_map_path.read_text())
    except OSError as e:
        print(f'error reading deployment-map: {e}', flush=True)
        return 2

    error_count = 0
    success_count = 0

    for surface_id, paths in dmap.get('surfaces', {}).items():
        if not surface_id.startswith('vault-'):
            continue
        # All vault-* surfaces use the same section today; future surfaces
        # may want their own — extend by adding a per-surface map then.
        for path_str in paths:
            p = Path(path_str)
            if not p.exists():
                continue
            try:
                result = sync_file(p, template, DEFAULT_SECTION)
                print(f'{surface_id}: {p.name} -> {result}')
                success_count += 1
            except Exception as e:
                print(f'{surface_id}: {p.name} -> ERROR: {e}', flush=True)
                error_count += 1

    # Only record sync timestamp if every attempted file succeeded AND we
    # actually attempted at least one. A run with zero matches OR any error
    # leaves the prior timestamp intact (better to be slightly stale than
    # to claim freshness we don't have).
    if error_count == 0 and success_count > 0:
        status.write_sync_timestamp('vault')

    return 1 if error_count > 0 else 0
