from __future__ import annotations

import json

from researchos.tools.paper_enrichment import build_deep_read_queue, enrich_papers


def test_build_deep_read_queue_prioritizes_seed_and_access(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "user_seeds").mkdir(parents=True)
    (workspace / "literature" / "pdfs").mkdir(parents=True)

    (workspace / "user_seeds" / "seed_papers.jsonl").write_text(
        json.dumps(
            {
                "title": "Seed Paper",
                "doi": "10.1234/seed",
                "role": "anchor",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    (workspace / "literature" / "pdfs" / "arxiv_2501.00001.pdf").write_bytes(b"%PDF-1.4 test")

    papers = enrich_papers(
        [
            {
                "id": "10.1234/seed",
                "canonical_id": "arxiv:2501.00001",
                "title": "Seed Paper",
                "doi": "10.1234/seed",
                "source": "crossref",
                "year": 2024,
                "abstract": "Seed abstract",
                "relevance_score": 0.7,
                "verification_status": "metadata_verified",
                "verification_confidence": 0.95,
            },
            {
                "id": "paper-2",
                "title": "Non Seed Paper",
                "source": "arxiv",
                "year": 2025,
                "abstract": "Other abstract",
                "relevance_score": 0.95,
                "verification_status": "metadata_verified",
                "verification_confidence": 0.9,
            },
        ]
    )

    queue, meta = build_deep_read_queue(
        papers,
        workspace,
        deep_read_min=1,
        deep_read_target=1,
        deep_read_max=2,
        probe_pool=2,
    )

    assert len(queue) == 2
    assert queue[0]["seed_priority"] is True
    assert queue[0]["paper_id"] == "arxiv:2501.00001"
    assert queue[0]["has_local_pdf"] is True
    assert meta["seed_in_queue"] == 1
    assert meta["queue_count"] == 2
