"""Tests for mechanism claim cluster hints in synthesis workbench."""

from __future__ import annotations

from researchos.tools.literature_synthesis import (
    _build_domain_consensus,
    _build_mechanism_claim_clusters,
    _mechanism_similar,
)


def _note(pid: str, mechanism: str = "", evidence_type: str = "claimed_untested",
          evidence_level: str = "FULL_TEXT") -> dict:
    return {
        "paper_id": pid,
        "title": f"Paper {pid}",
        "mechanism_claim": {
            "stated_mechanism": mechanism,
            "evidence_type": evidence_type,
            "supporting_artifact": "table 3",
        },
        "evidence_level": evidence_level,
    }


# ---------------------------------------------------------------------------
# _mechanism_similar
# ---------------------------------------------------------------------------


def test_mechanism_similar_identical():
    assert _mechanism_similar("noise improves embeddings", "noise improves embeddings")


def test_mechanism_similar_overlapping():
    assert _mechanism_similar(
        "noise regularization improves sparse user embeddings",
        "noise regularization on user embeddings helps",
    )


def test_mechanism_similar_different():
    assert not _mechanism_similar("noise improves embeddings", "graph convolution aggregates neighbors")


def test_mechanism_similar_empty():
    assert not _mechanism_similar("", "something")


# ---------------------------------------------------------------------------
# _build_mechanism_claim_clusters
# ---------------------------------------------------------------------------


def test_consensus_empty_notes():
    assert _build_mechanism_claim_clusters([]) == []


def test_consensus_single_claim():
    notes = [_note("p1", "noise regularization improves embeddings")]
    result = _build_mechanism_claim_clusters(notes)
    assert len(result) == 1
    assert result[0]["paper_count"] == 1
    assert result[0]["evidence_strength_hint"] == "llm_review_required"
    assert result[0]["challengeable_hint"] is True
    assert result[0]["requires_llm_judgment"] is True


def test_consensus_groups_similar_claims():
    notes = [
        _note("p1", "noise regularization improves sparse user embeddings"),
        _note("p2", "noise regularization on user embeddings helps performance"),
        _note("p3", "graph convolution aggregates neighbor features"),
    ]
    result = _build_mechanism_claim_clusters(notes)
    # The two noise claims should be grouped
    noise_cluster = [c for c in result if "noise" in c["mechanism"].lower()]
    assert len(noise_cluster) == 1
    assert noise_cluster[0]["paper_count"] == 2


def test_clusters_do_not_claim_strong_consensus_when_multiple_papers_tested():
    notes = [
        _note("p1", "noise regularization improves embeddings", evidence_type="controlled_experiment"),
        _note("p2", "noise regularization on embeddings works", evidence_type="controlled_experiment"),
        _note("p3", "noise regularization helps embeddings", evidence_type="controlled_experiment"),
    ]
    result = _build_mechanism_claim_clusters(notes)
    noise = [c for c in result if "noise" in c["mechanism"].lower()][0]
    assert noise["evidence_strength_hint"] == "llm_review_required"
    assert noise["challengeable_hint"] is False
    assert noise["semantics"] == "mechanical_mechanism_claim_cluster_not_domain_consensus"


def test_consensus_challengeable_when_untested():
    notes = [
        _note("p1", "noise regularization improves embeddings", evidence_type="claimed_untested"),
        _note("p2", "noise regularization helps embeddings", evidence_type="empirical_correlation"),
    ]
    result = _build_mechanism_claim_clusters(notes)
    noise = [c for c in result if "noise" in c["mechanism"].lower()][0]
    assert noise["challengeable_hint"] is True
    assert noise["has_untested_claims"] is True


def test_consensus_tracks_abstract_only():
    notes = [
        _note("p1", "noise regularization improves embeddings", evidence_level="FULL_TEXT"),
        _note("p2", "noise regularization helps embeddings", evidence_level="ABSTRACT_ONLY"),
    ]
    result = _build_mechanism_claim_clusters(notes)
    noise = [c for c in result if "noise" in c["mechanism"].lower()][0]
    assert noise["abstract_only_count"] == 1


def test_consensus_challengeable_first():
    notes = [
        _note("p1", "controlled experiment shows X", evidence_type="controlled_experiment"),
        _note("p2", "controlled experiment proves X", evidence_type="controlled_experiment"),
        _note("p3", "controlled experiment validates X", evidence_type="controlled_experiment"),
        _note("p4", "untested claim about Y", evidence_type="claimed_untested"),
    ]
    result = _build_mechanism_claim_clusters(notes)
    # challengeable hints should come first
    assert result[0]["challengeable_hint"] is True


def test_consensus_max_10():
    notes = [_note(f"p{i}", f"unique mechanism number {i} is important") for i in range(15)]
    result = _build_mechanism_claim_clusters(notes)
    assert len(result) <= 10


def test_consensus_skips_empty_mechanism():
    notes = [
        _note("p1", ""),
        _note("p2", "", evidence_type=""),
    ]
    result = _build_mechanism_claim_clusters(notes)
    assert len(result) == 0


def test_domain_consensus_alias_kept_for_backward_compatibility():
    result = _build_domain_consensus([_note("p1", "mechanism X")])
    assert result[0]["semantics"] == "mechanical_mechanism_claim_cluster_not_domain_consensus"
