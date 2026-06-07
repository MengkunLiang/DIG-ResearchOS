from __future__ import annotations

"""T3 运行期恢复与剩余队列裁剪。

目标：
1. 兼容旧 workspace：即使没有 deep_read_queue / papers_verified，也能基于现有产物续跑；
2. 把已完成的 `paper_notes/*.md` 从工作清单里裁掉，避免 T3 重复阅读；
3. 为 Reader 额外生成一个“只包含未完成论文”的 pending queue。
"""

import json
from pathlib import Path
from typing import Any

from ..literature_identity import (
    is_paper_note_file,
    paper_note_match_keys,
    record_is_covered,
)
from ..agents._common import load_jsonl
from ..runtime.t2_config import load_deep_read_queue_config
from ..runtime.t3_notes_manifest import build_t3_notes_manifest, target_entries
from ..tools.paper_enrichment import build_access_audit, build_deep_read_queue


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """统一写 JSONL，保证空列表时也生成空文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(item, ensure_ascii=False) for item in records)
    if content:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def _is_complete_note(note_path: Path) -> bool:
    """只有通过 Reader 结构校验的 note 才能在恢复时视为已完成。"""

    try:
        from ..agents.reader import _validate_note_structure

        ok, _ = _validate_note_structure(note_path)
        return ok
    except Exception:
        return False


def _note_paths(literature_dir: Path) -> list[Path]:
    paths: list[Path] = []
    notes_dir = literature_dir / "paper_notes"
    if notes_dir.exists():
        paths.extend(path for path in notes_dir.glob("*.md") if is_paper_note_file(path))
    bridge_dir = literature_dir / "paper_notes_bridge"
    if bridge_dir.exists():
        paths.extend(path for path in bridge_dir.glob("**/*.md") if is_paper_note_file(path))
    return sorted(paths)


def _note_keys(literature_dir: Path) -> set[str]:
    """把已有且合格的 note 转换成可比对 key。

    T3 的 note 文件名有时是 `arxiv_2605..._Title.md`，而 queue 里可能是
    `arxiv:2605...`、DOI、normalized_id 或标题。恢复时只看文件名会漏扣已读
    论文，导致 resume 重复深读。这里同时读取 note 头部里的 ID/DOI/arXiv
    元数据和标题，给恢复器一组保守但更完整的匹配 key。
    """

    keys: set[str] = set()
    for path in _note_paths(literature_dir):
        if not _is_complete_note(path):
            continue
        keys.update(paper_note_match_keys(path))
    return {key for key in keys if key}


def _re_rank_pending_queue(queue_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """重排剩余队列的 rank，避免恢复时出现断裂序号。

    ``queue_rank`` 是 Reader 在当前 pending queue 里看到的序号；
    ``original_queue_rank`` 保留 full queue 中的稳定序号，供日志、manifest
    和用户排障时对齐，避免 resume 后 rank 语义漂移。
    """

    pending: list[dict[str, Any]] = []
    for idx, record in enumerate(queue_records, start=1):
        updated = dict(record)
        updated.setdefault("original_queue_rank", record.get("original_queue_rank") or record.get("queue_rank") or idx)
        updated["queue_rank"] = idx
        updated["pending_queue_rank"] = idx
        pending.append(updated)
    return pending


def prepare_t3_resume_artifacts(workspace_dir: Path, *, refresh_reason: str | None = None) -> dict[str, Any]:
    """为 T3 恢复运行准备可直接消费的剩余队列和审计文件。"""

    literature_dir = workspace_dir / "literature"
    completed_keys = _note_keys(literature_dir)
    note_paths = _note_paths(literature_dir)
    valid_note_file_count = sum(
        1
        for path in note_paths
        if _is_complete_note(path)
    )
    invalid_note_file_count = sum(
        1
        for path in note_paths
        if not _is_complete_note(path)
    )

    queue_path = literature_dir / "deep_read_queue.jsonl"
    pending_queue_path = literature_dir / "deep_read_queue_pending.jsonl"
    pending_meta_path = literature_dir / "deep_read_queue_pending_meta.json"
    access_audit_path = literature_dir / "access_audit.md"

    queue_records = load_jsonl(queue_path) if queue_path.exists() else []
    source_label = "deep_read_queue"

    # 旧 workspace 可能没有 T2 新产物；这里用 verified/dedup 确定性补一份 queue。
    if not queue_records:
        verified_path = literature_dir / "papers_verified.jsonl"
        dedup_path = literature_dir / "papers_dedup.jsonl"
        candidate_papers = load_jsonl(verified_path) if verified_path.exists() else []
        if candidate_papers:
            source_label = "papers_verified"
        elif dedup_path.exists():
            candidate_papers = load_jsonl(dedup_path)
            source_label = "papers_dedup"
        else:
            candidate_papers = []

        if candidate_papers:
            queue_config = load_deep_read_queue_config()
            queue_records, metadata = build_deep_read_queue(
                candidate_papers,
                workspace_dir,
                deep_read_min=queue_config.deep_read_min,
                deep_read_target=queue_config.deep_read_target,
                deep_read_max=queue_config.deep_read_max,
                probe_pool=queue_config.probe_pool,
                mainline_screened_cap=queue_config.mainline_screened_cap,
                bridge_deep_floor=queue_config.bridge_deep_floor,
                bridge_screened_cap=queue_config.bridge_screened_cap,
                bridge_pool_cap=queue_config.bridge_pool_cap,
                citation_hub_slots=queue_config.citation_hub_slots,
            )
            _write_jsonl(queue_path, queue_records)
            (literature_dir / "deep_read_queue_meta.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            # 旧 workspace 缺 access_audit 时一并补上，减少 Reader 自己摸索成本。
            if not access_audit_path.exists():
                audit_records, audit_markdown = build_access_audit(candidate_papers, workspace_dir, top_n=50)
                _write_jsonl(literature_dir / "access_audit.jsonl", audit_records)
                access_audit_path.write_text(audit_markdown, encoding="utf-8")

    # 核心恢复逻辑：pending queue 只保留“尚未有结构合格 note 的论文”。
    # 不能只按文件名裁剪：同名 note 若缺少必需结构，仍必须留在 pending
    # 供 Reader 修补；而 alias/标题匹配到的合格 note 应被扣除。
    manifest = build_t3_notes_manifest(
        workspace_dir,
        queue_records=queue_records,
        source_queue=source_label,
        write=True,
    )
    manifest_entries = manifest.get("entries") if isinstance(manifest.get("entries"), list) else []
    pending_records = [
        record
        for record, entry in zip(queue_records, manifest_entries)
        if (
            (not isinstance(entry, dict) or entry.get("status") != "complete")
            and not bool(record.get("triaged_out"))
            and str(record.get("target_bucket") or "") != "overflow"
        )
    ]
    pending_records = _re_rank_pending_queue(pending_records)
    _write_jsonl(pending_queue_path, pending_records)
    incomplete_entries = [
        entry
        for entry in target_entries(manifest)
        if isinstance(entry, dict) and entry.get("status") == "incomplete"
    ]
    missing_entries = [
        entry
        for entry in target_entries(manifest)
        if isinstance(entry, dict) and entry.get("status") == "missing"
    ]
    pending_meta_path.write_text(
        json.dumps(
            {
                "source_queue": source_label,
                "resume_queue_path": "literature/deep_read_queue_pending.jsonl",
                "rank_semantics": (
                    "queue_rank in deep_read_queue_pending.jsonl is the current resume rank; "
                    "original_queue_rank points back to literature/deep_read_queue.jsonl."
                ),
                "refresh_reason": refresh_reason or "resume_snapshot",
                "original_queue_count": len(queue_records),
                "completed_note_count": valid_note_file_count,
                "completed_queue_entry_count": manifest.get("target_complete_count"),
                "completed_note_key_count": len(completed_keys),
                "pending_queue_count": len(pending_records),
                "valid_note_file_count": valid_note_file_count,
                "invalid_note_file_count": invalid_note_file_count,
                "notes_manifest": "literature/notes_manifest.json",
                "manifest_complete_count": manifest.get("complete_count"),
                "manifest_incomplete_count": manifest.get("incomplete_count"),
                "manifest_missing_count": manifest.get("missing_count"),
                "manifest_target_complete_count": manifest.get("target_complete_count"),
                "manifest_target_incomplete_count": manifest.get("target_incomplete_count"),
                "manifest_target_missing_count": manifest.get("target_missing_count"),
                "incomplete_examples": [
                    {
                        "queue_rank": entry.get("queue_rank"),
                        "paper": entry.get("record_display_key"),
                        "note_path": entry.get("note_path"),
                        "validation_error": entry.get("validation_error"),
                    }
                    for entry in incomplete_entries[:8]
                ],
                "missing_examples": [
                    {
                        "queue_rank": entry.get("queue_rank"),
                        "paper": entry.get("record_display_key"),
                    }
                    for entry in missing_entries[:8]
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return {
        "resume_queue_path": "literature/deep_read_queue_pending.jsonl",
        "resume_queue_source": source_label,
        "resume_queue_count": len(pending_records),
        "existing_note_count": valid_note_file_count,
        "existing_note_key_count": len(completed_keys),
        "notes_manifest_path": "literature/notes_manifest.json",
        "incomplete_note_count": len(incomplete_entries),
        "missing_note_count": len(missing_entries),
        "completed_queue_entry_count": manifest.get("target_complete_count"),
    }
