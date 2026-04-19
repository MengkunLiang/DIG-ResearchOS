from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from ..tools.builtin import register_builtin_tools
from ..tools.human_gate import CLIHumanInterface
from ..tools.registry import ToolRegistry
from ..tools.workspace_policy import WorkspaceAccessPolicy
from .mocks import MockHumanInterface, MockLLMClient


def pytest_addoption(parser) -> None:
    """兼容仓库里已有的 `asyncio_mode` 配置项。

    当前测试环境未必安装 `pytest-asyncio`，所以这里注册一个同名 ini 选项，
    避免 pytest 在启动阶段因为“未知配置项”直接报警。
    """

    parser.addini("asyncio_mode", "ResearchOS 内置 asyncio test runner mode", default="auto")


def pytest_configure(config) -> None:
    """注册本仓库自用的 asyncio mark。"""

    config.addinivalue_line("markers", "asyncio: 使用 asyncio.run 执行 async 测试")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """为 async def 测试提供一个最小运行器。

    设计目标很直接：
    - 不强依赖 `pytest-asyncio`；
    - 让本仓库的 async unit tests 在最小环境里也能真正执行，而不是被整批 skip。
    """

    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None

    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
    }
    asyncio.run(pyfuncitem.obj(**kwargs))
    return True


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "_runtime" / "traces").mkdir(parents=True)
    (workspace / "_runtime" / "logs").mkdir(parents=True)
    return workspace


@pytest.fixture
def workspace_policy(tmp_workspace: Path) -> WorkspaceAccessPolicy:
    return WorkspaceAccessPolicy(
        workspace_dir=tmp_workspace,
        allowed_read_prefixes=[""],
        allowed_write_prefixes=[""],
    )


@pytest.fixture
def mock_human() -> MockHumanInterface:
    return MockHumanInterface()


@pytest.fixture
def tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


@pytest.fixture
def mock_llm() -> MockLLMClient:
    return MockLLMClient([])
