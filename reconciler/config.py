"""Operator-local config loader.

Reads ~/.config/swanlake-reconciler/config.toml. The config holds:
  - deployment_map_path: absolute path to local DEFENSE-BEACON deployment map
  - vault_root: absolute path to operator's Obsidian vault
  - notion_master_page_id: Notion page ID for the Swanlake master page
  - notion_posture_page_id: Notion page ID for the live security posture page
  - swanlake_repo_path: absolute path to local Swanlake repo
  - canon_dir: absolute path to canon/ directory under swanlake_repo_path

No defaults are committed. Operator runs `--init` to create the file.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


CONFIG_PATH = Path.home() / '.config' / 'swanlake-reconciler' / 'config.toml'


@dataclass(frozen=True)
class Config:
    deployment_map_path: Path
    vault_root: Path
    notion_master_page_id: str
    notion_posture_page_id: str
    swanlake_repo_path: Path
    canon_dir: Path


class ConfigMissing(Exception):
    """Raised when the operator config does not exist; --init has not run."""


def load() -> Config:
    if not CONFIG_PATH.exists():
        raise ConfigMissing(
            f'No config at {CONFIG_PATH}. Run `swanlake-reconciler --init` first.'
        )
    with CONFIG_PATH.open('rb') as f:
        data = tomllib.load(f)
    return Config(
        deployment_map_path=Path(data['deployment_map_path']).expanduser(),
        vault_root=Path(data['vault_root']).expanduser(),
        notion_master_page_id=data['notion_master_page_id'],
        notion_posture_page_id=data['notion_posture_page_id'],
        swanlake_repo_path=Path(data['swanlake_repo_path']).expanduser(),
        canon_dir=Path(data['canon_dir']).expanduser(),
    )
