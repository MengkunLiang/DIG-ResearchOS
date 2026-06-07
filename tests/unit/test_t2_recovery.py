from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.runtime.t2_recovery import (
    _backfill_recovered_crossref_metadata,
    _backfill_recovered_openalex_metadata,
    _backfill_recovered_openalex_title_metadata,
    _merge_enriched_records_back_to_raw,
    _openalex_detail_url,
    _search_records_from_raw,
    finalize_t2_outputs,
)


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
async def test_finalize_t2_outputs_does_not_hidden_cap_verified_pool(monkeypatch, tmp_path: Path):
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
    monkeypatch.setattr(t2_recovery, "_expand_crossref_snowball_candidates", _no_snowball)

    result = await finalize_t2_outputs(workspace, trace_paths=[])

    assert result["ok"] is True
    assert result["dedup_count"] == 151

    verified_records = [
        json.loads(line)
        for line in (workspace / "literature" / "papers_verified.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(verified_records) == 151
    assert any(record.get("title") == "Seed Paper Zero" for record in verified_records)

    queue_meta = json.loads((workspace / "literature" / "deep_read_queue_meta.json").read_text(encoding="utf-8"))
    assert queue_meta["verified_pool_count"] == 151
    assert queue_meta["verified_disposition_coverage"] == 1.0
