from __future__ import annotations

"""ResearchOS 当前可用 agent 的注册中心。"""

from typing import Any

from .hello import HelloAgent
from .pi import PIAgent
from .scout import ScoutAgent
from .reader import ReaderAgent
from .ideation import IdeationAgent
from .novelty_auditor import NoveltyAuditorAgent
from .novelty import NoveltyAgent
from .experimenter import ExperimenterAgent
from .survey_writer import SurveyWriterAgent
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
    "survey_writer": SurveyWriterAgent,  # T3.6 optional survey branch
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
    "T3.6-GATE-SURVEY": SurveyWriterAgent,  # 综述支线入口
    "T3.6-PLAN": SurveyWriterAgent,  # 综述 taxonomy/outline 规划
    "T3.6-GATE-OUTLINE": SurveyWriterAgent,  # taxonomy 大纲确认
    "T3.6-GATE-CORPUS": SurveyWriterAgent,  # 综述素材范围确认
    "T3.6-EXPAND": SurveyWriterAgent,  # 一次性定向补检计划
    "T3.6-STATE": SurveyWriterAgent,  # 综述逐章状态初始化
    "T3.6-SEC-BACKGROUND": SurveyWriterAgent,
    "T3.6-SEC-TAXONOMY": SurveyWriterAgent,
    "T3.6-SEC-THEME-1": SurveyWriterAgent,
    "T3.6-SEC-THEME-2": SurveyWriterAgent,
    "T3.6-SEC-THEME-3": SurveyWriterAgent,
    "T3.6-SEC-THEME-4": SurveyWriterAgent,
    "T3.6-SEC-COMPARISON": SurveyWriterAgent,
    "T3.6-SEC-CHALLENGES": SurveyWriterAgent,
    "T3.6-SEC-FUTURE": SurveyWriterAgent,
    "T3.6-SEC-INTRO": SurveyWriterAgent,
    "T3.6-SEC-CONCLUSION": SurveyWriterAgent,
    "T3.6-SEC-ABSTRACT": SurveyWriterAgent,
    "T3.6-ASSEMBLE": SurveyWriterAgent,
    "T3.6-REVIEW": SurveyWriterAgent,
    "T3.6-COMPILE": SurveyWriterAgent,
    "T3.6-FEED": SurveyWriterAgent,
    "T4": IdeationAgent,  # 假设生成
    "T4.5": NoveltyAuditorAgent,  # 新颖性审计
    "T5-HANDOFF": ExperimenterAgent,  # 外部实验 handoff
    "T5-DRY-RUN": ExperimenterAgent,  # 外部实验 dry-run
    "T5": ExperimenterAgent,  # Pilot实验
    "T6": NoveltyAgent,  # 新颖性验证
    "T7-INGEST": ExperimenterAgent,  # 外部结果摄取
    "T7-AUDIT": ExperimenterAgent,  # 实验诚信审计
    "T7-CLAIMS": ExperimenterAgent,  # result-to-claim
    "T7": ExperimenterAgent,  # 完整实验
    "T8-STYLE-GATE": WriterAgent,  # 写作风格确认
    "T8-RESOURCE": WriterAgent,  # 写作资源索引
    "T8-WRITE": WriterAgent,  # 论文大纲
    "T8-SECTION-PLAN": WriterAgent,  # 逐章节写作状态
    "T8-SEC-METHOD": WriterAgent,  # Method 单章
    "T8-SEC-EXPERIMENTS": WriterAgent,  # Experiments 单章
    "T8-SEC-RELATED": WriterAgent,  # Related Work 单章
    "T8-SEC-ANALYSIS": WriterAgent,  # Analysis 单章
    "T8-SEC-INTRO": WriterAgent,  # Introduction 单章
    "T8-SEC-CONCLUSION": WriterAgent,  # Conclusion 单章
    "T8-SEC-ABSTRACT": WriterAgent,  # Abstract 单章
    "T8-SECTIONS": WriterAgent,  # 分章节草稿
    "T8-DRAFT": WriterAgent,  # 论文初稿
    "T8-SELF-CHECK": WriterAgent,  # 作者自查
    "T8-REVIEW": ReviewerAgent,  # 论文审稿
    "T8-REVIEW-1": ReviewerAgent,  # 第1轮审稿
    "T8-REVIEW-2": ReviewerAgent,  # 第2轮审稿
    "T8-REVISE-1": WriterAgent,  # 第1轮修订
    "T8-REVISE-2": WriterAgent,  # 第2轮修订
    "T9": SubmissionAgent,  # 投稿准备
}


def _instantiate_agent(agent_cls: type[Any], *, mode: str | None = None):
    if mode is not None:
        try:
            return agent_cls(mode=mode)
        except TypeError:
            pass
    return agent_cls()


def get_agent_by_id(agent_id: str, *, mode: str | None = None):
    """按 registry id 构造一个 agent 实例。"""
    agent_cls = AGENT_REGISTRY.get(agent_id)
    if agent_cls is None:
        raise KeyError(f"Unknown agent id: {agent_id}")
    return _instantiate_agent(agent_cls, mode=mode)
