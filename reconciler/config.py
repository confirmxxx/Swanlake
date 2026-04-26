"""Operator-local config loader.

Reads the reconciler config from one of two locations, in this precedence:

  1. ~/.swanlake/config.toml  (preferred, written by `swanlake init`)
  2. ~/.config/swanlake-reconciler/config.toml  (legacy XDG path)

The legacy path is still honoured for operators who installed before the
unified state root landed; reading it emits a one-line stderr deprecation
hint pointing at the new location. ConfigMissing is raised only when
neither file exists.

The config holds:
  - deployment_map_path: absolute path to local DEFENSE-BEACON deployment map
  - vault_root: absolute path to operator's Obsidian vault
  - notion_master_page_id: Notion page ID for the Swanlake master page
  - notion_posture_page_id: Notion page ID for the live security posture page
  - swanlake_repo_path: absolute path to local Swanlake repo
  - canon_dir: absolute path to canon/ directory under swanlake_repo_path

No defaults are committed. Operator runs `swanlake init` (or the legacy
`swanlake-reconciler --init`) to create the file.
"""
from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


# Preferred unified state root (spec A3 / A11). Kept in sync with
# swanlake.state.DEFAULT_STATE_ROOT but resolved independently so this
# module stays importable without the swanlake package.
NEW_CONFIG_PATH = Path.home() / '.swanlake' / 'config.toml'
LEGACY_CONFIG_PATH = Path.home() / '.config' / 'swanlake-reconciler' / 'config.toml'

# Back-compat alias: prior callers imported CONFIG_PATH directly. Point
# it at the new location so external scripts that read this constant
# follow the same precedence the loader does.
CONFIG_PATH = NEW_CONFIG_PATH


@dataclass(frozen=True)
class Config:
    deployment_map_path: Path
    vault_root: Path
    notion_master_page_id: str
    notion_posture_page_id: str
    swanlake_repo_path: Path
    canon_dir: Path


class ConfigMissing(Exception):
    """Raised when the operator config does not exist at either path."""


def _resolve_config_path() -> Path:
    """Pick the active config path, preferring the new location.

    Emits a one-line deprecation hint to stderr when only the legacy
    path is present so operators see the migration cue without it
    being noisy when they have already moved.
    """
    if NEW_CONFIG_PATH.exists():
        return NEW_CONFIG_PATH
    if LEGACY_CONFIG_PATH.exists():
        print(
            f'swanlake-reconciler: reading legacy config at {LEGACY_CONFIG_PATH}; '
            f'move to {NEW_CONFIG_PATH} (run `swanlake init`).',
            file=sys.stderr,
        )
        return LEGACY_CONFIG_PATH
    raise ConfigMissing(
        f'No config at {NEW_CONFIG_PATH} or {LEGACY_CONFIG_PATH}. '
        f'Run `swanlake init` first.'
    )


def load() -> Config:
    cfg_path = _resolve_config_path()
    with cfg_path.open('rb') as f:
        data = tomllib.load(f)
    return Config(
        deployment_map_path=Path(data['deployment_map_path']).expanduser(),
        vault_root=Path(data['vault_root']).expanduser(),
        notion_master_page_id=data['notion_master_page_id'],
        notion_posture_page_id=data['notion_posture_page_id'],
        swanlake_repo_path=Path(data['swanlake_repo_path']).expanduser(),
        canon_dir=Path(data['canon_dir']).expanduser(),
    )
