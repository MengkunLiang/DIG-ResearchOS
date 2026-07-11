from __future__ import annotations

"""User-facing configuration overlay.

Everyday LLM, budget, timeout, retry, and escalation changes should go through
`config/user_settings.yaml`. Other YAML files remain capability/topology/routing
registries and should not duplicate daily parameters.
"""

from copy import deepcopy
import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_USER_SETTINGS_PATH = Path(__file__).parent.parent.parent / "config" / "user_settings.yaml"


def should_apply_default_user_settings(config_path: Path, default_config_path: Path) -> bool:
    """Return true when the repo-level user settings should overlay a default config."""

    if "RESEARCHOS_USER_SETTINGS" in os.environ:
        return True
    try:
        return config_path.resolve() == default_config_path.resolve()
    except OSError:
        return False


def resolve_user_settings_path(default_path: Path | None = None) -> Path:
    """Return the active user settings path.

    `RESEARCHOS_USER_SETTINGS` is an explicit runtime override and therefore
    wins over any config-dir local default passed by validators/tests.
    """

    if "RESEARCHOS_CONFIG" in os.environ:
        env_path = os.environ.get("RESEARCHOS_CONFIG", "").strip()
        if not env_path:
            return Path("__researchos_user_settings_disabled__")
        return Path(env_path)
    if "RESEARCHOS_USER_SETTINGS" in os.environ:
        env_path = os.environ.get("RESEARCHOS_USER_SETTINGS", "").strip()
        if not env_path:
            return Path("__researchos_user_settings_disabled__")
        return Path(env_path)
    return default_path or DEFAULT_USER_SETTINGS_PATH


def load_user_settings(path: Path | None = None) -> dict[str, Any]:
    """Load user-facing settings.

    The environment variable is useful for tests and temporary experiments:
    - unset: use `config/user_settings.yaml` if it exists;
    - empty/nonexistent path: no overlay.
    """

    settings_path = resolve_user_settings_path(path)
    if not settings_path.exists():
        return {}
    data = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def apply_agent_param_overrides(config: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Apply user_settings.yaml overlays to agent_params.yaml data."""

    if not settings:
        return config
    out = deepcopy(config)
    _apply_runtime_overrides(out, settings)
    agents = out.setdefault("agents", {})
    if not isinstance(agents, dict):
        out["agents"] = agents = {}

    defaults = _collect_default_overrides(settings)
    default_llm = _as_mapping(defaults.get("llm"))
    default_budget = _as_mapping(defaults.get("budget"))
    default_behavior = _as_mapping(defaults.get("behavior"))
    default_tools = _as_mapping(defaults.get("tools"))

    for agent_name, agent_cfg in agents.items():
        if not isinstance(agent_cfg, dict):
            continue
        _merge_section(agent_cfg, "llm", default_llm)
        _merge_section(agent_cfg, "budget", default_budget)
        _merge_section(agent_cfg, "behavior", default_behavior)
        _merge_section(agent_cfg, "tools", default_tools)

    for agent_name, override in _collect_agent_overrides(settings).items():
        agent_cfg = agents.setdefault(agent_name, {})
        if not isinstance(agent_cfg, dict):
            agent_cfg = {}
            agents[agent_name] = agent_cfg
        _deep_update(agent_cfg, override)

    return out


def apply_model_routing_overrides(config: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Apply user_settings.yaml overlays to model_routing.yaml data."""

    if not settings:
        return config
    out = deepcopy(config)
    llm = _as_mapping(settings.get("llm"))
    if llm.get("default_profile"):
        out["default_profile"] = llm["default_profile"]
    if isinstance(llm.get("endpoints"), dict):
        endpoints = out.setdefault("endpoints", {})
        if isinstance(endpoints, dict):
            _deep_update(endpoints, llm["endpoints"])
    if isinstance(llm.get("profiles"), dict):
        profiles = out.setdefault("profiles", {})
        if isinstance(profiles, dict):
            _deep_update(profiles, llm["profiles"])
    return out


def _apply_runtime_overrides(config: dict[str, Any], settings: dict[str, Any]) -> None:
    runtime = _as_mapping(settings.get("runtime"))
    if not runtime:
        return
    mapping = {
        "global_budget": "global_budget",
        "timeouts": "global_timeout",
        "global_timeout": "global_timeout",
        "retry_policy": "retry_policy",
        "budget_escalation": "budget_escalation",
    }
    for source_key, target_key in mapping.items():
        block = runtime.get(source_key)
        if isinstance(block, dict):
            current = _as_mapping(config.get(target_key))
            merged = deepcopy(current)
            _deep_update(merged, block)
            config[target_key] = merged


def active_user_settings_summary(path: Path | None = None) -> dict[str, Any]:
    """Small diagnostic summary for validate-config."""

    settings_path = resolve_user_settings_path(path)
    settings = load_user_settings(path)
    if not settings:
        return {"enabled": False, "path": str(settings_path), "overrides": []}
    overrides: list[str] = []
    defaults = _as_mapping(settings.get("defaults"))
    if defaults:
        overrides.append("defaults (legacy concise)")
    llm = _as_mapping(settings.get("llm"))
    if llm:
        for key in ("default_profile", "endpoints", "profiles", "defaults"):
            if llm.get(key):
                overrides.append(f"llm.{key}")
        llm_agents = _as_mapping(llm.get("agents"))
        overrides.extend(f"llm.agents.{name}" for name in sorted(llm_agents))
    budget = _as_mapping(settings.get("budget"))
    if budget:
        if budget.get("defaults"):
            overrides.append("budget.defaults")
        budget_agents = _as_mapping(budget.get("agents"))
        overrides.extend(f"budget.agents.{name}" for name in sorted(budget_agents))
    runtime = _as_mapping(settings.get("runtime"))
    if runtime:
        overrides.append("runtime")
    agents = _as_mapping(settings.get("agents"))
    overrides.extend(f"agents.{name} (legacy concise)" for name in sorted(agents))
    return {"enabled": True, "path": str(settings_path), "overrides": overrides}


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


_LLM_SHORTHAND_KEYS = {"profile", "tier", "model", "endpoint", "max_context", "temperature"}
_BUDGET_SHORTHAND_KEYS = {
    "max_steps",
    "max_tokens_total",
    "max_tokens",
    "max_wall_seconds",
    "max_validation_retries",
    "unlimited_budget",
    "tags",
    "budget_tags",
}
_TOOLS_SHORTHAND_KEYS = {"tool_names", "allowed_read_prefixes", "allowed_write_prefixes", "extra_tool_names"}
_PROMPT_SHORTHAND_KEYS = {"prompt_template", "structured_outputs", "expected_outputs", "output_schemas"}


def _normalize_agent_like_block(block: dict[str, Any]) -> dict[str, Any]:
    """Allow concise user settings while preserving the sectioned runtime shape."""

    out = deepcopy(block)
    for key in list(block.keys()):
        if key in _LLM_SHORTHAND_KEYS:
            out.setdefault("llm", {})[key] = out.pop(key)
        elif key in _BUDGET_SHORTHAND_KEYS:
            budget_key = "max_tokens_total" if key == "max_tokens" else key
            out.setdefault("budget", {})[budget_key] = out.pop(key)
        elif key in _TOOLS_SHORTHAND_KEYS:
            out.setdefault("tools", {})[key] = out.pop(key)
        elif key in _PROMPT_SHORTHAND_KEYS:
            out.setdefault("prompt", {})[key] = out.pop(key)

    modes = out.get("modes")
    if isinstance(modes, dict):
        out["modes"] = {
            mode_name: _normalize_agent_like_block(mode_cfg) if isinstance(mode_cfg, dict) else mode_cfg
            for mode_name, mode_cfg in modes.items()
        }
    budget = out.get("budget")
    if isinstance(budget, dict) and "max_tokens" in budget:
        budget.setdefault("max_tokens_total", budget.pop("max_tokens"))
    return out


def _collect_default_overrides(settings: dict[str, Any]) -> dict[str, Any]:
    """Return merged default overrides from legacy and separated tables.

    Preferred schema:
    - `llm.defaults` owns default LLM fields;
    - `budget.defaults` owns default budget fields.

    Legacy `defaults` remains supported so old workspaces do not break, but
    checked-in config no longer uses it for daily parameters.
    """

    defaults = _normalize_agent_like_block(_as_mapping(settings.get("defaults")))
    llm_defaults = _section_override(_as_mapping(_as_mapping(settings.get("llm")).get("defaults")), "llm")
    budget_defaults = _section_override(
        _as_mapping(_as_mapping(settings.get("budget")).get("defaults")),
        "budget",
    )
    _deep_update(defaults, llm_defaults)
    _deep_update(defaults, budget_defaults)
    return defaults


def _collect_agent_overrides(settings: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Collect per-agent overrides from legacy concise and new separated tables."""

    collected: dict[str, dict[str, Any]] = {}

    # Backward compatibility: old `agents.<agent>` concise table may contain
    # both LLM and budget keys. New default config should not use this path.
    for agent_name, override in _as_mapping(settings.get("agents")).items():
        if not isinstance(override, dict):
            continue
        collected.setdefault(str(agent_name), {})
        _deep_update(collected[str(agent_name)], _normalize_agent_like_block(override))

    for section_name in ("llm", "budget"):
        table = _as_mapping(_as_mapping(settings.get(section_name)).get("agents"))
        for agent_name, override in table.items():
            if not isinstance(override, dict):
                continue
            collected.setdefault(str(agent_name), {})
            _deep_update(collected[str(agent_name)], _section_override(override, section_name))

    return collected


def _section_override(block: dict[str, Any], section_name: str) -> dict[str, Any]:
    """Extract one section from a concise agent-like block, including modes."""

    normalized = _normalize_agent_like_block(block)
    out: dict[str, Any] = {}
    section = _as_mapping(normalized.get(section_name))
    if section:
        out[section_name] = section

    modes_out: dict[str, Any] = {}
    for mode_name, mode_cfg in _as_mapping(normalized.get("modes")).items():
        if not isinstance(mode_cfg, dict):
            continue
        mode_section = _as_mapping(_normalize_agent_like_block(mode_cfg).get(section_name))
        if mode_section:
            modes_out[str(mode_name)] = {section_name: mode_section}
    if modes_out:
        out["modes"] = modes_out
    return out


def _merge_section(agent_cfg: dict[str, Any], section_name: str, defaults: dict[str, Any]) -> None:
    if not defaults:
        return
    current = _as_mapping(agent_cfg.get(section_name))
    merged = deepcopy(current)
    _deep_update(merged, defaults)
    agent_cfg[section_name] = merged


def _deep_update(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(base.get(key), dict) and isinstance(value, dict):
            _deep_update(base[key], value)
        else:
            base[key] = deepcopy(value)
    return base
