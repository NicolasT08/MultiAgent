"""Load config/models.yaml and config/pipeline.yaml, plus the ablation overlays (config/ablations/)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
ABLATIONS = ("C1", "C2", "C3", "C4", "C5")
FLOWS = ("full", "baseline_a", "baseline_b")


def load_yaml(name: str, config_dir: Path = CONFIG_DIR) -> dict:
    return yaml.safe_load((config_dir / name).read_text(encoding="utf-8")) or {}


def load_models(config_dir: Path = CONFIG_DIR) -> dict:
    return load_yaml("models.yaml", config_dir)


def load_pipeline(config_dir: Path = CONFIG_DIR) -> dict:
    return load_yaml("pipeline.yaml", config_dir)


def deep_merge(base: dict, overlay: dict) -> dict:
    """`overlay` over a copy of `base`, section by section."""
    out = deepcopy(base)
    for key, value in overlay.items():
        out[key] = deep_merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def ablation_path(name: str, config_dir: Path = CONFIG_DIR) -> Path:
    matches = sorted((config_dir / "ablations").glob(f"{name}_*.yaml"))
    if len(matches) != 1:
        raise ValueError(f"expected one file config/ablations/{name}_*.yaml, found {len(matches)}")
    return matches[0]


def apply_ablation(pipeline_cfg: dict, name: str, config_dir: Path = CONFIG_DIR) -> dict:
    """pipeline.yaml + the flags of one configuration (CLAUDE.md §1.7), validated."""
    overlay = yaml.safe_load(ablation_path(name, config_dir).read_text(encoding="utf-8")) or {}
    merged = deep_merge(pipeline_cfg, overlay)
    validate_pipeline(merged)
    return merged


def validate_pipeline(cfg: dict) -> None:
    """Fail at load time, not in the middle of a run. Raises ValueError."""
    if cfg.get("flow", "full") not in FLOWS:
        raise ValueError(f"flow must be one of {FLOWS}, got {cfg.get('flow')!r}")
    try:
        b_max, k_max, r_max = int(cfg["budget"]["b_max"]), int(cfg["critic"]["k_max"]), int(cfg["critic"]["r_max"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"budget.b_max, critic.k_max and critic.r_max are required integers: {exc!r}") from exc
    if b_max < 1 or k_max < 1 or r_max < 0:
        raise ValueError(f"need b_max ≥ 1, k_max ≥ 1, r_max ≥ 0; got {b_max}, {k_max}, {r_max}")
    planner = cfg.get("planner") or {}
    required = {"tactics", "heartbeats", "timeout_s"}
    for section, block in (("planner.refute", planner.get("refute")), ("planner.closing_auto", planner.get("closing_auto")),
                           ("prepass", cfg.get("prepass"))):
        if block and block.get("enabled", True) and (missing := required - set(block)):
            raise ValueError(f"{section} is missing {sorted(missing)}")
