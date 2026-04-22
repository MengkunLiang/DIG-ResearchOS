"""JSON Schema 到 Pydantic 模型的动态转换。

复用 MCP 适配层的转换逻辑，为 ResearchOS 的 JSON Schema 生成 Pydantic 模型。
用于 write_structured_file 工具的参数验证和未来的 LLM Structured Outputs。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from pydantic import BaseModel, Field, create_model

from .validator import load_schema

# 缓存已生成的 Pydantic 模型，避免重复生成
_SCHEMA_CACHE: dict[str, type[BaseModel]] = {}


def schema_to_pydantic(schema_name: str) -> type[BaseModel]:
    """将 JSON Schema 转换为 Pydantic 模型。

    Args:
        schema_name: Schema 名称（如 'project', 'exp_plan'）

    Returns:
        动态生成的 Pydantic BaseModel 子类

    Example:
        >>> ProjectModel = schema_to_pydantic("project")
        >>> project = ProjectModel(
        ...     project_id="test",
        ...     research_direction="AI research",
        ...     created_at="2026-04-21T10:00:00Z"
        ... )
    """
    if schema_name in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[schema_name]

    schema = load_schema(schema_name)
    model = _build_pydantic_model(schema_name, schema)
    _SCHEMA_CACHE[schema_name] = model
    return model


def _build_pydantic_model(schema_name: str, schema: dict[str, Any]) -> type[BaseModel]:
    """根据 JSON Schema 动态创建 Pydantic 模型。

    复用 MCP 适配层的转换逻辑（researchos/tools/mcp_adapter.py）。
    """
    schema_type = schema.get("type")
    if schema_type != "object":
        raise ValueError(f"Schema '{schema_name}' 必须是 object 类型，收到的是 {schema_type!r}")

    props = schema.get("properties", {})
    if not isinstance(props, Mapping):
        raise ValueError(f"Schema '{schema_name}' 的 properties 必须是对象映射")

    required = set(schema.get("required", []))
    fields: dict[str, tuple[Any, Any]] = {}

    for field_name, field_spec in props.items():
        if not isinstance(field_spec, Mapping):
            field_spec = {}

        py_type = _jsonschema_to_py_type(field_spec)
        field_required = field_name in required

        # required 字段按必填处理；optional 字段可继承 default，否则默认 None
        default = ... if field_required else field_spec.get("default", None)
        field_info = Field(
            default,
            description=str(field_spec.get("description", "")),
            title=str(field_spec.get("title", "")) or None,
        )
        fields[field_name] = (py_type, field_info)

    # 模型名用于调试和 schema 输出
    model_name = f"{schema_name.title().replace('_', '')}Model"

    if not fields:
        # 某些 schema 可能没有字段；返回空模型保持接口一致
        model = create_model(model_name)
    else:
        model = create_model(model_name, **fields)

    # Pydantic v1/v2 兼容性：补充 v2 风格别名
    if not hasattr(model, "model_json_schema"):
        model.model_json_schema = classmethod(lambda schema_cls: schema_cls.schema())

    return model


def _jsonschema_to_py_type(spec: Mapping[str, Any]) -> Any:
    """把 JSON Schema 类型映射到 Python / Pydantic 类型。

    注意：
    - enum 会保留成 Literal，便于模型看到离散取值范围
    - oneOf / anyOf / 深层 object 统一退化成 Any 或 dict/list
    - 这是"给 runtime 本地做第一层校验"的类型，不追求完整 JSON Schema 语义
    """
    # 处理 enum
    enum_values = spec.get("enum")
    if isinstance(enum_values, Sequence) and not isinstance(enum_values, (str, bytes)) and enum_values:
        literal_values = tuple(enum_values)
        literal_type = Any
        try:
            from typing import Literal
            literal_type = Literal.__getitem__(literal_values)
        except Exception:
            literal_type = Any
        return _apply_nullable(literal_type, spec)

    # 处理 type 字段
    raw_type = spec.get("type")
    nullable = False

    if isinstance(raw_type, Sequence) and not isinstance(raw_type, (str, bytes)):
        # type 是数组，如 ["string", "null"]
        members = [member for member in raw_type if member != "null"]
        nullable = "null" in raw_type
        json_type = members[0] if len(members) == 1 else None
    else:
        json_type = raw_type

    # 类型映射
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
        item_type = _jsonschema_to_py_type(item_spec) if isinstance(item_spec, Mapping) else Any
        py_type = list[item_type]
    elif json_type == "object":
        # 嵌套对象不继续展开，交给 jsonschema 库做最终校验
        py_type = dict[str, Any]
    else:
        py_type = Any

    if nullable:
        return py_type | None
    return _apply_nullable(py_type, spec)


def _apply_nullable(py_type: Any, spec: Mapping[str, Any]) -> Any:
    """兼容 `nullable: true` 这类 JSON Schema 变体。"""
    if spec.get("nullable") is True:
        return py_type | None
    return py_type


def clear_cache() -> None:
    """清空 schema 缓存（主要用于测试）。"""
    _SCHEMA_CACHE.clear()
