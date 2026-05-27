"""PI Agent Integration Tests.

测试项目初始化 Agent。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from researchos.agents.pi import PIAgent
from researchos.testing.mocks import FakeRawCompletion, MockLLMClient


class TestPIAgent:
    """PI Agent 测试套件。"""

    def test_agent_initialization(self):
        """测试 Agent 初始化。"""
        agent = PIAgent()
        assert agent is not None
        assert agent.spec.name == "pi"

    def test_agent_has_required_tools(self):
        """测试 Agent 有必需的工具。"""
        agent = PIAgent()
        # pi agent 应该有以下工具
        assert "read_file" in agent.spec.tool_names
        assert "write_file" in agent.spec.tool_names
        assert "finish_task" in agent.spec.tool_names

    def test_agent_has_no_docker_exec(self):
        """测试 pi agent 没有 docker_exec 工具。"""
        agent = PIAgent()
        # pi agent 不需要 docker_exec（因为不执行实验）
        assert "docker_exec" not in agent.spec.tool_names

    def test_agent_system_prompt(self, standard_workspace: Path, project_yaml: Path):
        """测试 system prompt 生成。"""
        from researchos.runtime.agent import ExecutionContext

        agent = PIAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="pi",
            run_id="pi_run",
            task_id="T1",
            mode="init",
            extra={},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_initial_user_message_init_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 init 模式的初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = PIAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="pi",
            run_id="pi_run",
            task_id="T1",
            mode="init",
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "t1" in msg.lower() or "初始化" in msg

    def test_agent_initial_user_message_evaluate_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 evaluate 模式的初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = PIAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="pi",
            run_id="pi_run",
            task_id="T1",
            mode="evaluate",
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "t7.5" in msg.lower() or "评估" in msg

    def test_agent_validate_outputs_no_files(self, standard_workspace: Path, project_yaml: Path):
        """测试输出验证（无文件时）。"""
        from researchos.runtime.agent import ExecutionContext

        agent = PIAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="pi",
            run_id="pi_run",
            task_id="T1",
            mode="init",
            extra={},
        )

        # 没有输出文件时应该失败
        ok, err = agent.validate_outputs(ctx)
        assert ok is False

    def test_agent_validate_outputs_with_files(self, standard_workspace: Path, project_yaml: Path):
        """测试输出验证（有文件时）。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建必需的文件（包含schema要求的必填字段）
        project_file = standard_workspace / "project.yaml"
        project_file.write_text(
            """\
project_id: test-project
research_direction: Test research direction
created_at: "2026-01-01T00:00:00Z"
""",
            encoding="utf-8",
        )

        state_file = standard_workspace / "state.yaml"
        state_file.write_text(
            """\
project_name: test-project
status: initialized
""",
            encoding="utf-8",
        )

        agent = PIAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="pi",
            run_id="pi_run",
            task_id="T1",
            mode="init",
            extra={},
        )

        # 有文件时应该通过
        ok, err = agent.validate_outputs(ctx)
        assert ok is True


class TestPIAgentEvaluateMode:
    """PI Agent Evaluate 模式测试。"""

    def test_evaluate_mode_with_incomplete_project(self, standard_workspace: Path, project_yaml: Path):
        """测试不完整项目的评估。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建不完整的 project.yaml
        project_file = standard_workspace / "project.yaml"
        project_file.write_text(
            """\
name: test-project
""",
            encoding="utf-8",
        )

        agent = PIAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="pi",
            run_id="pi_run",
            task_id="T1",
            mode="evaluate",
            extra={},
        )

        # 应该检测到缺失字段
        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "缺少" in err or "missing" in err.lower() or "required" in err.lower()

    def test_evaluate_mode_with_complete_project(self, standard_workspace: Path, project_yaml: Path):
        """测试完整项目的评估。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 evaluation/evaluation_decision.md（evaluate模式需要的输出）
        eval_dir = standard_workspace / "evaluation"
        eval_dir.mkdir(exist_ok=True)
        decision_file = eval_dir / "evaluation_decision.md"
        decision_file.write_text(
            """\
# Evaluation Decision

## Situation

**Situation A**: The experiments show significant improvements over the baseline.

## Options

### Option 1: Continue current direction
Proceed to next iteration with the current approach.

### Option 2: Refine hyperparameters
Focus on hyperparameter optimization.

## Recommendation

Proceed with Option 1.

next_task: T7
""",
            encoding="utf-8",
        )

        agent = PIAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="pi",
            run_id="pi_run",
            task_id="T1",
            mode="evaluate",
            extra={},
        )

        # 应该通过
        ok, err = agent.validate_outputs(ctx)
        assert ok is True
