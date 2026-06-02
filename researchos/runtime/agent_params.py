"""Agent 参数配置加载器。

从 config/agent_params.yaml 加载 Agent 参数，供运行时使用。
"""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from typing import Any

import yaml

from .agent import AgentSpec


DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "agent_params.yaml"

_config_cache: dict[str, Any] | None = None


def _get_config_path() -> Path:
    """获取配置文件路径，优先使用环境变量。"""

    env_path = os.environ.get("RESEARCHOS_AGENT_PARAMS")
    if env_path:
        return Path(env_path)
    return DEFAULT_CONFIG_PATH


def load_agent_params() -> dict[str, Any]:
    """加载 Agent 参数配置（带缓存）。"""

    global _config_cache
    if _config_cache is not None:
        return _config_cache

    config_path = _get_config_path()
    if not config_path.exists():
        raise FileNotFoundError(f"Agent params config not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        _config_cache = yaml.safe_load(f) or {}

    return _config_cache


def get_agent_params(agent_name: str) -> dict[str, Any]:
    """获取指定 Agent 的参数配置。"""

    config = load_agent_params()
    agents = config.get("agents", {})
    if agent_name not in agents:
        raise KeyError(f"Agent '{agent_name}' not found in agent_params.yaml")
    return _flatten_agent_sections(agents[agent_name])


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _flatten_agent_sections(params: dict[str, Any]) -> dict[str, Any]:
    """Normalize new sectioned agent params into the legacy flat shape.

    `agent_params.yaml` now groups each agent into:
    - `llm`
    - `budget`
    - `tools`
    - `prompt`
    - `behavior`
    - `modes`

    Runtime callers still expect keys such as `max_steps`, `tool_names`, or
    `prompt_template` at the top level. This adapter keeps old flat configs
    working while allowing the checked-in config to be easier to read.
    """

    if not isinstance(params, dict):
        return {}

    flat = deepcopy(params)
    for section_name in ("budget", "tools", "prompt", "behavior"):
        section = params.get(section_name)
        if isinstance(section, dict):
            flat.update(deepcopy(section))

    modes = params.get("modes")
    if isinstance(modes, dict):
        flat["modes"] = {
            mode_name: _flatten_agent_sections(mode_cfg) if isinstance(mode_cfg, dict) else mode_cfg
            for mode_name, mode_cfg in modes.items()
        }
    return flat


def get_agent_mode_params(agent_name: str, mode: str | None) -> dict[str, Any]:
    """获取指定 Agent 和模式的参数配置。"""

    base_params = get_agent_params(agent_name)

    if mode is None or "modes" not in base_params:
        return base_params

    modes = base_params.get("modes", {})
    if mode not in modes:
        return base_params

    merged = _deep_merge(base_params, modes[mode])
    return merged


def _pick_first(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _as_list(value: Any, *, fallback: list[Any]) -> list[Any]:
    if value is None:
        return list(fallback)
    if isinstance(value, list):
        return list(value)
    raise TypeError(f"Expected list value, got: {type(value).__name__}")


def _as_mapping(value: Any, *, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    if value is None:
        return deepcopy(fallback or {})
    if isinstance(value, dict):
        return deepcopy(value)
    raise TypeError(f"Expected mapping value, got: {type(value).__name__}")


def _tag_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list | tuple | set):
        values = list(value)
    else:
        return set()
    return {str(item).strip().lower().replace("-", "_") for item in values if str(item).strip()}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_")
        if normalized in {"1", "true", "yes", "y", "on", "unlimited", "unlimited_budget"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "limited", ""}:
            return False
    return bool(value)


def _is_unlimited_budget(params: dict[str, Any], defaults: dict[str, Any]) -> bool:
    if params.get("unlimited_budget") is not None:
        return _as_bool(params.get("unlimited_budget"))
    tags = _tag_set(params.get("tags")) | _tag_set(params.get("budget_tags"))
    if {"unlimited_budget", "unlimited"} & tags:
        return True
    return _as_bool(defaults.get("unlimited_budget", False))


def build_agent_spec(
    agent_name: str,
    *,
    defaults: dict[str, Any],
    mode: str | None = None,
) -> AgentSpec:
    """根据 YAML 配置与代码默认值构造 AgentSpec。"""

    params = get_agent_mode_params(agent_name, mode)
    llm_cfg = _as_mapping(params.get("llm"))

    model_tier = str(
        _pick_first(
            llm_cfg.get("tier"),
            params.get("model_tier"),
            defaults.get("model_tier"),
            "medium",
        )
    )

    temperature = float(
        _pick_first(
            llm_cfg.get("temperature"),
            params.get("temperature"),
            defaults.get("temperature"),
            0.7,
        )
    )

    llm_max_context = _pick_first(
        llm_cfg.get("max_context"),
        params.get("llm_max_context"),
        defaults.get("llm_max_context"),
    )
    llm_max_context = int(llm_max_context) if llm_max_context is not None else None

    return AgentSpec(
        name=str(_pick_first(params.get("name"), defaults.get("name"), agent_name)),
        model_tier=model_tier,
        tool_names=_as_list(
            params.get("tool_names"),
            fallback=defaults.get("tool_names", []),
        ),
        max_steps=int(_pick_first(params.get("max_steps"), defaults.get("max_steps"), 30)),
        max_tokens_total=int(
            _pick_first(params.get("max_tokens_total"), defaults.get("max_tokens_total"), 200_000)
        ),
        max_wall_seconds=int(
            _pick_first(params.get("max_wall_seconds"), defaults.get("max_wall_seconds"), 1800)
        ),
        unlimited_budget=_is_unlimited_budget(params, defaults),
        temperature=temperature,
        model_override=_pick_first(
            llm_cfg.get("model"),
            params.get("model_override"),
            defaults.get("model_override"),
        ),
        llm_profile=_pick_first(
            llm_cfg.get("profile"),
            params.get("llm_profile"),
            defaults.get("llm_profile"),
        ),
        llm_endpoint=_pick_first(
            llm_cfg.get("endpoint"),
            params.get("llm_endpoint"),
            defaults.get("llm_endpoint"),
        ),
        llm_max_context=llm_max_context,
        allowed_read_prefixes=_as_list(
            params.get("allowed_read_prefixes"),
            fallback=defaults.get("allowed_read_prefixes", [""]),
        ),
        allowed_write_prefixes=_as_list(
            params.get("allowed_write_prefixes"),
            fallback=defaults.get("allowed_write_prefixes", []),
        ),
        max_validation_retries=int(
            _pick_first(
                params.get("max_validation_retries"),
                defaults.get("max_validation_retries"),
                3,
            )
        ),
        pre_hooks=_as_list(params.get("pre_hooks"), fallback=defaults.get("pre_hooks", [])),
        post_hooks=_as_list(params.get("post_hooks"), fallback=defaults.get("post_hooks", [])),
        prompt_template=_pick_first(
            params.get("prompt_template"),
            defaults.get("prompt_template"),
        ),
        output_schemas=_as_mapping(
            params.get("output_schemas"),
            fallback=defaults.get("output_schemas"),
        )
        or None,
        structured_outputs=_as_mapping(
            params.get("structured_outputs"),
            fallback=defaults.get("structured_outputs"),
        )
        or None,
    )


def get_global_budget() -> dict[str, Any]:
    """获取全局预算配置。"""

    config = load_agent_params()
    return config.get("global_budget", {})


def get_global_timeout() -> dict[str, Any]:
    """获取全局超时配置。"""

    config = load_agent_params()
    return config.get("global_timeout", {})


def get_retry_policy() -> dict[str, Any]:
    """获取重试策略配置。"""

    config = load_agent_params()
    return config.get("retry_policy", {})


def get_budget_escalation_policy() -> dict[str, Any]:
    """获取预算触顶时的人类确认扩限策略。"""

    config = load_agent_params()
    return config.get("budget_escalation", {})


def get_docker_config() -> dict[str, Any]:
    """获取 Docker 配置。"""

    config = load_agent_params()
    return config.get("docker", {})


def get_logging_config() -> dict[str, Any]:
    """获取日志配置。"""

    config = load_agent_params()
    return config.get("logging", {})


def clear_cache() -> None:
    """清除配置缓存（用于测试或重新加载）。"""

    global _config_cache
    _config_cache = None
