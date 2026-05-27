"""边界和失败路径测试。

测试以下场景：
1. BudgetExceeded - Agent 预算耗尽
2. MaxStepsReached - Agent 达到最大步数
3. OutputValidationFailed - 输出校验失败
4. ToolError 子类 - 工具执行失败
5. LLMProviderError - LLM 提供商失败
6. WorkspaceError - workspace 问题
7. 状态异常和无效输入
8. 工具访问控制
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.runtime.agent import Agent, AgentSpec, AgentResult, ExecutionContext
from researchos.runtime.errors import (
    AgentError,
    BudgetExceeded,
    ConfigurationError,
    EmptyReplyStorm,
    HumanRejected,
    LLMProviderError,
    MaxStepsReached,
    OutputValidationFailed,
    ResearchOSError,
    RuntimeError_,
    ToolAccessDenied,
    ToolError,
    ToolParameterError,
    ToolRuntimeError,
    ToolTimeout,
    WorkspaceError,
)
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


# ══════════════════════════════════════════════════════
# 1. Error 类层级测试
# ══════════════════════════════════════════════════════

class TestErrorHierarchy:
    """测试错误类层级结构。"""

    def test_research_os_error_is_base(self):
        """ResearchOSError 是所有错误的根。"""
        assert issubclass(RuntimeError_, ResearchOSError)
        assert issubclass(AgentError, ResearchOSError)
        assert issubclass(ToolError, ResearchOSError)

    def test_budget_exceeded_contains_details(self):
        """BudgetExceeded 包含预算超限的详细信息。"""
        err = BudgetExceeded("tokens", 100000, 150000)
        assert err.dimension == "tokens"
        assert err.limit == 100000
        assert err.used == 150000
        assert "tokens" in str(err)
        assert "150000" in str(err)

    def test_output_validation_failed_contains_reasons(self):
        """OutputValidationFailed 包含多个失败原因。"""
        reasons = ["missing file A", "schema mismatch", "invalid format"]
        err = OutputValidationFailed("ideation", reasons)
        assert err.agent_name == "ideation"
        assert len(err.reasons) == 3
        assert "3 times" in str(err)

    def test_tool_timeout_contains_metadata(self):
        """ToolTimeout 包含工具名和超时时间。"""
        err = ToolTimeout("bash_run", 30.0)
        assert err.tool_name == "bash_run"
        assert err.timeout_s == 30.0
        assert "30.0s" in str(err)

    def test_tool_runtime_error_wraps_underlying(self):
        """ToolRuntimeError 包装底层异常。"""
        original = ValueError("docker not available")
        err = ToolRuntimeError("docker_exec", original)
        assert err.tool_name == "docker_exec"
        assert err.underlying is original
        assert "docker_exec" in str(err)
        assert "ValueError" in str(err)

    def test_empty_reply_storm(self):
        """EmptyReplyStorm 表示 LLM 产生过多空回复。"""
        err = EmptyReplyStorm()
        # 类名包含空回复相关的词
        assert "EmptyReplyStorm" in type(err).__name__ or "empty" in type(err).__name__.lower()

    def test_human_rejected(self):
        """HumanRejected 表示人工拒绝。"""
        err = HumanRejected()
        # 类名包含 Human
        assert "HumanRejected" in type(err).__name__


# ══════════════════════════════════════════════════════
# 2. WorkspaceAccessPolicy 边界测试
# ══════════════════════════════════════════════════════

class TestWorkspaceAccessPolicy:
    """测试 workspace 访问策略的边界情况。"""

    def test_resolve_read_absolute_path_denied(self, tmp_path):
        """绝对路径被拒绝。"""
        policy = WorkspaceAccessPolicy(
            workspace_dir=tmp_path,
            allowed_read_prefixes=[""],
            allowed_write_prefixes=[""],
        )
        with pytest.raises(ToolAccessDenied) as exc_info:
            policy.resolve_read("/etc/passwd")
        assert "Absolute paths not allowed" in str(exc_info.value)

    def test_resolve_read_escapes_workspace(self, tmp_path):
        """路径逃逸 workspace 被拒绝。"""
        policy = WorkspaceAccessPolicy(
            workspace_dir=tmp_path,
            allowed_read_prefixes=[""],
            allowed_write_prefixes=[""],
        )
        with pytest.raises(ToolAccessDenied) as exc_info:
            policy.resolve_read("../outside")
        assert "escapes workspace" in str(exc_info.value)

    def test_resolve_read_not_in_allowed_prefix(self, tmp_path):
        """不在允许前缀内的读取被拒绝。"""
        policy = WorkspaceAccessPolicy(
            workspace_dir=tmp_path,
            allowed_read_prefixes=["docs/", "data/"],
            allowed_write_prefixes=[""],
        )
        with pytest.raises(ToolAccessDenied) as exc_info:
            policy.resolve_read("secret.txt")
        assert "Read access denied" in str(exc_info.value)

    def test_resolve_read_subdirectory_allowed(self, tmp_path):
        """子目录在允许前缀内时允许读取。"""
        policy = WorkspaceAccessPolicy(
            workspace_dir=tmp_path,
            allowed_read_prefixes=["data/"],
            allowed_write_prefixes=[""],
        )
        # 创建目录和文件
        (tmp_path / "data" / "subdir").mkdir(parents=True)
        file_path = tmp_path / "data" / "subdir" / "file.txt"
        file_path.write_text("content")

        result = policy.resolve_read("data/subdir/file.txt")
        assert result == file_path

    def test_resolve_write_not_in_allowed_prefix(self, tmp_path):
        """不在允许前缀内的写入被拒绝。"""
        policy = WorkspaceAccessPolicy(
            workspace_dir=tmp_path,
            allowed_read_prefixes=[""],
            allowed_write_prefixes=["output/", "results/"],
        )
        with pytest.raises(ToolAccessDenied) as exc_info:
            policy.resolve_write("forbidden/file.txt")
        assert "Write access denied" in str(exc_info.value)

    def test_resolve_write_creates_parent_dirs(self, tmp_path):
        """写入时自动创建父目录。"""
        policy = WorkspaceAccessPolicy(
            workspace_dir=tmp_path,
            allowed_read_prefixes=[""],
            allowed_write_prefixes=["output/"],
        )
        result = policy.resolve_write("output/subdir/file.txt")
        assert result.parent.exists()
        assert result.name == "file.txt"

    def test_workspace_does_not_exist(self, tmp_path):
        """workspace 不存在时抛出错误。"""
        nonexistent = tmp_path / "nonexistent"
        with pytest.raises(WorkspaceError) as exc_info:
            WorkspaceAccessPolicy(
                workspace_dir=nonexistent,
                allowed_read_prefixes=[],
                allowed_write_prefixes=[],
            )
        assert "not found" in str(exc_info.value)


# ══════════════════════════════════════════════════════
# 3. Agent validate_outputs 边界测试
# ══════════════════════════════════════════════════════

class DummyAgent(Agent):
    """用于测试的简单 Agent 实现。"""

    def __init__(self):
        spec = AgentSpec(
            name="test",
            model_tier="medium",
            tool_names=["read_file"],
        )
        super().__init__(spec)

    def system_prompt(self, ctx: ExecutionContext) -> str:
        return "Test prompt"

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        return "Test message"


class StructuredDummyAgent(Agent):
    """用于测试 structured_outputs schema 校验的 Agent。"""

    def __init__(self):
        spec = AgentSpec(
            name="structured-test",
            model_tier="medium",
            tool_names=["read_file"],
            structured_outputs={"out.json": "idea_rationales"},
        )
        super().__init__(spec)

    def system_prompt(self, ctx: ExecutionContext) -> str:
        return "Test prompt"

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        return "Test message"


class TestAgentValidateOutputs:
    """测试 Agent.validate_outputs 的边界情况。"""

    def test_validate_missing_expected_output(self, tmp_path):
        """缺少预期输出文件时校验失败。"""
        agent = DummyAgent()
        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T1",
            run_id="r1",
            outputs_expected={
                "hypotheses": tmp_path / "hypotheses.md",
            },
        )

        ok, err = agent.validate_outputs(ctx)
        assert not ok
        assert "hypotheses.md" in err

    def test_validate_missing_multiple_outputs(self, tmp_path):
        """缺少多个预期输出文件时列出所有缺失。"""
        agent = DummyAgent()
        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T4",
            run_id="r1",
            outputs_expected={
                "hypotheses": tmp_path / "hypotheses.md",
                "exp_plan": tmp_path / "exp_plan.yaml",
                "novelty_audit": tmp_path / "novelty_audit.md",
            },
        )

        ok, err = agent.validate_outputs(ctx)
        assert not ok
        assert "hypotheses.md" in err
        assert "exp_plan.yaml" in err
        assert "novelty_audit.md" in err

    def test_validate_all_outputs_present(self, tmp_path):
        """所有预期输出都存在时校验成功。"""
        agent = DummyAgent()

        # 创建输出文件
        (tmp_path / "hypotheses.md").write_text("# Hypotheses")
        (tmp_path / "exp_plan.yaml").write_text("experiments: []")

        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T4",
            run_id="r1",
            outputs_expected={
                "hypotheses": tmp_path / "hypotheses.md",
                "exp_plan": tmp_path / "exp_plan.yaml",
            },
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok
        assert err is None

    def test_validate_no_expected_outputs(self, tmp_path):
        """没有声明预期输出时默认校验成功。"""
        agent = DummyAgent()
        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T1",
            run_id="r1",
            outputs_expected={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok

    def test_validate_structured_outputs_rejects_invalid_schema(self, tmp_path):
        """structured_outputs 声明的文件应按 schema 校验。"""
        agent = StructuredDummyAgent()
        (tmp_path / "out.json").write_text('{"not": "a valid idea rationales file"}', encoding="utf-8")
        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T4",
            run_id="r1",
            outputs_expected={"out": tmp_path / "out.json"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert not ok
        assert "Structured output" in err

    def test_validate_structured_outputs_skips_when_outputs_not_declared(self, tmp_path):
        """裸 agent.validate_outputs 调用不应被 agent-level structured_outputs 抢先卡住。"""
        agent = StructuredDummyAgent()
        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T4",
            run_id="r1",
            outputs_expected={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok, err


# ══════════════════════════════════════════════════════
# 4. ExecutionContext 边界测试
# ══════════════════════════════════════════════════════

class TestExecutionContext:
    """测试 ExecutionContext 的边界情况。"""

    def test_input_path_missing_key(self, tmp_path):
        """请求未声明的输入时抛出 KeyError。"""
        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T2",
            run_id="r1",
            inputs={},
        )

        with pytest.raises(KeyError) as exc_info:
            ctx.input_path("missing")
        assert "Input missing not declared" in str(exc_info.value)

    def test_output_path_missing_key(self, tmp_path):
        """请求未声明的输出时抛出 KeyError。"""
        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T3",
            run_id="r1",
            outputs_expected={},
        )

        with pytest.raises(KeyError) as exc_info:
            ctx.output_path("missing")
        assert "Output missing not declared" in str(exc_info.value)

    def test_input_path_valid(self, tmp_path):
        """请求已声明的输入返回正确路径。"""
        input_path = tmp_path / "papers" / "paper.pdf"
        input_path.parent.mkdir(parents=True)
        input_path.write_text("content")

        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T3",
            run_id="r1",
            inputs={"paper": input_path},
        )

        result = ctx.input_path("paper")
        assert result == input_path

    def test_output_path_valid(self, tmp_path):
        """请求已声明的输出返回正确路径。"""
        output_path = tmp_path / "output" / "results.json"

        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T7",
            run_id="r1",
            outputs_expected={"results": output_path},
        )

        result = ctx.output_path("results")
        assert result == output_path

    def test_extra_metadata(self, tmp_path):
        """extra 字段可以存储任意元数据。"""
        ctx = ExecutionContext(
            workspace_dir=tmp_path,
            project_id="test",
            task_id="T5",
            run_id="r1",
            mode="pilot",
            extra={
                "seed": 42,
                "data_fraction": 0.1,
                "custom_config": {"key": "value"},
            },
        )

        assert ctx.extra["seed"] == 42
        assert ctx.extra["data_fraction"] == 0.1
        assert ctx.extra["custom_config"]["key"] == "value"


# ══════════════════════════════════════════════════════
# 5. AgentResult 边界测试
# ══════════════════════════════════════════════════════

class TestAgentResult:
    """测试 AgentResult 的边界情况。"""

    def test_result_success(self):
        """成功的 Agent 结果。"""
        result = AgentResult(
            ok=True,
            message="All outputs produced",
            outputs_produced={"hypotheses": Path("hypotheses.md")},
            steps_used=15,
            tokens_in=5000,
            tokens_out=3000,
            cost_usd=0.05,
            duration_seconds=120.5,
            stop_reason=AgentResult.STOP_FINISHED,
        )

        assert result.ok
        assert result.stop_reason == "finished"
        assert result.error is None

    def test_result_failure_max_steps(self):
        """达到最大步数导致失败。"""
        result = AgentResult(
            ok=False,
            message="Max steps reached",
            outputs_produced={},
            steps_used=100,
            tokens_in=80000,
            tokens_out=60000,
            cost_usd=0.50,
            duration_seconds=300.0,
            stop_reason=AgentResult.STOP_MAX_STEPS,
            error="MaxStepsReached: 100 steps used",
        )

        assert not result.ok
        assert result.stop_reason == "max_steps"
        assert result.error is not None

    def test_result_failure_validation(self):
        """输出校验失败。"""
        result = AgentResult(
            ok=False,
            message="Output validation failed",
            outputs_produced={},
            steps_used=50,
            tokens_in=30000,
            tokens_out=20000,
            cost_usd=0.20,
            duration_seconds=180.0,
            stop_reason=AgentResult.STOP_ERROR,
            error="OutputValidationFailed: missing pilot_results.json",
        )

        assert not result.ok
        assert "validation" in result.error.lower()

    def test_result_with_trace_file(self):
        """结果包含 trace 文件路径。"""
        trace_path = Path("/tmp/trace.jsonl")
        result = AgentResult(
            ok=True,
            message="Completed",
            outputs_produced={},
            steps_used=10,
            tokens_in=2000,
            tokens_out=1500,
            cost_usd=0.01,
            duration_seconds=30.0,
            stop_reason=AgentResult.STOP_FINISHED,
            trace_file=trace_path,
        )

        assert result.trace_file == trace_path

    def test_result_with_llm_metadata(self):
        """结果包含 LLM 元数据。"""
        result = AgentResult(
            ok=True,
            message="Completed",
            outputs_produced={},
            steps_used=10,
            tokens_in=2000,
            tokens_out=1500,
            cost_usd=0.01,
            duration_seconds=30.0,
            stop_reason=AgentResult.STOP_FINISHED,
            llm_profile="medium",
            llm_tier="medium",
            llm_model_used="gpt-4o-mini",
            llm_endpoint_used="https://api.openai.com/v1",
        )

        assert result.llm_profile == "medium"
        assert result.llm_model_used == "gpt-4o-mini"


# ══════════════════════════════════════════════════════
# 6. resolve_effective_config 测试
# ══════════════════════════════════════════════════════

from researchos.runtime.agent import (
    resolve_effective_config,
    LLMConfigOverride,
    BudgetOverride,
    ToolPolicyOverride,
)


class TestResolveEffectiveConfig:
    """测试配置合并逻辑。"""

    def test_default_config_no_override(self):
        """无 override 时使用默认值。"""
        spec = AgentSpec(
            name="test",
            model_tier="medium",
            tool_names=["read_file", "write_file"],
            max_steps=30,
            max_tokens_total=200000,
            max_wall_seconds=1800,
            temperature=0.7,
            allowed_read_prefixes=["data/"],
            allowed_write_prefixes=["output/"],
        )
        ctx = ExecutionContext(
            workspace_dir=Path("/tmp"),
            project_id="test",
            task_id="T1",
            run_id="r1",
        )

        config = resolve_effective_config(spec, ctx)

        assert config.max_steps == 30
        assert config.llm_tier == "medium"
        assert config.llm_temperature == 0.7
        assert "read_file" in config.tool_names

    def test_llm_override_applied(self):
        """LLM override 生效。"""
        spec = AgentSpec(
            name="test",
            model_tier="medium",
            tool_names=["read_file"],
            temperature=0.7,
        )
        ctx = ExecutionContext(
            workspace_dir=Path("/tmp"),
            project_id="test",
            task_id="T1",
            run_id="r1",
            llm_override=LLMConfigOverride(tier="cheap", model="gpt-3.5-turbo"),
        )

        config = resolve_effective_config(spec, ctx)

        assert config.llm_tier == "cheap"
        assert config.llm_model_override == "gpt-3.5-turbo"

    def test_budget_override_applied(self):
        """Budget override 生效。"""
        spec = AgentSpec(
            name="test",
            model_tier="medium",
            tool_names=["read_file"],
            max_steps=30,
            max_tokens_total=200000,
            max_wall_seconds=1800,
        )
        ctx = ExecutionContext(
            workspace_dir=Path("/tmp"),
            project_id="test",
            task_id="T1",
            run_id="r1",
            budget_override=BudgetOverride(max_steps=10, max_tokens=50000),
        )

        config = resolve_effective_config(spec, ctx)

        assert config.max_steps == 10
        assert config.max_tokens == 50000
        assert config.max_wall_seconds == 1800  # 未被 override

    def test_tool_policy_extra_tools(self):
        """额外工具被添加。"""
        spec = AgentSpec(
            name="test",
            model_tier="medium",
            tool_names=["read_file", "write_file"],
        )
        ctx = ExecutionContext(
            workspace_dir=Path("/tmp"),
            project_id="test",
            task_id="T1",
            run_id="r1",
            tool_policy_override=ToolPolicyOverride(extra_tool_names=["bash_run", "docker_exec"]),
        )

        config = resolve_effective_config(spec, ctx)

        assert "bash_run" in config.tool_names
        assert "docker_exec" in config.tool_names
        assert len(config.tool_names) == 4


# ══════════════════════════════════════════════════════
# 7. 异常恢复和重试测试
# ══════════════════════════════════════════════════════

class TestRetryBehavior:
    """测试重试行为。"""

    @pytest.mark.asyncio
    async def test_retry_success_on_eventual_success(self):
        """最终成功时返回结果。"""
        from researchos.runtime.retry import retry_async

        attempts = 0

        async def flaky_function():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ValueError("Temporary failure")
            return "success"

        result = await retry_async(flaky_function, attempts=3, base_delay=0.01)
        assert result == "success"
        assert attempts == 3

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self):
        """重试耗尽后抛出最后一个异常。"""
        from researchos.runtime.retry import retry_async

        async def always_fails():
            raise RuntimeError("Permanent failure")

        with pytest.raises(RuntimeError) as exc_info:
            await retry_async(always_fails, attempts=3, base_delay=0.01)
        assert "Permanent failure" in str(exc_info.value)


# ══════════════════════════════════════════════════════
# 8. 工具访问拒绝场景
# ══════════════════════════════════════════════════════

class TestToolAccessDenial:
    """测试工具访问拒绝的各种场景。"""

    def test_read_disallowed_directory(self, tmp_path):
        """读取不在白名单的目录。"""
        policy = WorkspaceAccessPolicy(
            workspace_dir=tmp_path,
            allowed_read_prefixes=["public/", "shared/"],
            allowed_write_prefixes=["output/"],
        )

        # 创建禁止访问的目录
        (tmp_path / "private").mkdir()
        (tmp_path / "private" / "secret.txt").write_text("secret")

        with pytest.raises(ToolAccessDenied):
            policy.resolve_read("private/secret.txt")

    def test_read_root_level_file_when_only_subdirs_allowed(self, tmp_path):
        """只允许子目录时读取根级文件被拒绝。"""
        policy = WorkspaceAccessPolicy(
            workspace_dir=tmp_path,
            allowed_read_prefixes=["docs/"],
            allowed_write_prefixes=[],
        )

        # 根级文件
        (tmp_path / "README.md").write_text("README")

        with pytest.raises(ToolAccessDenied):
            policy.resolve_read("README.md")

    def test_empty_allowed_prefix_allows_root_files(self, tmp_path):
        """空前缀允许根级文件。"""
        policy = WorkspaceAccessPolicy(
            workspace_dir=tmp_path,
            allowed_read_prefixes=[""],
            allowed_write_prefixes=[],
        )

        (tmp_path / "root_file.txt").write_text("content")
        result = policy.resolve_read("root_file.txt")
        assert result.exists()

    def test_symlink_escape_blocked(self, tmp_path):
        """符号链接逃逸被阻止。"""
        policy = WorkspaceAccessPolicy(
            workspace_dir=tmp_path,
            allowed_read_prefixes=[""],
            allowed_write_prefixes=[],
        )

        # 创建符号链接指向 workspace 外
        outside = tmp_path.parent / "outside"
        outside.mkdir(exist_ok=True)
        (outside / "secret.txt").write_text("secret")

        symlink_path = tmp_path / "link_to_outside"
        symlink_path.symlink_to(outside)

        # 尝试访问符号链接
        with pytest.raises(ToolAccessDenied) as exc_info:
            policy.resolve_read("link_to_outside/secret.txt")
        assert "escapes workspace" in str(exc_info.value)
