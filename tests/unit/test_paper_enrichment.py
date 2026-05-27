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


def test_enrich_papers_preserves_unknown_year_as_none():
    papers = enrich_papers(
        [
            {
                "id": "unknown-year",
                "title": "Unknown Year Paper",
                "source": "arxiv",
                "year": "not-a-year",
                "abstract": "metadata without a reliable year",
            }
        ]
    )

    assert papers[0]["year"] is None


def test_enrich_papers_prefers_llm_annotations_over_fallbacks():
    papers = enrich_papers(
        [
            {
                "id": "p1",
                "title": "Specialized Domain Paper",
                "source": "unknown_source",
                "venue": "Domain Symposium",
                "year": 2026,
                "abstract": "A domain-specific mechanism.",
            }
        ],
        llm_annotations={
            "p1": {
                "source_type": "domain_flagship_symposium",
                "why_relevant": "LLM judged this paper as the closest baseline for the target mechanism.",
                "method_family": "activity-conditioned perturbation",
                "domain_tags": ["target-domain"],
            }
        },
    )

    assert papers[0]["source_type"] == "domain_flagship_symposium"
    assert papers[0]["why_relevant"].startswith("LLM judged")
    assert papers[0]["method_family"] == "activity-conditioned perturbation"
    assert papers[0]["domain_tags"] == ["target-domain"]
    assert papers[0]["llm_annotation_applied"] is True


def test_enrich_papers_unknown_source_type_is_marked_for_review():
    papers = enrich_papers(
        [
            {
                "id": "p1",
                "title": "Unknown Venue Paper",
                "source": "unknown_source",
                "venue": "Unfamiliar Venue",
                "abstract": "No profile terms here.",
            }
        ]
    )

    assert papers[0]["source_type"] == "unknown"
    assert papers[0]["_needs_llm_source_type"] is True
    assert papers[0]["_needs_llm_relevance_review"] is True


def test_enrich_papers_does_not_infer_full_text_from_access_metadata():
    papers = enrich_papers(
        [
            {
                "id": "p1",
                "title": "Likely Accessible",
                "source": "arxiv",
                "venue": "arXiv",
                "url": "https://arxiv.org/pdf/2501.00001",
                "pdf_url": "https://arxiv.org/pdf/2501.00001",
                "abstract": "Abstract is available.",
            }
        ]
    )

    assert papers[0]["access_level_hint"] == "LIKELY_FULL_TEXT"
    assert papers[0]["evidence_level"] == "ABSTRACT_ONLY"
    assert papers[0]["_needs_reader_evidence_level"] is True


def test_build_deep_read_queue_prefers_verified_pool_when_caller_passes_dedup(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "literature" / "pdfs").mkdir(parents=True)
    (workspace / "literature").mkdir(parents=True, exist_ok=True)
    (workspace / "user_seeds").mkdir(parents=True, exist_ok=True)

    # 模拟 agent 错把 dedup 池传给 build_deep_read_queue，其中混有未核验论文。
    candidate_records = enrich_papers(
        [
            {
                "id": "arxiv:2601.00000",
                "title": "Unverified Queue Intruder",
                "source": "arxiv",
                "year": 2026,
                "abstract": "intruder",
                "relevance_score": 0.99,
                "verification_status": "retrieved",
            },
            {
                "id": "arxiv:2603.00026",
                "title": "Verified Paper A",
                "source": "arxiv",
                "year": 2026,
                "abstract": "verified a",
                "relevance_score": 0.8,
                "verification_status": "retrieved",
            },
            {
                "id": "arxiv:2603.02473",
                "title": "Verified Paper B",
                "source": "arxiv",
                "year": 2026,
                "abstract": "verified b",
                "relevance_score": 0.7,
                "verification_status": "retrieved",
            },
        ]
    )

    verified_records = [
        {
            "id": "arxiv:2603.00026",
            "canonical_id": "arxiv:2603.00026",
            "title": "Verified Paper A",
            "source": "arxiv",
            "year": 2026,
            "abstract": "verified a",
            "relevance_score": 0.8,
            "verification_status": "metadata_verified",
            "verification_confidence": 0.93,
        },
        {
            "id": "arxiv:2603.02473",
            "canonical_id": "arxiv:2603.02473",
            "title": "Verified Paper B",
            "source": "arxiv",
            "year": 2026,
            "abstract": "verified b",
            "relevance_score": 0.7,
            "verification_status": "metadata_verified",
            "verification_confidence": 0.91,
        },
    ]
    (workspace / "literature" / "papers_verified.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in verified_records) + "\n",
        encoding="utf-8",
    )

    queue, meta = build_deep_read_queue(
        candidate_records,
        workspace,
        deep_read_min=1,
        deep_read_target=2,
        deep_read_max=2,
        probe_pool=2,
    )

    assert [item["paper_id"] for item in queue] == [
        "arxiv:2603.00026",
        "arxiv:2603.02473",
    ]
    assert all(item["verification_status"] == "metadata_verified" for item in queue)
    assert meta["source_pool"] == "papers_verified"
