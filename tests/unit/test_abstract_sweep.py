"""Tests for the abstract sweep module."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from researchos.time_utils import current_utc_year
from researchos.runtime.abstract_sweep import (
    build_metadata_triage_prompt,
    build_sweep_candidates,
    generate_abstract_note,
    generate_bib_entry,
    generate_comparison_row,
    normalize_metadata_triage_report,
    normalize_abstract_reader_note,
    repair_abstract_sweep_notes,
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


def test_generate_abstract_note_normalizes_dict_authors():
    note = generate_abstract_note(_sample_paper(authors=[{"name": "Alice Smith"}, {"display_name": "Bob Jones"}]))
    assert "Alice Smith, Bob Jones" in note
    assert "{'name'" not in note


def test_generate_abstract_note_handles_no_abstract():
    paper = _sample_paper(abstract="")
    note = generate_abstract_note(paper)
    assert "no abstract available" in note


def test_normalize_abstract_reader_note_repairs_partial_mechanism_claim():
    raw = """# Paper One

- **ID**: p1
- **Title**: Paper One
- **Status**: [ABSTRACT-ONLY]

## 1. Problem & Motivation
Problem.

## 2. Method Summary
Method.

### A. 核心做法/视角
View.

### B. 桥接点
Bridge.

## 3. Key Claimed Results
Result.

## Raw Abstract
Abstract.

## 13. Mechanism Claim
- **Evidence type**: abstract_claim_hint
Not available because no abstract was supplied.

## Source
- Read from: abstract / metadata only
"""

    note = normalize_abstract_reader_note(raw, _sample_paper(id="p1", paper_id="p1"))
    assert "\n## A. 核心做法/视角\n" in note
    assert "\n## B. 桥接点\n" in note
    assert "\n### A. 核心做法/视角\n" not in note
    assert "- **Stated mechanism**: not available from abstract" in note
    assert "- **Evidence type**: abstract_claim_hint" in note
    assert "- **Supporting artifact**: abstract metadata only" in note


def test_repair_abstract_sweep_notes_updates_existing_bad_note(tmp_path: Path):
    workspace = tmp_path / "ws"
    note_dir = workspace / "literature" / "paper_notes_abstract"
    note_dir.mkdir(parents=True)
    note_path = note_dir / "p1.md"
    note_path.write_text(
        """# Paper One

- **ID**: p1
- **Status**: [ABSTRACT-ONLY]

## 1. Problem & Motivation
Problem.

## 2. Method Summary
Method.

## A. 核心做法/视角
View.

## B. 桥接点
Bridge.

## 3. Key Claimed Results
Result.

## Raw Abstract
Abstract.

## 13. Mechanism Claim
- **Evidence type**: abstract_claim_hint

## Source
- Read from: abstract / metadata only
""",
        encoding="utf-8",
    )

    result = repair_abstract_sweep_notes(workspace)
    repaired = note_path.read_text(encoding="utf-8")

    assert result == {"checked": 1, "repaired": 1}
    assert "- **Stated mechanism**:" in repaired
    assert "- **Supporting artifact**:" in repaired


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


def test_generate_bib_entry_normalizes_dict_authors():
    entry = generate_bib_entry(_sample_paper(authors=[{"name": "Alice Smith"}, {"display_name": "Bob Jones"}]))
    assert "author = {Alice Smith and Bob Jones}" in entry
    assert "{'name'" not in entry


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


def test_build_sweep_candidates_dedupes_cross_source_aliases(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_verified.jsonl",
        [
            {
                "id": "S2-alias",
                "canonical_id": "S2-alias",
                "externalIds": {"OpenAlex": "W123"},
                "title": "Alias Paper Variant A",
                "abstract": "short",
                "relevance_score": 0.8,
            }
        ],
    )
    _write_jsonl(
        workspace / "literature" / "papers_dedup.jsonl",
        [
            {
                "id": "W123",
                "canonical_id": "W123",
                "title": "Alias Paper Variant B",
                "abstract": "longer abstract from another source",
                "relevance_score": 0.7,
            }
        ],
    )

    candidates = build_sweep_candidates(
        workspace,
        {"lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_verified", "papers_dedup"], "exclude_already_read": True},
    )

    assert len(candidates) == 1
    assert candidates[0]["abstract"] == "longer abstract from another source"


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


def test_build_sweep_candidates_excludes_semantic_excluded_by_default(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_dedup.jsonl",
        [
            {
                "id": "excluded",
                "title": "Shared Keyword Only",
                "abstract": "metadata",
                "relevance_score": 0.9,
                "semantic_screen": {"relation_to_project": "shared_keyword_only", "can_enter_deep_read": False},
            },
            {
                "id": "included",
                "title": "Transferable Method",
                "abstract": "metadata",
                "relevance_score": 0.8,
                "semantic_screen": {"relation_to_project": "method_transfer", "can_enter_deep_read": True},
            },
        ],
    )

    candidates = build_sweep_candidates(
        workspace,
        {"lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_dedup"], "exclude_already_read": True},
    )

    assert [item["id"] for item in candidates] == ["included"]


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


def test_build_sweep_candidates_uses_resource_and_year_priority(tmp_path: Path):
    workspace = tmp_path / "ws"
    current_year = current_utc_year()
    _write_jsonl(
        workspace / "literature" / "papers_dedup.jsonl",
        [
            {
                "id": "metadata_old",
                "title": "Metadata Old",
                "abstract": "",
                "relevance_score": 0.9,
                "year": current_year - 20,
                "access_level_hint": "METADATA_ONLY",
            },
            {
                "id": "fulltext_recent",
                "title": "Full Text Recent",
                "abstract": "has abstract",
                "relevance_score": 0.8,
                "year": current_year,
                "access_level_hint": "FULL_TEXT_LOCAL",
            },
        ],
    )

    candidates = build_sweep_candidates(
        workspace,
        {
            "lite_paper_num": 10,
            "min_relevance": 0.0,
            "sources": ["papers_dedup"],
            "exclude_already_read": True,
            "priority_weights": {"relevance": 0.7, "resource": 0.2, "year": 0.1},
        },
    )

    assert [c["id"] for c in candidates] == ["fulltext_recent", "metadata_old"]
    components = candidates[0]["abstract_sweep_score_components"]
    assert components["relevance"] == 0.8
    assert components["resource"] == 1.0
    assert components["year"] == 1.0
    assert components["weight_relevance"] == 0.7


def test_build_sweep_candidates_respects_lite_paper_num(tmp_path: Path):
    workspace = tmp_path / "ws"
    records = [{"id": f"p{i}", "title": f"P{i}", "abstract": f"a{i}", "relevance_score": 0.5 + i * 0.01} for i in range(20)]
    _write_jsonl(workspace / "literature" / "papers_dedup.jsonl", records)

    candidates = build_sweep_candidates(workspace, {"lite_paper_num": 5, "min_relevance": 0.0, "sources": ["papers_dedup"], "exclude_already_read": True})
    assert len(candidates) == 5


def test_build_sweep_candidates_counts_existing_abstract_notes_toward_target(tmp_path: Path):
    workspace = tmp_path / "ws"
    records = [{"id": f"p{i}", "title": f"P{i}", "abstract": f"a{i}", "relevance_score": 0.5 + i * 0.01} for i in range(10)]
    _write_jsonl(workspace / "literature" / "papers_dedup.jsonl", records)
    abstract_dir = workspace / "literature" / "paper_notes_abstract"
    abstract_dir.mkdir(parents=True)
    for i in range(3):
        (abstract_dir / f"p{i}.md").write_text(
            f"# P{i}\n\n- **ID**: p{i}\n- **Title**: P{i}\n- **Status**: [ABSTRACT-ONLY]\n",
            encoding="utf-8",
        )

    candidates = build_sweep_candidates(
        workspace,
        {"lite_paper_num": 5, "min_relevance": 0.0, "sources": ["papers_dedup"], "exclude_already_read": True},
    )

    assert len(candidates) == 2
    assert {item["id"] for item in candidates}.isdisjoint({"p0", "p1", "p2"})


def test_build_sweep_candidates_zero_lite_paper_num_disables_sweep(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_dedup.jsonl",
        [{"id": "p1", "title": "P1", "abstract": "a1", "relevance_score": 0.9}],
    )

    candidates = build_sweep_candidates(
        workspace,
        {"lite_paper_num": 0, "min_relevance": 0.0, "sources": ["papers_dedup"], "exclude_already_read": True},
    )

    assert candidates == []


def test_build_sweep_candidates_uses_shallow_queue_not_pending_deep_read(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_verified.jsonl",
        [
            {"id": "deep1", "title": "Deep One", "abstract": "deep abstract", "relevance_score": 0.99},
            {"id": "shallow1", "title": "Shallow One", "abstract": "shallow abstract", "relevance_score": 0.8},
        ],
    )
    _write_jsonl(
        workspace / "literature" / "deep_read_queue.jsonl",
        [
            {
                "id": "deep1",
                "paper_id": "deep1",
                "title": "Deep One",
                "read_disposition": "deep_read",
                "triaged_out": False,
            },
            {
                "id": "shallow1",
                "paper_id": "shallow1",
                "title": "Shallow One",
                "read_disposition": "shallow_read",
                "triaged_reason": "probe_pool_triage_out",
                "triaged_out": True,
            },
        ],
    )

    candidates = build_sweep_candidates(
        workspace,
        {"lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_verified"], "exclude_already_read": True},
    )

    assert [item["id"] for item in candidates] == ["shallow1"]


def test_build_sweep_candidates_all_readable_does_not_expand_to_backlog(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_verified.jsonl",
        [
            {"id": "active1", "title": "Active One", "abstract": "a1", "relevance_score": 0.8},
            {"id": "active2", "title": "Active Two", "abstract": "", "relevance_score": 0.7},
        ],
    )
    _write_jsonl(
        workspace / "literature" / "papers_backlog.jsonl",
        [
            {
                "id": "backlog1",
                "title": "Backlog One",
                "abstract": "readable backlog",
                "relevance_score": 0.99,
                "t2_pool_role": "backlog",
                "read_disposition": "backlog",
                "triaged_reason": "t2_active_pool_cap_exceeded",
                "triaged_out": True,
            }
        ],
    )

    candidates = build_sweep_candidates(
        workspace,
        {
            "lite_paper_num": "all_readable",
            "min_relevance": 0.0,
            "sources": ["papers_verified", "papers_backlog"],
            "exclude_already_read": True,
            "include_metadata_only": True,
            "metadata_replacement_policy": "replace_metadata_only_with_readable_backlog_when_available",
        },
    )

    assert {item["id"] for item in candidates} == {"active1", "active2"}


def test_build_sweep_candidates_numeric_target_refills_from_readable_backlog(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_verified.jsonl",
        [{"id": "active1", "title": "Active One", "abstract": "a1", "relevance_score": 0.8}],
    )
    _write_jsonl(
        workspace / "literature" / "papers_backlog.jsonl",
        [
            {
                "id": "backlog1",
                "title": "Backlog One",
                "abstract": "readable backlog",
                "relevance_score": 0.99,
                "t2_pool_role": "backlog",
                "read_disposition": "backlog",
                "triaged_reason": "t2_active_pool_cap_exceeded",
                "triaged_out": True,
            },
            {
                "id": "backlog-metadata",
                "title": "Backlog Metadata",
                "abstract": "",
                "relevance_score": 1.0,
                "t2_pool_role": "backlog",
                "read_disposition": "backlog",
                "triaged_reason": "t2_active_pool_cap_exceeded",
                "triaged_out": True,
            },
        ],
    )

    candidates = build_sweep_candidates(
        workspace,
        {
            "lite_paper_num": 3,
            "min_relevance": 0.0,
            "sources": ["papers_verified", "papers_backlog"],
            "exclude_already_read": True,
            "include_metadata_only": True,
            "metadata_replacement_policy": "replace_metadata_only_with_readable_backlog_when_available",
        },
    )

    assert [item["id"] for item in candidates] == ["active1", "backlog1"]


def test_build_sweep_candidates_keeps_metadata_only_by_default(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_dedup.jsonl",
        [
            {"id": "p1", "title": "P1", "abstract": "", "relevance_score": 0.9},
            {"id": "p2", "title": "P2", "abstract": "has abstract", "relevance_score": 0.8},
        ],
    )

    candidates = build_sweep_candidates(workspace, {"lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_dedup"], "exclude_already_read": True})
    assert {item["id"] for item in candidates} == {"p1", "p2"}
    assert candidates[0]["id"] == "p2"  # abstract/resource availability can outrank pure metadata.


def test_build_sweep_candidates_can_skip_metadata_only_when_configured(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_dedup.jsonl",
        [
            {"id": "p1", "title": "P1", "abstract": "", "relevance_score": 0.9},
            {"id": "p2", "title": "P2", "abstract": "has abstract", "relevance_score": 0.8},
        ],
    )

    candidates = build_sweep_candidates(
        workspace,
        {
            "lite_paper_num": 10,
            "min_relevance": 0.0,
            "sources": ["papers_dedup"],
            "exclude_already_read": True,
            "include_metadata_only": False,
        },
    )
    assert [item["id"] for item in candidates] == ["p2"]


def test_build_sweep_candidates_skips_duplicates_and_semantic_excludes_by_default(tmp_path: Path):
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


def test_build_sweep_candidates_skips_queue_deferred_records(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_backlog.jsonl",
        [
            {
                "id": "bridge-overflow",
                "title": "Bridge Overflow",
                "abstract": "This bridge record exceeded the per-bridge queue cap.",
                "relevance_score": 0.99,
            },
            {
                "id": "ordinary-shallow",
                "title": "Ordinary Shallow",
                "abstract": "This shallow record remains eligible for abstract sweep.",
                "relevance_score": 0.8,
            },
        ],
    )
    _write_jsonl(
        workspace / "literature" / "deep_read_queue.jsonl",
        [
            {
                "paper_id": "bridge-overflow",
                "id": "bridge-overflow",
                "title": "Bridge Overflow",
                "read_disposition": "deferred",
                "triaged_reason": "bridge_pool_cap_exceeded",
                "target_bucket": "bridge_screened",
                "triaged_out": True,
            },
            {
                "paper_id": "ordinary-shallow",
                "id": "ordinary-shallow",
                "title": "Ordinary Shallow",
                "read_disposition": "shallow_read",
                "target_bucket": "mainline_screened",
                "triaged_out": True,
            },
        ],
    )

    candidates = build_sweep_candidates(
        workspace,
        {"lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_backlog"], "exclude_already_read": True},
    )

    assert [item["id"] for item in candidates] == ["ordinary-shallow"]


def test_build_sweep_candidates_skips_t2_backlog_overflow_records(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_backlog.jsonl",
        [
            {
                "id": "overflow",
                "title": "Overflow Candidate",
                "abstract": "This candidate exceeded the active pool cap.",
                "relevance_score": 0.95,
                "t2_pool_role": "backlog",
                "read_disposition": "backlog",
                "triaged_reason": "t2_active_pool_cap_exceeded",
                "triaged_out": True,
            },
            {
                "id": "domain-filtered",
                "title": "Domain Filtered Candidate",
                "abstract": "This candidate was excluded by domain profile.",
                "relevance_score": 0.94,
                "t2_pool_role": "backlog",
                "read_disposition": "backlog",
                "triaged_reason": "domain_profile_filtered",
                "triaged_out": True,
            },
        ],
    )

    candidates = build_sweep_candidates(
        workspace,
        {"lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_backlog"], "exclude_already_read": True},
    )

    assert candidates == []


def test_build_sweep_candidates_can_keep_semantic_excludes_when_configured(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_verified.jsonl",
        [
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
        {
            "lite_paper_num": 10,
            "min_relevance": 0.0,
            "sources": ["papers_verified"],
            "exclude_already_read": True,
            "exclude_semantic_excluded": False,
        },
    )

    assert [item["id"] for item in candidates] == ["excluded", "keep"]


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
    assert result["sweep_plan"]["target_total"] == 10
    assert result["sweep_plan"]["selected_for_this_run"] == 2

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


def test_run_abstract_sweep_routes_metadata_only_to_triage_report(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_dedup.jsonl",
        [
            {"id": "p1", "title": "Metadata Paper", "abstract": "", "relevance_score": 0.9, "year": 2024, "venue": "ICML"},
        ],
    )

    result = run_abstract_sweep(
        workspace,
        {"enabled": True, "lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_dedup"], "exclude_already_read": True},
    )

    assert result["notes_generated"] == 0
    assert result["fallback_notes_generated"] == 0
    assert result["metadata_triage_count"] == 1
    assert result["metadata_triage_report"] == "literature/metadata_triage.md"
    assert not (workspace / "literature" / "paper_notes_abstract" / "p1.md").exists()

    report = (workspace / "literature" / "metadata_triage.md").read_text(encoding="utf-8")
    assert "Metadata-only Literature Triage" in report
    assert "Metadata Paper" in report
    assert "Do not cite these metadata-only candidates" in report

    assert not (workspace / "literature" / "comparison_table.csv").exists()
    assert not (workspace / "literature" / "related_work.bib").exists()


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


@pytest.mark.asyncio
async def test_run_abstract_sweep_with_reader_routes_missing_abstract_to_metadata_triage(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_verified.jsonl",
        [
            {
                "id": "p1",
                "title": "Metadata Only Paper",
                "abstract": "",
                "relevance_score": 0.9,
                "year": 2024,
                "venue": "ICML",
            },
        ],
    )

    async def should_not_call_reader(paper: dict, prompt: str) -> str:
        raise AssertionError("metadata-only records should not create per-paper abstract notes")

    async def fake_triage_reader(papers: list[dict], prompt: str) -> str:
        assert len(papers) == 1
        assert "Metadata Only Paper" in prompt
        return """# Metadata-only Literature Triage

## Metadata-only Triage Summary
One metadata-only candidate requires resource upgrade.

## Likely Useful To Upgrade
- `p1` may be useful, based on metadata-only title/venue.

## Low Evidence / Defer
- No claims can be made from this record.

## Resource Acquisition Suggestions
- Search DOI/OpenAlex/venue pages before using it.

## Do Not Use As Evidence
- Do not cite as mechanism or result evidence.
"""

    result = await run_abstract_sweep_with_reader(
        workspace,
        {
            "enabled": True,
            "lite_paper_num": 10,
            "min_relevance": 0.0,
            "sources": ["papers_verified"],
            "exclude_already_read": True,
            "include_metadata_only": True,
        },
        abstract_reader=should_not_call_reader,
        metadata_triage_reader=fake_triage_reader,
    )

    assert result["notes_generated"] == 0
    assert result["llm_notes_generated"] == 0
    assert result["fallback_notes_generated"] == 0
    assert result["metadata_triage_count"] == 1
    assert result["metadata_triage_llm"] == 1
    assert not (workspace / "literature" / "paper_notes_abstract" / "p1.md").exists()
    report = (workspace / "literature" / "metadata_triage.md").read_text(encoding="utf-8")
    assert "metadata-only title/venue" in report
    assert "metadata_triage_source: reader_llm" in report


@pytest.mark.asyncio
async def test_run_abstract_sweep_with_reader_batches_multiple_metadata_only_records(tmp_path: Path):
    workspace = tmp_path / "ws"
    _write_jsonl(
        workspace / "literature" / "papers_verified.jsonl",
        [
            {"id": "p1", "title": "Metadata One", "abstract": "", "relevance_score": 0.9, "year": 2024},
            {"id": "p2", "title": "Metadata Two", "abstract": "", "relevance_score": 0.8, "year": 2023},
            {"id": "p3", "title": "Abstract Paper", "abstract": "Real abstract. Method.", "relevance_score": 0.7, "year": 2024},
        ],
    )
    calls = {"triage": 0}

    async def fake_note_reader(paper: dict, prompt: str) -> str:
        return generate_abstract_note(paper)

    async def fake_triage_reader(papers: list[dict], prompt: str) -> str:
        calls["triage"] += 1
        assert [paper["id"] for paper in papers] == ["p1", "p2"]
        assert "Metadata One" in prompt
        assert "Metadata Two" in prompt
        return normalize_metadata_triage_report("## Metadata-only Triage Summary\nBatch reviewed.", papers)

    result = await run_abstract_sweep_with_reader(
        workspace,
        {"enabled": True, "lite_paper_num": 10, "min_relevance": 0.0, "sources": ["papers_verified"], "exclude_already_read": True},
        abstract_reader=fake_note_reader,
        metadata_triage_reader=fake_triage_reader,
    )

    assert result["notes_generated"] == 1
    assert result["llm_notes_generated"] == 1
    assert result["metadata_triage_count"] == 2
    assert result["metadata_triage_llm"] == 1
    assert calls["triage"] == 1
    assert (workspace / "literature" / "paper_notes_abstract" / "p3.md").exists()
    assert not (workspace / "literature" / "paper_notes_abstract" / "p1.md").exists()
    assert not (workspace / "literature" / "paper_notes_abstract" / "p2.md").exists()


def test_metadata_triage_prompt_and_normalizer_mark_non_evidence():
    papers = [{"id": "p1", "title": "Metadata Only", "abstract": "", "relevance_score": 0.8}]
    prompt = build_metadata_triage_prompt(papers)
    assert "不要假装读过摘要或全文" in prompt
    assert "Metadata Only" in prompt

    report = normalize_metadata_triage_report("Short note.", papers)
    assert "## Metadata-only Triage Summary" in report
    assert "## Do Not Use As Evidence" in report
