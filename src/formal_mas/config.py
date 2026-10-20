"""Load config/models.yaml and config/pipeline.yaml (ablation overlays arrive in Phase 6)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


def load_yaml(name: str, config_dir: Path = CONFIG_DIR) -> dict:
    return yaml.safe_load((config_dir / name).read_text(encoding="utf-8")) or {}


def load_models(config_dir: Path = CONFIG_DIR) -> dict:
    return load_yaml("models.yaml", config_dir)


def load_pipeline(config_dir: Path = CONFIG_DIR) -> dict:
    return load_yaml("pipeline.yaml", config_dir)
