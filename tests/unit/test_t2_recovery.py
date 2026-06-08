from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.runtime.agent_params import clear_cache
from researchos.runtime.t2_recovery import (
    _backfill_recovered_crossref_metadata,
    _backfill_recovered_openalex_metadata,
    _backfill_recovered_openalex_title_metadata,
    _cap_active_pool_after_seed_repair,
    _dedupe_search_records,
    _expand_crossref_snowball_candidates,
    _expand_openalex_snowball_candidates,
    _merge_enriched_records_back_to_raw,
    _openalex_detail_url,
    _select_active_candidate_pool,
    _search_records_from_raw,
    finalize_t2_outputs,
)
from researchos.runtime.t2_config import T2FinalizeConfig


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


def test_search_records_from_raw_keeps_single_bridge_on_bridge_bucket_only():
    records = _search_records_from_raw(
        [
            {
                "title": "Merged Record",
                "source_queries": ["core query", "bridge query"],
                "search_buckets": ["core", "theory_bridge"],
                "source_tools": ["crossref_search", "multi_source_search"],
                "recalled_by_bridges": ["b2"],
            }
        ]
    )

    by_query = {record["query"]: record for record in records}
    assert by_query["core query"]["bridge_id"] == ""
    assert by_query["bridge query"]["bridge_id"] == "b2"


def test_search_records_from_raw_does_not_guess_bridge_when_provenance_lists_misaligned():
    records = _search_records_from_raw(
        [
            {
                "title": "Merged Record",
                "source_queries": ["core query", "another core query", "bridge query"],
                "search_buckets": ["core", "core", "theory_bridge"],
                "source_tools": ["openalex_search", "openalex_search", "multi_source_search"],
                "recalled_by_bridges": ["b1", "b2"],
            }
        ]
    )

    by_query = {record["query"]: record for record in records}
    assert by_query["core query"]["bridge_id"] == ""
    assert by_query["another core query"]["bridge_id"] == ""
    assert by_query["bridge query"]["bridge_id"] == ""


def test_dedupe_search_records_merges_normalized_duplicate_queries_and_counts_calls():
    records = _dedupe_search_records(
        [
            {
                "query": "cross domain uplift modeling",
                "query_bucket": "core",
                "bridge_id": "b1",
                "tool_name": "openalex_search",
                "result_count": 20,
                "persisted_count": 20,
            },
            {
                "query": "Cross-domain uplift modeling",
                "query_bucket": "core",
                "bridge_id": "b2",
                "tool_name": "openalex_search",
                "result_count": 5,
                "persisted_count": 4,
            },
            {
                "query": "cross domain uplift modeling",
                "query_bucket": "theory_bridge",
                "bridge_id": "b2",
                "tool_name": "openalex_search",
                "result_count": 3,
                "persisted_count": 2,
            },
        ]
    )

    assert len(records) == 2
    core = next(record for record in records if record["query_bucket"] == "core")
    assert core["bridge_id"] == ""
    assert core["duplicate_call_count"] == 2
    assert core["result_count"] == 25
    assert core["persisted_count"] == 24
    bridge = next(record for record in records if record["query_bucket"] == "theory_bridge")
    assert bridge["bridge_id"] == "b2"


def test_cap_active_pool_after_seed_repair_preserves_seed_and_moves_overflow_to_backlog(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "user_seeds" / "seed_papers.jsonl",
        [
            {
                "id": "seed-special",
                "canonical_id": "seed-special",
                "title": "Seed Special Paper",
            }
        ],
    )
    active_records = [
        {
            "id": "seed-special",
            "canonical_id": "seed-special",
            "title": "Seed Special Paper",
            "source": "user_seed",
            "seed_priority": True,
        },
        *[
            {
                "id": f"paper-{idx}",
                "canonical_id": f"paper-{idx}",
                "title": f"Overflow Paper {idx}",
                "source": "openalex",
            }
            for idx in range(125)
        ],
    ]

    active, backlog, meta = _cap_active_pool_after_seed_repair(
        active_records,
        [],
        workspace,
        {"active_pool_max": 120},
    )

    assert len(active) == 120
    assert len(backlog) == 6
    assert any(record.get("title") == "Seed Special Paper" for record in active)
    assert all(record.get("t2_pool_role") == "backlog" for record in backlog)
    assert all(record.get("triaged_out") is True for record in backlog)
    assert meta["seed_repair_overflow_count"] == 6


def test_select_active_candidate_pool_caps_should_bridge_lower_than_must(tmp_path: Path):
    workspace = tmp_path / "ws"
    (workspace / "literature").mkdir(parents=True)
    (workspace / "literature" / "bridge_domain_plan.json").write_text(
        json.dumps(
            {
                "semantics": "bridge_domain_plan",
                "source": "user",
                "bridge_domains": [
                    {"bridge_id": "b1", "name": "Must", "why": "x", "priority": "must_explore", "queries": ["q1"], "source": "user"},
                    {"bridge_id": "b2", "name": "Should", "why": "x", "priority": "should_explore", "queries": ["q2"], "source": "user"},
                    {"bridge_id": "b3", "name": "Skip", "why": "x", "priority": "no_cross", "queries": ["q3"], "source": "user"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    records = []
    for bridge_id in ("b1", "b2", "b3"):
        for idx in range(10):
            records.append(
                {
                    "id": f"{bridge_id}-{idx}",
                    "canonical_id": f"{bridge_id}-{idx}",
                    "title": f"{bridge_id} Candidate {idx}",
                    "bridge_id": bridge_id,
                    "relevance_score": 0.8 - idx * 0.01,
                }
            )

    active, backlog, meta = _select_active_candidate_pool(
        records,
        workspace,
        config=T2FinalizeConfig(
            active_pool_max=30,
            screened_active_pool_cap=0,
            must_bridge_active_pool_cap_per_bridge=4,
            should_bridge_active_pool_cap_per_bridge=2,
            snowball_active_pool_cap=0,
        ),
    )

    reasons = [record.get("active_pool_reason") for record in active]
    assert sum(str(reason).startswith("bridge_recall:b1:must_explore") for reason in reasons) == 4
    assert sum(str(reason).startswith("bridge_recall:b2:should_explore") for reason in reasons) == 2
    assert not any(str(reason).startswith("bridge_recall:b3") for reason in reasons)
    assert meta["bridge_priorities"]["b3"] == "no_cross"
    assert len(backlog) == len(records) - len(active)


def test_select_active_candidate_pool_bridge_caps_not_bypassed_by_metadata_fill(tmp_path: Path):
    workspace = tmp_path / "ws"
    (workspace / "literature").mkdir(parents=True)
    (workspace / "literature" / "bridge_domain_plan.json").write_text(
        json.dumps(
            {
                "semantics": "bridge_domain_plan",
                "source": "user",
                "bridge_domains": [
                    {"bridge_id": "b1", "priority": "must_explore", "queries": ["q1"], "source": "user"},
                    {"bridge_id": "b2", "priority": "no_cross", "queries": ["q2"], "source": "user"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    records = [
        {
            "id": f"b1-{idx}",
            "canonical_id": f"b1-{idx}",
            "title": f"Bridge One Candidate {idx}",
            "bridge_id": "b1",
            "relevance_score": 0.99 - idx * 0.01,
        }
        for idx in range(8)
    ]
    records.extend(
        {
            "id": f"b2-{idx}",
            "canonical_id": f"b2-{idx}",
            "title": f"Skipped Bridge Candidate {idx}",
            "bridge_id": "b2",
            "relevance_score": 0.95 - idx * 0.01,
        }
        for idx in range(5)
    )
    records.extend(
        {
            "id": f"core-{idx}",
            "canonical_id": f"core-{idx}",
            "title": f"Core Candidate {idx}",
            "relevance_score": 0.5 - idx * 0.01,
        }
        for idx in range(10)
    )

    active, backlog, _ = _select_active_candidate_pool(
        records,
        workspace,
        config=T2FinalizeConfig(
            active_pool_max=20,
            screened_active_pool_cap=0,
            must_bridge_active_pool_cap_per_bridge=3,
            should_bridge_active_pool_cap_per_bridge=1,
            snowball_active_pool_cap=0,
        ),
    )

    active_b1 = [record for record in active if record.get("bridge_id") == "b1"]
    active_b2 = [record for record in active if record.get("bridge_id") == "b2"]
    assert len(active_b1) == 3
    assert active_b2 == []
    assert any(record.get("bridge_id") == "b1" for record in backlog)
    assert any(record.get("bridge_id") == "b2" for record in backlog)
    assert len(active) == 13


def test_select_active_candidate_pool_screened_bridge_records_obey_bridge_cap(tmp_path: Path):
    workspace = tmp_path / "ws"
    (workspace / "literature").mkdir(parents=True)
    (workspace / "literature" / "bridge_domain_plan.json").write_text(
        json.dumps(
            {
                "semantics": "bridge_domain_plan",
                "source": "user",
                "bridge_domains": [
                    {"bridge_id": "b1", "priority": "must_explore", "queries": ["q1"], "source": "user"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    records = [
        {
            "id": f"b1-screened-{idx}",
            "canonical_id": f"b1-screened-{idx}",
            "title": f"Bridge Screened Candidate {idx}",
            "bridge_id": "b1",
            "relevance_score": 0.99 - idx * 0.01,
            "semantic_screen": {"can_enter_deep_read": True, "bridge_id": "b1"},
        }
        for idx in range(8)
    ]
    records.extend(
        {
            "id": f"core-{idx}",
            "canonical_id": f"core-{idx}",
            "title": f"Core Candidate {idx}",
            "relevance_score": 0.7 - idx * 0.01,
            "semantic_screen": {"can_enter_deep_read": True},
        }
        for idx in range(5)
    )

    active, backlog, _ = _select_active_candidate_pool(
        records,
        workspace,
        config=T2FinalizeConfig(
            active_pool_max=20,
            screened_active_pool_cap=20,
            must_bridge_active_pool_cap_per_bridge=3,
            should_bridge_active_pool_cap_per_bridge=1,
            snowball_active_pool_cap=0,
        ),
    )

    assert len([record for record in active if record.get("bridge_id") == "b1"]) == 3
    assert len([record for record in active if str(record.get("id", "")).startswith("core-")]) == 5
    assert len([record for record in backlog if record.get("bridge_id") == "b1"]) == 5


@pytest.mark.asyncio
async def test_crossref_snowball_uses_existing_raw_pool_for_idempotence():
    candidates, stats = await _expand_crossref_snowball_candidates(
        [
                {
                    "id": "source",
                    "title": "Source Paper",
                    "doi": "10.1111/source",
                    "seed_priority": True,
                    "references": [{"doi": "10.2222/ref", "title": "Existing Ref"}],
                }
        ],
        existing_papers=[{"id": "doi:10.2222/ref", "title": "Existing Ref", "doi": "10.2222/ref"}],
    )

    assert candidates == []
    assert stats["attempted"] == 0
    assert stats["skipped_existing_or_duplicate_reference_dois"] == 1


@pytest.mark.asyncio
async def test_openalex_snowball_uses_existing_raw_pool_for_idempotence():
    candidates, stats = await _expand_openalex_snowball_candidates(
        [
                {
                    "id": "W_source",
                    "canonical_id": "W_source",
                    "title": "Source Paper",
                    "seed_priority": True,
                    "referenced_works": ["https://openalex.org/W456"],
                }
        ],
        existing_papers=[{"id": "W456", "canonical_id": "W456", "title": "Existing Ref"}],
    )

    assert candidates == []
    assert stats["attempted"] == 0
    assert stats["skipped_existing_or_duplicate_openalex_ids"] == 1


@pytest.mark.asyncio
async def test_openalex_snowball_detects_existing_plural_source_tools():
    candidates, stats = await _expand_openalex_snowball_candidates(
        [
                {
                    "id": "W_source",
                    "canonical_id": "W_source",
                    "title": "Source Paper",
                    "seed_priority": True,
                    "referenced_works": ["https://openalex.org/W456"],
                }
        ],
        existing_papers=[
            {
                "id": "merged-record",
                "canonical_id": "merged-record",
                "title": "Merged Snowball Record",
                "source_tool": "multi_source_search",
                "source_tools": ["multi_source_search", "openalex_snowball_backfill"],
            }
        ],
    )

    assert candidates == []
    assert stats["attempted"] == 0
    assert stats["skipped_existing_snowball_records"] == 1


@pytest.mark.asyncio
async def test_crossref_snowball_detects_existing_generic_snowball_record():
    candidates, stats = await _expand_crossref_snowball_candidates(
        [
                {
                    "id": "source",
                    "title": "Source Paper",
                    "doi": "10.1111/source",
                    "seed_priority": True,
                    "references": [{"doi": "10.2222/ref", "title": "Existing Ref"}],
                }
        ],
        existing_papers=[
            {
                "id": "merged-snowball",
                "title": "Merged Snowball Record",
                "retrieval_intent": "citation_snowball",
                "citation_snowball_source_ids": ["source"],
            }
        ],
    )

    assert candidates == []
    assert stats["attempted"] == 0
    assert stats["skipped_existing_snowball_records"] == 1


def test_openalex_detail_url_uses_cheap_doi_lookup_endpoint():
    assert _openalex_detail_url("https://doi.org/10.1145/3477495.3532058").endswith(
        "/works/doi:10.1145%2F3477495.3532058"
    )
    assert _openalex_detail_url("doi:10.1145/3477495.3532058").endswith(
        "/works/doi:10.1145%2F3477495.3532058"
    )
    assert _openalex_detail_url("W4224983022").endswith("/works/W4224983022")


def test_merge_citation_edges_preserves_existing_directed_edges():
    from researchos.runtime.t2_recovery import _merge_citation_edge_payload

    merged = _merge_citation_edge_payload(
        [["A", "B"], ["B", "A"]],
        [{"source_id": "A", "referenced_works": ["C"], "source": "recovered_existing_metadata"}],
    )

    assert ["A", "B"] in merged
    assert ["B", "A"] in merged
    assert any(isinstance(item, dict) and item.get("source_id") == "A" for item in merged)


def test_merge_enriched_records_back_to_raw_persists_metadata_cache(tmp_path):
    raw_path = tmp_path / "literature" / "papers_raw.jsonl"
    _write_jsonl(
        raw_path,
        [
            {
                "id": "doi:10.1234/source",
                "title": "Source Paper",
                "doi": "10.1234/source",
                "source_query": "core query",
                "search_bucket": "core",
            }
        ],
    )

    result = _merge_enriched_records_back_to_raw(
        raw_path,
        [
            {
                "id": "W123",
                "title": "Source Paper",
                "doi": "10.1234/source",
                "abstract": "Backfilled abstract.",
                "externalIds": {"OpenAlex": "W123", "DOI": "10.1234/source"},
                "referenced_works": ["W456"],
                "pdf_url": "https://example.org/paper.pdf",
                "source_query": "bridge query",
                "search_bucket": "theory_bridge",
            }
        ],
    )

    records = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert result["raw_cache_records_merged"] == 1
    assert len(records) == 1
    assert records[0]["abstract"] == "Backfilled abstract."
    assert records[0]["referenced_works"] == ["W456"]
    assert records[0]["pdf_urls"] == ["https://example.org/paper.pdf"]
    assert "core query" in records[0]["source_queries"]
    assert "bridge query" in records[0]["source_queries"]


class _FakeCrossrefResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeCrossrefClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, **kwargs):
        if "10.1111%2Fsource" in url:
            return _FakeCrossrefResponse(
                {
                    "message": {
                        "DOI": "10.1111/source",
                        "title": ["Source Paper"],
                        "published": {"date-parts": [[2025]]},
                        "abstract": "<jats:p>Source abstract.</jats:p>",
                        "reference-count": 1,
                        "reference": [
                            {
                                "DOI": "10.2222/ref",
                                "article-title": "Referenced Snowball Paper",
                                "year": "2024",
                            }
                        ],
                    }
                }
            )
        if "10.2222%2Fref" in url:
            return _FakeCrossrefResponse(
                {
                    "message": {
                        "DOI": "10.2222/ref",
                        "title": ["Referenced Snowball Paper"],
                        "author": [{"given": "Ada", "family": "Lovelace"}],
                        "published": {"date-parts": [[2024]]},
                        "abstract": "<jats:p>Reference abstract.</jats:p>",
                        "container-title": ["Journal of Tests"],
                        "is-referenced-by-count": 3,
                    }
                }
            )
        raise AssertionError(f"Unexpected Crossref URL: {url}")


class _FakeOpenAlexClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, **kwargs):
        assert "api.openalex.org/works/" in url
        return _FakeCrossrefResponse(
            {
                "id": "https://openalex.org/W123",
                "title": "OpenAlex Backfilled Paper",
                "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
                "publication_year": 2025,
                "doi": "https://doi.org/10.1111/openalex",
                "cited_by_count": 9,
                "abstract_inverted_index": {"Backfilled": [0], "abstract": [1]},
                "referenced_works": ["https://openalex.org/W456"],
                "related_works": ["https://openalex.org/W789"],
                "primary_location": {
                    "source": {"display_name": "Journal of Tests"},
                    "pdf_url": "https://example.org/openalex.pdf",
                    "landing_page_url": "https://example.org/openalex",
                },
                "best_oa_location": {
                    "pdf_url": "https://example.org/openalex.pdf",
                    "landing_page_url": "https://example.org/openalex",
                },
                "locations": [
                    {
                        "pdf_url": "https://example.org/openalex.pdf",
                        "landing_page_url": "https://example.org/openalex",
                    }
                ],
                "open_access": {"is_oa": True, "oa_status": "gold"},
            }
        )


class _FakeOpenAlexTitleClient:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, **kwargs):
        assert url.endswith("/works")
        params = kwargs.get("params") or {}
        assert params.get("search") == "Title Only Seed Paper"
        return _FakeCrossrefResponse(
            {
                "results": [
                    {
                        "id": "https://openalex.org/W999",
                        "title": "Title Only Seed Paper",
                        "authorships": [{"author": {"display_name": "Seed Author"}}],
                        "publication_year": 2026,
                        "doi": "https://doi.org/10.9999/seed",
                        "cited_by_count": 12,
                        "abstract_inverted_index": {"Seed": [0], "abstract": [1], "recovered": [2]},
                        "referenced_works": ["https://openalex.org/W111"],
                        "related_works": ["https://openalex.org/W222"],
                        "primary_location": {
                            "source": {"display_name": "Seed Venue"},
                            "pdf_url": "https://example.org/seed.pdf",
                        },
                        "best_oa_location": {"pdf_url": "https://example.org/seed.pdf"},
                    }
                ]
            }
        )


@pytest.mark.asyncio
async def test_openalex_backfill_adds_id_refs_abstract_and_pdf_hints(monkeypatch):
    papers = [
        {
            "id": "doi:10.1111/openalex",
            "canonical_id": "doi:10.1111/openalex",
            "title": "OpenAlex Backfilled Paper",
            "doi": "10.1111/openalex",
            "authors": ["Ada Lovelace"],
            "abstract": "",
        }
    ]

    import researchos.runtime.t2_recovery as t2_recovery

    async def _no_openalex_backfill(*args, **kwargs):
        return {
            "enabled": True,
            "candidate_count": 0,
            "attempted": 0,
            "openalex_id_filled": 0,
            "abstract_filled": 0,
            "references_filled": 0,
            "pdf_hints_filled": 0,
            "failed": 0,
        }

    monkeypatch.setattr(t2_recovery, "_backfill_recovered_openalex_metadata", _no_openalex_backfill)
    monkeypatch.setattr(
        t2_recovery.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeOpenAlexClient(*args, **kwargs),
    )

    stats = await _backfill_recovered_openalex_metadata(papers)

    assert stats["attempted"] == 1
    assert stats["openalex_id_filled"] == 1
    assert stats["abstract_filled"] == 1
    assert stats["references_filled"] == 1
    assert stats["pdf_hints_filled"] == 1
    assert papers[0]["canonical_id"] == "W123"
    assert papers[0]["doi"] == "10.1111/openalex"
    assert papers[0]["externalIds"]["OpenAlex"] == "W123"
    assert papers[0]["referenced_works"] == ["W456"]
    assert papers[0]["related_works"] == ["W789"]
    assert papers[0]["pdf_url"] == "https://example.org/openalex.pdf"
    assert papers[0]["open_access"]["is_oa"] is True


@pytest.mark.asyncio
async def test_crossref_backfill_merges_references_when_openalex_refs_exist(monkeypatch):
    papers = [
        {
            "id": "doi:10.1111/source",
            "canonical_id": "W_source",
            "title": "Source Paper",
            "doi": "10.1111/source",
            "abstract": "Already has abstract.",
            "referenced_works": ["W_openalex_ref"],
        }
    ]

    import researchos.runtime.t2_recovery as t2_recovery

    monkeypatch.setattr(
        t2_recovery.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeCrossrefClient(*args, **kwargs),
    )

    stats = await _backfill_recovered_crossref_metadata(papers)

    assert stats["references_filled"] == 1
    assert papers[0]["referenced_works"] == ["W_openalex_ref"]
    assert any(ref.get("doi") == "10.2222/ref" for ref in papers[0]["references"])


@pytest.mark.asyncio
async def test_crossref_backfill_accepts_string_title_and_issued_year(monkeypatch):
    papers = [
        {
            "id": "doi:10.1111/source",
            "canonical_id": "doi:10.1111/source",
            "doi": "10.1111/source",
            "title": "",
            "abstract": "",
        }
    ]

    class _IssuedCrossrefClient(_FakeCrossrefClient):
        async def get(self, url: str, **kwargs):
            return _FakeCrossrefResponse(
                {
                    "message": {
                        "DOI": "10.1111/source",
                        "title": "String Title From Crossref",
                        "issued": {"date-parts": [[2026, 1, 1]]},
                        "abstract": "<jats:p>Recovered abstract.</jats:p>",
                    }
                }
            )

    import researchos.runtime.t2_recovery as t2_recovery

    monkeypatch.setattr(
        t2_recovery.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _IssuedCrossrefClient(*args, **kwargs),
    )

    stats = await _backfill_recovered_crossref_metadata(papers)

    assert stats["abstract_filled"] == 1
    assert papers[0]["title"] == "String Title From Crossref"
    assert papers[0]["year"] == 2026


@pytest.mark.asyncio
async def test_openalex_title_backfill_repairs_title_only_seed_metadata(monkeypatch):
    papers = [
        {
            "id": "seed-title-only",
            "canonical_id": "seed-title-only",
            "source": "user_seed",
            "title": "Title Only Seed Paper",
            "authors": ["Seed Author"],
            "abstract": "",
        }
    ]

    import researchos.runtime.t2_recovery as t2_recovery

    monkeypatch.setattr(
        t2_recovery.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeOpenAlexTitleClient(*args, **kwargs),
    )

    stats = await _backfill_recovered_openalex_title_metadata(papers)

    assert stats["eligible_count"] == 1
    assert stats["matched"] == 1
    assert stats["doi_filled"] == 1
    assert stats["openalex_id_filled"] == 1
    assert stats["abstract_filled"] == 1
    assert stats["references_filled"] == 1
    assert stats["pdf_hints_filled"] == 1
    assert papers[0]["canonical_id"] == "W999"
    assert papers[0]["doi"] == "10.9999/seed"
    assert papers[0]["abstract"] == "Seed abstract recovered"
    assert papers[0]["pdf_url"] == "https://example.org/seed.pdf"
    assert papers[0]["_metadata_backfilled_from_title"] == "openalex"


@pytest.mark.asyncio
async def test_backfill_stats_report_skipped_by_cap_and_remaining(monkeypatch):
    papers = [
        {
            "id": f"doi:10.1111/{idx}",
            "canonical_id": f"doi:10.1111/{idx}",
            "title": f"Paper {idx}",
            "doi": f"10.1111/{idx}",
            "abstract": "",
        }
        for idx in range(3)
    ]

    import researchos.runtime.t2_recovery as t2_recovery

    monkeypatch.setattr(
        t2_recovery.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeOpenAlexClient(*args, **kwargs),
    )

    stats = await _backfill_recovered_openalex_metadata(papers, max_papers=1)

    assert stats["eligible_count"] == 3
    assert stats["candidate_count"] == 1
    assert stats["attempted"] == 1
    assert stats["skipped_by_cap"] == 2
    assert "remaining_missing_abstract" in stats
    assert "remaining_missing_pdf_hints" in stats


@pytest.mark.asyncio
async def test_finalize_t2_outputs_persists_crossref_snowball_and_structured_log(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "ws"
    (workspace / "literature").mkdir(parents=True)
    (workspace / "project.yaml").write_text(
        "research_direction: citation recovery\nkeywords: [citation, recovery]\n",
        encoding="utf-8",
    )
    _write_jsonl(
        workspace / "literature" / "papers_raw.jsonl",
        [
            {
                "id": "doi:10.1111/source",
                "canonical_id": "doi:10.1111/source",
                "source": "crossref",
                "source_tool": "crossref_search",
                "source_query": "citation recovery",
                "search_bucket": "core",
                "title": "Source Paper",
                "authors": ["Grace Hopper"],
                "year": 2025,
                "abstract": "",
                "doi": "10.1111/source",
                "venue": "Source Venue",
                "citation_count": 2,
                "seed_priority": True,
            }
        ],
    )

    import researchos.runtime.t2_recovery as t2_recovery

    monkeypatch.setattr(
        t2_recovery.httpx,
        "AsyncClient",
        lambda *args, **kwargs: _FakeCrossrefClient(*args, **kwargs),
    )

    result = await finalize_t2_outputs(workspace, trace_paths=[])

    assert result["ok"] is True
    assert result["citation_backfill"]["added"] == 1
    assert result["citation_backfill"]["raw_persist_ok"] is True

    raw_records = [
        json.loads(line)
        for line in (workspace / "literature" / "papers_raw.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(record.get("source") == "crossref_snowball" for record in raw_records)
    assert any(record.get("source_tool") == "crossref_snowball_backfill" for record in raw_records)

    dedup_records = [
        json.loads(line)
        for line in (workspace / "literature" / "papers_dedup.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(record.get("title") == "Referenced Snowball Paper" for record in dedup_records)

    search_log = (workspace / "literature" / "search_log.md").read_text(encoding="utf-8")
    assert "crossref_snowball_backfill" in search_log
    assert "Crossref citation snowball 补全" in search_log
    assert "raw_persisted=1" in search_log
    assert "## Bucket 覆盖" in search_log

    verified_records = [
        json.loads(line)
        for line in (workspace / "literature" / "papers_verified.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    snowball_verified = next(record for record in verified_records if record.get("title") == "Referenced Snowball Paper")
    assert snowball_verified["citation_snowball_source_id"] == "doi:10.1111/source"

    citation_edges = json.loads((workspace / "literature" / "citation_edges.json").read_text(encoding="utf-8"))
    assert any(
        edge.get("source") == "crossref_snowball_backfill"
        and edge.get("source_id") == "doi:10.1111/source"
        and "doi:10.2222/ref" in edge.get("referenced_works", [])
        for edge in citation_edges
    )


@pytest.mark.asyncio
async def test_finalize_t2_outputs_caps_active_pool_and_writes_backlog(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "ws"
    (workspace / "literature").mkdir(parents=True)
    (workspace / "project.yaml").write_text(
        "research_direction: hidden cap regression\nkeywords: [cap, regression]\n",
        encoding="utf-8",
    )
    _write_jsonl(
        workspace / "user_seeds" / "seed_papers.jsonl",
        [
            {
                "id": "seed-0",
                "canonical_id": "seed-0",
                "title": "Seed Paper Zero",
                "why_relevant": "user seed",
            }
        ],
    )
    raw_records = [
        {
            "id": f"paper-{idx}",
            "canonical_id": f"paper-{idx}",
            "source": "openalex",
            "source_tool": "multi_source_search",
            "source_query": "cap regression",
            "search_bucket": "core",
            "title": f"Paper {idx:03d} UniqueToken{idx * 7919:08x} Evidence {idx}",
            "authors": ["A. Researcher"],
            "year": 2025,
            "abstract": f"Abstract for cap regression paper {idx}.",
            "venue": "Test Venue",
            "citation_count": idx,
            "relevance_score": 0.1,
        }
        for idx in range(150)
    ]
    raw_records.append(
        {
            "id": "seed-0",
            "canonical_id": "seed-0",
            "source": "user_seed",
            "source_bucket": "seed",
            "title": "Seed Paper Zero",
            "authors": ["Seed Author"],
            "year": 2024,
            "abstract": "Seed abstract.",
            "venue": "Seed Venue",
            "citation_count": 999,
            "relevance_score": 1.0,
        }
    )
    _write_jsonl(workspace / "literature" / "papers_raw.jsonl", raw_records)

    import researchos.runtime.t2_recovery as t2_recovery

    async def _no_openalex_backfill(*args, **kwargs):
        return {
            "enabled": True,
            "candidate_count": 0,
            "attempted": 0,
            "openalex_id_filled": 0,
            "abstract_filled": 0,
            "references_filled": 0,
            "pdf_hints_filled": 0,
            "failed": 0,
        }

    async def _no_metadata_backfill(*args, **kwargs):
        return {"enabled": True, "candidate_count": 0, "attempted": 0, "abstract_filled": 0, "references_filled": 0, "failed": 0}

    async def _no_snowball(*args, **kwargs):
        return [], {"enabled": True, "source_candidates": 0, "sources_used": 0, "reference_dois_seen": 0, "attempted": 0, "added": 0, "failed": 0}

    monkeypatch.setattr(t2_recovery, "_backfill_recovered_openalex_metadata", _no_openalex_backfill)
    monkeypatch.setattr(
        t2_recovery,
        "_backfill_recovered_openalex_title_metadata",
        lambda *args, **kwargs: _no_openalex_backfill(*args, **kwargs),
    )
    monkeypatch.setattr(t2_recovery, "_backfill_recovered_crossref_metadata", _no_metadata_backfill)
    monkeypatch.setattr(t2_recovery, "_backfill_recovered_multisource_abstracts", _no_metadata_backfill)
    monkeypatch.setattr(t2_recovery, "_expand_openalex_snowball_candidates", _no_snowball)
    monkeypatch.setattr(t2_recovery, "_expand_crossref_snowball_candidates", _no_snowball)

    result = await finalize_t2_outputs(workspace, trace_paths=[])

    assert result["ok"] is True
    assert result["dedup_count"] == 120
    assert result["backlog_count"] == 31

    verified_records = [
        json.loads(line)
        for line in (workspace / "literature" / "papers_verified.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(verified_records) == 120
    assert any(record.get("title") == "Seed Paper Zero" for record in verified_records)


@pytest.mark.asyncio
async def test_finalize_t2_outputs_keeps_domain_filtered_records_in_backlog(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "ws"
    (workspace / "literature").mkdir(parents=True)
    (workspace / "project.yaml").write_text(
        "research_direction: domain profile audit\n"
        "keywords: [target]\n"
        "domain_profile:\n"
        "  target_domain: target-domain\n"
        "  include_keywords: [target]\n"
        "  exclude_keywords: [excluded]\n",
        encoding="utf-8",
    )
    _write_jsonl(
        workspace / "literature" / "papers_raw.jsonl",
        [
            {
                "id": "keep",
                "canonical_id": "keep",
                "title": "Target Mechanism Paper",
                "abstract": "A target-domain abstract.",
                "year": 2025,
                "source": "openalex",
                "source_query": "target",
            },
            {
                "id": "filtered",
                "canonical_id": "filtered",
                "title": "Excluded Mechanism Paper",
                "abstract": "An excluded-domain abstract.",
                "year": 2025,
                "source": "openalex",
                "source_query": "target",
            },
        ],
    )

    import researchos.runtime.t2_recovery as t2_recovery

    async def _no_openalex_backfill(*args, **kwargs):
        return {"enabled": True, "candidate_count": 0, "attempted": 0}

    async def _no_metadata_backfill(*args, **kwargs):
        return {"enabled": True, "candidate_count": 0, "attempted": 0, "abstract_filled": 0, "references_filled": 0, "failed": 0}

    async def _no_snowball(*args, **kwargs):
        return [], {"enabled": True, "source_candidates": 0, "sources_used": 0, "attempted": 0, "added": 0, "failed": 0}

    monkeypatch.setattr(t2_recovery, "_backfill_recovered_openalex_metadata", _no_openalex_backfill)
    monkeypatch.setattr(t2_recovery, "_backfill_recovered_openalex_title_metadata", _no_openalex_backfill)
    monkeypatch.setattr(t2_recovery, "_backfill_recovered_crossref_metadata", _no_metadata_backfill)
    monkeypatch.setattr(t2_recovery, "_backfill_recovered_multisource_abstracts", _no_metadata_backfill)
    monkeypatch.setattr(t2_recovery, "_expand_openalex_snowball_candidates", _no_snowball)
    monkeypatch.setattr(t2_recovery, "_expand_crossref_snowball_candidates", _no_snowball)

    result = await finalize_t2_outputs(workspace, trace_paths=[])

    assert result["ok"] is True
    verified = [json.loads(line) for line in (workspace / "literature" / "papers_verified.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    backlog = [json.loads(line) for line in (workspace / "literature" / "papers_backlog.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [record["id"] for record in verified] == ["keep"]
    assert [record["id"] for record in backlog] == ["filtered"]
    assert backlog[0]["triaged_reason"] == "domain_profile_filtered"
    assert backlog[0]["read_disposition"] == "backlog"
    search_log = (workspace / "literature" / "search_log.md").read_text(encoding="utf-8")
    assert "T2 active candidate pool" in search_log
    assert "papers_backlog.jsonl" in search_log
    progress = (workspace / "literature" / "temp" / "scout_progress.md").read_text(encoding="utf-8")
    assert "runtime_finalize_started" in progress
    assert "runtime_active_pool_final" in progress
    assert "runtime_finalize_done" in progress


@pytest.mark.asyncio
async def test_finalize_t2_outputs_uses_agent_params_for_pool_and_queue(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "agent_params.yaml"
    config_path.write_text(
        """
agents:
  scout:
    behavior:
      t2_finalize:
        finish_finalize_min_raw: 10
        active_pool_max: 20
        screened_active_pool_cap: 5
        bridge_active_pool_cap_per_bridge: 2
        snowball_active_pool_cap: 1
        dedup_title_threshold: 0.96
        access_audit_top_n: 8
        metadata_backfill_max_concurrency: 2
        abstract_backfill_title_match_threshold: 0.87
        abstract_backfill_max_concurrency: 2
        snowball_max_sources: 2
        snowball_refs_per_source: 3
        snowball_max_candidates: 4
        snowball_max_concurrency: 2
        snowball_title_match_threshold: 0.92
      progress:
        enabled: true
        update_on_tool_results: true
        update_on_finalize: true
  reader:
    modes:
      read:
        behavior:
          abstract_sweep:
            enabled: true
        deep_read_min: 12
        deep_read_target: 12
        deep_read_max: 14
        probe_pool: 14
        mainline_screened_cap: 18
        bridge_deep_floor: 1
        bridge_screened_cap: 2
        bridge_pool_cap: 3
        citation_hub_slots: 1
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCHOS_AGENT_PARAMS", str(config_path))
    clear_cache()

    workspace = tmp_path / "configured-ws"
    (workspace / "literature").mkdir(parents=True)
    (workspace / "project.yaml").write_text(
        "research_direction: configured cap regression\nkeywords: [configured, cap]\n",
        encoding="utf-8",
    )
    _write_jsonl(
        workspace / "literature" / "papers_raw.jsonl",
        [
            {
                "id": f"paper-{idx}",
                "canonical_id": f"paper-{idx}",
                "source": "openalex",
                "source_tool": "multi_source_search",
                "source_query": "configured cap",
                "search_bucket": "core",
                "title": f"Configured Paper {idx:03d} Unique {idx}",
                "authors": ["A. Researcher"],
                "year": 2025,
                "abstract": f"Abstract for configured paper {idx}.",
                "venue": "Test Venue",
                "citation_count": idx,
                "relevance_score": 0.1,
            }
            for idx in range(45)
        ],
    )

    import researchos.runtime.t2_recovery as t2_recovery

    async def _no_openalex_backfill(*args, **kwargs):
        return {
            "enabled": True,
            "candidate_count": 0,
            "attempted": 0,
            "openalex_id_filled": 0,
            "abstract_filled": 0,
            "references_filled": 0,
            "pdf_hints_filled": 0,
            "failed": 0,
        }

    async def _no_metadata_backfill(*args, **kwargs):
        return {"enabled": True, "candidate_count": 0, "attempted": 0, "abstract_filled": 0, "references_filled": 0, "failed": 0}

    async def _no_snowball(*args, **kwargs):
        return [], {"enabled": True, "source_candidates": 0, "sources_used": 0, "reference_dois_seen": 0, "attempted": 0, "added": 0, "failed": 0}

    monkeypatch.setattr(t2_recovery, "_backfill_recovered_openalex_metadata", _no_openalex_backfill)
    monkeypatch.setattr(
        t2_recovery,
        "_backfill_recovered_openalex_title_metadata",
        lambda *args, **kwargs: _no_openalex_backfill(*args, **kwargs),
    )
    monkeypatch.setattr(t2_recovery, "_backfill_recovered_crossref_metadata", _no_metadata_backfill)
    monkeypatch.setattr(t2_recovery, "_backfill_recovered_multisource_abstracts", _no_metadata_backfill)
    monkeypatch.setattr(t2_recovery, "_expand_openalex_snowball_candidates", _no_snowball)
    monkeypatch.setattr(t2_recovery, "_expand_crossref_snowball_candidates", _no_snowball)

    try:
        result = await finalize_t2_outputs(workspace, trace_paths=[])
    finally:
        clear_cache()

    assert result["ok"] is True
    assert result["dedup_count"] == 20
    assert result["backlog_count"] == 25
    assert result["t2_finalize_config"]["active_pool_max"] == 20
    assert result["deep_read_queue_config"]["deep_read_target"] == 12

    queue_meta = json.loads((workspace / "literature" / "deep_read_queue_meta.json").read_text(encoding="utf-8"))
    assert queue_meta["deep_read_target"] == 12
    assert queue_meta["deep_read_max"] == 14
    assert queue_meta["mainline_screened_cap"] == 18

    search_log = (workspace / "literature" / "search_log.md").read_text(encoding="utf-8")
    assert "finish_finalize_min_raw=10" in search_log
    assert "active_pool_max=20" in search_log
    assert "dedup_title_threshold=0.96" in search_log
    assert "snowball_max_candidates=4" in search_log
    assert "deep_read_target=12" in search_log
    assert "mainline_screened_cap=18" in search_log

    progress = (workspace / "literature" / "temp" / "scout_progress.md").read_text(encoding="utf-8")
    assert "active_pool_max=20" in progress
    assert "deep_read_target=12" in progress
