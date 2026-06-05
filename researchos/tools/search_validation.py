from __future__ import annotations

"""Mechanical validation helpers for literature search tools."""

from typing import Any

from .base import ToolResult


def clean_search_query(value: Any) -> str:
    """Collapse whitespace and reject blank search requests at tool boundary."""

    return " ".join(str(value or "").split())


def empty_query_result(tool_name: str, raw_query: Any) -> ToolResult:
    return ToolResult(
        ok=False,
        content=(
            f"{tool_name} query 不能为空。请先基于 project.yaml、seed papers/ideas "
            "和 Scout LLM 的 domain_profile 设计具体检索式。"
        ),
        error="empty_query",
        data={"query": raw_query},
    )


def is_usable_paper_metadata(record: dict[str, Any]) -> bool:
    """Return whether a search hit has enough metadata to enter raw pool.

    This only filters empty/shell records, for example Crossref DOI entries
    without title. It is not a relevance filter.
    """

    title = str(record.get("title") or "").strip()
    if not title:
        return False
    if title.casefold() in {"unknown", "untitled"}:
        return False
    return True


def filter_usable_papers(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if is_usable_paper_metadata(record)]
