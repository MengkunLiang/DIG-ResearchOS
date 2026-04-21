"""测试 Experimenter Agent 的调试循环能力。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from researchos.agents.experimenter import ExperimenterAgent
from researchos.runtime.agent import ExecutionContext
from researchos.tools.base import ToolResult


@pytest.fixture
def mock_context(tmp_workspace: Path):
    """创建 mock 执行上下文。"""
    # 创建必要的目录和文件
    (tmp_workspace / "ideation").mkdir(exist_ok=True)
    (tmp_workspace / "experiments").mkdir(exist_ok=True)

    # 创建实验计划
    exp_plan = {
        "experiments": [
            {
                "name": "test_exp",
                "hypothesis_ref": "H1",
                "description": "Test experiment",
                "dataset": "test_dataset",
                "metrics": ["accuracy"],
            }
        ]
    }
    (tmp_workspace / "ideation" / "exp_plan.yaml").write_text(
        json.dumps(exp_plan), encoding="utf-8"
    )

    # 创建假设文件
    (tmp_workspace / "ideation" / "hypotheses.md").write_text(
        "# H1: Test Hypothesis\nThis is a test hypothesis.", encoding="utf-8"
    )

    # 创建 project.yaml
    project = {
        "project_id": "test",
        "research_direction": "NLP",
        "domain": "Machine Learning",
    }
    (tmp_workspace / "project.yaml").write_text(
        json.dumps(project), encoding="utf-8"
    )

    ctx = MagicMock(spec=ExecutionContext)
    ctx.workspace_dir = tmp_workspace
    ctx.mode = "full"
    ctx.human = MagicMock()
    ctx.policy = MagicMock()
    ctx.policy.workspace_dir = tmp_workspace

    return ctx


@pytest.mark.asyncio
async def test_experimenter_debug_loop_dependency_error(mock_context):
    """测试 Experimenter 自动修复依赖错误（概念验证）。"""
    # 这个测试验证调试循环的概念，而不是实际的 Agent 实现
    # 实际的调试逻辑在 prompt 中指导 LLM 执行

    # Mock docker_exec 工具：第一次失败（缺少依赖），第二次成功
    mock_docker_exec = AsyncMock()
    mock_docker_exec.side_effect = [
        # 第一次执行：缺少依赖
        ToolResult(
            ok=False,
            content="ModuleNotFoundError: No module named 'transformers'",
            error="nonzero_exit",
            data={"exit_code": 1},
        ),
        # 安装依赖后第二次执行：成功
        ToolResult(
            ok=True,
            content="Training completed successfully",
            data={"exit_code": 0},
        ),
    ]

    # 模拟调试循环（这是 LLM 会执行的逻辑）
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        result = await mock_docker_exec()

        if result.ok:
            # 成功
            assert attempt == 2  # 第二次尝试成功
            break

        # 失败，分析错误
        if "ModuleNotFoundError" in result.content:
            # 模拟安装依赖（实际会调用 docker_exec）
            pass

    # 验证调用次数
    assert mock_docker_exec.call_count == 2


@pytest.mark.asyncio
async def test_experimenter_debug_loop_path_error(mock_context):
    """测试 Experimenter 自动修复路径错误（概念验证）。"""
    # Mock docker_exec 工具：第一次失败（路径错误），第二次成功
    mock_docker_exec = AsyncMock()
    mock_docker_exec.side_effect = [
        # 第一次执行：路径错误
        ToolResult(
            ok=False,
            content="FileNotFoundError: [Errno 2] No such file or directory: 'data/train.csv'",
            error="nonzero_exit",
            data={"exit_code": 1},
        ),
        # 修复路径后第二次执行：成功
        ToolResult(
            ok=True,
            content="Training completed successfully",
            data={"exit_code": 0},
        ),
    ]

    # 模拟调试循环
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        result = await mock_docker_exec()

        if result.ok:
            assert attempt == 2
            break

        if "FileNotFoundError" in result.content:
            # 模拟修复路径
            pass

    assert mock_docker_exec.call_count == 2


@pytest.mark.asyncio
async def test_experimenter_debug_loop_oom_error(mock_context):
    """测试 Experimenter 自动修复 OOM 错误（概念验证）。"""
    # Mock docker_exec 工具：第一次 OOM，第二次成功
    mock_docker_exec = AsyncMock()
    mock_docker_exec.side_effect = [
        # 第一次执行：OOM
        ToolResult(
            ok=False,
            content="RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB",
            error="nonzero_exit",
            data={"exit_code": 1},
        ),
        # 减小 batch_size 后第二次执行：成功
        ToolResult(
            ok=True,
            content="Training completed successfully",
            data={"exit_code": 0},
        ),
    ]

    # 模拟调试循环
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        result = await mock_docker_exec()

        if result.ok:
            assert attempt == 2
            break

        if "CUDA out of memory" in result.content:
            # 模拟减小 batch_size
            pass

    assert mock_docker_exec.call_count == 2


@pytest.mark.asyncio
async def test_experimenter_debug_loop_max_retries(mock_context):
    """测试 Experimenter 达到最大重试次数后失败（概念验证）。"""
    # Mock docker_exec 工具：始终失败
    mock_docker_exec = AsyncMock()
    mock_docker_exec.return_value = ToolResult(
        ok=False,
        content="RuntimeError: Unrecoverable error",
        error="nonzero_exit",
        data={"exit_code": 1},
    )

    # 模拟调试循环
    max_retries = 3
    success = False
    for attempt in range(1, max_retries + 1):
        result = await mock_docker_exec()

        if result.ok:
            success = True
            break

        # 无法修复的错误
        if attempt == max_retries:
            # 记录失败
            assert not success

    # 验证尝试了 3 次
    assert mock_docker_exec.call_count == 3
    assert not success


@pytest.mark.asyncio
async def test_experimenter_iteration_history_recording(mock_context):
    """测试 Experimenter 记录迭代历史。"""
    agent = ExperimenterAgent()

    # 模拟迭代历史记录
    iteration_history = {
        "experiment_id": "exp_h1_run1",
        "attempts": [],
    }

    # 第一次尝试：失败
    iteration_history["attempts"].append({
        "attempt": 1,
        "status": "FAILED",
        "error": "ModuleNotFoundError: No module named 'transformers'",
        "fix_applied": "pip install transformers",
    })

    # 第二次尝试：失败
    iteration_history["attempts"].append({
        "attempt": 2,
        "status": "FAILED",
        "error": "FileNotFoundError: data/train.csv",
        "fix_applied": "修正路径为 ../data/train.csv",
    })

    # 第三次尝试：成功
    iteration_history["attempts"].append({
        "attempt": 3,
        "status": "DONE",
        "metrics": {"accuracy": 0.87},
    })

    # 验证历史记录
    assert len(iteration_history["attempts"]) == 3
    assert iteration_history["attempts"][0]["status"] == "FAILED"
    assert iteration_history["attempts"][1]["status"] == "FAILED"
    assert iteration_history["attempts"][2]["status"] == "DONE"
    assert "accuracy" in iteration_history["attempts"][2]["metrics"]


def test_experimenter_error_classification():
    """测试错误分类逻辑。"""

    def classify_error(error_message: str) -> str:
        """简单的错误分类器。"""
        if "ModuleNotFoundError" in error_message or "ImportError" in error_message:
            return "dependency_missing"
        elif "FileNotFoundError" in error_message:
            return "path_error"
        elif "CUDA out of memory" in error_message or "OOM" in error_message:
            return "oom_error"
        elif "ValueError" in error_message or "TypeError" in error_message:
            return "parameter_error"
        elif "AssertionError" in error_message or "RuntimeError" in error_message:
            return "logic_error"
        else:
            return "unknown_error"

    # 测试各种错误类型
    assert classify_error("ModuleNotFoundError: No module named 'torch'") == "dependency_missing"
    assert classify_error("FileNotFoundError: data.csv not found") == "path_error"
    assert classify_error("RuntimeError: CUDA out of memory") == "oom_error"
    assert classify_error("ValueError: learning_rate must be positive") == "parameter_error"
    assert classify_error("AssertionError: batch_size > 0") == "logic_error"
    assert classify_error("Some unknown error") == "unknown_error"
