from __future__ import annotations

from pathlib import Path

import yaml

from researchos.ideation.formalization import (
    legacy_t45_upgrade_reason,
    validate_blueprint_and_claim_registry,
    validate_t45_formalization_core,
)
from researchos.ideation.proposal import validate_t45_research_proposal
from researchos.tools.external_experiment import _build_reboost_pack
from tests.unit.t45_unified_fixture import populate_valid_t45_workspace, write, write_yaml


def _proposal_path(workspace: Path) -> Path:
    return workspace / "ideation" / "proposal" / "research_proposal.md"


def test_one_unified_template_accepts_all_three_orientations(tmp_path: Path) -> None:
    for orientation in ("ccf_a", "utd", "hybrid"):
        workspace = tmp_path / orientation
        populate_valid_t45_workspace(workspace, orientation=orientation)
        ok, error = validate_t45_research_proposal(workspace, workspace / "ideation" / "novelty_audit.md")
        assert ok is True, f"{orientation}: {error}"
        proposal = _proposal_path(workspace).read_text(encoding="utf-8")
        assert proposal.count("## ") == 7


def test_short_heading_complete_proposal_fails_with_substantive_reason(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    write(
        _proposal_path(tmp_path),
        "# Research Proposal\n\n"
        "## Research Motivation and Core Problem\nshort\n\n"
        "## Prior Research, Gap and Key Challenges\nshort\n\n"
        "## Proposed Approach and Design Rationale\nshort\n\n"
        "## Research Questions, Claims and Hypotheses\nshort\n\n"
        "## Research Design and Evaluation\nshort\n\n"
        "## Expected Contributions and Implications\nshort\n\n"
        "## Risks, Limitations and Execution Plan\nshort\n",
    )
    ok, error = validate_t45_research_proposal(tmp_path, tmp_path / "ideation" / "novelty_audit.md")
    assert ok is False
    assert "too short" in (error or "") or "fragments" in (error or "")


def test_repetitive_or_audit_dominated_proposal_fails(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    path = _proposal_path(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + "\nT4.5 Level 0 true_collision. T4.5 Level 0 true_collision.\n", encoding="utf-8")
    ok, error = validate_t45_research_proposal(tmp_path, tmp_path / "ideation" / "novelty_audit.md")
    assert ok is False
    assert "audit-dominated" in (error or "")


def test_removed_h1_cannot_satisfy_active_claim_contract(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    write(
        tmp_path / "ideation" / "hypotheses.md",
        "# Research Claims and Hypotheses\n\n### H1 [removed]\nThis claim was dropped after an internal audit and is not active.\n",
    )
    ok, error = validate_t45_formalization_core(tmp_path)
    assert ok is False
    assert "TC1" in (error or "") or "too short" in (error or "")


def test_claim_without_experiment_mapping_and_component_test_fails(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    plan_path = tmp_path / "ideation" / "exp_plan.yaml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    plan["experiments"] = [plan["experiments"][0]]
    write_yaml(plan_path, plan)
    ok, error = validate_t45_formalization_core(tmp_path)
    assert ok is False
    assert "MC1" in (error or "")

    populate_valid_t45_workspace(tmp_path)
    blueprint_path = tmp_path / "ideation" / "research_blueprint.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    blueprint["evaluation"]["ablations"] = [{"component_id": "COMP1", "planned_test": "Remove COMP1 only."}]
    blueprint["evaluation"]["mechanism_tests"] = [{"component_id": "COMP1", "planned_test": "Test COMP1 only."}]
    write_yaml(blueprint_path, blueprint)
    ok, error = validate_t45_formalization_core(tmp_path)
    assert ok is False
    assert "COMP2" in (error or "")


def test_orientation_specific_failure_modes_are_blocked(tmp_path: Path) -> None:
    ccf = tmp_path / "ccf"
    populate_valid_t45_workspace(ccf, orientation="ccf_a")
    proposal = _proposal_path(ccf)
    proposal.write_text(proposal.read_text(encoding="utf-8").replace("algorithmic model", "narrative description").replace("algorithm and diagnostic system artifact", "application paragraph"), encoding="utf-8")
    # The validator must still reject lack of explicit method rationale when
    # the component/alternative language is removed.
    proposal.write_text(proposal.read_text(encoding="utf-8").replace("A simpler alternative", "A conventional option").replace("cannot distinguish", "is not described"), encoding="utf-8")
    ok, error = validate_t45_research_proposal(ccf, ccf / "ideation" / "novelty_audit.md")
    assert ok is False
    assert "alternative" in (error or "") or "computational" in (error or "")

    utd = tmp_path / "utd"
    populate_valid_t45_workspace(utd, orientation="utd")
    text = _proposal_path(utd).read_text(encoding="utf-8")
    before, tail = text.split("## Proposed Approach and Design Rationale\n", 1)
    _old_approach, after = tail.split("## Research Questions, Claims and Hypotheses\n", 1)
    text = (
        before
        + "## Proposed Approach and Design Rationale\n"
        + "Central Insight: call an existing LLM API for the task. COMP1 and COMP2 are labels for the same API call. "
        + "A simpler alternative is not discussed beyond reusing the API. "
        + " ".join(
            f"API-only approach statement {index} deliberately omits learned structure, a training objective, optimization, inference design, and a new technical artifact."
            for index in range(1, 9)
        )
        + "\n\n## Research Questions, Claims and Hypotheses\n"
        + after
    )
    _proposal_path(utd).write_text(text, encoding="utf-8")
    ok, error = validate_t45_research_proposal(utd, utd / "ideation" / "novelty_audit.md")
    assert ok is False
    assert "LLM/API" in (error or "") or "technical artifact" in (error or "")

    hybrid = tmp_path / "hybrid"
    populate_valid_t45_workspace(hybrid, orientation="hybrid")
    blueprint_path = hybrid / "ideation" / "research_blueprint.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    blueprint["research_claims"]["cross_level_links"] = []
    write_yaml(blueprint_path, blueprint)
    ok, error = validate_blueprint_and_claim_registry(hybrid)
    assert ok is False
    assert "cross-level" in (error or "")


def test_t5_is_blocked_when_final_quality_gate_is_not_passed(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    review_path = tmp_path / "ideation" / "orientation_review.json"
    review = yaml.safe_load(review_path.read_text(encoding="utf-8"))
    review["status"] = "requires_repair"
    write(review_path, __import__("json").dumps(review, ensure_ascii=False, indent=2))
    ok, error = validate_t45_research_proposal(tmp_path, tmp_path / "ideation" / "novelty_audit.md")
    assert ok is False
    assert "requires targeted repair" in (error or "")
    pack, _report = _build_reboost_pack(tmp_path)
    assert pack["generation_status"] == "blocked"
    assert any("T4.5 formalization" in item["question"] for item in pack["unresolved_items"])


def test_legacy_passed_audit_requires_formalization_upgrade_not_silent_t5_use(tmp_path: Path) -> None:
    write(
        tmp_path / "ideation" / "novelty_audit.md",
        "# Old attempt\nFinal Gate Verdict: drop_due_to_collision\n\n# Final attempt\nFinal Gate Verdict: pass_to_experiment\n",
    )
    write_yaml(tmp_path / "ideation" / "hypothesis_brief.yaml", {"draft_hypotheses": [{"id": "H1", "statement": "draft"}]})
    write(tmp_path / "ideation" / "selected" / "selected_candidate.json", "{}\n")
    write(tmp_path / "literature" / "synthesis.md", "# Synthesis\n")
    reason = legacy_t45_upgrade_reason(tmp_path)
    assert reason is not None
    assert "统一研究正式化质量 gate" in reason
