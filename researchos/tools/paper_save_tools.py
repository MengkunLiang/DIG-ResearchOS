"""论文数据保存工具。

提供专门用于保存论文数据的工具，支持：
1. 流式写入：LLM 检索到论文后立即追加原始数据（不转换）
2. 批量处理：一次性转换和验证所有论文数据

支持从多种搜索源（Semantic Scholar、arXiv、OpenAlex）的数据格式转换为 papers_raw schema。
"""

from __future__ import annotations

from datetime import datetime, timezone
import html
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..literature_identity import normalize_openalex_work_id, stable_noopenalex_id
from ..schemas.validator import validate_record
from .abstract_utils import clean_abstract
from .base import Tool, ToolResult
from .search_validation import is_usable_paper_metadata
from .workspace_policy import ToolAccessDenied, WorkspaceAccessPolicy
from ..runtime.errors import ToolRuntimeError


def _now_iso() -> str:
    """返回统一的 UTC 时间戳字符串。"""

    return datetime.now(timezone.utc).isoformat()


def _normalize_authors(authors: Any) -> list[str]:
    """标准化 authors 字段为字符串列表。

    处理多种输入格式：
    - 单个字符串: "John Doe"
    - 字符串列表: ["John Doe", "Jane Smith"]
    - 对象列表: [{"name": "John Doe"}]
    - 混合格式
    """
    if not authors:
        return []

    if isinstance(authors, str):
        name = authors.strip()
        return [name] if name else []
    if not isinstance(authors, list):
        name = str(authors).strip()
        return [name] if name else []

    result: list[str] = []
    for author in authors:
        if isinstance(author, str):
            name = author.strip()
            if name:
                result.append(name)
        elif isinstance(author, dict):
            # 尝试多个可能的 name 字段
            name = (
                author.get("name")
                or author.get("display_name")
                or author.get("author_name")
                or author.get("full_name")
                or ""
            )
            if name:
                result.append(str(name).strip())
        else:
            name = str(author).strip()
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


def _first_text(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return html.unescape(value.strip())
    if isinstance(value, list):
        for item in value:
            text = _first_text(item)
            if text:
                return text
        return default
    if value is None:
        return default
    return html.unescape(str(value).strip())


def _extract_year_from_paper(paper: dict[str, Any]) -> int | None:
    """Extract publication year across common API payload shapes."""

    for key in ("year", "publication_year", "published_year", "pubYear"):
        year = _normalize_year(paper.get(key))
        if year is not None:
            return year
    for key in ("published", "published-print", "published-online", "issued", "created"):
        value = paper.get(key)
        if isinstance(value, dict):
            parts = value.get("date-parts")
            if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
                year = _normalize_year(parts[0][0])
                if year is not None:
                    return year
        else:
            year = _normalize_year(value)
            if year is not None:
                return year
    return None


def _infer_source(paper: dict[str, Any]) -> str:
    """Infer a stable source label when an upstream API omits `source`."""

    source = str(paper.get("source") or "").strip()
    if source and source != "unknown":
        return source

    external_ids = paper.get("externalIds") or {}
    provenance = paper.get("provenance") if isinstance(paper.get("provenance"), dict) else {}
    provenance_source = str(provenance.get("source_tool") or "").strip()
    if provenance_source and provenance_source != "unknown":
        return provenance_source
    url = str(paper.get("url") or provenance.get("source_url") or "").lower()
    raw_id = str(paper.get("id") or paper.get("paperId") or provenance.get("source_id") or "")

    if paper.get("paperId") or "semanticscholar.org" in url:
        return "semantic_scholar"
    if external_ids.get("ArXiv") or str(raw_id).lower().startswith("arxiv:"):
        return "arxiv"
    if "openalex.org" in url or str(raw_id).startswith("https://openalex.org/"):
        return "openalex"
    if str(paper.get("doi") or external_ids.get("DOI") or "").startswith("10.1287/"):
        return "informs_crossref"
    return source or "unknown"


def _normalize_arxiv_identifier(value: str) -> str:
    """把各种 arXiv 表示统一成 `arxiv:<id>`。"""

    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if normalized.startswith("arxiv:"):
        return normalized
    if normalized.startswith("http"):
        tail = normalized.rstrip("/").split("/")[-1]
        if tail.endswith(".pdf"):
            tail = tail[:-4]
        return f"arxiv:{tail}"
    return f"arxiv:{normalized}"


def _select_preferred_paper_id(paper: dict[str, Any]) -> tuple[str, str]:
    """选择稳定的主 ID。

    设计约束：
    - 对开放论文优先用 arXiv ID，便于 PDF 抓取与文件命名；
    - 已有上游 source id 时保留它，避免 raw 记录在保存时突然换主键；
    - 只有缺少 source id 时才回退到 DOI。
    """

    external_ids = paper.get("externalIds") or {}
    arxiv_id = (
        external_ids.get("ArXiv")
        or paper.get("arxiv_id")
        or (paper.get("id") if str(paper.get("source", "")).lower() == "arxiv" else "")
    )
    if str(arxiv_id or "").strip():
        return _normalize_arxiv_identifier(str(arxiv_id)), "arxiv"

    doi = str(paper.get("doi") or external_ids.get("DOI") or "").strip()
    if doi and not str(paper.get("id") or paper.get("paperId") or "").strip():
        return doi.replace("https://doi.org/", "").replace("http://doi.org/", ""), "doi"

    raw_id = str(paper.get("id") or paper.get("paperId") or "").strip()
    if raw_id:
        return raw_id, "source_id"

    return "", "missing"


def _select_canonical_literature_id(paper: dict[str, Any], readable_id: str) -> tuple[str, str, bool]:
    """Select the graph/linkage id used across T2 artifacts.

    ``id`` remains a readable source identifier for humans and PDF fetchers.
    ``canonical_id`` is the association key. It must not silently fall back to
    a raw title because citation graph edges cannot align against titles.
    """

    external_ids = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
    provenance = paper.get("provenance") if isinstance(paper.get("provenance"), dict) else {}
    for candidate in (
        paper.get("canonical_id"),
        paper.get("openalex_id"),
        external_ids.get("OpenAlex"),
        provenance.get("canonical_id"),
        provenance.get("source_url"),
        paper.get("url"),
        paper.get("id"),
        readable_id,
    ):
        work_id = normalize_openalex_work_id(candidate)
        if work_id:
            return work_id, "openalex", False
    if str(readable_id or "").startswith("arxiv:"):
        return readable_id, "arxiv_noopenalex", True
    doi = str(paper.get("doi") or external_ids.get("DOI") or "").strip()
    if doi:
        doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").removeprefix("doi:")
        return f"doi:{doi}", "doi_noopenalex", True
    fallback = stable_noopenalex_id({**paper, "id": readable_id})
    return fallback, "noopenalex_fallback", True


def _ensure_provenance(
    paper: dict[str, Any],
    *,
    canonical_id: str,
    id_source: str,
    source_tool: str,
) -> dict[str, Any]:
    """补齐 provenance，保证后续真实性审计有最小可追溯信息。"""

    provenance = paper.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}

    # 这里补的是最小追溯骨架；如果上游搜索工具已经写了更详细 provenance，就保留。
    if not str(provenance.get("source_tool") or "").strip():
        provenance["source_tool"] = source_tool
    provenance.setdefault("source_id", str(paper.get("id") or paper.get("paperId") or "").strip())
    provenance.setdefault("source_url", str(paper.get("url", "")).strip())
    provenance.setdefault("canonical_id", canonical_id)
    provenance.setdefault("id_source", id_source)
    provenance.setdefault("fetched_at", _now_iso())
    for key in ("source_query", "source_tool", "query_bucket", "search_bucket", "bridge_id"):
        value = paper.get(key)
        if value not in (None, "", []):
            provenance.setdefault(key, value)
    return provenance


def _transform_to_papers_raw(paper: dict[str, Any]) -> dict[str, Any]:
    """将各种格式的论文数据转换为 papers_raw schema。

    处理来自不同搜索源的格式差异：
    - Semantic Scholar: authors=[{name: "..."}], citationCount, externalIds
    - arXiv: authors=[{name: "..."}], citationCount=0
    - OpenAlex: authors=["..."], citation_count
    """
    # 统一主 ID，避免同一篇论文在不同来源下反复变换标识。
    paper_id, id_source = _select_preferred_paper_id(paper)
    canonical_id, canonical_id_source, no_openalex_id = _select_canonical_literature_id(paper, paper_id)
    if not paper_id:
        paper_id = canonical_id
        id_source = "canonical_fallback"

    # 提取 source
    source = _infer_source(paper)

    # 标准化 authors
    authors = _normalize_authors(paper.get("authors", []))

    # 标准化引用数
    citation_count = _normalize_citation_count(
        paper.get("citation_count") or paper.get("citationCount", 0)
    )

    # 标准化年份
    year = _extract_year_from_paper(paper)

    # 提取 abstract
    abstract = clean_abstract(paper.get("abstract"))

    # 提取 URL
    url = _first_text(paper.get("url") or paper.get("id", ""))

    # 提取 DOI。很多源（尤其 Semantic Scholar）只放在 externalIds.DOI；
    # 必须提升到顶层，后续 dedup/PDF fetch/OpenAlex/Crossref backfill 才能统一识别。
    external_ids = paper.get("externalIds") or {}
    doi = str(paper.get("doi") or external_ids.get("DOI") or "").strip()
    if doi.startswith("https://doi.org/"):
        doi = doi.replace("https://doi.org/", "")
    if doi.startswith("http://doi.org/"):
        doi = doi.replace("http://doi.org/", "")
    if doi.startswith("doi:"):
        doi = doi.removeprefix("doi:")
    provenance = _ensure_provenance(
        paper,
        canonical_id=canonical_id,
        id_source=canonical_id_source,
        source_tool=source,
    )

    return {
        "id": paper_id,
        "canonical_id": canonical_id,
        "preferred_id_source": id_source,
        "canonical_id_source": canonical_id_source,
        "no_openalex_id": no_openalex_id,
        "source": source,
        "title": _first_text(paper.get("title"), "Unknown"),
        "authors": authors,
        "year": year,
        "abstract": abstract,
        "venue": _first_text(paper.get("venue")),
        "citation_count": citation_count,
        "doi": doi,
        "url": url,
        "externalIds": external_ids,
        "provenance": provenance,
        **_passthrough_raw_annotations(paper),
    }


def _passthrough_raw_annotations(paper: dict[str, Any]) -> dict[str, Any]:
    """Preserve non-claim routing annotations supplied by Scout/runtime."""

    allowed = (
        "referenced_works",
        "related_works",
        "references",
        "refs_unavailable",
        "reference_count",
        "retrieval_intent",
        "bridge_id",
        "search_bucket",
        "query_bucket",
        "source_bucket",
        "adjacent_field",
        "source_query",
        "source_tool",
        "citation_snowball_source_id",
        "citation_snowball_source_title",
        "fallback_source",
        "llm_annotation_applied",
        "domain_tags",
        "semantic_screen",
        "has_seed_pdf",
        "seed_pdf_path",
        "access_level_hint",
        "access_score",
        "access_score_estimate",
        "pdf_url",
        "open_access_pdf_url",
        "oa_pdf_url",
        "best_pdf_url",
        "full_text_url",
        "pmc_pdf_url",
        "url_for_pdf",
        "landing_page_url",
        "openAccessPdf",
        "open_access_pdf",
        "oa_pdf",
        "open_access",
        "best_oa_location",
        "primary_location",
        "locations",
        "oa_locations",
        "open_access_locations",
        "openAccessLocations",
        "open_access_pdfs",
        "pdf_urls",
        "open_access_pdf_urls",
        "oa_pdf_urls",
        "best_pdf_urls",
        "full_text_urls",
        "pmc_pdf_urls",
        "url_for_pdfs",
        "landing_page_urls",
        "source_queries",
        "source_tools",
        "search_buckets",
        "query_buckets",
        "source_buckets",
        "citation_snowball_source_ids",
        "citation_snowball_source_titles",
        "recalled_by_bridges",
        "contributed_bridges",
    )
    annotations: dict[str, Any] = {}
    for key in allowed:
        value = paper.get(key)
        if value not in (None, "", []):
            annotations[key] = value
    return annotations


def _raw_record_identity_keys(record: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    external_ids = record.get("externalIds") if isinstance(record.get("externalIds"), dict) else {}
    for key, value in (
        ("id", record.get("id")),
        ("canonical", record.get("canonical_id")),
        ("doi", record.get("doi") or external_ids.get("DOI")),
        ("arxiv", external_ids.get("ArXiv")),
    ):
        text = str(value or "").strip().casefold()
        if not text:
            continue
        text = text.removeprefix("https://doi.org/").removeprefix("http://doi.org/").removeprefix("doi:")
        keys.append(f"{key}:{text}")
    title = " ".join(str(record.get("title") or "").casefold().split())
    if title and title not in {"unknown", "untitled"}:
        keys.append(f"title:{title}")
    return keys


def _append_unique_scalar(record: dict[str, Any], key: str, value: Any) -> None:
    values = record.get(key)
    if not isinstance(values, list):
        values = []
    candidates = value if isinstance(value, (list, tuple, set)) else [value]
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text and text not in values:
            values.append(text)
    if values:
        record[key] = values


def _dedupe_list_payload(items: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for item in items:
        key = str(item)
        if isinstance(item, dict):
            key = str(item.get("doi") or item.get("id") or item.get("url") or item.get("title") or item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _merge_raw_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge duplicate raw records so later bridge/citation/PDF provenance is not lost."""

    merged = dict(existing)
    for key, value in incoming.items():
        if value in (None, "", [], {}):
            continue
        current = merged.get(key)
        if key in {"bridge_id", "search_bucket", "query_bucket", "source_bucket", "source_query", "source_tool"}:
            plural_key = {
                "bridge_id": "recalled_by_bridges",
                "search_bucket": "search_buckets",
                "query_bucket": "query_buckets",
                "source_bucket": "source_buckets",
                "source_query": "source_queries",
                "source_tool": "source_tools",
            }[key]
            if current not in (None, "", [], {}):
                _append_unique_scalar(merged, plural_key, current)
            _append_unique_scalar(merged, plural_key, value)
            if current in (None, "", [], {}):
                merged[key] = value
            if key == "bridge_id":
                if current not in (None, "", [], {}):
                    _append_unique_scalar(merged, "recalled_by_bridges", current)
                _append_unique_scalar(merged, "recalled_by_bridges", value)
            continue
        if key in {"citation_snowball_source_id", "citation_snowball_source_title"}:
            _append_unique_scalar(merged, f"{key}s", current)
            _append_unique_scalar(merged, f"{key}s", value)
            continue
        if key in {
            "pdf_url",
            "open_access_pdf_url",
            "oa_pdf_url",
            "best_pdf_url",
            "full_text_url",
            "pmc_pdf_url",
            "url_for_pdf",
            "landing_page_url",
        }:
            if current not in (None, "", [], {}):
                _append_unique_scalar(merged, f"{key}s", current)
            _append_unique_scalar(merged, f"{key}s", value)
            if current in (None, "", [], {}):
                merged[key] = value
            continue
        if key in {
            "openAccessPdf",
            "open_access_pdf",
            "oa_pdf",
            "open_access",
            "best_oa_location",
            "primary_location",
        } and isinstance(current, dict) and isinstance(value, dict):
            merged[key] = {**current, **{k: v for k, v in value.items() if v not in (None, "", [], {})}}
            continue
        if current in (None, "", [], {}):
            merged[key] = value
            continue
        if key == "abstract":
            if len(str(value)) > len(str(current)):
                merged[key] = value
        elif key == "citation_count":
            try:
                merged[key] = max(int(current or 0), int(value or 0))
            except (TypeError, ValueError):
                pass
        elif key in {"externalIds", "provenance"} and isinstance(current, dict) and isinstance(value, dict):
            merged[key] = {**current, **{k: v for k, v in value.items() if v not in (None, "", [], {})}}
        elif key in {
            "references",
            "referenced_works",
            "related_works",
            "locations",
            "oa_locations",
            "open_access_locations",
            "openAccessLocations",
            "open_access_pdfs",
            "recalled_by_bridges",
            "contributed_bridges",
        } and isinstance(current, list) and isinstance(value, list):
            merged[key] = _dedupe_list_payload([*current, *value])
        elif key == "source":
            sources = [item for item in str(current).split("+") if item]
            incoming_source = str(value)
            if incoming_source and incoming_source not in sources:
                sources.append(incoming_source)
                merged[key] = "+".join(sources)
    return merged


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

    LLM 检索到论文后立即调用此工具追加到文件。工具会先把上游异构
    metadata 规范化为 papers_raw schema，再按 DOI/OpenAlex/arXiv/title
    合并重复记录，避免 raw 文件写入 dict authors、list title 等后续阶段
    无法处理的格式。

    流程：
    1. LLM 调用搜索 API
    2. LLM 立即调用 append_papers_raw 追加结果
    3. 重复步骤 1-2 直到检索完成
    4. 最后调用 finish_task，由 runtime deterministic finalize 生成 T2 产物

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
        "会规范化为 papers_raw schema 并合并重复 provenance。"
        "LLM 检索到论文后立即调用，专注检索不手工处理数据。"
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

            existing_records: list[dict[str, Any]] = []
            index: dict[str, int] = {}
            if abs_path.exists():
                for line in abs_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        existing = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(existing, dict):
                        continue
                    record_index = len(existing_records)
                    existing_records.append(existing)
                    for key in _raw_record_identity_keys(existing):
                        index.setdefault(key, record_index)

            transformed_papers: list[dict[str, Any]] = []
            skipped_records: list[dict[str, Any]] = []
            for idx, paper in enumerate(params.papers):
                try:
                    transformed = _transform_to_papers_raw(paper)
                except Exception as exc:
                    skipped_records.append({"index": idx, "reason": f"transform_failed: {exc}"})
                    continue
                ok, err = validate_record(transformed, "papers_raw")
                if not ok:
                    skipped_records.append(
                        {
                            "index": idx,
                            "reason": f"schema_validation_failed: {err}",
                            "title": str(transformed.get("title") or "")[:200],
                            "id": str(transformed.get("id") or transformed.get("doi") or "")[:200],
                        }
                    )
                    continue
                transformed_papers.append(transformed)

            if not transformed_papers:
                return ToolResult(
                    ok=False,
                    content="没有可追加的有效 papers_raw 记录",
                    error="no_valid_papers",
                    data={"skipped_records": skipped_records[:20], "skipped_count": len(skipped_records)},
                )

            appended_count = 0
            merged_count = 0
            for paper in transformed_papers:
                keys = _raw_record_identity_keys(paper)
                existing_index = next((index[key] for key in keys if key in index), None)
                if existing_index is None:
                    record_index = len(existing_records)
                    existing_records.append(paper)
                    for key in keys:
                        index.setdefault(key, record_index)
                    appended_count += 1
                    continue
                existing_records[existing_index] = _merge_raw_records(existing_records[existing_index], paper)
                for key in _raw_record_identity_keys(existing_records[existing_index]):
                    index.setdefault(key, existing_index)
                merged_count += 1

            abs_path.write_text(
                "\n".join(json.dumps(paper, ensure_ascii=False) for paper in existing_records)
                + ("\n" if existing_records else ""),
                encoding="utf-8",
            )
            return ToolResult(
                ok=True,
                content=(
                    f"✅ 追加 {appended_count} 篇论文到 literature/papers_raw.jsonl"
                    f"（合并重复 {merged_count} 条，跳过无效 {len(skipped_records)} 条）"
                ),
                data={
                    "path": "literature/papers_raw.jsonl",
                    "count": appended_count,
                    "merged_count": merged_count,
                    "valid_input_count": len(transformed_papers),
                    "skipped_count": len(skipped_records),
                    "skipped_records": skipped_records[:20],
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
            skipped_records: list[dict[str, Any]] = []
            for i, paper in enumerate(params.papers):
                try:
                    transformed = _transform_to_papers_raw(paper)
                except Exception as e:
                    skipped_records.append(
                        {
                            "index": i + 1,
                            "reason": f"transform_failed: {e}",
                            "title": str(paper.get("title") or "")[:200],
                            "id": str(paper.get("id") or paper.get("doi") or "")[:200],
                        }
                    )
                    continue

                if not is_usable_paper_metadata(transformed):
                    skipped_records.append(
                        {
                            "index": i + 1,
                            "reason": "metadata_hygiene_failed: missing_or_unknown_title",
                            "title": str(transformed.get("title") or "")[:200],
                            "id": str(transformed.get("id") or transformed.get("doi") or "")[:200],
                        }
                    )
                    continue

                ok, err = validate_record(transformed, "papers_raw")
                if not ok:
                    skipped_records.append(
                        {
                            "index": i + 1,
                            "reason": f"schema_validation_failed: {err}",
                            "title": str(transformed.get("title") or "")[:200],
                            "id": str(transformed.get("id") or transformed.get("doi") or "")[:200],
                        }
                    )
                    continue
                transformed_papers.append(transformed)

            if not transformed_papers:
                return ToolResult(
                    ok=False,
                    content=(
                        "❌ 没有可保存的有效论文记录；所有记录都因转换或 schema 校验失败被跳过。"
                    ),
                    error="no_valid_papers",
                    data={
                        "path": "literature/papers_raw.jsonl",
                        "count": 0,
                        "skipped_count": len(skipped_records),
                        "skipped_records": skipped_records[:20],
                    },
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
                # 追加模式：读取现有数据；重复论文合并 provenance，而不是静默跳过。
                import json

                existing_records: list[dict[str, Any]] = []
                index: dict[str, int] = {}
                for line in abs_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            existing = json.loads(line)
                            if not isinstance(existing, dict):
                                continue
                            record_index = len(existing_records)
                            existing_records.append(existing)
                            for key in _raw_record_identity_keys(existing):
                                index.setdefault(key, record_index)
                        except json.JSONDecodeError:
                            pass

                appended_count = 0
                merged_count = 0
                for paper in transformed_papers:
                    keys = _raw_record_identity_keys(paper)
                    existing_index = next((index[key] for key in keys if key in index), None)
                    if existing_index is None:
                        record_index = len(existing_records)
                        existing_records.append(paper)
                        for key in keys:
                            index.setdefault(key, record_index)
                        appended_count += 1
                        continue
                    existing_records[existing_index] = _merge_raw_records(existing_records[existing_index], paper)
                    for key in _raw_record_identity_keys(existing_records[existing_index]):
                        index.setdefault(key, existing_index)
                    merged_count += 1

                final_content = "\n".join(json.dumps(item, ensure_ascii=False) for item in existing_records) + "\n"
                abs_path.write_text(final_content, encoding="utf-8")
            else:
                # 覆盖模式
                abs_path.write_text(content, encoding="utf-8")
                appended_count = len(transformed_papers)
                merged_count = 0

            return ToolResult(
                ok=True,
                content=(
                    f"✅ 成功保存 {appended_count} 篇论文到 literature/papers_raw.jsonl\n"
                    f"（模式: {'追加' if params.append else '覆盖'}；"
                    f"有效输入 {len(transformed_papers)} 条，合并重复 {merged_count} 条，"
                    f"跳过坏记录 {len(skipped_records)} 条）"
                ),
                data={
                    "path": "literature/papers_raw.jsonl",
                    "count": appended_count,
                    "merged_count": merged_count,
                    "valid_input_count": len(transformed_papers),
                    "skipped_count": len(skipped_records),
                    "skipped_records": skipped_records[:20],
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
                    for key, value in paper.items():
                        if key not in transformed:
                            transformed[key] = value
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
                # 追加模式也必须合并同一论文的后续 metadata，避免旧调用路径
                # 静默跳过 abstract/PDF/references/semantic_screen 等增强字段。
                existing_records: list[dict[str, Any]] = []
                index: dict[str, int] = {}
                for line in abs_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            existing = json.loads(line)
                            if not isinstance(existing, dict):
                                continue
                            row = len(existing_records)
                            existing_records.append(existing)
                            for key in _raw_record_identity_keys(existing):
                                index.setdefault(key, row)
                        except json.JSONDecodeError:
                            pass

                appended_count = 0
                merged_count = 0
                for paper in transformed_papers:
                    keys = _raw_record_identity_keys(paper)
                    match_idx = next((index[key] for key in keys if key in index), None)
                    if match_idx is None:
                        match_idx = len(existing_records)
                        existing_records.append(paper)
                        appended_count += 1
                    else:
                        before = json.dumps(existing_records[match_idx], ensure_ascii=False, sort_keys=True)
                        existing_records[match_idx] = _merge_raw_records(existing_records[match_idx], paper)
                        after = json.dumps(existing_records[match_idx], ensure_ascii=False, sort_keys=True)
                        if after != before:
                            merged_count += 1
                    for key in _raw_record_identity_keys(existing_records[match_idx]):
                        index.setdefault(key, match_idx)

                final_content = "\n".join(json.dumps(item, ensure_ascii=False) for item in existing_records) + "\n"
                abs_path.write_text(final_content, encoding="utf-8")
            else:
                abs_path.write_text(content, encoding="utf-8")
                appended_count = len(transformed_papers)
                merged_count = 0

            return ToolResult(
                ok=True,
                content=f"✅ 成功保存 {len(transformed_papers)} 篇去重后论文到 literature/papers_dedup.jsonl\n"
                f"（模式: {'追加' if params.append else '覆盖'}；新增 {appended_count}，合并 {merged_count}）",
                data={
                    "path": "literature/papers_dedup.jsonl",
                    "count": appended_count,
                    "merged_count": merged_count,
                    "valid_input_count": len(transformed_papers),
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
