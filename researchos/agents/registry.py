from __future__ import annotations

"""ResearchOS 当前可用 agent 的注册中心。"""

from .hello import HelloAgent
from .pi import PIAgent
from .scout import ScoutAgent


# 说明：
# - 现在仓库里有 HelloAgent、PIAgent 和 ScoutAgent。
# - 后续 T3-T9 agent 落地后，统一在这里扩展映射，CLI 与启动检查都从这里读取。
AGENT_REGISTRY = {
    "hello": HelloAgent,
    "pi": PIAgent,
    "scout": ScoutAgent,
}

# task -> agent 的映射目前只覆盖当前仓库真实存在的调试 task。
TASK_TO_AGENT_MAP = {
    "HELLO": HelloAgent,
    "T1": PIAgent,
    "T7.5": PIAgent,
    "T2": ScoutAgent,
}


def get_agent_by_id(agent_id: str):
    """按 registry id 构造一个 agent 实例。"""
    agent_cls = AGENT_REGISTRY.get(agent_id)
    if agent_cls is None:
        raise KeyError(f"Unknown agent id: {agent_id}")
    return agent_cls()
