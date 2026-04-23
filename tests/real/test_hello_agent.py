"""Hello Agent Integration Tests.

测试最简单的 Hello Agent，验证测试框架正确性。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from researchos.agents.hello import HelloAgent
from researchos.testing.mocks import FakeRawCompletion, MockLLMClient
from researchos.testing.fixtures import tmp_workspace, tool_registry, workspace_policy


class TestHelloAgent:
    """Hello Agent 测试套件。"""

    def test_agent_initialization(self):
        """测试 Agent 初始化。"""
        agent = HelloAgent()
        assert agent is not None
        assert agent.spec.name == "hello"

    def test_agent_has_required_tools(self):
        """测试 Agent 有必需的工具。"""
        agent = HelloAgent()
        # hello agent 应该只需要 finish_task
        assert "finish_task" in agent.spec.tool_names

    def test_agent_system_prompt(self, standard_workspace: Path, project_yaml: Path):
        """测试 system prompt 生成。"""
        from researchos.runtime.agent import ExecutionContext

        agent = HelloAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="hello",
            run_id="hello_run",
            task_id="HELLO",
            mode=None,
            extra={},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_initial_user_message(self, standard_workspace: Path, project_yaml: Path):
        """测试初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = HelloAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="hello",
            run_id="hello_run",
            task_id="HELLO",
            mode=None,
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert len(msg) > 0

    def test_agent_validate_outputs(self, standard_workspace: Path, project_yaml: Path):
        """测试输出验证。"""
        from researchos.runtime.agent import ExecutionContext

        agent = HelloAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="hello",
            run_id="hello_run",
            task_id="HELLO",
            mode=None,
            extra={},
        )

        # 没有输出文件时应该失败
        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert err is not None

    def test_agent_validate_outputs_with_file(self, standard_workspace: Path, project_yaml: Path):
        """测试输出验证（有文件时）。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 hello.txt
        hello_file = standard_workspace / "hello.txt"
        hello_file.write_text("Hello, Runtime!", encoding="utf-8")

        agent = HelloAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="hello",
            run_id="hello_run",
            task_id="HELLO",
            mode=None,
            extra={},
            outputs_expected={"hello_file": hello_file},
        )

        # 有文件且内容正确时应该通过
        ok, err = agent.validate_outputs(ctx)
        assert ok is True, f"Expected ok=True, got ok={ok}, err={err}"
