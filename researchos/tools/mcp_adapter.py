from __future__ import annotations

"""MCP tool 适配层。

本模块只依赖一个“通用协议”而不是具体的 mcp Python SDK：
- client 需要暴露 `name` 属性；
- client 需要提供 `async call_tool(tool_name, arguments)` 方法。

这样做的目的有两个：
1. runtime 代码可以先稳定下来，不被某个第三方 MCP 库绑定；
2. 单元测试可以直接用 fake client 覆盖 happy path / error path。
"""

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field, create_model
import yaml

from ..runtime.errors import ToolRuntimeError
from .base import Tool, ToolResult
from .registry import ToolRegistry


@runtime_checkable
class MCPClientProtocol(Protocol):
    """MCP client 的最小协议。

    说明：
    - 这里故意不引用真实 MCP SDK 的类型，避免把 runtime 和外部库耦合死；
    - 只要求具备 runtime 真正会用到的两个能力：名字、tool 调用。
    """

    name: str

    async def call_tool(self, tool_name: str, arguments: Mapping[str, Any]) -> Any:
        """执行远端 MCP tool。"""


@runtime_checkable
class MCPDiscoveryClientProtocol(MCPClientProtocol, Protocol):
    """支持 tool discover 的 MCP client 最小协议。"""

    async def list_tools(self) -> Sequence[Any]:
        """列出远端 MCP server 暴露的所有 tool。"""


class MCPTool(Tool):
    """把一个远端 MCP tool 包装成 runtime 内部可直接使用的 Tool。

    设计原则：
    - tool 名称遵循 runtime 约定：`mcp_<server>_<remote_tool>`；
    - 参数 schema 在构造时由远端 JSON Schema 动态生成；
    - 实际执行时只做轻量包装，错误与超时由 runtime 外层统一处理。
    """

    timeout_seconds = 60.0
    requires_human_approval = False
    idempotent = True

    def __init__(
        self,
        mcp_client: MCPClientProtocol,
        remote_name: str,
        remote_description: str,
        remote_input_schema: Mapping[str, Any] | None,
        timeout_seconds: float = 60.0,
        requires_human_approval: bool = False,
    ) -> None:
        # MCP server 名称通常会直接进 tool name；这里统一做轻量规范化，
        # 避免空格、连字符等字符影响后续作为 function/tool 名称暴露给模型。
        server_name = self._normalize_name(getattr(mcp_client, "name", "server"))
        normalized_remote_name = self._normalize_name(remote_name)

        self.mcp = mcp_client
        self.remote_name = remote_name
        self.remote_description = remote_description
        self.remote_input_schema = dict(remote_input_schema or {})
        self.name = f"mcp_{server_name}_{normalized_remote_name}"
        self.description = remote_description
        self.parameters_schema = self._build_schema(self.remote_input_schema)
        self.timeout_seconds = timeout_seconds
        self.requires_human_approval = requires_human_approval

    @classmethod
    def _build_schema(cls, json_schema: Mapping[str, Any]) -> type[BaseModel]:
        """根据 MCP 暴露的 JSON Schema 动态创建 pydantic 参数模型。

        当前实现重点支持 MCP tool 最常见的“平铺 object schema”：
        - 顶层按 `properties` 逐个生成字段；
        - required 字段在本地直接做必填校验；
        - 嵌套 object / array 若过于复杂，则退化成 `dict[str, Any]` / `list[Any]`；
        - 更精细的约束仍交给远端 MCP server 做最终校验。
        """

        schema_type = json_schema.get("type")
        if schema_type not in {None, "object"}:
            raise ValueError(f"MCP tool input schema 必须是 object，收到的是 {schema_type!r}")

        props = json_schema.get("properties", {})
        if not isinstance(props, Mapping):
            raise ValueError("MCP tool input schema 的 properties 必须是对象映射")

        required = set(json_schema.get("required", []))
        fields: dict[str, tuple[Any, Any]] = {}
        for field_name, field_spec in props.items():
            if not isinstance(field_spec, Mapping):
                field_spec = {}

            py_type = cls._jsonschema_to_py_type(field_spec)
            field_required = field_name in required

            # required 字段仍然按必填处理；optional 字段可继承 default，否则默认 None。
            default = ... if field_required else field_spec.get("default", None)
            field_info = Field(
                default,
                description=str(field_spec.get("description", "")),
                title=str(field_spec.get("title", "")) or None,
            )
            fields[field_name] = (py_type, field_info)

        # 模型名只服务于调试和 schema 输出，可读即可。
        model_name = f"MCP_{cls._normalize_name(str(json_schema.get('title') or 'Params'))}"
        if not fields:
            # 某些远端 tool 没有参数；这里仍返回一个空模型，保持 Tool 接口一致。
            model = create_model(model_name)
        else:
            model = create_model(model_name, **fields)

        # 当前仓库环境里仍可能是 pydantic v1；补一个 v2 风格别名，避免动态创建出来的
        # schema 类在导出 OpenAI tool schema 或单测断言时出现 API 不兼容。
        if not hasattr(model, "model_json_schema"):
            model.model_json_schema = classmethod(lambda schema_cls: schema_cls.schema())
        return model

    @classmethod
    def _jsonschema_to_py_type(cls, spec: Mapping[str, Any]) -> Any:
        """把 JSON Schema 类型粗粒度映射到 Python / pydantic 类型。

        注意：
        - enum 会尽量保留成 Literal，便于模型看到离散取值范围；
        - oneOf / anyOf / 深层 object 这类复杂结构统一退化成 Any 或 dict/list；
        - 这是“给 runtime 本地做第一层校验”的类型，不追求完整 JSON Schema 语义。
        """

        enum_values = spec.get("enum")
        if isinstance(enum_values, Sequence) and not isinstance(enum_values, (str, bytes)) and enum_values:
            literal_values = tuple(enum_values)
            literal_type = Any
            try:
                # 运行期动态构造 Literal，便于 schema 输出中保留枚举信息。
                from typing import Literal

                literal_type = Literal.__getitem__(literal_values)
            except Exception:
                literal_type = Any
            return cls._apply_nullable(literal_type, spec)

        raw_type = spec.get("type")
        nullable = False
        if isinstance(raw_type, Sequence) and not isinstance(raw_type, (str, bytes)):
            members = [member for member in raw_type if member != "null"]
            nullable = "null" in raw_type
            json_type = members[0] if len(members) == 1 else None
        else:
            json_type = raw_type

        if json_type == "string":
            py_type: Any = str
        elif json_type == "integer":
            py_type = int
        elif json_type == "number":
            py_type = float
        elif json_type == "boolean":
            py_type = bool
        elif json_type == "array":
            item_spec = spec.get("items", {})
            item_type = cls._jsonschema_to_py_type(item_spec) if isinstance(item_spec, Mapping) else Any
            py_type = list[item_type]
        elif json_type == "object":
            # 嵌套对象这里不继续展开，交给远端 MCP server 做最终校验。
            py_type = dict[str, Any]
        else:
            py_type = Any

        if nullable:
            return py_type | None
        return cls._apply_nullable(py_type, spec)

    @staticmethod
    def _apply_nullable(py_type: Any, spec: Mapping[str, Any]) -> Any:
        """兼容 `nullable: true` 这类 JSON Schema 变体。"""

        if spec.get("nullable") is True:
            return py_type | None
        return py_type

    @staticmethod
    def _normalize_name(raw_name: str) -> str:
        """把任意名字规整成 runtime 可接受的 tool name 片段。"""

        cleaned = [
            ch.lower() if ch.isalnum() else "_"
            for ch in str(raw_name).strip()
        ]
        normalized = "".join(cleaned).strip("_")
        while "__" in normalized:
            normalized = normalized.replace("__", "_")
        return normalized or "unnamed"

    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行远端 MCP tool，并把返回结果统一转换成 ToolResult。

        约束：
        - 这里不做额外的超时控制，runtime 外层会统一 `asyncio.wait_for`；
        - 这里也不主动吞掉异常，真实调用失败时转换成 ToolRuntimeError 让上层感知。
        """

        try:
            raw_result = await self.mcp.call_tool(self.remote_name, kwargs)
        except Exception as exc:
            raise ToolRuntimeError(self.name, exc) from exc

        return self._convert_result(raw_result)

    def _convert_result(self, raw_result: Any) -> ToolResult:
        """把 MCP 返回结果规范化成 ToolResult。

        兼容两类常见返回风格：
        - dict 风格：`{\"content\": [...], \"isError\": bool}`
        - 对象风格：`result.content` / `result.isError`
        """

        content_blocks = self._read_member(raw_result, "content", [])
        text_parts: list[str] = []
        normalized_blocks: list[dict[str, Any]] = []

        if isinstance(content_blocks, Sequence) and not isinstance(content_blocks, (str, bytes)):
            for block in content_blocks:
                normalized_block = self._normalize_content_block(block)
                normalized_blocks.append(normalized_block)
                if normalized_block.get("type") == "text":
                    text = normalized_block.get("text", "")
                    if text:
                        text_parts.append(str(text))

        content = "\n".join(text_parts).strip()
        if not content:
            if normalized_blocks:
                # 如果没有文本块，就回退成对 content block 的紧凑字符串表示，
                # 避免上层只能看到一个无意义的对象 repr。
                content = str(normalized_blocks)
            else:
                content = str(raw_result)

        is_error = bool(self._read_member(raw_result, "isError", False))
        structured_content = self._read_member(raw_result, "structuredContent", None)

        return ToolResult(
            ok=not is_error,
            content=content,
            data={
                "raw": raw_result,
                "content_blocks": normalized_blocks,
                "structured_content": structured_content,
            },
            error="mcp_reported_error" if is_error else None,
            metadata={
                "mcp_server": self._normalize_name(getattr(self.mcp, "name", "server")),
                "remote_tool": self.remote_name,
                "is_error": is_error,
            },
        )

    @staticmethod
    def _normalize_content_block(block: Any) -> dict[str, Any]:
        """把 content block 统一成 dict，方便测试与后续序列化。"""

        if isinstance(block, Mapping):
            return dict(block)

        normalized: dict[str, Any] = {}
        for attr in ("type", "text", "mimeType", "data", "name"):
            if hasattr(block, attr):
                normalized[attr] = getattr(block, attr)

        if normalized:
            return normalized
        return {"type": "unknown", "value": str(block)}

    @staticmethod
    def _read_member(container: Any, key: str, default: Any) -> Any:
        """同时支持 dict 风格与对象属性风格的字段读取。"""

        if isinstance(container, Mapping):
            return container.get(key, default)
        if hasattr(container, key):
            return getattr(container, key)
        return default


def load_mcp_server_configs(config_path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """从 YAML 读取 MCP server 配置，并展开 `${ENV_VAR}` 占位符。

    这里故意只负责“加载配置”和“展开环境变量”，不负责真实连接：
    - runtime 可以先稳定地拥有配置格式与注册逻辑；
    - 具体使用哪一个 MCP Python SDK，可以由上层在 `connector` 中决定；
    - 单元测试也因此不需要拉起真实 MCP 进程。
    """

    path = os.fspath(config_path)
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    servers = raw.get("servers", [])
    if not isinstance(servers, list):
        raise ValueError("MCP config 的 servers 字段必须是列表")
    return [_expand_env_placeholders(item) for item in servers]


def _expand_env_placeholders(value: Any) -> Any:
    """递归展开配置里的环境变量占位符。"""

    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env_placeholders(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _expand_env_placeholders(item) for key, item in value.items()}
    return value


def _tool_info_to_factory(
    client: MCPClientProtocol,
    tool_info: Any,
    *,
    timeout_seconds: float = 60.0,
    requires_human_approval: bool = False,
):
    """把远端 tool 元信息包装成 registry 工厂。"""

    remote_name = _read_tool_info(tool_info, "name")
    if not remote_name:
        raise ValueError("MCP tool info 缺少 name 字段")
    remote_description = str(_read_tool_info(tool_info, "description", "") or "")
    input_schema = _read_tool_info(tool_info, "inputSchema", None)
    return lambda _ctx: MCPTool(
        mcp_client=client,
        remote_name=str(remote_name),
        remote_description=remote_description,
        remote_input_schema=input_schema,
        timeout_seconds=timeout_seconds,
        requires_human_approval=requires_human_approval,
    )


def _read_tool_info(tool_info: Any, key: str, default: Any = None) -> Any:
    """兼容 dict 风格与对象属性风格的 MCP tool discover 结果。"""

    if isinstance(tool_info, Mapping):
        return tool_info.get(key, default)
    if hasattr(tool_info, key):
        return getattr(tool_info, key)
    return default


def _normalized_tool_name(client: MCPClientProtocol, remote_name: str) -> str:
    """根据 client 名称与远端 tool 名组装 runtime 约定的 tool 名。"""

    server_name = MCPTool._normalize_name(getattr(client, "name", "server"))
    remote_name_normalized = MCPTool._normalize_name(remote_name)
    return f"mcp_{server_name}_{remote_name_normalized}"


async def register_mcp_client(
    registry: ToolRegistry,
    client: MCPDiscoveryClientProtocol,
    *,
    timeout_seconds: float = 60.0,
    requires_human_approval: bool = False,
) -> list[str]:
    """发现一个 MCP client 的全部 tool，并注册到 runtime registry。"""

    discovered_tools = await client.list_tools()
    registered_names: list[str] = []
    for tool_info in discovered_tools:
        remote_name = str(_read_tool_info(tool_info, "name") or "")
        factory = _tool_info_to_factory(
            client,
            tool_info,
            timeout_seconds=timeout_seconds,
            requires_human_approval=requires_human_approval,
        )
        tool_name = _normalized_tool_name(client, remote_name)
        registry.register(tool_name, factory)
        registered_names.append(tool_name)
    return registered_names


async def register_mcp_servers(
    registry: ToolRegistry,
    mcp_servers: list[dict[str, Any]],
    connector,
) -> list[MCPDiscoveryClientProtocol]:
    """连接配置的 MCP servers，discover tool 并注册工厂。

    参数说明：
    - `mcp_servers` 来自 `load_mcp_server_configs(...)`；
    - `connector` 是上层注入的异步连接函数，签名约定为：
      `async def connector(server_cfg: dict) -> MCPDiscoveryClientProtocol`

    这样 runtime 保持对具体 MCP SDK 的零绑定，同时把注册逻辑固化下来。
    """

    clients: list[MCPDiscoveryClientProtocol] = []
    for server_cfg in mcp_servers:
        client = await connector(server_cfg)
        clients.append(client)
        await register_mcp_client(registry, client)
    return clients
