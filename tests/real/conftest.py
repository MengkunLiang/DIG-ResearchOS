"""Fixtures for real integration tests.

这个文件提供真实集成测试所需的 fixtures，包括：
- workspace fixtures（带标准目录结构）
- mock LLM fixtures（支持多轮对话）
- agent fixtures（简化 agent 创建）
- pipeline fixtures（用于测试 agent 协作）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from researchos.testing.mocks import FakeRawCompletion, FakeToolCall, MockLLMClient
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.human_gate import CLIHumanInterface
from researchos.tools.registry import ToolRegistry
from researchos.tools.workspace_policy import WorkspaceAccessPolicy

# 直接从具体模块导入 agents
from researchos.agents.hello import HelloAgent
from researchos.agents.pi import PIAgent
from researchos.agents.scout import ScoutAgent
from researchos.agents.reader import ReaderAgent
from researchos.agents.ideation import IdeationAgent
from researchos.agents.novelty_auditor import NoveltyAuditorAgent
from researchos.agents.novelty import NoveltyAgent
from researchos.agents.writer import WriterAgent
from researchos.agents.reviewer import ReviewerAgent
from researchos.agents.submission import SubmissionAgent
from researchos.agents.experimenter import ExperimenterAgent


# ══════════════════════════════════════════════════════════
# Workspace Fixtures
# ══════════════════════════════════════════════════════════


@pytest.fixture
def standard_workspace(tmp_path: Path) -> Path:
    """创建标准 workspace 结构。

    目录结构：
    - _runtime/traces/
    - _runtime/logs/
    - ideation/
    - literature/
    - drafts/
    - experiments/
    - pilot/
    - novelty/
    - submission/
    """
    workspace = tmp_path / "workspace"
    (workspace / "_runtime" / "traces").mkdir(parents=True)
    (workspace / "_runtime" / "logs").mkdir(parents=True)
    (workspace / "ideation").mkdir()
    (workspace / "literature").mkdir()
    (workspace / "literature" / "paper_notes").mkdir()
    (workspace / "drafts").mkdir()
    (workspace / "drafts" / "review_rounds").mkdir()
    (workspace / "experiments" / "runs").mkdir(parents=True)
    (workspace / "pilot").mkdir()
    (workspace / "pilot" / "pilot_code").mkdir()
    (workspace / "novelty").mkdir()
    (workspace / "submission" / "bundle").mkdir(parents=True)
    return workspace


@pytest.fixture
def project_yaml(standard_workspace: Path) -> Path:
    """创建项目配置文件。"""
    project_file = standard_workspace / "project.yaml"
    project_file.write_text(
        """\
name: test-project
direction: 研究高效大语言模型推理
research_direction: 研究高效大语言模型推理
domain: 机器学习
keywords:
  - LLM
  - efficiency
  - inference
target_venue: neurips2026
constraints:
  max_budget_usd: 100.0
  gpu_enabled: true
seed_ensemble:
  tier1_seeds: [42, 123, 456]
  tier2_seeds: [789]
  tier3_seeds: [999]
""",
        encoding="utf-8",
    )
    return project_file


# ══════════════════════════════════════════════════════════
# Tool Fixtures
# ══════════════════════════════════════════════════════════


@pytest.fixture
def tool_registry() -> ToolRegistry:
    """创建工具注册表。"""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


@pytest.fixture
def workspace_policy(standard_workspace: Path) -> WorkspaceAccessPolicy:
    """创建工作空间访问策略。"""
    return WorkspaceAccessPolicy(
        workspace_dir=standard_workspace,
        allowed_read_prefixes=[""],
        allowed_write_prefixes=[""],
    )


@pytest.fixture
def human_interface() -> CLIHumanInterface:
    """创建 CLI 人机交互接口。"""
    return CLIHumanInterface()


# ══════════════════════════════════════════════════════════
# Agent Fixtures
# ══════════════════════════════════════════════════════════


@pytest.fixture
def hello_agent() -> HelloAgent:
    """创建 Hello Agent。"""
    return HelloAgent()


@pytest.fixture
def pi_agent() -> PIAgent:
    """创建 PI Agent。"""
    return PIAgent()


@pytest.fixture
def scout_agent() -> ScoutAgent:
    """创建 Scout Agent。"""
    return ScoutAgent()


@pytest.fixture
def reader_agent() -> ReaderAgent:
    """创建 Reader Agent。"""
    return ReaderAgent()


@pytest.fixture
def ideation_agent() -> IdeationAgent:
    """创建 Ideation Agent。"""
    return IdeationAgent()


@pytest.fixture
def novelty_auditor_agent() -> NoveltyAuditorAgent:
    """创建 Novelty Auditor Agent。"""
    return NoveltyAuditorAgent()


@pytest.fixture
def novelty_agent() -> NoveltyAgent:
    """创建 Novelty Agent。"""
    return NoveltyAgent()


@pytest.fixture
def writer_agent() -> WriterAgent:
    """创建 Writer Agent。"""
    return WriterAgent()


@pytest.fixture
def reviewer_agent() -> ReviewerAgent:
    """创建 Reviewer Agent。"""
    return ReviewerAgent()


@pytest.fixture
def submission_agent() -> SubmissionAgent:
    """创建 Submission Agent。"""
    return SubmissionAgent()


# ══════════════════════════════════════════════════════════
# Mock LLM Fixtures
# ══════════════════════════════════════════════════════════


class RecordingMockLLM(MockLLMClient):
    """记录工具调用的 Mock LLM。"""

    def __init__(self, responses: list[FakeRawCompletion]):
        super().__init__(responses)
        self.tool_calls: list[dict[str, Any]] = []

    def record_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> None:
        """记录一次工具调用。"""
        self.tool_calls.append({"tool": tool_name, "arguments": arguments})


@pytest.fixture
def mock_llm() -> MockLLMClient:
    """创建默认 Mock LLM（无响应）。"""
    return MockLLMClient([])


def create_mock_response(
    content: str | None = None,
    tool_calls: list[FakeToolCall] | None = None,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
    cost_usd: float = 0.01,
) -> FakeRawCompletion:
    """创建 Mock 响应。"""
    return FakeRawCompletion(
        message=json.dumps({"content": content, "tool_calls": tool_calls}) if content or tool_calls else None,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
    )


# ══════════════════════════════════════════════════════════
# Helper Functions
# ══════════════════════════════════════════════════════════


def create_finish_response() -> FakeRawCompletion:
    """创建 finish_task 响应。"""
    return FakeRawCompletion(
        message=json.dumps({
            "content": "Task completed successfully.",
            "tool_calls": [
                {"name": "finish_task", "arguments": {"message": "Done"}}
            ]
        }),
        prompt_tokens=50,
        completion_tokens=20,
        cost_usd=0.005,
    )


def create_write_file_response(file_path: str, content: str) -> FakeRawCompletion:
    """创建 write_file 响应。"""
    return FakeRawCompletion(
        message=json.dumps({
            "content": f"Writing file: {file_path}",
            "tool_calls": [
                {"name": "write_file", "arguments": {"file_path": file_path, "content": content}}
            ]
        }),
        prompt_tokens=50,
        completion_tokens=20,
        cost_usd=0.005,
    )


def create_tool_call_response(tool_calls: list[dict[str, Any]]) -> FakeRawCompletion:
    """创建工具调用响应。"""
    return FakeRawCompletion(
        message=json.dumps({
            "content": "Calling tools...",
            "tool_calls": tool_calls
        }),
        prompt_tokens=50,
        completion_tokens=30,
        cost_usd=0.005,
    )


def create_text_response(text: str) -> FakeRawCompletion:
    """创建纯文本响应。"""
    return FakeRawCompletion(
        message=json.dumps({"content": text}),
        prompt_tokens=50,
        completion_tokens=30,
        cost_usd=0.005,
    )


# ══════════════════════════════════════════════════════════
# Test Data Fixtures
# ══════════════════════════════════════════════════════════


@pytest.fixture
def sample_project_yaml() -> str:
    """样例项目配置。"""
    return """\
name: llm-efficiency-research
description: Research on LLM inference efficiency
research_direction: 高效大语言模型推理方法
domain: 机器学习
target_venue: neurips2026
research_area: efficient-llm-inference
keywords:
  - LLM
  - inference
  - efficiency
  - optimization
constraints:
  max_budget_usd: 500.0
  gpu_enabled: true
  max_gpu_hours: 100
seed_ensemble:
  tier1_seeds: [42, 123, 456]
  tier2_seeds: [789]
  tier3_seeds: [999]
"""


@pytest.fixture
def sample_synthesis_md() -> str:
    """样例文献综述。"""
    return """\
# 文献综述

## 方法家族

### 1. 量化方法
- INT8 量化
- FP16 量化
- 动态量化

### 2. 剪枝方法
- 结构化剪枝
- 非结构化剪枝
- 知识蒸馏

### 3. 注意力优化
- Flash Attention
- Linear Attention
- Sparse Attention

## 共同假设
- 精度损失可控
- 推理加速显著

## 技术趋势
- 混合精度量化
- 动态稀疏
"""
