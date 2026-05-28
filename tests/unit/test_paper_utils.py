from __future__ import annotations

from researchos.tools.paper_utils import expand_queries, filter_by_domain, score_papers
from researchos.tools.semantic_scholar import _normalize_paper


def test_expand_queries_uses_dynamic_recent_year_windows():
    queries = expand_queries([], "adaptive retrieval", current_year=2028)

    assert "adaptive retrieval 2026-2028" in queries
    assert "adaptive retrieval 2025-2027" in queries
    assert "adaptive retrieval 2024-2026" not in queries


def test_expand_queries_uses_llm_profile_without_builtin_ai_expansion():
    queries = expand_queries(
        [],
        "memory retrieval",
        current_year=2028,
        max_queries=8,
        domain_profile={"query_variants": ["cognitive psychology recall"], "include_keywords": ["human memory"]},
        llm_queries=["episodic recall experiments"],
    )

    assert "episodic recall experiments" in queries
    assert "cognitive psychology recall memory retrieval" in queries
    assert "LLM memory retrieval" not in queries
    assert "AI memory retrieval" not in queries


def test_filter_by_domain_without_profile_keeps_all_papers():
    papers = [
        {"title": "Human memory retrieval", "abstract": "psychology experiment"},
        {"title": "LLM memory retrieval", "abstract": "agent system"},
    ]

    assert filter_by_domain(papers, target_domain="cs") == papers


def test_filter_by_domain_uses_llm_profile_terms():
    papers = [
        {"title": "Human memory retrieval", "abstract": "psychology experiment", "venue": "CogSci"},
        {"title": "LLM memory retrieval", "abstract": "agent system", "venue": "arXiv"},
    ]

    filtered = filter_by_domain(
        papers,
        target_domain="llm_agents",
        domain_profile={
            "include_keywords": ["llm", "agent"],
            "exclude_keywords": ["psychology"],
        },
    )

    assert [paper["title"] for paper in filtered] == ["LLM memory retrieval"]


def test_score_papers_accepts_explicit_current_year_for_reproducibility():
    papers = [
        {"title": "New keyword", "abstract": "keyword", "year": 2028, "citation_count": 0},
        {"title": "Old keyword", "abstract": "keyword", "year": 2021, "citation_count": 0},
    ]

    scored = score_papers(papers, ["keyword"], current_year=2028)

    assert scored[0]["relevance_score"] > scored[1]["relevance_score"]
    assert scored[0]["priority_score_hint"] == scored[0]["relevance_score"]
    assert scored[0]["relevance_score_semantics"] == "metadata_priority_hint_requires_llm_review"


def test_score_papers_methodological_signal_is_hint_not_default_rank_factor():
    papers = [
        {"title": "Ablation keyword", "abstract": "keyword ablation without component", "year": 2028, "citation_count": 0},
        {"title": "Plain keyword", "abstract": "keyword", "year": 2028, "citation_count": 0},
    ]

    scored = score_papers(papers, ["keyword"], current_year=2028)

    assert scored[0]["methodological_signal"] > scored[1]["methodological_signal"]
    assert scored[0]["relevance_score"] == scored[1]["relevance_score"]


def test_score_papers_missing_source_type_is_unknown_neutral_hint():
    papers = [
        {"title": "Unknown keyword", "abstract": "keyword", "year": 2028, "citation_count": 0},
        {
            "title": "Preprint keyword",
            "abstract": "keyword",
            "year": 2028,
            "citation_count": 0,
            "source_type": "preprint",
        },
    ]

    scored = score_papers(papers, ["keyword"], current_year=2028)

    unknown = next(paper for paper in scored if paper["title"].startswith("Unknown"))
    preprint = next(paper for paper in scored if paper["title"].startswith("Preprint"))
    assert unknown["relevance_score_components"]["source_type"] == 0.5
    assert preprint["relevance_score_components"]["source_type"] == 0.6


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
