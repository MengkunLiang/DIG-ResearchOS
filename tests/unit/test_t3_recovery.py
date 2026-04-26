from __future__ import annotations

import json
from pathlib import Path

from researchos.runtime.t3_recovery import prepare_t3_resume_artifacts


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + ("\n" if records else ""),
        encoding="utf-8",
    )


def test_prepare_t3_resume_artifacts_builds_pending_queue_from_dedup(tmp_path: Path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    notes_dir = literature / "paper_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "paper1.md").write_text("# done", encoding="utf-8")

    _write_jsonl(
        literature / "papers_dedup.jsonl",
        [
            {
                "id": "paper1",
                "title": "Paper 1",
                "year": 2025,
                "source": "arxiv",
                "relevance_score": 0.91,
                "access_score_estimate": 0.8,
                "access_score": 0.8,
                "evidence_level": "PARTIAL_TEXT",
            },
            {
                "id": "paper2",
                "title": "Paper 2",
                "year": 2025,
                "source": "openalex",
                "relevance_score": 0.89,
                "access_score_estimate": 0.7,
                "access_score": 0.7,
                "evidence_level": "ABSTRACT_ONLY",
            },
        ],
    )

    info = prepare_t3_resume_artifacts(workspace)

    pending_queue = literature / "deep_read_queue_pending.jsonl"
    full_queue = literature / "deep_read_queue.jsonl"
    assert full_queue.exists()
    assert pending_queue.exists()
    assert info["existing_note_count"] == 1
    assert info["resume_queue_count"] == 1
    assert info["resume_queue_source"] == "papers_dedup"
    pending_records = [json.loads(line) for line in pending_queue.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(pending_records) == 1
    assert pending_records[0]["paper_id"] == "paper2"


def test_prepare_t3_resume_artifacts_filters_existing_queue(tmp_path: Path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    notes_dir = literature / "paper_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "seed_paper.md").write_text("# done", encoding="utf-8")

    _write_jsonl(
        literature / "deep_read_queue.jsonl",
        [
            {"paper_id": "seed_paper", "normalized_id": "seed_paper", "queue_rank": 1, "title": "Seed"},
            {"paper_id": "paper2", "normalized_id": "paper2", "queue_rank": 2, "title": "Paper 2"},
        ],
    )

    info = prepare_t3_resume_artifacts(workspace)

    pending_records = [
        json.loads(line)
        for line in (literature / "deep_read_queue_pending.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert info["resume_queue_source"] == "deep_read_queue"
    assert info["resume_queue_count"] == 1
    assert pending_records[0]["paper_id"] == "paper2"
    assert pending_records[0]["queue_rank"] == 1
