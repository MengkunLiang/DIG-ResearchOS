from __future__ import annotations

import json

import pytest

from researchos.tools.paper_enrichment import (
    apply_semantic_screening,
    build_access_audit,
    build_deep_read_queue,
    enrich_papers,
)
from researchos.tools.paper_enrichment_tool import BackfillPaperAbstractsTool, BuildVerifiedPapersTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


class _FakeMetadataResponse:
    def __init__(self, payload: dict | None = None, *, text: str = "") -> None:
        self._payload = payload or {}
        self.text = text

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeMetadataClient:
    async def get(self, url: str, **kwargs):
        if "api.crossref.org/works/" in url:
            return _FakeMetadataResponse(
                {
                    "message": {
                        "title": ["Metadata Verified Paper"],
                        "published": {"date-parts": [[2024]]},
                        "DOI": "10.1234/abstract",
                    }
                }
            )
        if "api.openalex.org/works/" in url:
            return _FakeMetadataResponse(
                {
                    "title": "Metadata Verified Paper",
                    "publication_year": 2024,
                    "doi": "https://doi.org/10.1234/abstract",
                    "abstract_inverted_index": {
                        "Recovered": [0],
                        "abstract": [1],
                        "from": [2],
                        "OpenAlex.": [3],
                    },
                }
            )
        raise AssertionError(f"Unexpected metadata URL: {url}")


class _FakeBackfillClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, **kwargs):
        if "api.semanticscholar.org/graph/v1/paper/batch" in url:
            ids = kwargs.get("json", {}).get("ids", [])
            return _FakeListResponse(
                [
                    {
                        "title": "Batch Backfilled Paper",
                        "abstract": "<jats:p>Batch abstract &amp; cleaned.</jats:p>",
                        "externalIds": {"DOI": "10.5555/batch"},
                    }
                    if paper_id == "DOI:10.5555/batch"
                    else None
                    for paper_id in ids
                ]
            )
        raise AssertionError(f"Unexpected POST URL: {url}")

    async def get(self, url: str, **kwargs):
        if "api.openalex.org/works/https://doi.org/10.5555/fallback" in url:
            return _FakeMetadataResponse(
                {
                    "abstract_inverted_index": {},
                }
            )
        if "api.crossref.org/works/10.5555%2Ffallback" in url:
            return _FakeMetadataResponse(
                {"message": {"abstract": "<jats:p>CrossRef fallback &amp; cleaned.</jats:p>"}}
            )
        if "api.openalex.org/works" in url:
            return _FakeMetadataResponse({"results": []})
        if "api.semanticscholar.org/graph/v1/paper/search" in url:
            return _FakeMetadataResponse({"data": []})
        if "europepmc" in url:
            return _FakeMetadataResponse({"resultList": {"result": []}})
        raise AssertionError(f"Unexpected GET URL: {url}")


class _FakeListResponse:
    def __init__(self, payload: list) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list:
        return self._payload


@pytest.mark.asyncio
async def test_backfill_paper_abstracts_cleans_and_backfills_papers_raw(monkeypatch, tmp_path):
    workspace = tmp_path / "ws"
    literature = workspace / "literature"
    literature.mkdir(parents=True)
    papers_path = literature / "papers_raw.jsonl"
    papers_path.write_text(
        "\n".join(
            json.dumps(item, ensure_ascii=False)
            for item in [
                {
                    "id": "existing",
                    "title": "Existing Abstract",
                    "abstract": "<jats:p>Existing &amp; clean.</jats:p>",
                },
                {
                    "id": "batch",
                    "title": "Batch Backfilled Paper",
                    "doi": "10.5555/batch",
                    "abstract": "",
                    "_missing_abstract": True,
                },
                {
                    "id": "fallback",
                    "title": "Fallback Backfilled Paper",
                    "doi": "10.5555/fallback",
                    "_missing_abstract": True,
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    import researchos.tools.paper_enrichment_tool as enrichment_tool

    monkeypatch.setattr(
        enrichment_tool.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeBackfillClient(),
    )

    policy = WorkspaceAccessPolicy(workspace, ["", "literature/"], ["", "literature/"])
    result = await BackfillPaperAbstractsTool(policy).execute(
        papers_path="literature/papers_raw.jsonl"
    )

    assert result.ok
    assert result.data["cleaned_existing"] == 1
    assert result.data["filled"] == 2
    assert result.data["remaining"] == 0

    records = [json.loads(line) for line in papers_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["abstract"] == "Existing & clean."
    assert records[1]["abstract"] == "Batch abstract & cleaned."
    assert records[1]["_abstract_backfilled_from"] == "semantic_scholar_batch"
    assert records[2]["abstract"] == "CrossRef fallback & cleaned."
    assert records[2]["_abstract_backfilled_from"] == "crossref"
    assert "_missing_abstract" not in records[1]
    assert "_missing_abstract" not in records[2]


def _screen(
    *,
    relation: str = "baseline_or_dataset_relevance",
    role: str = "baseline",
    bridge_id: str | None = None,
    can_enter_core: bool = False,
    can_enter_deep_read: bool = True,
) -> dict:
    return {
        "relation_to_project": relation,
        "role": role,
        "confidence": "medium",
        "bridge_id": bridge_id,
        "can_enter_core": can_enter_core,
        "can_enter_deep_read": can_enter_deep_read,
        "rationale": "LLM screening says this record has a concrete downstream use.",
        "evidence_fields_used": ["title", "abstract"],
    }


def test_apply_semantic_screening_only_merges_llm_supplied_judgments():
    papers = [
        {
            "id": "p1",
            "canonical_id": "W1",
            "title": "Screened Candidate",
            "search_bucket": "theory_bridge",
        },
        {
            "id": "p2",
            "canonical_id": "W2",
            "title": "Unscreened Bucket Candidate",
            "search_bucket": "theory_bridge",
        },
    ]
    merged = apply_semantic_screening(
        papers,
        [
            {
                "paper_id": "W1",
                **_screen(
                    relation="method_transfer",
                    role="theory_bridge",
                    bridge_id="b1",
                ),
            }
        ],
    )

    assert merged[0]["semantic_screen"]["relation_to_project"] == "method_transfer"
    assert merged[0]["bridge_id"] == "b1"
    assert "semantic_screen" not in merged[1]


@pytest.mark.asyncio
async def test_build_verified_papers_backfills_missing_abstract_from_verified_metadata(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    policy = WorkspaceAccessPolicy(workspace, [""], [""])
    tool = BuildVerifiedPapersTool(policy)

    verified, failure = await tool._verify_one_paper(
        _FakeMetadataClient(),
        {
            "id": "10.1234/abstract",
            "canonical_id": "10.1234/abstract",
            "doi": "10.1234/abstract",
            "title": "Metadata Verified Paper",
            "year": 2024,
            "source": "crossref",
            "abstract": "",
            "_missing_abstract": True,
        },
        title_similarity_threshold=0.84,
    )

    assert failure is None
    assert verified is not None
    assert verified["verification_method"] == "crossref"
    assert verified["abstract"] == "Recovered abstract from OpenAlex."
    assert verified["_abstract_backfilled_from"] == "openalex"
    assert "_missing_abstract" not in verified


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
                "semantic_screen": _screen(),
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
            "semantic_screen": _screen(),
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
            "semantic_screen": _screen(),
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


def test_build_deep_read_queue_protects_llm_screened_cross_domain_candidate(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "literature").mkdir(parents=True, exist_ok=True)
    (workspace / "user_seeds").mkdir(parents=True, exist_ok=True)

    papers = []
    for idx in range(6):
        papers.append(
            {
                "id": f"core-{idx}",
                "title": f"Core Paper {idx}",
                "source": "openalex",
                "year": 2026,
                "abstract": "core target-domain paper",
                "relevance_score": 0.95 - idx * 0.02,
                "verification_status": "metadata_verified",
                "verification_confidence": 0.9,
                "semantic_screen": _screen(
                    relation="baseline_or_dataset_relevance",
                    role="core",
                    can_enter_core=True,
                ),
            }
        )
    papers.append(
        {
            "id": "adjacent-low-score",
            "title": "Adjacent Field Analogy",
            "source": "crossref",
            "year": 2024,
            "abstract": "adjacent field design rationale",
            "relevance_score": 0.1,
            "verification_status": "metadata_verified",
            "verification_confidence": 0.9,
            "search_bucket": "adjacent_field",
            "retrieval_intent": "cross_domain_bridge",
            "bridge_id": "b1",
            "semantic_screen": _screen(
                relation="mechanism_bridge",
                role="adjacent",
                bridge_id="b1",
            ),
        }
    )

    queue, meta = build_deep_read_queue(
        enrich_papers(papers),
        workspace,
        deep_read_min=2,
        deep_read_target=4,
        deep_read_max=4,
        probe_pool=4,
    )

    assert "adjacent-low-score" in {item["paper_id"] for item in queue}
    adjacent = next(item for item in queue if item["paper_id"] == "adjacent-low-score")
    assert adjacent["cross_domain_candidate"] is True
    assert adjacent["adjacent_field"] is True
    assert adjacent["search_bucket"] == "adjacent_field"
    assert meta["protected_slot_in_queue"] >= 1
    assert meta["protected_slot_in_target"] >= 1
    assert adjacent["target_bucket"] != "overflow"


def test_build_deep_read_queue_does_not_admit_unscreened_bridge_query(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "literature").mkdir(parents=True, exist_ok=True)
    (workspace / "user_seeds").mkdir(parents=True, exist_ok=True)

    papers = [
        {
            "id": "screened-core",
            "title": "Screened Core",
            "source": "openalex",
            "year": 2026,
            "abstract": "target-domain baseline",
            "relevance_score": 0.8,
            "verification_status": "metadata_verified",
            "verification_confidence": 0.9,
            "semantic_screen": _screen(
                relation="baseline_or_dataset_relevance",
                role="core",
                can_enter_core=True,
            ),
        },
        {
            "id": "unscreened-bridge",
            "title": "Unscreened Bridge Query Hit",
            "source": "crossref",
            "year": 2025,
            "abstract": "shares terms but not reviewed by Scout",
            "relevance_score": 0.99,
            "verification_status": "metadata_verified",
            "verification_confidence": 0.9,
            "search_bucket": "theory_bridge",
            "retrieval_intent": "cross_domain_bridge",
            "bridge_id": "b1",
        },
    ]

    queue, meta = build_deep_read_queue(
        enrich_papers(papers),
        workspace,
        deep_read_min=1,
        deep_read_target=2,
        deep_read_max=2,
        probe_pool=2,
    )

    assert {item["paper_id"] for item in queue} == {"screened-core"}
    assert meta["protected_slot_in_queue"] == 0
    enriched_unscreened = next(item for item in enrich_papers(papers) if item["id"] == "unscreened-bridge")
    assert enriched_unscreened["cross_domain_retrieval_candidate"] is True
    assert "semantic_screen" not in enriched_unscreened


def test_build_deep_read_queue_protected_slot_cannot_be_sorted_to_overflow(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "literature").mkdir(parents=True, exist_ok=True)
    (workspace / "user_seeds").mkdir(parents=True, exist_ok=True)

    papers = [
        {
            "id": f"core-{idx}",
            "title": f"High Score Core {idx}",
            "source": "openalex",
            "year": 2026,
            "abstract": "high score core target-domain paper",
            "relevance_score": 0.99 - idx * 0.01,
            "verification_status": "metadata_verified",
            "verification_confidence": 0.95,
            "semantic_screen": _screen(
                relation="baseline_or_dataset_relevance",
                role="core",
                can_enter_core=True,
            ),
        }
        for idx in range(10)
    ]
    papers.append(
        {
            "id": "theory-bridge-low-score",
            "title": "Low Score Theory Bridge",
            "source": "openalex",
            "year": 2024,
            "abstract": "theory bridge material with low lexical relevance",
            "relevance_score": 0.01,
            "verification_status": "metadata_verified",
            "verification_confidence": 0.9,
            "search_bucket": "theory_bridge",
            "retrieval_intent": "cross_domain_bridge",
            "bridge_id": "b2",
            "semantic_screen": _screen(
                relation="method_transfer",
                role="theory_bridge",
                bridge_id="b2",
            ),
        }
    )

    queue, meta = build_deep_read_queue(
        enrich_papers(papers),
        workspace,
        deep_read_min=2,
        deep_read_target=3,
        deep_read_max=6,
        probe_pool=6,
    )

    bridge = next(item for item in queue if item["paper_id"] == "theory-bridge-low-score")
    assert bridge["target_bucket"] == "target"
    assert meta["protected_slot_in_target"] >= 1
    assert len(queue) == 6


def test_apply_semantic_screening_sanitizes_invalid_admission_flags():
    merged = apply_semantic_screening(
        [{"id": "p1", "title": "Invalid Screening"}],
        [
            {
                "paper_id": "p1",
                "relation_to_project": "shared_keyword_only",
                "role": "core",
                "confidence": "high",
                "can_enter_core": True,
                "can_enter_deep_read": True,
                "rationale": "LLM accidentally set conflicting flags.",
                "evidence_fields_used": ["title"],
            }
        ],
    )

    screen = merged[0]["semantic_screen"]
    assert screen["relation_to_project"] == "shared_keyword_only"
    assert screen["role"] == "core"
    assert screen["can_enter_core"] is False
    assert screen["can_enter_deep_read"] is False
    assert "can_enter_core_true_but_relation_not_core_allowed" in screen["normalization_warnings"]


def test_seed_pdf_fuzzy_matching_treats_user_seed_pdfs_as_full_text(tmp_path):
    workspace = tmp_path / "ws"
    seed_dir = workspace / "user_seeds"
    pdf_dir = seed_dir / "pdfs"
    (workspace / "literature").mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    seed_titles = [
        "Adaptive Perturbation for Sparse Feedback Loops",
        "Robust Counterfactual Evaluation Under Shift",
        "Mechanism Transfer in Iterative Decision Systems",
        "Benchmark Design for Long-Horizon Agents",
        "Failure Mode Taxonomy for Open-Ended Search",
        "Evidence Grounding in Automated Research Workflows",
    ]
    seed_records = [
        {"id": f"seed-{idx}", "canonical_id": f"seed-{idx}", "title": title, "role": "anchor"}
        for idx, title in enumerate(seed_titles, start=1)
    ]
    (seed_dir / "seed_papers.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in seed_records),
        encoding="utf-8",
    )
    messy_pdf_names = [
        "Smith 2026 Adaptive-Perturbation for Sparse Feedback Loops.pdf",
        "2025_robust counterfactual evaluation under shift v2.pdf",
        "Li等 - Mechanism transfer in iterative decision systems.pdf",
        "Benchmark.Design.for.Long Horizon Agents camera ready.pdf",
        "failure-mode taxonomy for open ended search.pdf",
        "Evidence grounding in automated research workflows - accepted.pdf",
    ]
    for name in messy_pdf_names:
        (pdf_dir / name).write_bytes(b"%PDF-1.4 seed")

    papers = enrich_papers(
        [
            {
                **record,
                "source": "user_seed",
                "year": 2026,
                "abstract": "Seed abstract.",
                "relevance_score": 1.0,
                "verification_status": "metadata_verified",
                "verification_confidence": 1.0,
            }
            for record in seed_records
        ]
    )

    queue, meta = build_deep_read_queue(
        papers,
        workspace,
        deep_read_min=6,
        deep_read_target=6,
        deep_read_max=6,
        probe_pool=6,
    )
    audit_records, audit_md = build_access_audit(papers, workspace)

    assert len(queue) == 6
    assert meta["seed_in_queue"] == 6
    assert all(item["has_seed_pdf"] is True for item in queue)
    assert all(item["has_local_pdf"] is True for item in queue)
    assert all(item["access_score"] == 1.0 for item in queue)
    assert all(item["access_level_hint"] == "FULL_TEXT_LOCAL" for item in queue)
    assert sum(1 for item in audit_records if item["has_seed_pdf"]) == 6
    assert "`user_seeds/pdfs/` 可匹配的 seed PDF: 6" in audit_md


def test_build_deep_read_queue_protects_semantically_screened_citation_hub(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / "user_seeds").mkdir(parents=True)
    (workspace / "literature").mkdir(parents=True)
    (workspace / "user_seeds" / "seed_papers.jsonl").write_text(
        json.dumps({"id": "W_seed", "canonical_id": "W_seed", "title": "Seed Anchor"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    papers = [
        {
            "id": "W_seed",
            "canonical_id": "W_seed",
            "source": "user_seed",
            "source_bucket": "seed",
            "title": "Seed Anchor",
            "year": 2025,
            "abstract": "Seed abstract.",
            "relevance_score": 1.0,
            "verification_status": "metadata_verified",
            "verification_confidence": 1.0,
            "referenced_works": ["W_neighbor"],
        },
        {
            "id": "W_neighbor",
            "canonical_id": "W_neighbor",
            "source": "openalex",
            "title": "Mechanism Neighbor",
            "year": 2024,
            "abstract": "Directly connected to the seed and semantically useful.",
            "relevance_score": 0.4,
            "verification_status": "metadata_verified",
            "verification_confidence": 0.9,
            "semantic_screen": _screen(relation="method_transfer", role="theory_bridge", can_enter_deep_read=True),
        },
        {
            "id": "W_bad",
            "canonical_id": "W_bad",
            "source": "openalex",
            "title": "Generic High Degree Survey",
            "year": 2020,
            "abstract": "High degree but unrelated.",
            "relevance_score": 0.99,
            "verification_status": "metadata_verified",
            "verification_confidence": 0.9,
            "semantic_screen": _screen(relation="shared_keyword_only", role="core", can_enter_deep_read=True),
        },
        {
            "id": "W_a",
            "canonical_id": "W_a",
            "source": "openalex",
            "title": "Allowed Paper A",
            "year": 2023,
            "abstract": "Allowed.",
            "relevance_score": 0.8,
            "verification_status": "metadata_verified",
            "verification_confidence": 0.9,
            "semantic_screen": _screen(),
            "referenced_works": ["W_bad"],
        },
        {
            "id": "W_b",
            "canonical_id": "W_b",
            "source": "openalex",
            "title": "Allowed Paper B",
            "year": 2023,
            "abstract": "Allowed.",
            "relevance_score": 0.7,
            "verification_status": "metadata_verified",
            "verification_confidence": 0.9,
            "semantic_screen": _screen(),
            "referenced_works": ["W_bad"],
        },
    ]

    queue, meta = build_deep_read_queue(
        papers,
        workspace,
        deep_read_min=2,
        deep_read_target=2,
        deep_read_max=5,
        probe_pool=5,
        cross_domain_slots=0,
        citation_hub_slots=1,
    )

    by_id = {item["paper_id"]: item for item in queue}
    assert by_id["W_neighbor"]["is_citation_hub"] is True
    assert by_id["W_neighbor"]["hub_type"] == "seed_neighbor"
    assert by_id["W_neighbor"]["citation_hub_protected_slot"] is True
    assert by_id["W_neighbor"]["target_bucket"] == "target"
    assert "W_bad" not in by_id
    assert meta["citation_hub_slots"] == 1
    assert meta["citation_hub_in_target"] >= 1
