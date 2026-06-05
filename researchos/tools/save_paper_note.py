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

from ..runtime.errors import ToolAccessDenied, ToolRuntimeError
from ..runtime.t3_notes_manifest import (
    build_t3_notes_manifest,
    find_queue_record_by_rank,
)
from ..literature_identity import record_note_id
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
        "自动生成 paper_notes 文件名、即时校验结构、刷新 literature/notes_manifest.json；"
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
        rel_path = f"literature/paper_notes/{note_id}.md"
        try:
            abs_path = self.policy.resolve_write(rel_path)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")

        existing_complete = False
        if abs_path.exists() and not params.allow_overwrite_complete:
            existing_complete, existing_err = _validate_note(abs_path)
            if existing_complete:
                manifest = build_t3_notes_manifest(self.policy.workspace_dir, write=True)
                return ToolResult(
                    ok=True,
                    content=(
                        f"{rel_path} 已存在且结构合格，未覆盖。"
                        f"notes_manifest 已刷新，complete_count={manifest.get('complete_count')}。"
                    ),
                    data={
                        "path": rel_path,
                        "queue_rank": params.queue_rank,
                        "source_queue": source_queue,
                        "status": "already_complete",
                        "validation_error": "",
                    },
                )

        try:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(params.content, encoding="utf-8")
        except OSError as exc:
            raise ToolRuntimeError("save_paper_note", exc) from exc

        ok, err = _validate_note(abs_path)
        manifest = build_t3_notes_manifest(self.policy.workspace_dir, write=True)
        entry = _find_manifest_entry(manifest, rel_path, params.queue_rank)
        data = {
            "path": rel_path,
            "queue_rank": params.queue_rank,
            "source_queue": source_queue,
            "status": "complete" if ok else "incomplete",
            "validation_error": err or "",
            "manifest_path": "literature/notes_manifest.json",
            "manifest_entry": entry,
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
            content=f"已保存并校验通过: {rel_path}；notes_manifest 已刷新。",
            data=data,
        )


def _validate_note(path: Path) -> tuple[bool, str | None]:
    try:
        from ..agents.reader import _validate_note_structure

        return _validate_note_structure(path)
    except Exception as exc:  # pragma: no cover - defensive fallback
        return False, f"{path.name} note validation crashed: {exc}"


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
    for entry in entries:
        if isinstance(entry, dict) and int(entry.get("queue_rank") or -1) == queue_rank:
            return entry
    return {}
