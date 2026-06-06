from __future__ import annotations

"""Human-readable runtime timeline logger.

`_runtime/logs/researchos.log` is intentionally concise: one line per event,
with compact summaries rather than full prompts, full responses, or large JSON
payloads. Machine-level details remain in `_runtime/traces/*.jsonl`.
"""

from datetime import datetime
import json
from pathlib import Path
from typing import Any


SEARCH_TOOL_NAMES = frozenset(
    {
        "multi_source_search",
        "search_papers",
        "semantic_scholar_search",
        "arxiv_search",
        "openalex_search",
        "crossref_search",
        "elsevier_scopus_search",
        "informs_search",
        "fetch_outgoing_citations",
    }
)


class RunLogger:
    """Append compact timeline events to a workspace-local log file."""

    def __init__(
        self,
        workspace_dir: Path,
        *,
        runtime_dir_name: str = "_runtime",
        quiet: bool = False,
        verbose: bool = False,
    ) -> None:
        self.workspace_dir = Path(workspace_dir)
        self.log_path = self.workspace_dir / runtime_dir_name / "logs" / "researchos.log"
        self.quiet = bool(quiet)
        self.verbose = bool(verbose)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, event_type: str, **fields: Any) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        compact = " ".join(
            f"{key}={self._format_value(value)}"
            for key, value in fields.items()
            if value is not None and value != ""
        )
        line = f"{timestamp} | {event_type}"
        if compact:
            line += f" | {compact}"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def tool_call(self, tool_name: str, arguments: dict[str, Any], *, step: int | None = None) -> None:
        self.event(
            "TOOL_CALL",
            step=step,
            tool=tool_name,
            args=self._summarize_arguments(tool_name, arguments),
        )

    def tool_result(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        ok: bool,
        content: str | None,
        data: dict[str, Any] | None,
        error: str | None,
        duration_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
        step: int | None = None,
    ) -> None:
        summary = self._summarize_tool_result(
            tool_name=tool_name,
            arguments=arguments,
            ok=ok,
            content=content or "",
            data=data or {},
            error=error,
            metadata=metadata or {},
        )
        self.event(
            "TOOL_RESULT",
            step=step,
            tool=tool_name,
            ok=ok,
            duration_ms=duration_ms,
            **summary,
        )

    @staticmethod
    def _format_value(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, (dict, list, tuple, set)):
            text = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
        else:
            text = str(value)
        text = " ".join(text.split())
        if len(text) > 240:
            text = text[:237] + "..."
        if any(ch.isspace() for ch in text) or "|" in text:
            return json.dumps(text, ensure_ascii=False)
        return text

    @staticmethod
    def _summarize_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name in SEARCH_TOOL_NAMES:
            return {
                "query": arguments.get("query") or arguments.get("search_query") or "",
                "source": tool_name,
                "max": arguments.get("max_results") or arguments.get("per_page") or arguments.get("rows"),
                "bucket": arguments.get("query_bucket") or arguments.get("search_bucket"),
                "bridge": arguments.get("bridge_id"),
            }
        if tool_name in {"read_file", "write_file", "write_structured_file", "append_file"}:
            return {"path": arguments.get("path")}
        if tool_name == "ask_human":
            question = str(arguments.get("question") or "")
            return {"question": question[:120]}
        if tool_name == "finish_task":
            return {"summary": str(arguments.get("summary") or "")[:160]}
        return {
            key: value
            for key, value in arguments.items()
            if key in {"path", "paper_id", "work_id", "doi", "command", "action", "source"}
        }

    @staticmethod
    def _summarize_tool_result(
        *,
        tool_name: str,
        arguments: dict[str, Any],
        ok: bool,
        content: str,
        data: dict[str, Any],
        error: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        if tool_name in SEARCH_TOOL_NAMES:
            papers = data.get("papers")
            auto_persist = metadata.get("auto_persist_raw")
            if isinstance(auto_persist, dict):
                persisted_delta = auto_persist.get("count")
                merged_count = auto_persist.get("merged_count")
                retained_count = auto_persist.get("retained_count")
                append_status = "ok" if auto_persist.get("ok") else "raw_append_failed"
                raw_count_after = auto_persist.get("raw_count_after")
            else:
                persisted_delta = 0
                merged_count = 0
                retained_count = 0
                append_status = error or ("no_papers" if ok and not papers else "auto_persist_missing")
                raw_count_after = None
            reported_count = len(papers) if isinstance(papers, list) else data.get("count") or data.get("total") or 0
            if (
                isinstance(papers, list)
                and papers
                and (not isinstance(auto_persist, dict) or not auto_persist.get("ok"))
            ):
                append_status = "raw_persistence_mismatch"
            return {
                "query": data.get("query") or arguments.get("query") or "",
                "source": tool_name,
                "bucket": arguments.get("query_bucket") or arguments.get("search_bucket") or data.get("query_bucket"),
                "bridge": arguments.get("bridge_id") or data.get("bridge_id"),
                "reported_paper_count": reported_count,
                "persisted_raw_delta": persisted_delta,
                "merged_raw_count": merged_count,
                "retained_raw_count": retained_count,
                "raw_count_after": raw_count_after,
                "append_status": append_status,
                "error": error,
            }
        if tool_name == "save_papers_raw":
            return {
                "path": data.get("path"),
                "persisted_raw_delta": data.get("count"),
                "merged_raw_count": data.get("merged_count"),
                "valid_input_count": data.get("valid_input_count"),
                "skipped_count": data.get("skipped_count"),
                "append_status": "ok" if ok else error or "failed",
            }
        if tool_name in {"read_file", "write_file", "write_structured_file", "append_file"}:
            return {
                "path": data.get("path") or arguments.get("path"),
                "bytes": data.get("bytes") or data.get("size"),
                "error": error,
            }
        if tool_name == "finish_task":
            return {"finish_ack": ok, "error": error}
        if tool_name == "ask_human":
            return {"human_input": "received" if ok else error or "unavailable"}
        return {
            "status": "ok" if ok else "error",
            "error": error,
            "summary": content[:180],
        }
