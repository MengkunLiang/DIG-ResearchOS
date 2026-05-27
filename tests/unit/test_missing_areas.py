"""Tests for generate_missing_areas_report retrieval coverage hint output."""

from __future__ import annotations

from researchos.runtime.t2_recovery import generate_missing_areas_report


def _make_project(keywords: list[str] | None = None, direction: str = "Test direction") -> dict:
    return {
        "research_direction": direction,
        "keywords": keywords or ["memory", "retrieval", "agent"],
    }


def _make_paper(title: str, abstract: str = "", year: int = 2025, venue: str = "arxiv") -> dict:
    return {"title": title, "abstract": abstract, "year": year, "venue": venue}


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------


def test_report_has_structured_gap_section_when_keywords_missing():
    """Low-coverage keywords should produce retrieval hint sections."""
    project = _make_project(keywords=["quantum_computing", "blockchain"])
    papers = [
        _make_paper("Paper A", "about memory systems"),
        _make_paper("Paper B", "about retrieval augmented generation"),
    ]
    report = generate_missing_areas_report(project, papers, current_year=2026)
    assert "## Retrieval Coverage Hints（不是研究缺口结论）" in report
    assert "### 提示" in report
    assert "**覆盖缺口**" in report
    assert "**为什么需要复核**" in report
    assert "**建议动作**" in report
    assert "- **难度**:" in report
    assert "不能直接宣称领域空白" in report


def test_report_no_gap_section_when_all_covered():
    """When all keywords are well-covered, no structured gaps."""
    project = _make_project(keywords=["memory"])
    # Generate enough papers with "memory" to cross threshold
    papers = [_make_paper(f"Memory Paper {i}", "memory is important") for i in range(20)]
    report = generate_missing_areas_report(project, papers, current_year=2026)
    # Should not have structured gaps since keyword is well-covered
    # (might still have structural gaps, but not keyword-based ones)
    assert "覆盖较好的主题" in report


def test_structured_gap_from_recent_year_shortage():
    """When recent papers are scarce, should produce a year-based hint."""
    project = _make_project(keywords=["memory", "retrieval"])
    # All papers from 2020 — old
    papers = [_make_paper(f"Old Paper {i}", "memory retrieval", year=2020) for i in range(30)]
    report = generate_missing_areas_report(project, papers, current_year=2026)
    assert "## Retrieval Coverage Hints（不是研究缺口结论）" in report
    assert "最新论文覆盖不足" in report


def test_structured_hint_from_source_type_review_need():
    """When source_type is unknown for many papers, should produce a review hint."""
    project = _make_project(keywords=["memory", "retrieval"])
    papers = [
        dict(_make_paper(f"Paper {i}", "memory retrieval", year=2025, venue="Unfamiliar"), source_type="unknown")
        for i in range(30)
    ]
    report = generate_missing_areas_report(project, papers, current_year=2026)
    assert "## Retrieval Coverage Hints（不是研究缺口结论）" in report
    assert "source_type 复核不足" in report


def test_structured_gap_from_concentration():
    """When one keyword dominates, should produce a concentration gap."""
    project = _make_project(keywords=["memory", "retrieval", "agent", "planning", "reasoning"])
    # 25 out of 40 papers mention "memory" — highly concentrated
    # Need at least 3 covered keywords (threshold = max(4, 40//12) = 4)
    papers = [_make_paper(f"Memory Paper {i}", "memory is great") for i in range(25)]
    papers += [_make_paper(f"Retrieval Paper {i}", "retrieval augmented") for i in range(6)]
    papers += [_make_paper(f"Agent Paper {i}", "agent system") for i in range(5)]
    papers += [_make_paper(f"Other Paper {i}", "something else") for i in range(4)]
    report = generate_missing_areas_report(project, papers, current_year=2026)
    assert "## Retrieval Coverage Hints（不是研究缺口结论）" in report
    assert "检索视角过于集中" in report


# ---------------------------------------------------------------------------
# Gap bullet completeness
# ---------------------------------------------------------------------------


def test_each_hint_has_all_four_bullets():
    """Every ### 提示 section must have all 4 required bullets."""
    project = _make_project(keywords=["nonexistent_topic_xyz"])
    papers = [_make_paper("Unrelated paper", "about cooking recipes", year=2020, venue="arxiv")]
    report = generate_missing_areas_report(project, papers, current_year=2026)

    # Split by hint headers
    import re
    hint_sections = re.split(r"### 提示 \d+", report)
    for i, section in enumerate(hint_sections[1:], 1):
        assert "**覆盖缺口**" in section, f"提示 {i} missing 覆盖缺口"
        assert "**为什么需要复核**" in section, f"提示 {i} missing 为什么需要复核"
        assert "**建议动作**" in section, f"提示 {i} missing 建议动作"
        assert "- **难度**:" in section, f"提示 {i} missing 难度"


# ---------------------------------------------------------------------------
# Year distribution section preserved
# ---------------------------------------------------------------------------


def test_year_distribution_still_present():
    project = _make_project(keywords=["memory"])
    papers = [_make_paper(f"P{i}", year=2020 + (i % 5)) for i in range(20)]
    report = generate_missing_areas_report(project, papers, current_year=2026)
    assert "## 年份分布" in report


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_papers():
    project = _make_project(keywords=["memory"])
    report = generate_missing_areas_report(project, [], current_year=2026)
    assert "## 当前覆盖概况" in report


def test_missing_abstract_counter():
    """Papers with _missing_abstract flag should be counted."""
    project = _make_project(keywords=["memory", "retrieval"])
    papers = [_make_paper(f"P{i}", "memory retrieval", year=2025) for i in range(10)]
    # Mark half as missing abstract
    for p in papers[:6]:
        p["_missing_abstract"] = True
    report = generate_missing_areas_report(project, papers, current_year=2026)
    assert "## Retrieval Coverage Hints（不是研究缺口结论）" in report
    assert "摘要缺失" in report
