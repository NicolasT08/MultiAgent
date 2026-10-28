"""Resolve a profile of config/models.yaml into per-role request specs (CLAUDE.md §1.2).

Nothing here names a model: every id, URL and price comes from models.yaml. Resolution
also enforces the rules a profile must follow:
    - `same_as` copies another role (baselines share model and parameters with the agent
      they are compared with);
    - a thinking role may not set a parameter the model ignores in thinking mode
      (`thinking.ignores`), so the YAML never shows a value that has no effect;
    - `fallbacks` only where the profile says `fallbacks_allowed: true`;
    - a `pinned` profile may not use `-latest`/`latest` ids (RNF-2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from formal_mas.config import load_models

ROLE_KEYS = {"model", "thinking", "reasoning_effort", "max_tokens", "temperature", "top_p",
             "presence_penalty", "frequency_penalty", "fallbacks"}
REQUEST_PARAMS = ("max_tokens", "temperature", "top_p", "presence_penalty", "frequency_penalty", "reasoning_effort")


class ConfigError(ValueError):
    """models.yaml does not describe a valid profile."""


@dataclass(frozen=True)
class ModelSpec:
    key: str  # entry under `models:`
    provider: str
    base_url: str
    api_key_env: str
    model_id: str  # sent as `model`
    expected_version: str | None
    json_mode: bool
    prompt_style: str
    pricing: dict
    thinking: dict  # {enabled: {extra_body…}, disabled: {…}, ignores: […]}
    extra_body: dict = field(default_factory=dict)
    min_interval_s: float = 0.0


@dataclass(frozen=True)
class RoleSpec:
    role: str
    models: tuple[ModelSpec, ...]  # primary first, then fallbacks
    params: dict  # request parameters (max_tokens, temperature, reasoning_effort…)
    thinking: bool | None  # None = the model's default

    @property
    def model(self) -> ModelSpec:
        return self.models[0]

    def describe(self) -> dict:
        """For run_config.json: the exact models and parameters of this role."""
        return {"model": self.model.key, "model_id": self.model.model_id, "provider": self.model.provider,
                "expected_version": self.model.expected_version, "thinking": self.thinking, **self.params,
                "fallbacks": [m.key for m in self.models[1:]]}


def profile_name(models_cfg: dict | None = None) -> str:
    """MODEL_PROFILE, else `default_profile` (dev)."""
    cfg = models_cfg if models_cfg is not None else load_models()
    return os.environ.get("MODEL_PROFILE") or cfg.get("default_profile") or "dev"


def model_spec(cfg: dict, key: str) -> ModelSpec:
    models = cfg.get("models") or {}
    if key not in models:
        raise ConfigError(f"unknown model {key!r} (not under `models:`)")
    m = models[key]
    prov_key = m.get("provider")
    prov = (cfg.get("providers") or {}).get(prov_key)
    if not prov:
        raise ConfigError(f"model {key!r}: unknown provider {prov_key!r}")
    for k in ("id", "pricing"):
        if m.get(k) is None:
            raise ConfigError(f"model {key!r}: missing `{k}`")
    return ModelSpec(key=key, provider=prov_key, base_url=prov["base_url"], api_key_env=prov["api_key_env"],
                     model_id=str(m["id"]), expected_version=m.get("expected_version"),
                     json_mode=bool(m.get("json_mode", True)), prompt_style=m.get("prompt_style", "chat"),
                     pricing=dict(m["pricing"]), thinking=dict(m.get("thinking") or {}),
                     extra_body=dict(m.get("extra_body") or {}), min_interval_s=float(prov.get("min_interval_s", 0)))


def _raw_role(roles: dict, role: str, seen: tuple[str, ...] = ()) -> dict:
    if role not in roles:
        raise ConfigError(f"unknown role {role!r}")
    raw = dict(roles[role] or {})
    if "same_as" in raw:
        target = raw.pop("same_as")
        if target in seen or target == role:
            raise ConfigError(f"`same_as` cycle: {' → '.join((*seen, role, target))}")
        if raw:
            raise ConfigError(f"role {role!r}: `same_as` cannot be combined with {sorted(raw)}")
        return _raw_role(roles, target, (*seen, role))
    return raw


def resolve_role(cfg: dict, profile: str, role: str) -> RoleSpec:
    profiles = cfg.get("profiles") or {}
    if profile not in profiles:
        raise ConfigError(f"unknown profile {profile!r} (have {sorted(profiles)})")
    prof = profiles[profile]
    raw = _raw_role(prof.get("roles") or {}, role)
    unknown = set(raw) - ROLE_KEYS
    if unknown:
        raise ConfigError(f"{profile}.{role}: unknown keys {sorted(unknown)}")
    if "model" not in raw:
        raise ConfigError(f"{profile}.{role}: missing `model`")
    fallbacks = list(raw.get("fallbacks") or [])
    if fallbacks and not prof.get("fallbacks_allowed", False):
        raise ConfigError(f"{profile}.{role}: fallbacks are not allowed in profile {profile!r}")
    models = tuple(model_spec(cfg, k) for k in [raw["model"], *fallbacks])
    thinking = raw.get("thinking")
    params = {k: raw[k] for k in REQUEST_PARAMS if raw.get(k) is not None}
    for m in models:
        if prof.get("pinned") and "latest" in m.model_id.lower():
            raise ConfigError(f"{profile}.{role}: pinned profile cannot use alias {m.model_id!r}")
        if thinking is not None:
            mode = "enabled" if thinking else "disabled"
            if mode not in m.thinking:
                raise ConfigError(f"{profile}.{role}: model {m.key!r} cannot set thinking {mode}")
        if thinking is not False:  # thinking on (explicit or default)
            ignored = sorted(set(params) & set(m.thinking.get("ignores") or []))
            if ignored:
                raise ConfigError(f"{profile}.{role}: {ignored} are ignored by {m.key!r} in thinking mode; remove them")
    return RoleSpec(role=role, models=models, params=params, thinking=thinking)


def resolve_profile(cfg: dict | None = None, profile: str | None = None) -> dict[str, RoleSpec]:
    """Every role of the profile, validated."""
    cfg = cfg if cfg is not None else load_models()
    profile = profile or profile_name(cfg)
    roles = ((cfg.get("profiles") or {}).get(profile) or {}).get("roles")
    if not roles:
        raise ConfigError(f"profile {profile!r} has no roles")
    return {r: resolve_role(cfg, profile, r) for r in roles}
