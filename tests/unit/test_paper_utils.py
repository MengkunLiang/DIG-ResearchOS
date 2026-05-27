from __future__ import annotations

from researchos.tools.paper_utils import expand_queries, score_papers
from researchos.tools.semantic_scholar import _normalize_paper


def test_expand_queries_uses_dynamic_recent_year_windows():
    queries = expand_queries([], "adaptive retrieval", current_year=2028)

    assert "adaptive retrieval 2026-2028" in queries
    assert "adaptive retrieval 2025-2027" in queries
    assert "adaptive retrieval 2024-2026" not in queries


def test_score_papers_accepts_explicit_current_year_for_reproducibility():
    papers = [
        {"title": "New keyword", "abstract": "keyword", "year": 2028, "citation_count": 0},
        {"title": "Old keyword", "abstract": "keyword", "year": 2021, "citation_count": 0},
    ]

    scored = score_papers(papers, ["keyword"], current_year=2028)

    assert scored[0]["relevance_score"] > scored[1]["relevance_score"]


def test_semantic_scholar_normalizer_returns_common_shape():
    paper = _normalize_paper(
        {
            "paperId": "S2-1",
            "title": "A Paper",
            "authors": [{"name": "Ada Lovelace"}],
            "year": None,
            "externalIds": {"DOI": "10.1234/test"},
            "url": "https://semanticscholar.org/paper/S2-1",
        }
    )

    assert paper["source"] == "semantic_scholar"
    assert paper["authors"] == ["Ada Lovelace"]
    assert paper["year"] is None
    assert paper["doi"] == "10.1234/test"
    assert paper["provenance"]["source_tool"] == "semantic_scholar_search"
