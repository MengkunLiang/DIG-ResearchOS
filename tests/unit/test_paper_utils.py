from __future__ import annotations

import pytest

from researchos.tools.paper_enrichment_tool import DetectDuplicateQueriesTool
from researchos.tools.paper_utils import expand_queries, filter_by_domain, score_papers
from researchos.tools.paper_utils_tool import ExpandQueriesTool, LogScoutProgressTool
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


def test_expand_queries_uses_seed_title_as_nonempty_fallback():
    queries = expand_queries(
        [{"title": "Transfer Learning on Heterogeneous Feature Spaces for Treatment Effects Estimation"}],
        "",
        max_queries=3,
    )

    assert queries
    assert queries[0] == "Transfer Learning on Heterogeneous Feature Spaces for Treatment Effects Estimation"


@pytest.mark.asyncio
async def test_expand_queries_tool_rejects_empty_query_plan():
    tool = ExpandQueriesTool()

    result = await tool.execute(seed_papers=[], topic="   ", llm_queries=["", "  "], max_queries=5)

    assert not result.ok
    assert result.error == "empty_query_plan"
    assert result.data["queries"] == []


@pytest.mark.asyncio
async def test_detect_duplicate_queries_rejects_all_blank_queries():
    tool = DetectDuplicateQueriesTool()

    result = await tool.execute(queries=["", "   "], threshold=0.7)

    assert not result.ok
    assert result.error == "empty_query_plan"


@pytest.mark.asyncio
async def test_log_scout_progress_rejects_empty_search_result(tmp_path):
    tool = LogScoutProgressTool()
    tool.set_workspace_dir(str(tmp_path))

    result = await tool.execute(action="search_result", query=" ", source="", count=0)

    assert not result.ok
    assert result.error == "invalid_progress_event"
    assert not (tmp_path / "literature" / "temp" / "scout_progress.md").exists()


@pytest.mark.asyncio
async def test_log_scout_progress_allows_queries_without_detail(tmp_path):
    tool = LogScoutProgressTool()
    tool.set_workspace_dir(str(tmp_path))

    result = await tool.execute(action="queries", queries=["causal retrieval"])

    assert result.ok
    progress = (tmp_path / "literature" / "temp" / "scout_progress.md").read_text(encoding="utf-8")
    assert "causal retrieval" in progress


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
