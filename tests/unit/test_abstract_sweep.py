"""Tests for the abstract sweep module."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from researchos.runtime.abstract_sweep import (
    build_sweep_candidates,
    generate_abstract_note,
    generate_bib_entry,
    generate_comparison_row,
    run_abstract_sweep,
    run_abstract_sweep_with_reader,
    _normalize_id,
    _split_sentences,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _sample_paper(**overrides) -> dict:
    base = {
        "id": "arxiv:2301.12345",
        "paper_id": "arxiv:2301.12345",
        "title": "Efficient Graph Learning via Causal Contrastive",
        "year": 2023,
        "venue": "NeurIPS 2023",
        "authors": ["Alice Smith", "Bob Jones"],
        "abstract": (
            "Graph neural networks have shown remarkable performance. "
            "However, existing methods rely on spurious correlations. "
            "We propose a causal contrastive framework that disentangles true effects. "
            "Our method achieves 5.2% improvement on ogbn-arxiv. "
            "Extensive ablation studies confirm the effectiveness of each component."
        ),
        "relevance_score": 0.85,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _normalize_id
# ---------------------------------------------------------------------------


def test_normalize_id_basic():
    assert _normalize_id({"id": "arxiv:2301.12345"}) == "arxiv_2301.12345"


def test_normalize_id_uses_normalized_id_first():
    assert _normalize_id({"normalized_id": "custom_id", "id": "other"}) == "custom_id"


def test_normalize_id_empty():
    assert _normalize_id({}) == ""


# ---------------------------------------------------------------------------
# _split_sentences
# ---------------------------------------------------------------------------


def test_split_sentences_basic():
    text = "First sentence. Second one! Third?"
    sentences = _split_sentences(text)
    assert len(sentences) == 3
    assert sentences[0] == "First sentence."


def test_split_sentences_empty():
    assert _split_sentences("") == []


# ---------------------------------------------------------------------------
# generate_abstract_note
# ---------------------------------------------------------------------------


def test_generate_abstract_note_has_required_sections():
    note = generate_abstract_note(_sample_paper())
    assert "## 1. Problem & Motivation" in note
    assert "## 2. Method Summary" in note
    assert "## 3. Key Claimed Results" in note
    assert "## 13. Mechanism Claim" in note
    assert "## Source" in note


def test_generate_abstract_note_status_is_abstract_only():
    note = generate_abstract_note(_sample_paper())
    assert "ABSTRACT-ONLY" in note


def test_generate_abstract_note_mechanism_claim_fields():
    note = generate_abstract_note(_sample_paper())
    assert "- **Stated mechanism**:" in note
    assert "- **Evidence type**:" in note
    assert "- **Supporting artifact**:" in note
    assert "abstract_claim_hint" in note


def test_generate_abstract_note_includes_metadata():
    note = generate_abstract_note(_sample_paper())
    assert "Efficient Graph Learning" in note
    assert "Alice Smith" in note
    assert "2023" in note


def test_generate_abstract_note_handles_no_abstract():
    paper = _sample_paper(abstract="")
    note = generate_abstract_note(paper)
    assert "no abstract available" in note


# ---------------------------------------------------------------------------
# generate_comparison_row
# ---------------------------------------------------------------------------


def test_generate_comparison_row_has_evidence_level():
    row = generate_comparison_row(_sample_paper())
    assert "ABSTRACT_ONLY" in row


def test_generate_comparison_row_csv_format():
    row = generate_comparison_row(_sample_paper())
    fields = row.split(",")
    assert len(fields) == 11  # 11 columns
    assert fields[-1] == "ABSTRACT_ONLY"


def test_generate_comparison_row_escapes_commas():
    paper = _sample_paper(title="A, B, and C")
    row = generate_comparison_row(paper)
    assert '"A, B, and C"' in row


# ---------------------------------------------------------------------------
# generate_bib_entry
# ---------------------------------------------------------------------------


def test_generate_bib_entry_format():
    entry = generate_bib_entry(_sample_paper())
    assert entry.startswith("@inproceedings{")
    assert "title = {" in entry
    assert "author = {Alice Smith and Bob Jones}" in entry
    assert "year = {2023}" in entry


def test_generate_bib_entry_article_type():
    paper = _sample_paper(venue="JMLR 2023")
    entry = generate_bib_entry(paper)
    assert entry.startswith("@article{")
    assert "journal = {JMLR 2023}" in entry


# ---------------------------------------------------------------------------
# build_sweep_candidates
# ---------------------------------------------------------------------------


def test_build_sweep_candidates_excludes_already_read(tmp_path: Path):
    workspace = tmp_path / "ws"
    notes_dir = workspace / "literature" / "paper_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "paper1.md").write_text("# paper1", encoding="utf-8")

    _write_jsonl(
        workspace / "literature" / "papers_dedup.jsonl",
        [
            {"id": "paper1", "title": "P1", "abstract": "abstract 1", "relevance_score": 0.9},
            {"id": "paper2", "title": "P2", "abstract": "abstract 2", "relevance_score": 0.8},
        ],
    )

    candidates = build_sweep_candidates(workspace, {"lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_dedup"], "exclude_already_read": True})
    assert len(candidates) == 1
    assert candidates[0]["id"] == "paper2"


def test_build_sweep_candidates_excludes_existing_abstract_notes(tmp_path: Path):
    workspace = tmp_path / "ws"
    abstract_dir = workspace / "literature" / "paper_notes_abstract"
    abstract_dir.mkdir(parents=True)
    (abstract_dir / "paper2.md").write_text("# paper2", encoding="utf-8")

    _write_jsonl(
        workspace / "literature" / "papers_dedup.jsonl",
        [
            {"id": "paper1", "title": "P1", "abstract": "a1", "relevance_score": 0.9},
            {"id": "paper2", "title": "P2", "abstract": "a2", "relevance_score": 0.8},
        ],
    )

    candidates = build_sweep_candidates(workspace, {"lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_dedup"], "exclude_already_read": True})
    assert len(candidates) == 1
    assert candidates[0]["id"] == "paper1"


def test_build_sweep_candidates_filters_by_relevance(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_dedup.jsonl",
        [
            {"id": "p1", "title": "P1", "abstract": "a1", "relevance_score": 0.9},
            {"id": "p2", "title": "P2", "abstract": "a2", "relevance_score": 0.3},
        ],
    )

    candidates = build_sweep_candidates(workspace, {"lite_paper_num": 10, "min_relevance": 0.5, "sources": ["papers_dedup"], "exclude_already_read": True})
    assert len(candidates) == 1
    assert candidates[0]["id"] == "p1"


def test_build_sweep_candidates_sorts_by_relevance_desc(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_dedup.jsonl",
        [
            {"id": "p1", "title": "P1", "abstract": "a1", "relevance_score": 0.5},
            {"id": "p2", "title": "P2", "abstract": "a2", "relevance_score": 0.9},
            {"id": "p3", "title": "P3", "abstract": "a3", "relevance_score": 0.7},
        ],
    )

    candidates = build_sweep_candidates(workspace, {"lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_dedup"], "exclude_already_read": True})
    assert [c["id"] for c in candidates] == ["p2", "p3", "p1"]


def test_build_sweep_candidates_respects_lite_paper_num(tmp_path: Path):
    workspace = tmp_path / "ws"
    records = [{"id": f"p{i}", "title": f"P{i}", "abstract": f"a{i}", "relevance_score": 0.5 + i * 0.01} for i in range(20)]
    _write_jsonl(workspace / "literature" / "papers_dedup.jsonl", records)

    candidates = build_sweep_candidates(workspace, {"lite_paper_num": 5, "min_relevance": 0.0, "sources": ["papers_dedup"], "exclude_already_read": True})
    assert len(candidates) == 5


def test_build_sweep_candidates_skips_no_abstract(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_dedup.jsonl",
        [
            {"id": "p1", "title": "P1", "abstract": "", "relevance_score": 0.9},
            {"id": "p2", "title": "P2", "abstract": "has abstract", "relevance_score": 0.8},
        ],
    )

    candidates = build_sweep_candidates(workspace, {"lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_dedup"], "exclude_already_read": True})
    assert len(candidates) == 1
    assert candidates[0]["id"] == "p2"


def test_build_sweep_candidates_skips_duplicates_semantic_excludes_and_title_covered_notes(tmp_path: Path):
    workspace = tmp_path / "ws"
    notes_dir = workspace / "literature" / "paper_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "already_read_alias.md").write_text(
        "# Transfer Learning on Heterogeneous Feature Spaces for Treatment Effects Estimation\n\n"
        "- **Title**: Transfer Learning on Heterogeneous Feature Spaces for Treatment Effects Estimation\n",
        encoding="utf-8",
    )
    _write_jsonl(
        workspace / "literature" / "papers_verified.jsonl",
        [
            {
                "id": "alias-id",
                "title": "Transfer Learning on Heterogeneous Feature Spaces",
                "abstract": "Covered by existing title note.",
                "relevance_score": 0.9,
            },
            {
                "id": "dup",
                "title": "Duplicate Paper",
                "abstract": "Duplicate.",
                "relevance_score": 0.9,
                "duplicate_of": "p0",
            },
            {
                "id": "excluded",
                "title": "Keyword Only Paper",
                "abstract": "Excluded.",
                "relevance_score": 0.9,
                "semantic_screen": {"relation_to_project": "shared_keyword_only"},
            },
            {
                "id": "keep",
                "title": "Useful Abstract Candidate",
                "abstract": "Useful abstract.",
                "relevance_score": 0.8,
                "semantic_screen": {"relation_to_project": "method_transfer", "can_enter_deep_read": True},
            },
        ],
    )

    candidates = build_sweep_candidates(
        workspace,
        {"lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_verified"], "exclude_already_read": True},
    )

    assert [item["id"] for item in candidates] == ["keep"]


# ---------------------------------------------------------------------------
# run_abstract_sweep
# ---------------------------------------------------------------------------


def test_run_abstract_sweep_disabled(tmp_path: Path):
    result = run_abstract_sweep(tmp_path, {"enabled": False})
    assert result["enabled"] is False
    assert result["notes_generated"] == 0


def test_run_abstract_sweep_generates_notes(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_dedup.jsonl",
        [
            {"id": "p1", "title": "Paper One", "abstract": "Abstract one. Method two. Results three.", "relevance_score": 0.9, "year": 2023, "venue": "ICML"},
            {"id": "p2", "title": "Paper Two", "abstract": "Another abstract. More method. More results.", "relevance_score": 0.8, "year": 2023, "venue": "NeurIPS"},
        ],
    )

    result = run_abstract_sweep(workspace, {"enabled": True, "lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_dedup"], "exclude_already_read": True})

    assert result["enabled"] is True
    assert result["notes_generated"] == 2

    # Check notes exist
    abstract_dir = workspace / "literature" / "paper_notes_abstract"
    assert abstract_dir.exists()
    assert len(list(abstract_dir.glob("*.md"))) == 2

    # Check note content
    note = (abstract_dir / "p1.md").read_text(encoding="utf-8")
    assert "ABSTRACT-ONLY" in note
    assert "## 13. Mechanism Claim" in note


def test_run_abstract_sweep_appends_to_comparison_table(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_dedup.jsonl",
        [
            {"id": "p1", "title": "Paper One", "abstract": "Abstract text.", "relevance_score": 0.9, "year": 2023, "venue": "ICML"},
        ],
    )

    run_abstract_sweep(workspace, {"enabled": True, "lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_dedup"], "exclude_already_read": True})

    ct_path = workspace / "literature" / "comparison_table.csv"
    assert ct_path.exists()
    lines = ct_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # header + 1 row
    assert "evidence_level" in lines[0]
    assert "ABSTRACT_ONLY" in lines[1]


def test_run_abstract_sweep_appends_to_bib(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_dedup.jsonl",
        [
            {"id": "p1", "title": "Paper One", "abstract": "Abstract text.", "relevance_score": 0.9, "year": 2023, "venue": "ICML"},
        ],
    )

    run_abstract_sweep(workspace, {"enabled": True, "lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_dedup"], "exclude_already_read": True})

    bib_path = workspace / "literature" / "related_work.bib"
    assert bib_path.exists()
    bib_content = bib_path.read_text(encoding="utf-8")
    assert "@inproceedings{" in bib_content
    assert "Paper One" in bib_content


def test_run_abstract_sweep_no_candidates(tmp_path: Path):
    workspace = tmp_path / "ws"
    # No papers at all
    result = run_abstract_sweep(workspace, {"enabled": True, "lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_dedup"], "exclude_already_read": True})
    assert result["candidates_found"] == 0
    assert result["notes_generated"] == 0


@pytest.mark.asyncio
async def test_run_abstract_sweep_with_reader_uses_llm_note_and_updates_audit(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_verified.jsonl",
        [
            {
                "id": "p1",
                "title": "Paper One",
                "abstract": "Abstract text with a method and claimed result.",
                "relevance_score": 0.9,
                "year": 2024,
                "venue": "ICML",
            },
        ],
    )

    async def fake_reader(paper: dict, prompt: str) -> str:
        assert "Paper One" in prompt
        return """# Paper One

- **ID**: p1
- **Title**: Paper One
- **Status**: [ABSTRACT-ONLY]

## 1. Problem & Motivation
Reader LLM identified the abstract-level problem.

## 2. Method Summary
Reader LLM summarized the method.

## A. 核心做法/视角
Reader LLM extracted a viewpoint.

## B. 桥接点
Reader LLM identified a bridge.

## 3. Key Claimed Results
Reader LLM kept the result cautious.

## Raw Abstract
Abstract text with a method and claimed result.

## 13. Mechanism Claim
- **Stated mechanism**: abstract-only mechanism hint
- **Evidence type**: abstract_claim_hint
- **Supporting artifact**: abstract metadata only

## Source
- Read from: abstract / metadata only
"""

    result = await run_abstract_sweep_with_reader(
        workspace,
        {"enabled": True, "lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_verified"], "exclude_already_read": True},
        abstract_reader=fake_reader,
    )

    assert result["notes_generated"] == 1
    assert result["llm_notes_generated"] == 1
    assert result["fallback_notes_generated"] == 0
    note = (workspace / "literature" / "paper_notes_abstract" / "p1.md").read_text(encoding="utf-8")
    assert "Reader LLM summarized the method" in note
    assert "LLM_REVIEW_REQUIRED" not in note
    audit = (workspace / "literature" / "access_audit.md").read_text(encoding="utf-8")
    assert "T3 Abstract Sweep" in audit
    assert "Reader LLM notes: 1" in audit
