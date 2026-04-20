from __future__ import annotations

"""ResearchOS 当前可用 agent 的注册中心。"""

from .hello import HelloAgent
from .pi import PIAgent
from .scout import ScoutAgent
from .reader import ReaderAgent
from .ideation import IdeationAgent
from .novelty_auditor import NoveltyAuditorAgent
from .novelty import NoveltyAgent
from .experimenter import ExperimenterAgent
from .writer import WriterAgent
from .reviewer import ReviewerAgent
from .submission import SubmissionAgent


# 说明：
# - 现在仓库里有 HelloAgent、PIAgent、ScoutAgent。
# - T3 ReaderAgent 和 T4 IdeationAgent 正在开发中。
# - 后续 T5-T9 agent 落地后，统一在这里扩展映射，CLI 与启动检查都从这里读取。
AGENT_REGISTRY = {
    "hello": HelloAgent,
    "pi": PIAgent,
    "scout": ScoutAgent,
    "reader": ReaderAgent,  # T3/T3.5
    "ideation": IdeationAgent,  # T4
    "novelty_auditor": NoveltyAuditorAgent,  # T4.5
    "novelty": NoveltyAgent,  # T6
    "experimenter": ExperimenterAgent,  # T5/T7
    "writer": WriterAgent,  # T8
    "reviewer": ReviewerAgent,  # T8
    "submission": SubmissionAgent,  # T9
}

# task -> agent 的映射目前只覆盖当前仓库真实存在的调试 task。
TASK_TO_AGENT_MAP = {
    "HELLO": HelloAgent,
    "T1": PIAgent,
    "T7.5": PIAgent,
    "T2": ScoutAgent,
    "T3": ReaderAgent,  # 深度阅读
    "T3.5": ReaderAgent,  # 文献综合
    "T4": IdeationAgent,  # 假设生成
    "T4.5": NoveltyAuditorAgent,  # 新颖性审计
    "T5": ExperimenterAgent,  # Pilot实验
    "T6": NoveltyAgent,  # 新颖性验证
    "T7": ExperimenterAgent,  # 完整实验
    "T8-WRITE": WriterAgent,  # 论文写作
    "T8-REVIEW": ReviewerAgent,  # 论文审稿
    "T9": SubmissionAgent,  # 投稿准备
}


def get_agent_by_id(agent_id: str):
    """按 registry id 构造一个 agent 实例。"""
    agent_cls = AGENT_REGISTRY.get(agent_id)
    if agent_cls is None:
        raise KeyError(f"Unknown agent id: {agent_id}")
    return agent_cls()
