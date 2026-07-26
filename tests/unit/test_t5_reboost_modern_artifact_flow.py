from __future__ import annotations

import json
from pathlib import Path

from researchos.tools.external_experiment import (
    _allowed_path_rules_for_external_executor,
    _build_reboost_pack,
    _validate_research_reboost_pack,
)
from researchos.runtime.workspace import initialize_workspace
from tests.unit.t45_unified_fixture import populate_valid_t45_workspace


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _modern_t45_workspace(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)


def test_reboost_accepts_current_t45_artifacts_and_nested_protocol_fields(tmp_path: Path) -> None:
    _modern_t45_workspace(tmp_path)

    pack, report = _build_reboost_pack(tmp_path)

    assert pack["generation_status"] == "completed"
    assert report["missing_required_sources"] == []
    assert pack["validation_summary"]["required_source_coverage"] == 1.0
    assert {item["path"] for item in pack["source_manifest"] if item["requirement"] == "required"} >= {
        "ideation/research_blueprint.yaml",
        "ideation/claim_registry.yaml",
        "ideation/orientation_review.json",
    }
    assert len(pack["claim_evidence_matrix"]) == 2
    assert len(pack["minimum_experiment_loop"]["required_experiments"]) == 2

    handoff_path = tmp_path / "external_executor/handoff_pack.json"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    ok, error, _report = _validate_research_reboost_pack(tmp_path, handoff_path)

    assert ok is True, error


def test_reboost_keeps_legacy_scorecard_and_risk_artifacts_as_fallbacks(tmp_path: Path) -> None:
    _modern_t45_workspace(tmp_path)
    (tmp_path / "ideation/selected/selected_candidate.json").unlink()
    (tmp_path / "ideation/kill_criteria.yaml").unlink()
    _write(tmp_path / "ideation/idea_scorecard.yaml", "scores: {}\n")
    _write(tmp_path / "ideation/risks.md", "# Risks\nLegacy risk record.\n")

    pack, report = _build_reboost_pack(tmp_path)

    assert pack["generation_status"] == "blocked"
    assert report["missing_required_sources"] == []
    assert any("T4.5 formalization" in item["question"] for item in pack["unresolved_items"])


def test_reboost_preserves_post_novelty_research_context_without_promoting_it_to_results(tmp_path: Path) -> None:
    _modern_t45_workspace(tmp_path)
    _write(
        tmp_path / "ideation/research_dossier.json",
        json.dumps(
            {
                "semantics": "t45_research_dossier",
                "status": "formalized_after_novelty_pass",
                "candidate_id": "D1",
                    "selection_fingerprint": "unified-fingerprint",
                "novelty_audit_verdict": "pass_to_experiment",
                "central_thesis": {
                    "statement": "Planning structure changes uplift estimation.",
                    "evidence_status": "proposed_not_verified",
                    "source_paths": ["ideation/hypotheses.md"],
                },
                "research_problem": {
                    "statement": "Human-trained uplift models may misstate agentic-commerce treatment effects.",
                    "evidence_status": "proposed_not_verified",
                    "source_paths": ["ideation/selected/selected_candidate.json"],
                },
                "why_it_matters": {
                    "scholarly": [{"statement": "The study tests a structural source of transport failure."}],
                    "practical": [{"statement": "If supported, teams can audit planning interventions before deployment."}],
                    "commercial": [{"statement": "If supported, promotion design can be calibrated to agent decision paths."}],
                    "stakeholders_or_processes": [{"statement": "Marketing analysts and agent-commerce policy owners."}],
                },
                "contributions": [{"id": "C1", "statement": "Identify planning mismatch as a candidate mechanism."}],
                "hypotheses": [{"id": "H1", "statement": "Planning changes create larger estimation error."}],
                "evidence_boundary": {"statement": "The mechanism remains unverified."},
                "novelty_boundary": {"statement": "Compare against required baselines.", "required_baselines": []},
                "risks_and_kill_criteria": [{"statement": "Planning reuse may not occur in commerce."}],
                "traceability": {"source_artifacts": ["ideation/hypotheses.md"]},
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    _write(tmp_path / "ideation/validation_map.yaml", "hypotheses: []\n")
    _write(tmp_path / "ideation/contribution_hypothesis_map.yaml", "contributions: []\n")

    pack, _report = _build_reboost_pack(tmp_path)
    context = pack["context_reboost"]["research_context"]

    assert context["research_problem"] == "The problem asks when a computational estimator remains identifiable and useful after the decision-maker changes."
    assert context["commercial_implications"] == [
        "If supported, promotion design can be calibrated to agent decision paths."
    ]
    assert context["evidence_status"] == "proposed_not_verified"
    assert "SRC_RESEARCH_DOSSIER" in [item["source_id"] for item in context["source_refs"]]
    assert "research_context" in pack["writer_handoff_contract"]["must_not_use_as_final_fact_source"]

    handoff_path = tmp_path / "external_executor/handoff_pack.json"
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    ok, error, _report = _validate_research_reboost_pack(tmp_path, handoff_path)

    assert ok is True, error


def test_external_executor_rules_authorize_cross_skill_iteration_artifacts() -> None:
    rules = set(_allowed_path_rules_for_external_executor())

    assert "rw  external_executor/expr/" in rules
    assert "rw  external_executor/raw_results/" in rules
    assert "rw  external_executor/runs/" in rules
    assert "rw  external_executor/reviews/" in rules
    assert "rw  external_executor/report/phase_D/" in rules
    assert "rw  external_executor/report/phase_A/" in rules
    assert "rw  external_executor/report/phase_F/" in rules
    assert "rw  external_executor/report/run_manifest.json" in rules
    assert "rw  external_executor/report/" not in rules
    assert "rw  external_executor/figure/" in rules
    assert "rw  external_executor/table/" in rules


def test_workspace_initialization_creates_phase_report_directories(tmp_path: Path) -> None:
    initialize_workspace(tmp_path, create_project_file=False)

    report = tmp_path / "external_executor/report"
    for phase in "ABCDEF":
        assert (report / f"phase_{phase}").is_dir()
