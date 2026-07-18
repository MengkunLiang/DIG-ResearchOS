"""结构化文件写入工具。

提供 write_structured_file 工具，接收结构化数据（JSON 对象）而非文本，
自动进行 schema 验证和序列化，确保生成的文件格式正确。
"""

from __future__ import annotations

import json
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from ..schemas.validator import validate_record, validate_record_diagnostics
from .base import Tool, ToolResult
from .workspace_policy import ToolAccessDenied, WorkspaceAccessPolicy
from ..runtime.errors import ToolRuntimeError


class WriteStructuredFileParams(BaseModel):
    """write_structured_file 工具的参数。"""

    path: str = Field(
        ...,
        description="文件路径（相对 workspace 根目录）",
    )
    data: dict[str, Any] = Field(
        ...,
        description="结构化数据（将根据 schema 验证）",
    )
    schema_name: str = Field(
        ...,
        description="Schema 名称（如 'project', 'exp_plan', 'results_summary'）",
    )
    format: Literal["yaml", "json", "jsonl"] = Field(
        default="yaml",
        description="输出格式：yaml（默认）、json 或 jsonl",
    )


class WriteStructuredFileTool(Tool):
    """写入结构化文件（自动验证 schema）。

    与 write_file 的区别：
    - write_file: 接收文本内容，Agent 需要手动格式化 YAML/JSON
    - write_structured_file: 接收 JSON 对象，工具自动验证和序列化

    优势：
    - Schema 验证前置，错误信息清晰（指出具体字段问题）
    - 标准序列化，避免格式错误（如 YAML 语法错误）
    - Agent 不需要了解 YAML/JSON 格式细节

    Example:
        >>> tool = WriteStructuredFileTool(policy)
        >>> result = await tool.execute(
        ...     path="project.yaml",
        ...     schema_name="project",
        ...     format="yaml",
        ...     data={
        ...         "project_id": "test",
        ...         "research_direction": "AI research",
        ...         "keywords": ["AI", "ML"],
        ...         "created_at": "2026-04-21T10:00:00Z",
        ...         "constraints": {...},
        ...         "seed_ensemble": {...}
        ...     }
        ... )
    """

    name = "write_structured_file"
    description = "写入结构化文件（自动验证 schema 并序列化）"
    parameters_schema = WriteStructuredFileParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        """执行结构化文件写入。

        流程：
        1. Schema 验证（使用 jsonschema 库）
        2. 序列化为指定格式（YAML/JSON/JSONL）
        3. 写入文件
        """
        path = kwargs["path"]
        data = kwargs["data"]
        schema_name = kwargs["schema_name"]
        format_type = kwargs["format"]
        normalized_path = str(path).strip().lstrip("./")
        if schema_name == "bridge_domain_plan" and normalized_path != "literature/bridge_domain_plan.json":
            return ToolResult(
                ok=False,
                content=(
                    "bridge_domain_plan 是 T1 给 T2 使用的正式跨领域召回计划，"
                    "必须写入 literature/bridge_domain_plan.json；"
                    f"当前 path={path!r} 不会被 T2 读取。请改用 "
                    "write_structured_file(path='literature/bridge_domain_plan.json', "
                    "schema_name='bridge_domain_plan', format='json', data=...)。"
                ),
                error="wrong_artifact_path",
            )

        try:
            # 1. Schema 验证
            # 对于 JSONL 格式的数组数据，逐个验证每个元素
            if format_type == "jsonl" and isinstance(data, list):
                for i, item in enumerate(data):
                    ok, err = validate_record(item, schema_name)
                    if not ok:
                        diagnostics = validate_record_diagnostics(item, schema_name)
                        return ToolResult(
                            ok=False,
                            content=_schema_failure_content(
                                schema_name=schema_name,
                                diagnostics=diagnostics,
                                record_label=f"第 {i + 1} 条记录",
                            ),
                            data=_schema_failure_data(
                                path=path,
                                schema_name=schema_name,
                                diagnostics=diagnostics,
                            ),
                            error="schema_validation_failed",
                        )
            else:
                ok, err = validate_record(data, schema_name)
                if not ok:
                    diagnostics = validate_record_diagnostics(data, schema_name)
                    return ToolResult(
                        ok=False,
                        content=_schema_failure_content(
                            schema_name=schema_name,
                            diagnostics=diagnostics,
                        ),
                        data=_schema_failure_data(
                            path=path,
                            schema_name=schema_name,
                            diagnostics=diagnostics,
                        ),
                        error="schema_validation_failed",
                    )

            # 2. 序列化
            if format_type == "yaml":
                content = yaml.safe_dump(
                    data,
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                )
            elif format_type == "json":
                content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            elif format_type == "jsonl":
                # JSONL: 每行一个 JSON 对象
                # 如果 data 是数组，每个元素一行；否则整个对象一行
                if isinstance(data, list):
                    lines = [json.dumps(item, ensure_ascii=False) for item in data]
                    content = "\n".join(lines) + "\n"
                else:
                    content = json.dumps(data, ensure_ascii=False) + "\n"
            else:
                return ToolResult(
                    ok=False,
                    content=f"不支持的格式: {format_type}",
                    error="unsupported_format",
                )

            # 3. 写入文件
            abs_path = self.policy.resolve_write(path)
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(content, encoding="utf-8")

            return ToolResult(
                ok=True,
                content=f"✅ 成功写入 {len(content)} 字符到 {path}\n"
                f"格式: {format_type}, Schema: {schema_name}",
                data={
                    "path": path,
                    "bytes": len(content.encode("utf-8")),
                    "format": format_type,
                    "schema_name": schema_name,
                },
            )

        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except OSError as exc:
            raise ToolRuntimeError("write_structured_file", exc) from exc
        except Exception as exc:
            # 捕获其他异常（如序列化错误）
            return ToolResult(
                ok=False,
                content=f"写入失败: {exc}",
                error="write_failed",
            )


def _schema_failure_data(
    *,
    path: str,
    schema_name: str,
    diagnostics: list[dict[str, str]],
) -> dict[str, Any]:
    """Expose compact repair data to both the model and the CLI renderer."""

    return {
        "path": path,
        "schema_name": schema_name,
        "schema_errors": diagnostics,
        # The active Agent receives these diagnostics and is instructed to
        # correct the same artifact before it can continue.
        "display_disposition": "auto_repair",
        "repair_scope": "structured_artifact_schema",
        "repairable": True,
        "repair_hint": (
            "逐项修复列出的字段后再调用 write_structured_file；"
            "不要为了绕过 schema 删除候选或改写其他已通过字段。"
        ),
    }


def _schema_failure_content(
    *,
    schema_name: str,
    diagnostics: list[dict[str, str]],
    record_label: str | None = None,
) -> str:
    label = f"，{record_label}" if record_label else ""
    lines = [f"Schema 验证失败（schema: {schema_name}{label}）。"]
    for item in diagnostics:
        lines.append(f"- {item['path']} [{item['rule']}]: {item['message']}")
    lines.extend(
        [
            "修复方式：逐项修复以上字段后再重试同一写入。",
            "不要删除候选、降低证据等级语义或把数组字段改成单个字符串来规避校验。",
        ]
    )
    return "\n".join(lines)
