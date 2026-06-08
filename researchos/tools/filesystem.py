from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .base import Tool, ToolResult
from .seed_outline import looks_like_seed_outline
from .workspace_policy import WorkspaceAccessPolicy
from ..literature_identity import is_placeholder_text, is_workspace_guide_or_template
from ..runtime.errors import ToolAccessDenied, ToolRuntimeError
from ..runtime.logger import get_logger

_LOG = get_logger("filesystem")
STRUCTURED_ONLY_WRITE_PATHS = {
    "bridge_domain_plan.json": "bridge_domain_plan",
    "literature/bridge_domain_plan.json": "bridge_domain_plan",
    "ideation/exp_plan.yaml": "exp_plan",
    "ideation/idea_rationales.json": "idea_rationales",
    "ideation/idea_scorecard.yaml": "idea_scorecard",
    "ideation/gate_decisions.json": "gate_decisions",
    "pilot/pilot_plan.yaml": "pilot_plan",
    "pilot/pilot_results.json": "pilot_results",
}


class ReadFileParams(BaseModel):
    path: str = Field(..., description="相对 workspace 的路径")
    offset: int = Field(
        default=0,
        ge=0,
        description="从第几个字符开始读取；用于分页读取大文件",
    )
    max_chars: int = Field(
        default=50_000,
        ge=1,
        le=200_000,
        description="最多返回多少字符，防止大文件挤爆模型上下文",
    )


class ReadFileTool(Tool):
    name = "read_file"
    description = "读取 workspace 中的 UTF-8 文本文件"
    parameters_schema = ReadFileParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        path = kwargs["path"]
        offset = int(kwargs.get("offset") or 0)
        max_chars = int(kwargs.get("max_chars") or 50_000)
        try:
            abs_path = self.policy.resolve_read(path)
            full_content = abs_path.read_text(encoding="utf-8")
            size = len(full_content)
            content = full_content[offset : offset + max_chars]
            truncated = offset > 0 or offset + max_chars < size
            if truncated:
                content = (
                    f"[Runtime] 文件较大或请求了分页读取；仅返回字符区间 "
                    f"{offset}:{min(offset + max_chars, size)} / {size}。"
                    "如需继续读取，请再次调用 read_file 并设置 offset。\n\n"
                    + content
                )
            return ToolResult(
                ok=True,
                content=content,
                data={
                    "path": path,
                    "size": size,
                    "offset": offset,
                    "returned_chars": len(content),
                    "truncated": truncated,
                },
            )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except FileNotFoundError:
            return ToolResult(ok=False, content=f"File not found: {path}", error="not_found")
        except IsADirectoryError:
            return ToolResult(
                ok=False,
                content=(
                    f"Path is a directory, not a file: {path}. "
                    "Use list_files on the directory first, then read_file on a concrete file."
                ),
                error="is_directory",
            )
        except UnicodeDecodeError:
            return ToolResult(ok=False, content=f"File is not UTF-8 text: {path}", error="not_text")


class WriteFileParams(BaseModel):
    path: str = Field(..., description="相对 workspace 的路径")
    content: str | dict[str, Any] | list[Any] = Field(
        ...,
        description=(
            "要写入的文本内容；写 .json/.jsonl/.yaml/.yml 时也可以传 JSON 对象或数组，"
            "工具会自动序列化"
        ),
    )


class WriteFileTool(Tool):
    name = "write_file"
    description = "写入 UTF-8 文本文件到 workspace"
    parameters_schema = WriteFileParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        path = kwargs["path"]
        content = kwargs["content"]
        normalized_path = path.strip().lstrip("./")
        if normalized_path in STRUCTURED_ONLY_WRITE_PATHS:
            schema_name = STRUCTURED_ONLY_WRITE_PATHS[normalized_path]
            correct_path = (
                "literature/bridge_domain_plan.json"
                if normalized_path == "bridge_domain_plan.json"
                else normalized_path
            )
            output_format = "json" if normalized_path.endswith(".json") else "yaml"
            return ToolResult(
                ok=False,
                content=(
                    f"{normalized_path} 是结构化产物，不能用 write_file 写入。"
                    f"请改用 write_structured_file(path='{correct_path}', "
                    f"schema_name='{schema_name}', format='{output_format}', data=...)。"
                ),
                error="structured_output_requires_write_structured_file",
            )
        content, conversion_error = self._coerce_content(content, normalized_path)
        if conversion_error:
            return ToolResult(ok=False, content=conversion_error, error="invalid_content_type")
        try:
            abs_path = self.policy.resolve_write(path)

            # 特殊处理：如果是 project.yaml，自动修正格式错误
            if path == "project.yaml" or path.endswith("/project.yaml"):
                content = self._fix_project_yaml(content, path)

            abs_path.write_text(content, encoding="utf-8")
            return ToolResult(
                ok=True,
                content=f"Wrote {len(content)} chars to {path}",
                data={"path": path, "bytes": len(content.encode('utf-8'))},
            )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except OSError as exc:
            raise ToolRuntimeError("write_file", exc) from exc

    def _coerce_content(
        self,
        content: str | dict[str, Any] | list[Any],
        normalized_path: str,
    ) -> tuple[str, str | None]:
        """允许 LLM 对 JSON/YAML 文件传对象，避免在参数校验阶段反复失败。"""

        if isinstance(content, str):
            return content, None

        suffix = Path(normalized_path).suffix.lower()
        if suffix == ".json":
            return json.dumps(content, ensure_ascii=False, indent=2) + "\n", None
        if suffix == ".jsonl":
            if isinstance(content, list):
                return "\n".join(json.dumps(item, ensure_ascii=False) for item in content) + "\n", None
            return json.dumps(content, ensure_ascii=False) + "\n", None
        if suffix in {".yaml", ".yml"}:
            return yaml.safe_dump(content, allow_unicode=True, sort_keys=False), None

        return "", (
            "write_file 的 content 只有在写 .json/.jsonl/.yaml/.yml 时可以是对象或数组；"
            "其他文件请传入字符串内容。"
        )

    def _fix_project_yaml(self, content: str, path: str) -> str:
        """自动修正 project.yaml 的常见格式错误。

        常见错误：
        1. constraints 是空对象 {} - 填充默认值
        2. constraints 是数组 [] - 转换为对象
        3. seed_ensemble 是数组 [] - 转换为对象
        4. seed_ensemble 包含论文信息 - 移除论文信息，只保留随机种子
        5. created_at 格式错误 - 修正为 ISO 8601
        6. keywords 是字符串 - 转换为数组
        """
        try:
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                return content

            fixed = False

            # 修正 1: constraints 是空对象、缺失、或是数组
            constraints = data.get("constraints")
            if not constraints or constraints == {} or isinstance(constraints, list):
                data["constraints"] = {
                    "max_budget_usd": 100.0,
                    "compute_resources": {
                        "allow_gpu": True,
                        "max_memory_gb": 16
                    }
                }
                fixed = True
                reason = "array" if isinstance(constraints, list) else "empty or missing"
                _LOG.info("auto_fix_project_yaml", field="constraints", reason=reason)

            # 修正 2: seed_ensemble 是数组
            if isinstance(data.get("seed_ensemble"), list):
                data["seed_ensemble"] = {
                    "tier1_seeds": [42, 123, 456],
                    "tier2_seeds": [789],
                    "tier3_seeds": [999]
                }
                fixed = True
                _LOG.info("auto_fix_project_yaml", field="seed_ensemble", reason="array instead of object")

            # 修正 3: seed_ensemble 缺失
            if not data.get("seed_ensemble"):
                data["seed_ensemble"] = {
                    "tier1_seeds": [42, 123, 456],
                    "tier2_seeds": [789],
                    "tier3_seeds": [999]
                }
                fixed = True
                _LOG.info("auto_fix_project_yaml", field="seed_ensemble", reason="missing")

            # 修正 3.5: seed_ensemble 包含论文信息（而非随机种子）
            # 检测：seed_ensemble 是对象，但没有 tier1_seeds/tier2_seeds/tier3_seeds 字段
            # 或者包含论文相关字段（title, authors, source, doi 等）
            seed_ensemble = data.get("seed_ensemble")
            if isinstance(seed_ensemble, dict):
                has_seed_fields = (
                    "tier1_seeds" in seed_ensemble or
                    "tier2_seeds" in seed_ensemble or
                    "tier3_seeds" in seed_ensemble
                )
                has_paper_fields = (
                    "title" in seed_ensemble or
                    "authors" in seed_ensemble or
                    "source" in seed_ensemble or
                    "doi" in seed_ensemble or
                    "arxiv_id" in seed_ensemble or
                    "url" in seed_ensemble or
                    "year" in seed_ensemble or
                    "abstract" in seed_ensemble or
                    "venue" in seed_ensemble or
                    "papers" in seed_ensemble
                )
                if not has_seed_fields or has_paper_fields:
                    data["seed_ensemble"] = {
                        "tier1_seeds": [42, 123, 456],
                        "tier2_seeds": [789],
                        "tier3_seeds": [999]
                    }
                    fixed = True
                    reason = "paper_fields" if has_paper_fields else "missing_seed_fields"
                    _LOG.info("auto_fix_project_yaml", field="seed_ensemble", reason=reason)

            # 修正 4: created_at 格式错误
            if "created_at" in data:
                created_at = data["created_at"]
                # 如果是字符串但格式不对（没有时间部分）
                if isinstance(created_at, str) and "T" not in created_at:
                    # 添加时间部分
                    data["created_at"] = f"{created_at}T00:00:00Z"
                    fixed = True
                    _LOG.info("auto_fix_project_yaml", field="created_at", reason="missing time part")
                # 如果是 date 对象（YAML 解析器自动转换的）
                elif not isinstance(created_at, str):
                    # 转换为 ISO 8601 字符串
                    data["created_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    fixed = True
                    _LOG.info("auto_fix_project_yaml", field="created_at", reason="not a string")

            # 修正 5: keywords 是字符串（逗号分隔）
            if isinstance(data.get("keywords"), str):
                # 将逗号分隔的字符串转换为列表
                keywords_str = data["keywords"]
                data["keywords"] = [k.strip() for k in keywords_str.split(",") if k.strip()]
                fixed = True
                _LOG.info("auto_fix_project_yaml", field="keywords", reason="string instead of array")

            if fixed:
                # 重新序列化为 YAML
                fixed_content = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
                _LOG.info("auto_fix_project_yaml_success", path=path, fixes_applied=True)
                return fixed_content

            return content

        except yaml.YAMLError as e:
            # YAML 解析失败，尝试修复常见问题
            _LOG.warning("auto_fix_project_yaml_parse_error", path=path, error=str(e))

            # 修复 1: 检测是否是未转义的冒号导致的错误（如论文标题中的冒号）
            error_str = str(e)
            if "mapping values are not allowed here" in error_str:
                # 尝试修复：将包含冒号的值用引号包裹
                fixed_content = self._fix_unquoted_colons(content)
                if fixed_content != content:
                    _LOG.info("auto_fix_project_yaml_colons", path=path)
                    return fixed_content

            # 修复 2: 检测是否是 seed_ensemble 包含论文信息导致的错误
            if "seed_ensemble:" in content and ("title:" in content or "authors:" in content or "source:" in content):
                _LOG.error("auto_fix_project_yaml_paper_info_detected", path=path)
                # 不自动修复，而是返回原始内容，让 Agent 看到错误信息后自己修正
                # 这样 Agent 会意识到应该用 process_seed_paper 工具处理论文
                return content

            # 其他 YAML 错误，返回原始内容
            return content
        except Exception as e:
            # 如果修正失败，返回原始内容
            _LOG.warning("auto_fix_project_yaml_failed", path=path, error=str(e))
            return content

    def _fix_unquoted_colons(self, content: str) -> str:
        """修复 YAML 中未转义的冒号（如论文标题中的冒号）。

        策略：逐行检查，如果某行包含冒号但值部分也包含冒号且未被引号包裹，
        则将值部分用引号包裹。
        """
        lines = content.split('\n')
        fixed_lines = []

        for line in lines:
            # 跳过注释和空行
            if line.strip().startswith('#') or not line.strip():
                fixed_lines.append(line)
                continue

            # 检查是否是键值对（包含冒号）
            if ':' in line:
                # 找到第一个冒号的位置
                colon_idx = line.find(':')
                key_part = line[:colon_idx]
                value_part = line[colon_idx+1:].strip()

                # 如果值部分包含冒号且未被引号包裹
                if ':' in value_part and not (
                    (value_part.startswith('"') and value_part.endswith('"')) or
                    (value_part.startswith("'") and value_part.endswith("'"))
                ):
                    # 检查是否是嵌套对象（如 "key: {}"）
                    if value_part.strip() in ['{', '[', '{}', '[]'] or value_part.strip().startswith('{') or value_part.strip().startswith('['):
                        fixed_lines.append(line)
                        continue

                    # 用双引号包裹值
                    indent = len(line) - len(line.lstrip())
                    fixed_line = ' ' * indent + key_part + ': "' + value_part + '"'
                    fixed_lines.append(fixed_line)
                    continue

            fixed_lines.append(line)

        return '\n'.join(fixed_lines)


class ListFilesParams(BaseModel):
    path: str = Field(".", description="相对 workspace 的目录路径")
    recursive: bool = Field(False, description="是否递归列出子目录")


class ListFilesTool(Tool):
    name = "list_files"
    description = "列出 workspace 中的文件"
    parameters_schema = ListFilesParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        path = kwargs["path"]
        recursive = kwargs["recursive"]
        try:
            rel_path = "" if path == "." else path
            abs_path = self.policy.resolve_read(rel_path) if rel_path else self.policy.workspace_dir
            if not abs_path.exists():
                return ToolResult(ok=False, content=f"Path not found: {path}", error="not_found")
            pattern = "**/*" if recursive else "*"
            items = sorted(
                p.relative_to(self.policy.workspace_dir).as_posix()
                for p in abs_path.glob(pattern)
                if p != abs_path
            )
            data: dict[str, Any] = {"items": items}
            content = "\n".join(items)
            if rel_path.rstrip("/") in {"user_seeds", "user_seeds/pdfs"}:
                seed_summary = _inspect_user_seed_dir(self.policy.workspace_dir, rel_path.rstrip("/"))
                data["user_seed_inspection"] = seed_summary
                content = _format_user_seed_listing(items, seed_summary)
            return ToolResult(ok=True, content=content, data=data)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")


class InspectUserSeedsParams(BaseModel):
    path: str = Field("user_seeds", description="要检查的 seed 目录，通常为 user_seeds")


class InspectUserSeedsTool(Tool):
    name = "inspect_user_seeds"
    description = (
        "机械检查 user_seeds/ 中哪些是真实用户材料，哪些只是初始化 guide、template、"
        "空文件或“暂无”占位。用于 T1 扫描前后避免把模板误判为 seed。"
    )
    parameters_schema = InspectUserSeedsParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        path = str(kwargs.get("path") or "user_seeds").strip().strip("/") or "user_seeds"
        try:
            abs_path = self.policy.resolve_read(path)
            if not abs_path.exists():
                return ToolResult(ok=False, content=f"Path not found: {path}", error="not_found")
            if not abs_path.is_dir():
                return ToolResult(ok=False, content=f"Path is not a directory: {path}", error="not_directory")
            inspection = _inspect_user_seed_dir(self.policy.workspace_dir, path)
            return ToolResult(ok=True, content=_format_user_seed_inspection(inspection), data=inspection)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")


def _inspect_user_seed_dir(workspace_dir: Path, rel_path: str = "user_seeds") -> dict[str, Any]:
    root = (workspace_dir / rel_path).resolve()
    if not root.exists() or not root.is_dir():
        return {
            "path": rel_path,
            "items_detailed": [],
            "actual_material_count": 0,
            "placeholder_count": 0,
            "guide_or_template_count": 0,
            "actual_material_paths": [],
            "has_actual_user_material": False,
        }

    items: list[dict[str, Any]] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(workspace_dir).as_posix()
        detail = _classify_user_seed_file(path, rel)
        items.append(detail)

    actual_paths = [item["path"] for item in items if item["kind"] in {"user_material", "pdf"}]
    return {
        "path": rel_path,
        "items_detailed": items,
        "actual_material_count": len(actual_paths),
        "placeholder_count": sum(1 for item in items if item["kind"] == "placeholder"),
        "guide_or_template_count": sum(1 for item in items if item["kind"] in {"guide", "template"}),
        "actual_material_paths": actual_paths,
        "has_actual_user_material": bool(actual_paths),
        "agent_instruction": (
            "Only paths with kind=user_material or kind=pdf are real user seed materials. "
            "Do not treat README.md, _DIR_GUIDE.md, *.example, empty files, or files containing only 暂无 placeholders as seeds."
        ),
    }


def _classify_user_seed_file(path: Path, rel: str) -> dict[str, Any]:
    name = path.name
    lower_name = name.casefold()
    size = path.stat().st_size
    kind = "user_material"
    reason = "non-placeholder content"

    if is_workspace_guide_or_template(path):
        kind = "guide"
        reason = "workspace initialization guide"
    elif lower_name.endswith(".pdf"):
        kind = "pdf"
        reason = "user-provided PDF seed"
    elif size == 0:
        kind = "placeholder"
        reason = "empty file"
    elif lower_name in {"seed_ideas.md", "seed_constraints.md"}:
        text = _safe_read_text(path)
        if is_placeholder_text(text):
            kind = "placeholder"
            reason = "default markdown placeholder"
    elif lower_name.endswith(".md"):
        text = _safe_read_text(path)
        if is_placeholder_text(text):
            kind = "placeholder"
            reason = "default markdown placeholder"
        elif looks_like_seed_outline(text):
            kind = "user_material"
            reason = "seed outline markdown; call normalize_seed_outline before downstream use"
    elif lower_name.endswith(".jsonl"):
        text = _safe_read_text(path)
        if is_placeholder_text(text) or not any(line.strip() and not line.lstrip().startswith("#") for line in text.splitlines()):
            kind = "placeholder"
            reason = "empty jsonl seed file"

    return {
        "path": rel,
        "size": size,
        "kind": kind,
        "reason": reason,
        "agent_hint": (
            "real seed material" if kind in {"user_material", "pdf"}
            else "not a user seed; ignore for material-count decisions"
        ),
    }


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _format_user_seed_listing(items: list[str], inspection: dict[str, Any]) -> str:
    base = "\n".join(items)
    return base + "\n\n" + _format_user_seed_inspection(inspection)


def _format_user_seed_inspection(inspection: dict[str, Any]) -> str:
    lines = [
        "[ResearchOS user_seeds inspection]",
        f"- path: {inspection.get('path')}",
        f"- actual_user_materials: {inspection.get('actual_material_count', 0)}",
        f"- placeholders: {inspection.get('placeholder_count', 0)}",
        f"- guides_or_templates: {inspection.get('guide_or_template_count', 0)}",
        "- rule: kind=user_material 或 kind=pdf 才是真实 seed；guide/template/placeholder 不算 seed。",
        "",
        "| path | kind | size | reason |",
        "|---|---|---:|---|",
    ]
    for item in inspection.get("items_detailed", []):
        lines.append(
            f"| {item.get('path')} | {item.get('kind')} | {item.get('size', 0)} | {item.get('reason')} |"
        )
    return "\n".join(lines)
