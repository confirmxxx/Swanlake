"""--init setup wizard for fresh-machine onboarding.

Steps:
  1. Prompt for deployment-map path, vault root, Notion page IDs, Swanlake repo path
  2. Write ~/.config/swanlake-reconciler/config.toml (idempotent, atomic)
  3. Copy systemd timer + service files into ~/.config/systemd/user/
  4. Print activation commands the operator must run (sudoless)
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


CONFIG_DIR = Path.home() / '.config' / 'swanlake-reconciler'
SYSTEMD_USER_DIR = Path.home() / '.config' / 'systemd' / 'user'
TEMPLATES_DIR = Path(__file__).resolve().parent / 'templates'


def _prompt_inputs() -> dict[str, str]:
    """Interactive prompt — patched in tests."""
    return {
        'deployment_map_path': input('deployment-map path: ').strip(),
        'vault_root': input('vault root (Obsidian dir): ').strip(),
        'notion_master_page_id': input('Notion master page ID: ').strip(),
        'notion_posture_page_id': input('Notion posture page ID: ').strip(),
        'swanlake_repo_path': input('Swanlake repo path: ').strip(),
        'canon_dir': input('canon/ dir (default: <repo>/canon): ').strip(),
    }


def _atomic_write(path: Path, text: str) -> None:
    """Atomic write: tempfile in same dir + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + '.', suffix='.tmp', dir=str(path.parent),
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


def _toml_value(s: str) -> str:
    """Escape a string for TOML — quote and escape backslashes + quotes."""
    escaped = s.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def _format_config(values: dict[str, str]) -> str:
    """Render the dict as TOML key = "value" lines, alphabetized for stable output."""
    lines = [
        '# swanlake-reconciler config — written by --init.',
        '# Edit by hand or re-run --init to regenerate.',
        '',
    ]
    for key in sorted(values):
        lines.append(f'{key} = {_toml_value(values[key])}')
    return '\n'.join(lines) + '\n'


def _write_config(values: dict[str, str]) -> Path:
    cfg_path = CONFIG_DIR / 'config.toml'
    _atomic_write(cfg_path, _format_config(values))
    return cfg_path


def _install_systemd_units() -> None:
    """Copy systemd unit files from templates/ into ~/.config/systemd/user/.

    Idempotent: copies overwrite. Skipped silently if templates don't exist
    (so init still completes for users without systemd or before Task 10
    template files land)."""
    try:
        SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    for name in ('swanlake-vault-sync.service', 'swanlake-vault-sync.timer'):
        src = TEMPLATES_DIR / name
        if src.exists():
            dst = SYSTEMD_USER_DIR / name
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass


def run_init(skip_systemd: bool = False) -> int:
    """Run the --init wizard. Returns 0 on success."""
    values = _prompt_inputs()
    cfg = _write_config(values)
    if not skip_systemd:
        _install_systemd_units()
    print(f'wrote {cfg}')
    if not skip_systemd:
        print('next steps:')
        print('  systemctl --user daemon-reload')
        print('  systemctl --user enable --now swanlake-vault-sync.timer')
    return 0
