from __future__ import annotations

"""文献记录查找工具。

T3 Reader 经常只需要某一篇论文的 metadata。如果直接 read_file 读取
`papers_verified.jsonl`，会把整份 100KB+ JSONL 塞进上下文。这个工具按 ID /
标题逐行扫描并只返回匹配记录，作为更稳的上下文入口。
"""

from collections.abc import Iterator
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from ..runtime.errors import ToolAccessDenied
from ..runtime.t3_notes_manifest import find_queue_record_by_rank
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy


DEFAULT_PAPER_RECORD_SOURCES = [
    "literature/papers_verified.jsonl",
    "literature/deep_read_queue_pending.jsonl",
    "literature/deep_read_queue.jsonl",
    "literature/papers_dedup.jsonl",
]


class LookupPaperRecordParams(BaseModel):
    queue_rank: int | None = Field(
        default=None,
        ge=1,
        description="T3 队列序号；提供后工具直接从 pending/full deep_read_queue 查对应论文",
    )
    paper_id: str | None = Field(
        default=None,
        description="论文 ID，可为 DOI、arXiv、OpenAlex ID 或 normalized_id 文件名形式",
    )
    title: str | None = Field(default=None, description="论文标题；paper_id 缺失时用于匹配")
    sources: list[str] | None = Field(
        default=None,
        description="可选 JSONL 来源路径；默认查 verified、pending queue、full queue、dedup",
    )
    max_abstract_chars: int = Field(
        default=1500,
        ge=200,
        le=5000,
        description="返回摘要的最大字符数",
    )

    @model_validator(mode="after")
    def _require_lookup_key(self) -> "LookupPaperRecordParams":
        if self.queue_rank is None and not (self.paper_id or "").strip() and not (self.title or "").strip():
            raise ValueError("queue_rank、paper_id 或 title 至少提供一个")
        return self


class LookupPaperRecordTool(Tool):
    """按 ID / 标题从 T2 文献产物中查一篇论文记录。"""

    name = "lookup_paper_record"
    description = (
        "按 paper_id 或 title 从 literature/papers_verified.jsonl、deep_read_queue*.jsonl "
        "和 papers_dedup.jsonl 中查单篇论文 metadata，避免读取整份 JSONL。"
    )
    parameters_schema = LookupPaperRecordParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = LookupPaperRecordParams(**kwargs)
        if params.queue_rank is not None:
            record, source = find_queue_record_by_rank(self.policy.workspace_dir, params.queue_rank)
            if record is None:
                return ToolResult(
                    ok=True,
                    content=f"No matching paper record found for queue_rank={params.queue_rank}.",
                    data={
                        "found": False,
                        "query": {"queue_rank": params.queue_rank},
                        "scanned": 0,
                        "skipped_sources": [],
                    },
                )
            content = _format_record(record, [(source or "queue_rank", record)])
            return ToolResult(
                ok=True,
                content=content,
                data={
                    "found": True,
                    "record": record,
                    "matched_sources": [source],
                    "match_count": 1,
                    "scanned": params.queue_rank,
                    "query": {"queue_rank": params.queue_rank},
                },
            )
        source_paths = params.sources or DEFAULT_PAPER_RECORD_SOURCES
        query_ids = _identifier_variants(params.paper_id or "")
        query_title = _normalize_title(params.title or "")

        matches: list[tuple[str, dict[str, Any]]] = []
        skipped_sources: list[str] = []
        scanned = 0

        for rel_path in source_paths:
            try:
                abs_path = self.policy.resolve_read(rel_path)
            except ToolAccessDenied as exc:
                return ToolResult(ok=False, content=str(exc), error="access_denied")
            if not abs_path.exists() or not abs_path.is_file():
                skipped_sources.append(rel_path)
                continue
            for record in _iter_jsonl(abs_path):
                scanned += 1
                if _record_matches(record, query_ids=query_ids, query_title=query_title):
                    matches.append((rel_path, record))

        if not matches:
            return ToolResult(
                ok=True,
                content=(
                    "No matching paper record found. "
                    f"scanned={scanned}, skipped_sources={skipped_sources}"
                ),
                data={
                    "found": False,
                    "scanned": scanned,
                    "skipped_sources": skipped_sources,
                    "query": {"paper_id": params.paper_id, "title": params.title},
                },
            )

        merged = _merge_records(matches)
        abstract = str(merged.get("abstract") or "").strip()
        if len(abstract) > params.max_abstract_chars:
            merged["abstract"] = (
                abstract[: params.max_abstract_chars]
                + f"\n[... abstract truncated, full length: {len(abstract)} chars]"
            )

        content = _format_record(merged, matches)
        return ToolResult(
            ok=True,
            content=content,
            data={
                "found": True,
                "record": merged,
                "matched_sources": [source for source, _ in matches],
                "match_count": len(matches),
                "scanned": scanned,
            },
        )


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                yield item


def _record_matches(
    record: dict[str, Any],
    *,
    query_ids: set[str],
    query_title: str,
) -> bool:
    if query_ids and _record_identifier_variants(record) & query_ids:
        return True
    if query_title:
        title = _normalize_title(str(record.get("title") or ""))
        return bool(title and (title == query_title or query_title in title or title in query_title))
    return False


def _record_identifier_variants(record: dict[str, Any]) -> set[str]:
    external_ids = record.get("externalIds") if isinstance(record.get("externalIds"), dict) else {}
    candidates = [
        record.get("paper_id"),
        record.get("normalized_id"),
        record.get("id"),
        record.get("canonical_id"),
        record.get("doi"),
        record.get("url"),
        external_ids.get("DOI") if external_ids else None,
        external_ids.get("ArXiv") if external_ids else None,
        external_ids.get("OpenAlex") if external_ids else None,
    ]
    out: set[str] = set()
    for candidate in candidates:
        out.update(_identifier_variants(str(candidate or "")))
    return out


def _identifier_variants(value: str) -> set[str]:
    raw = value.strip()
    if not raw:
        return set()
    lowered = raw.casefold()
    stripped = lowered
    for prefix in ("doi:", "https://doi.org/", "http://doi.org/", "https://dx.doi.org/"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break
    if stripped.startswith("arxiv:"):
        stripped_no_arxiv = stripped.removeprefix("arxiv:")
    else:
        stripped_no_arxiv = stripped
    return {
        lowered,
        stripped,
        stripped_no_arxiv,
        _safe_id(lowered),
        _safe_id(stripped),
        _safe_id(stripped_no_arxiv),
        _compact_id(lowered),
        _compact_id(stripped),
        _compact_id(stripped_no_arxiv),
    }


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "_", value.casefold()).strip("_")


def _compact_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _merge_records(matches: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for source, record in matches:
        for key, value in record.items():
            if value in (None, "", [], {}):
                continue
            if key not in merged or merged[key] in (None, "", [], {}):
                merged[key] = value
        merged.setdefault("_lookup_sources", []).append(source)
    return merged


def _format_record(record: dict[str, Any], matches: list[tuple[str, dict[str, Any]]]) -> str:
    lines = [
        "Paper record found.",
        f"- matched_sources: {', '.join(dict.fromkeys(source for source, _ in matches))}",
        f"- id: {record.get('paper_id') or record.get('canonical_id') or record.get('id') or ''}",
        f"- normalized_id: {record.get('normalized_id') or ''}",
        f"- title: {record.get('title') or ''}",
        f"- year: {record.get('year') or ''}",
        f"- venue: {record.get('venue') or ''}",
        f"- doi: {record.get('doi') or ''}",
        f"- source: {record.get('source') or ''}",
        f"- verification: {record.get('verification_status') or ''} confidence={record.get('verification_confidence') or ''}",
        f"- access/evidence: {record.get('evidence_level') or ''} access={record.get('access_score') or record.get('access_score_estimate') or ''}",
    ]
    abstract = str(record.get("abstract") or "").strip()
    if abstract:
        lines.extend(["", "Abstract:", abstract])
    return "\n".join(lines)
