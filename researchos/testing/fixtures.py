from __future__ import annotations

from pathlib import Path

import pytest

from ..tools.builtin import register_builtin_tools
from ..tools.human_gate import CLIHumanInterface
from ..tools.registry import ToolRegistry
from ..tools.workspace_policy import WorkspaceAccessPolicy
from .mocks import MockHumanInterface, MockLLMClient


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

