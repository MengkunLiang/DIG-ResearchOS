from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from researchos.runtime.errors import ToolRuntimeError
from researchos.testing.mocks import MockHumanInterface
from researchos.tools.mcp_adapter import MCPTool, load_mcp_server_configs, register_mcp_client
from researchos.tools.registry import ToolBuildContext, ToolRegistry
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


@dataclass
class _FakeContentBlock:
    """模拟对象风格的 MCP content block。"""

    type: str
    text: str | None = None


@dataclass
class _FakeResult:
    """模拟对象风格的 MCP tool 返回对象。"""

    content: list[object]
    isError: bool = False
    structuredContent: dict | None = None


class _FakeClient:
    """最小 fake MCP client。

    测试目标不是还原真实 SDK，而是验证 MCPTool 对“通用协议”的兼容性：
    - 记录调用参数，确认 remote tool 名称和 arguments 透传正确；
    - 可按需要返回成功结果或抛异常。
    """

    def __init__(self, *, name: str = "arxiv", result=None, exc: Exception | None = None):
        self.name = name
        self.result = result
        self.exc = exc
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tool_name: str, arguments: dict):
        self.calls.append((tool_name, arguments))
        if self.exc is not None:
            raise self.exc
        return self.result


@dataclass
class _FakeToolInfo:
    """模拟 MCP 的 discover tool 元信息。"""

    name: str
    description: str
    inputSchema: dict


class _FakeDiscoveryClient(_FakeClient):
    """额外支持 `list_tools()` 的 fake client。"""

    def __init__(self, *, tools: list[_FakeToolInfo], **kwargs):
        super().__init__(**kwargs)
        self._tools = tools

    async def list_tools(self):
        return list(self._tools)


def _sample_schema() -> dict:
    """构造一个带 required / default / enum / 嵌套字段的样例 schema。"""

    return {
        "type": "object",
        "title": "Arxiv Search Params",
        "properties": {
            "query": {"type": "string", "description": "检索关键词"},
            "limit": {"type": "integer", "default": 5, "description": "最多条数"},
            "source": {
                "type": "string",
                "enum": ["title", "abstract"],
                "description": "检索字段",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "标签过滤",
            },
            "filters": {
                "type": "object",
                "description": "复杂过滤条件，交给远端服务校验",
            },
        },
        "required": ["query"],
    }


def test_mcp_tool_builds_dynamic_schema():
    """动态 schema 应该把常见 MCP JSON Schema 转成 pydantic 模型。"""

    tool = MCPTool(
        mcp_client=_FakeClient(),
        remote_name="search",
        remote_description="Search papers from remote MCP server",
        remote_input_schema=_sample_schema(),
    )

    params = tool.parameters_schema(
        query="llm agents",
        source="title",
        tags=["survey"],
        filters={"year_from": 2024},
    )

    assert tool.name == "mcp_arxiv_search"
    assert params.limit == 5

    schema = tool.parameters_schema.model_json_schema()
    assert schema["required"] == ["query"]
    assert schema["properties"]["source"]["enum"] == ["title", "abstract"]
    assert schema["properties"]["filters"]["type"] == "object"
    assert schema["properties"]["tags"]["items"]["type"] == "string"

    with pytest.raises(ValidationError):
        tool.parameters_schema(source="title")


@pytest.mark.asyncio
async def test_mcp_tool_execute_happy_path_with_object_result():
    """happy path：对象风格结果应被正确转成 ToolResult。"""

    fake_result = _FakeResult(
        content=[
            _FakeContentBlock(type="text", text="paper-1"),
            _FakeContentBlock(type="text", text="paper-2"),
        ],
        isError=False,
        structuredContent={"count": 2},
    )
    client = _FakeClient(name="semantic_scholar", result=fake_result)
    tool = MCPTool(
        mcp_client=client,
        remote_name="search",
        remote_description="Search papers",
        remote_input_schema=_sample_schema(),
    )

    result = await tool.execute(query="test", limit=2)

    assert client.calls == [("search", {"query": "test", "limit": 2})]
    assert result.ok is True
    assert result.error is None
    assert result.content == "paper-1\npaper-2"
    assert result.data["raw"] is fake_result
    assert result.data["structured_content"] == {"count": 2}
    assert result.metadata["mcp_server"] == "semantic_scholar"
    assert result.metadata["remote_tool"] == "search"


@pytest.mark.asyncio
async def test_mcp_tool_execute_returns_reported_error_for_dict_result():
    """error path：MCP 自己报告 isError 时，不抛异常，返回失败 ToolResult。"""

    client = _FakeClient(
        result={
            "content": [
                {"type": "text", "text": "remote server rejected the request"},
                {"type": "image", "data": "ignored"},
            ],
            "isError": True,
        }
    )
    tool = MCPTool(
        mcp_client=client,
        remote_name="search",
        remote_description="Search papers",
        remote_input_schema=_sample_schema(),
    )

    result = await tool.execute(query="bad-input")

    assert result.ok is False
    assert result.error == "mcp_reported_error"
    assert result.content == "remote server rejected the request"
    assert result.data["content_blocks"][1]["type"] == "image"
    assert result.metadata["is_error"] is True


@pytest.mark.asyncio
async def test_mcp_tool_execute_wraps_client_exception():
    """error path：真实调用异常应转换成 ToolRuntimeError。"""

    client = _FakeClient(exc=RuntimeError("boom"))
    tool = MCPTool(
        mcp_client=client,
        remote_name="get_paper",
        remote_description="Fetch one paper",
        remote_input_schema={"type": "object", "properties": {"id": {"type": "string"}}},
    )

    with pytest.raises(ToolRuntimeError) as exc_info:
        await tool.execute(id="1234.5678")

    assert exc_info.value.tool_name == "mcp_arxiv_get_paper"
    assert isinstance(exc_info.value.underlying, RuntimeError)


@pytest.mark.asyncio
async def test_register_mcp_client_discovers_and_registers_tools(tmp_path: Path):
    client = _FakeDiscoveryClient(
        name="arxiv",
        tools=[
            _FakeToolInfo(
                name="search",
                description="Search papers",
                inputSchema=_sample_schema(),
            ),
            _FakeToolInfo(
                name="get_paper",
                description="Fetch one paper",
                inputSchema={"type": "object", "properties": {"id": {"type": "string"}}},
            ),
        ],
        result={"content": [{"type": "text", "text": "ok"}], "isError": False},
    )
    registry = ToolRegistry()

    registered_names = await register_mcp_client(registry, client)

    assert registered_names == ["mcp_arxiv_search", "mcp_arxiv_get_paper"]

    built = registry.build(
        registered_names,
        ToolBuildContext(
            policy=WorkspaceAccessPolicy(tmp_path, [""], [""]),
            human=MockHumanInterface(),
        ),
    )
    assert sorted(built) == ["mcp_arxiv_get_paper", "mcp_arxiv_search"]
    result = await built["mcp_arxiv_search"].execute(query="agents")
    assert result.ok is True
    assert client.calls == [("search", {"query": "agents"})]


def test_load_mcp_server_configs_expands_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("S2_API_KEY", "secret-token")
    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(
        """
servers:
  - name: semantic_scholar
    command: python
    args: ["-m", "semantic_scholar_mcp"]
    env:
      S2_API_KEY: "${S2_API_KEY}"
""".strip(),
        encoding="utf-8",
    )

    configs = load_mcp_server_configs(config_path)

    assert configs[0]["name"] == "semantic_scholar"
    assert configs[0]["env"]["S2_API_KEY"] == "secret-token"
