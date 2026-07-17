from __future__ import annotations

"""T3 paper note save tool.

The Reader LLM should not manually choose opaque note filenames. It supplies a
queue rank and markdown content; this tool resolves the paper record, writes the
canonical note path, validates the note immediately, and refreshes the T3 notes
manifest.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..paper_notes import compact_paper_note_view
from ..runtime.errors import ToolAccessDenied, ToolRuntimeError
from ..literature_citations import refresh_literature_citation_maps
from ..runtime.t3_notes_manifest import (
    build_t3_notes_manifest,
    find_queue_record_by_rank,
    load_jsonl,
)
from ..literature_identity import record_note_id
from ..literature_identity import display_record_key
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy


class SavePaperNoteParams(BaseModel):
    queue_rank: int = Field(..., ge=1, description="T3 deep-read queue rank shown to the Reader.")
    content: str = Field(..., min_length=1, description="Complete markdown note content for this paper.")
    queue_path: str = Field(
        default="auto",
        description="Queue JSONL path. Use auto unless explicitly repairing an old workspace.",
    )
    allow_overwrite_complete: bool = Field(
        default=False,
        description="By default, do not overwrite an existing structurally complete note.",
    )


class SavePaperNoteTool(Tool):
    name = "save_paper_note"
    description = (
        "按 queue_rank 保存 T3 paper note。工具从 deep_read_queue/pending queue 解析论文，"
        "自动生成 deep_read_notes 文件名、即时校验结构、刷新 literature/notes_manifest.json；"
        "Reader 不需要手写 normalized_id 或十六进制 ID。"
    )
    parameters_schema = SavePaperNoteParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = SavePaperNoteParams(**kwargs)
        record, source_queue = find_queue_record_by_rank(
            self.policy.workspace_dir,
            params.queue_rank,
            queue_path=params.queue_path,
        )
        if record is None:
            return ToolResult(
                ok=False,
                content=(
                    f"queue_rank={params.queue_rank} 不存在。请先读取 "
                    "literature/deep_read_queue_pending.jsonl 或 deep_read_queue.jsonl 确认队列。"
                ),
                error="queue_rank_not_found",
            )

        note_id = record_note_id(record)
        if not note_id:
            return ToolResult(
                ok=False,
                content=f"queue_rank={params.queue_rank} 的记录缺少可用 ID/title，无法生成 note 文件名。",
                error="missing_record_identity",
            )
        rel_path = _note_rel_path(record, note_id)
        try:
            abs_path = self.policy.resolve_write(rel_path)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")

        existing_complete = False
        is_new_note = not abs_path.exists()
        if abs_path.exists() and not params.allow_overwrite_complete:
            existing_complete, existing_err = _validate_note(abs_path)
            if existing_complete:
                manifest = _build_manifest_for_source_queue(self.policy.workspace_dir, source_queue)
                citation_maps = refresh_literature_citation_maps(self.policy.workspace_dir, write=True)
                progress = _progress_summary(manifest)
                entry = _find_manifest_entry(manifest, rel_path, params.queue_rank)
                note_status = _extract_note_status(_safe_read_text(abs_path))
                return ToolResult(
                    ok=True,
                    content=(
                        f"{rel_path} 已存在且结构合格，未覆盖。"
                        f"论文阅读笔记清单已更新；当前进度：{progress}。"
                    ),
                    data={
                        "path": rel_path,
                        "queue_rank": params.queue_rank,
                        "original_queue_rank": record.get("original_queue_rank") or record.get("queue_rank") or params.queue_rank,
                        "pending_queue_rank": record.get("pending_queue_rank") or params.queue_rank,
                        "source_queue": source_queue,
                        "resolved_paper_id": record.get("paper_id") or record.get("canonical_id") or record.get("id") or "",
                        "record_display_key": display_record_key(record),
                        "paper_title": _record_title(record, entry),
                        "paper_year": _record_year(record),
                        "paper_venue": _record_venue(record),
                        "target_bucket": str(record.get("target_bucket") or entry.get("target_bucket") or ""),
                        "note_status": note_status,
                        "status": "already_complete",
                        "validation_error": "",
                        "manifest_entry": entry,
                        "progress": progress,
                        "paper_note_index_path": "literature/paper_note_index.json",
                        "citation_map_path": "literature/citation_map.json",
                        "mapped_bib_count": (citation_maps.get("citation_map") or {}).get("mapped_bib_count", 0),
                        "compact_note_view": _compact_note_view(abs_path, self.policy.workspace_dir),
                    },
                )

        try:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(params.content, encoding="utf-8")
        except OSError as exc:
            raise ToolRuntimeError("save_paper_note", exc) from exc

        ok, err = _validate_note(abs_path, require_current_schema=is_new_note)
        manifest = _build_manifest_for_source_queue(self.policy.workspace_dir, source_queue)
        citation_maps = refresh_literature_citation_maps(self.policy.workspace_dir, write=True)
        entry = _find_manifest_entry(manifest, rel_path, params.queue_rank)
        progress = _progress_summary(manifest)
        data = {
            "path": rel_path,
            "queue_rank": params.queue_rank,
            "original_queue_rank": record.get("original_queue_rank") or record.get("queue_rank") or params.queue_rank,
            "pending_queue_rank": record.get("pending_queue_rank") or params.queue_rank,
            "source_queue": source_queue,
            "resolved_paper_id": record.get("paper_id") or record.get("canonical_id") or record.get("id") or "",
            "record_display_key": display_record_key(record),
            "paper_title": _record_title(record, entry),
            "paper_year": _record_year(record),
            "paper_venue": _record_venue(record),
            "target_bucket": str(record.get("target_bucket") or entry.get("target_bucket") or ""),
            "note_status": _extract_note_status(params.content),
            "status": "complete" if ok else "incomplete",
            "validation_error": err or "",
            "manifest_path": "literature/notes_manifest.json",
            "manifest_entry": entry,
            "progress": progress,
            "paper_note_index_path": "literature/paper_note_index.json",
            "citation_map_path": "literature/citation_map.json",
            "mapped_bib_count": (citation_maps.get("citation_map") or {}).get("mapped_bib_count", 0),
        }
        if not ok:
            return ToolResult(
                ok=False,
                content=(
                    f"已保存草稿到 {rel_path}，但 note 结构尚未合格：{err}。"
                    "请补齐缺失字段后再次调用 save_paper_note 保存同一 queue_rank。"
                ),
                data=data,
                error="note_incomplete",
            )
        return ToolResult(
            ok=True,
            content=f"论文阅读笔记已保存并通过校验：{rel_path}；当前进度：{progress}。",
            data={
                **data,
                "compact_note_view": _compact_note_view(abs_path, self.policy.workspace_dir),
            },
        )


def _validate_note(path: Path, *, require_current_schema: bool = False) -> tuple[bool, str | None]:
    try:
        from ..agents.reader import _validate_note_structure

        return _validate_note_structure(path, require_current_schema=require_current_schema)
    except Exception as exc:  # pragma: no cover - defensive fallback
        return False, f"{path.name} note validation crashed: {exc}"


def _compact_note_view(path: Path, workspace_dir: Path) -> dict[str, Any]:
    """Return a bounded researcher-facing note view without affecting validation.

    The detailed Markdown note remains the durable evidence record. A compact
    extraction failure must never turn a successfully validated note save into
    a failed tool call, so this helper degrades to an empty view.
    """

    try:
        return compact_paper_note_view(path, workspace_dir=workspace_dir).model_dump(mode="json")
    except (OSError, ValueError):
        return {}


def _note_rel_path(record: dict[str, Any], note_id: str) -> str:
    bridge_id = str(record.get("bridge_id") or "").strip()
    target_bucket = str(record.get("target_bucket") or "").strip()
    core_passed = bool(record.get("core_screen_passed"))
    # Cross-domain material has its own knowledge track. A bounded Reader
    # probe is still bridge material even when it has not been admitted as a
    # mainline deep-read paper, so it must not disappear into core notes.
    if bridge_id and target_bucket in {"bridge_deep", "bridge_probe"} and not core_passed:
        safe_bridge_id = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in bridge_id).strip("._-")
        safe_bridge_id = safe_bridge_id or "unknown_bridge"
        return f"literature/bridge_notes/{safe_bridge_id}/{note_id}.md"
    return f"literature/deep_read_notes/{note_id}.md"


def _build_manifest_for_source_queue(workspace_dir: Path, source_queue: str) -> dict[str, Any]:
    """Refresh notes manifest against the queue that was just consumed.

    On resume, Reader sees ``deep_read_queue_pending.jsonl`` where rank 1 means
    "first unfinished paper", not rank 1 of the original full queue. Building
    the manifest against the same source queue keeps tool feedback and pending
    progress coherent. T3 final validation still passes the full queue
    explicitly when it needs full-task completion accounting.
    """

    queue_path = workspace_dir / source_queue if source_queue else None
    if queue_path is not None and queue_path.exists() and queue_path.is_file():
        return build_t3_notes_manifest(
            workspace_dir,
            queue_records=load_jsonl(queue_path),
            source_queue=source_queue,
            write=True,
        )
    return build_t3_notes_manifest(workspace_dir, write=True)


def _find_manifest_entry(
    manifest: dict[str, Any],
    rel_path: str,
    queue_rank: int,
) -> dict[str, Any]:
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        return {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("note_path") == rel_path:
            return entry
        if rel_path in (entry.get("matched_note_paths") or []):
            return entry
    for entry in entries:
        if isinstance(entry, dict) and int(entry.get("queue_rank") or -1) == queue_rank:
            return entry
    return {}


def _progress_summary(manifest: dict[str, Any]) -> str:
    target_total = int(manifest.get("target_entry_count") or 0)
    target_done = int(manifest.get("target_complete_count") or 0)
    if target_total > 0:
        return f"{target_done}/{target_total} target notes complete"
    return f"{int(manifest.get('complete_count') or 0)}/{int(manifest.get('entry_count') or 0)} queue notes complete"


def _record_title(record: dict[str, Any], entry: dict[str, Any] | None = None) -> str:
    entry = entry if isinstance(entry, dict) else {}
    return str(record.get("title") or entry.get("title") or "").strip()


def _record_year(record: dict[str, Any]) -> str:
    for key in ("year", "publication_year", "published_year"):
        value = record.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _record_venue(record: dict[str, Any]) -> str:
    for key in ("venue", "journal", "conference", "source_display_name", "source", "publication_venue"):
        value = record.get(key)
        if isinstance(value, dict):
            value = value.get("display_name") or value.get("name")
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _extract_note_status(content: str | None) -> str:
    if not content:
        return ""
    for line in content.splitlines():
        if "**Status**" not in line:
            continue
        _, _, tail = line.partition(":")
        status = tail.strip() if tail else line.strip()
        return status.strip("[] ").upper()
    return ""


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
