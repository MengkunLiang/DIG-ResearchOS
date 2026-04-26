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

from ..agents._common import load_jsonl, normalize_text_key
from ..runtime.agent_params import get_agent_mode_params
from ..tools.paper_enrichment import build_access_audit, build_deep_read_queue


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """统一写 JSONL，保证空列表时也生成空文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(item, ensure_ascii=False) for item in records)
    if content:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def _note_keys(notes_dir: Path) -> set[str]:
    """把已有 note 文件名转换成可比对的规范化 key。"""

    if not notes_dir.exists():
        return set()
    return {
        normalize_text_key(path.stem)
        for path in notes_dir.glob("*.md")
        if path.is_file() and path.suffix == ".md"
    }


def _queue_item_key(item: dict[str, Any]) -> str:
    """为 queue 记录生成用于去重/比对的 key。"""

    return normalize_text_key(
        str(item.get("normalized_id") or item.get("paper_id") or item.get("id") or "")
    )


def _re_rank_pending_queue(queue_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """重排剩余队列的 rank，避免恢复时出现断裂序号。"""

    pending: list[dict[str, Any]] = []
    for idx, record in enumerate(queue_records, start=1):
        updated = dict(record)
        updated["queue_rank"] = idx
        pending.append(updated)
    return pending


def prepare_t3_resume_artifacts(workspace_dir: Path) -> dict[str, Any]:
    """为 T3 恢复运行准备可直接消费的剩余队列和审计文件。"""

    literature_dir = workspace_dir / "literature"
    notes_dir = literature_dir / "paper_notes"
    completed_keys = _note_keys(notes_dir)

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
            mode_params = get_agent_mode_params("reader", "read")
            queue_records, metadata = build_deep_read_queue(
                candidate_papers,
                workspace_dir,
                deep_read_min=int(mode_params.get("deep_read_min", 18)),
                deep_read_target=int(mode_params.get("deep_read_target", 24)),
                deep_read_max=int(mode_params.get("deep_read_max", 30)),
                probe_pool=int(mode_params.get("probe_pool", 45)),
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

    # 核心恢复逻辑：pending queue 只保留“还没有 note 的论文”。
    pending_records = [
        record for record in queue_records if _queue_item_key(record) not in completed_keys
    ]
    pending_records = _re_rank_pending_queue(pending_records)
    _write_jsonl(pending_queue_path, pending_records)
    pending_meta_path.write_text(
        json.dumps(
            {
                "source_queue": source_label,
                "original_queue_count": len(queue_records),
                "completed_note_count": len(completed_keys),
                "pending_queue_count": len(pending_records),
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
        "existing_note_count": len(completed_keys),
    }
