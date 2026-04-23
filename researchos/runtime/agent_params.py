"""Agent 参数配置加载器。

从 config/agent_params.yaml 加载 Agent 参数，供运行时使用。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


# 默认配置路径
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "agent_params.yaml"

# 缓存已加载的配置
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
        _config_cache = yaml.safe_load(f)

    return _config_cache


def get_agent_params(agent_name: str) -> dict[str, Any]:
    """获取指定 Agent 的参数配置。

    Args:
        agent_name: Agent 名称（如 "hello", "scout", "reader" 等）

    Returns:
        Agent 参数配置字典，包含：
        - model_tier: 模型层级
        - max_steps: 最大步数
        - max_tokens_total: 最大 token 数
        - max_wall_seconds: 最大运行时间（秒）
        - max_validation_retries: 最大验证重试次数
        - 以及其他 agent 特定参数

    Raises:
        KeyError: 如果 agent 不存在
    """
    config = load_agent_params()
    agents = config.get("agents", {})
    if agent_name not in agents:
        raise KeyError(f"Agent '{agent_name}' not found in agent_params.yaml")
    return agents[agent_name]


def get_agent_mode_params(agent_name: str, mode: str | None) -> dict[str, Any]:
    """获取指定 Agent 和模式的参数配置。

    如果 mode 存在且有对应配置，优先使用 mode 特定的参数。

    Args:
        agent_name: Agent 名称
        mode: 模式（如 "read", "synthesize", "pilot", "full" 等）

    Returns:
        Agent 参数配置字典（已合并 mode 特定参数）
    """
    base_params = get_agent_params(agent_name)

    if mode is None or "modes" not in base_params:
        return base_params

    modes = base_params.get("modes", {})
    if mode not in modes:
        return base_params

    # 合并 base params 和 mode specific params
    merged = base_params.copy()
    merged.update(modes[mode])
    return merged


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
