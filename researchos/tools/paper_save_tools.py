"""论文数据保存工具。

提供专门用于保存论文数据的工具，支持：
1. 流式写入：LLM 检索到论文后立即追加原始数据（不转换）
2. 批量处理：一次性转换和验证所有论文数据

支持从多种搜索源（Semantic Scholar、arXiv、OpenAlex）的数据格式转换为 papers_raw schema。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..schemas.validator import validate_record
from .base import Tool, ToolResult
from .workspace_policy import ToolAccessDenied, WorkspaceAccessPolicy
from ..runtime.errors import ToolRuntimeError


def _normalize_authors(authors: Any) -> list[str]:
    """标准化 authors 字段为字符串列表。

    处理多种输入格式：
    - 字符串列表: ["John Doe", "Jane Smith"]
    - 对象列表: [{"name": "John Doe"}]
    - 混合格式
    """
    if not authors:
        return []

    result = []
    for author in authors:
        if isinstance(author, str):
            result.append(author)
        elif isinstance(author, dict):
            # 尝试多个可能的 name 字段
            name = author.get("name") or author.get("display_name") or ""
            if name:
                result.append(name)
    return result


def _normalize_citation_count(citations: Any) -> int:
    """标准化引用数字段。"""
    if citations is None:
        return 0
    if isinstance(citations, int):
        return citations
    if isinstance(citations, (float, str)):
        try:
            return int(float(citations))
        except (ValueError, TypeError):
            return 0
    return 0


def _normalize_year(year: Any) -> int | None:
    """标准化年份字段。"""
    if year is None:
        return None
    if isinstance(year, int):
        return year
    if isinstance(year, (float, str)):
        try:
            return int(float(year))
        except (ValueError, TypeError):
            return None
    return None


def _transform_to_papers_raw(paper: dict[str, Any]) -> dict[str, Any]:
    """将各种格式的论文数据转换为 papers_raw schema。

    处理来自不同搜索源的格式差异：
    - Semantic Scholar: authors=[{name: "..."}], citationCount, externalIds
    - arXiv: authors=[{name: "..."}], citationCount=0
    - OpenAlex: authors=["..."], citation_count
    """
    # 提取 id
    paper_id = (
        paper.get("id")
        or paper.get("paperId")
        or paper.get("externalIds", {}).get("ArXiv")
        or ""
    )

    # 提取 source
    source = paper.get("source", "unknown")

    # 标准化 authors
    authors = _normalize_authors(paper.get("authors", []))

    # 标准化引用数
    citation_count = _normalize_citation_count(
        paper.get("citation_count") or paper.get("citationCount", 0)
    )

    # 标准化年份
    year = _normalize_year(paper.get("year"))

    # 提取 abstract
    abstract = paper.get("abstract") or ""

    # 提取 URL
    url = paper.get("url") or paper.get("id", "")

    # 提取 DOI
    doi = paper.get("doi") or ""
    if doi.startswith("https://doi.org/"):
        doi = doi.replace("https://doi.org/", "")

    # 提取 externalIds
    external_ids = paper.get("externalIds") or {}

    return {
        "id": paper_id,
        "source": source,
        "title": paper.get("title", "Unknown"),
        "authors": authors,
        "year": year,
        "abstract": abstract,
        "venue": paper.get("venue", ""),
        "citation_count": citation_count,
        "doi": doi,
        "url": url,
        "externalIds": external_ids,
    }


# ============================================================================
# 流式写入工具：LLM 检索到论文后立即追加，不转换
# ============================================================================


class AppendPapersRawParams(BaseModel):
    """append_papers_raw 工具的参数。"""

    papers: list[dict[str, Any]] = Field(
        ...,
        description="论文列表（来自搜索工具的原始返回 data.papers）",
    )


class AppendPapersRawTool(Tool):
    """流式追加论文到 papers_raw.jsonl。

    LLM 检索到论文后立即调用此工具追加到文件。
    **不进行任何数据转换或验证**，只做简单的 JSONL 追加。
    这样 LLM 不需要处理数据格式，可以专注检索。

    流程：
    1. LLM 调用搜索 API
    2. LLM 立即调用 append_papers_raw 追加结果
    3. 重复步骤 1-2 直到检索完成
    4. 最后调用 process_papers_raw 批量转换和验证

    示例用法：
    ```
    # 检索后立即追加（不等待）
    result = search_semantic_scholar(query="...", ...)
    append_papers_raw(papers=result.data.papers)
    ```
    """

    name = "append_papers_raw"
    description = (
        "流式追加论文到 literature/papers_raw.jsonl。"
        "不做任何数据转换，只追加原始 JSON。"
        "LLM 检索到论文后立即调用，专注检索不处理数据。"
    )
    parameters_schema = AppendPapersRawParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        params = AppendPapersRawParams(**kwargs)

        try:
            import json

            abs_path = self.policy.resolve_write("literature/papers_raw.jsonl")
            abs_path.parent.mkdir(parents=True, exist_ok=True)

            # 追加到文件
            with abs_path.open("a", encoding="utf-8") as f:
                for paper in params.papers:
                    line = json.dumps(paper, ensure_ascii=False)
                    f.write(line + "\n")

            count = len(params.papers)
            return ToolResult(
                ok=True,
                content=f"✅ 追加 {count} 篇论文到 literature/papers_raw.jsonl",
                data={
                    "path": "literature/papers_raw.jsonl",
                    "count": count,
                },
            )

        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(
                ok=False,
                content=f"追加失败: {exc}",
                error="append_failed",
            )


class ProcessPapersRawParams(BaseModel):
    """process_papers_raw 工具的参数。"""

    pass  # 无需参数，从文件读取


class ProcessPapersRawTool(Tool):
    """批量处理 papers_raw.jsonl。

    读取所有原始论文数据，批量转换格式并验证 schema。
    在 LLM 完成所有检索后调用。

    流程：
    1. 读取 literature/papers_raw.jsonl
    2. 批量转换数据格式（标准化 authors、citation_count 等）
    3. Schema 验证
    4. 覆盖写入 literature/papers_raw.jsonl

    示例用法：
    ```
    # LLM 完成所有检索后调用
    process_papers_raw()
    ```
    """

    name = "process_papers_raw"
    description = (
        "批量处理 papers_raw.jsonl：读取原始数据、转换格式、验证 schema。"
        "在 LLM 完成所有检索后调用一次。"
    )
    parameters_schema = ProcessPapersRawParams
    timeout_seconds = 60.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        try:
            import json

            abs_path = self.policy.resolve_write("literature/papers_raw.jsonl")

            if not abs_path.exists():
                return ToolResult(
                    ok=False,
                    content="❌ papers_raw.jsonl 文件不存在",
                    error="file_not_found",
                )

            # 1. 读取所有原始数据
            raw_papers = []
            errors = []
            for i, line in enumerate(abs_path.read_text(encoding="utf-8").splitlines()):
                if line.strip():
                    try:
                        raw_papers.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        errors.append(f"第 {i+1} 行 JSON 解析失败: {e}")

            if errors:
                return ToolResult(
                    ok=False,
                    content=f"❌ 读取文件时遇到 {len(errors)} 个错误:\n" + "\n".join(errors[:5]),
                    error="parse_error",
                )

            # 2. 批量转换
            transformed_papers = []
            transform_errors = []
            for i, paper in enumerate(raw_papers):
                try:
                    transformed = _transform_to_papers_raw(paper)
                    transformed_papers.append(transformed)
                except Exception as e:
                    transform_errors.append(f"第 {i+1} 条转换失败: {e}")

            if transform_errors:
                return ToolResult(
                    ok=False,
                    content=f"❌ 数据转换失败:\n" + "\n".join(transform_errors[:5]),
                    error="transform_failed",
                )

            # 3. Schema 验证
            validation_errors = []
            for i, paper in enumerate(transformed_papers):
                ok, err = validate_record(paper, "papers_raw")
                if not ok:
                    validation_errors.append(f"第 {i+1} 条验证失败: {err}")

            if validation_errors:
                error_msg = f"❌ Schema 验证失败（{len(validation_errors)} 条）:\n"
                error_msg += "\n".join(validation_errors[:5])
                if len(validation_errors) > 5:
                    error_msg += f"\n... 还有 {len(validation_errors) - 5} 条错误"
                return ToolResult(
                    ok=False,
                    content=error_msg,
                    error="schema_validation_failed",
                )

            # 4. 覆盖写入
            lines = [json.dumps(p, ensure_ascii=False) for p in transformed_papers]
            abs_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            return ToolResult(
                ok=True,
                content=f"✅ 成功处理 {len(transformed_papers)} 篇论文（转换 + 验证通过）",
                data={
                    "path": "literature/papers_raw.jsonl",
                    "count": len(transformed_papers),
                },
            )

        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(
                ok=False,
                content=f"处理失败: {exc}",
                error="process_failed",
            )


class SavePapersRawParams(BaseModel):
    """save_papers_raw 工具的参数。"""

    papers: list[dict[str, Any]] = Field(
        ...,
        description="论文列表（来自搜索工具的 data.papers）",
    )
    append: bool = Field(
        default=False,
        description="是否追加模式（True=追加，False=覆盖）",
    )


class SavePapersRawTool(Tool):
    """保存论文到 papers_raw.jsonl。

    自动处理：
    1. 数据格式转换（支持多种搜索源格式）
    2. Schema 验证
    3. JSONL 序列化

    示例用法：
    ```
    # 保存搜索结果
    save_papers_raw(papers=search_result.data.papers)

    # 追加更多结果
    save_papers_raw(papers=search_result2.data.papers, append=True)
    ```
    """

    name = "save_papers_raw"
    description = (
        "保存论文列表到 literature/papers_raw.jsonl。"
        "自动处理格式转换和 schema 验证。"
        "接收搜索工具返回的 papers 数据。"
    )
    parameters_schema = SavePapersRawParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        params = SavePapersRawParams(**kwargs)

        try:
            # 1. 转换数据格式
            transformed_papers = []
            for i, paper in enumerate(params.papers):
                try:
                    transformed = _transform_to_papers_raw(paper)
                    transformed_papers.append(transformed)
                except Exception as e:
                    return ToolResult(
                        ok=False,
                        content=f"❌ 数据转换失败（第 {i+1} 条记录）: {e}\n\n"
                        f"原始数据: {paper}",
                        error="transform_failed",
                    )

            # 2. Schema 验证
            for i, paper in enumerate(transformed_papers):
                ok, err = validate_record(paper, "papers_raw")
                if not ok:
                    return ToolResult(
                        ok=False,
                        content=f"❌ Schema 验证失败（第 {i+1} 条记录）:\n\n{err}\n\n"
                        f"数据: {paper}",
                        error="schema_validation_failed",
                    )

            # 3. 序列化为 JSONL
            lines = []
            for paper in transformed_papers:
                import json

                line = json.dumps(paper, ensure_ascii=False)
                lines.append(line)

            content = "\n".join(lines) + "\n"

            # 4. 写入文件
            abs_path = self.policy.resolve_write("literature/papers_raw.jsonl")
            abs_path.parent.mkdir(parents=True, exist_ok=True)

            if params.append and abs_path.exists():
                # 追加模式：读取现有数据，过滤掉已存在的 id，然后追加
                import json

                existing_ids = set()
                existing_lines = []
                for line in abs_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            existing = json.loads(line)
                            existing_ids.add(existing.get("id"))
                            existing_lines.append(line)
                        except json.JSONDecodeError:
                            pass

                # 添加新论文（排除已存在的 id）
                new_lines = []
                for paper in transformed_papers:
                    if paper["id"] not in existing_ids:
                        new_lines.append(json.dumps(paper, ensure_ascii=False))

                final_content = "\n".join(existing_lines + new_lines) + "\n"
                abs_path.write_text(final_content, encoding="utf-8")
            else:
                # 覆盖模式
                abs_path.write_text(content, encoding="utf-8")

            return ToolResult(
                ok=True,
                content=f"✅ 成功保存 {len(transformed_papers)} 篇论文到 literature/papers_raw.jsonl\n"
                f"（模式: {'追加' if params.append else '覆盖'}）",
                data={
                    "path": "literature/papers_raw.jsonl",
                    "count": len(transformed_papers),
                    "mode": "append" if params.append else "overwrite",
                },
            )

        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(
                ok=False,
                content=f"保存失败: {exc}",
                error="save_failed",
            )


class SavePapersDedupParams(BaseModel):
    """save_papers_dedup 工具的参数。"""

    papers: list[dict[str, Any]] = Field(
        ...,
        description="去重后的论文列表",
    )
    append: bool = Field(
        default=False,
        description="是否追加模式（True=追加，False=覆盖）",
    )


class SavePapersDedupTool(Tool):
    """保存去重后的论文到 papers_dedup.jsonl。

    自动处理：
    1. 数据格式转换
    2. Schema 验证
    3. JSONL 序列化
    """

    name = "save_papers_dedup"
    description = (
        "保存去重后的论文列表到 literature/papers_dedup.jsonl。"
        "自动处理格式转换和 schema 验证。"
    )
    parameters_schema = SavePapersDedupParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        params = SavePapersDedupParams(**kwargs)

        try:
            # 1. 转换数据格式
            transformed_papers = []
            for i, paper in enumerate(params.papers):
                try:
                    transformed = _transform_to_papers_raw(paper)
                    # papers_dedup 需要额外的字段
                    transformed["relevance_score"] = paper.get("relevance_score", 0.0)
                    transformed["why_relevant"] = paper.get("why_relevant", "")
                    transformed["source_type"] = paper.get("source_type", "")
                    transformed_papers.append(transformed)
                except Exception as e:
                    return ToolResult(
                        ok=False,
                        content=f"❌ 数据转换失败（第 {i+1} 条记录）: {e}\n\n"
                        f"原始数据: {paper}",
                        error="transform_failed",
                    )

            # 2. Schema 验证
            for i, paper in enumerate(transformed_papers):
                ok, err = validate_record(paper, "papers_dedup")
                if not ok:
                    return ToolResult(
                        ok=False,
                        content=f"❌ Schema 验证失败（第 {i+1} 条记录）:\n\n{err}\n\n"
                        f"数据: {paper}",
                        error="schema_validation_failed",
                    )

            # 3. 序列化为 JSONL
            import json

            lines = []
            for paper in transformed_papers:
                line = json.dumps(paper, ensure_ascii=False)
                lines.append(line)

            content = "\n".join(lines) + "\n"

            # 4. 写入文件
            abs_path = self.policy.resolve_write("literature/papers_dedup.jsonl")
            abs_path.parent.mkdir(parents=True, exist_ok=True)

            if params.append and abs_path.exists():
                # 追加模式
                existing_ids = set()
                existing_lines = []
                for line in abs_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            existing = json.loads(line)
                            existing_ids.add(existing.get("id"))
                            existing_lines.append(line)
                        except json.JSONDecodeError:
                            pass

                # 添加新论文
                new_lines = []
                for paper in transformed_papers:
                    if paper["id"] not in existing_ids:
                        new_lines.append(json.dumps(paper, ensure_ascii=False))

                final_content = "\n".join(existing_lines + new_lines) + "\n"
                abs_path.write_text(final_content, encoding="utf-8")
            else:
                abs_path.write_text(content, encoding="utf-8")

            return ToolResult(
                ok=True,
                content=f"✅ 成功保存 {len(transformed_papers)} 篇去重后论文到 literature/papers_dedup.jsonl\n"
                f"（模式: {'追加' if params.append else '覆盖'}）",
                data={
                    "path": "literature/papers_dedup.jsonl",
                    "count": len(transformed_papers),
                    "mode": "append" if params.append else "overwrite",
                },
            )

        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(
                ok=False,
                content=f"保存失败: {exc}",
                error="save_failed",
            )
