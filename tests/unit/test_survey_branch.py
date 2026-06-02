from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from researchos.agents.registry import get_agent_by_id
from researchos.agents.survey_writer import SurveyWriterAgent
from researchos.orchestration.state_machine import StateMachine
from researchos.runtime.agent import AgentResult
from researchos.schemas.validator import validate_task_artifacts
from researchos.schemas.state import StateYaml
from researchos.tools.survey_tools import (
    AssembleSurveyTool,
    AuditSurveyCoverageTool,
    BuildSurveyStateTool,
    ExportSurveyForIdeationTool,
    UpdateSurveySectionStateTool,
)
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _survey_plan() -> dict:
    return {
        "semantics": "llm_authored_taxonomy_driven_survey_plan",
        "taxonomy": {
            "dimension": "mechanism families",
            "rationale": "Readers need a mechanism-level map.",
            "tree": [
                {"class_id": "T1", "name": "Perturbation mechanisms", "parent": None, "paper_ids": ["P1", "P2"]},
                {"class_id": "T2", "name": "Routing mechanisms", "parent": None, "paper_ids": ["P3"]},
            ],
        },
        "evolution_narrative": "Foundational perturbation work led to routing-aware methods.",
        "outline": [
            {"section_id": "background", "title": "Background and Scope", "covers": ["scope"]},
            {"section_id": "taxonomy", "title": "Taxonomy", "covers": ["T1", "T2"]},
            {"section_id": "theme_T1", "title": "Perturbation Mechanisms", "covers": ["T1"], "paper_ids": ["P1", "P2"]},
            {"section_id": "theme_T2", "title": "Routing Mechanisms", "covers": ["T2"], "paper_ids": ["P3"]},
            {"section_id": "comparison", "title": "Comparative Analysis", "covers": ["cross_paper_tensions"]},
            {"section_id": "challenges", "title": "Open Challenges", "covers": ["challenge_hints"]},
            {"section_id": "future", "title": "Future Directions", "covers": ["adjacent_transfers"]},
        ],
        "coverage_selfcheck": {
            "unclassified_papers": [],
            "empty_classes": [],
            "corpus_sufficiency": "sufficient",
            "classes_needing_more_lit": [],
        },
    }


def _policy(workspace: Path) -> WorkspaceAccessPolicy:
    return WorkspaceAccessPolicy(
        workspace,
        allowed_read_prefixes=["", "drafts/", "literature/", "ideation/"],
        allowed_write_prefixes=["drafts/", "ideation/"],
    )


@pytest.mark.asyncio
async def test_survey_tools_build_state_assemble_audit_and_export(tmp_path: Path):
    ws = tmp_path
    _write_json(ws / "drafts" / "survey" / "survey_plan.json", _survey_plan())
    _write_json(ws / "drafts" / "survey" / "corpus_decision.json", {"scope": "conservative"})
    (ws / "literature").mkdir()
    (ws / "literature" / "related_work.bib").write_text(
        "@article{p1,title={A}}\n@article{p2,title={B}}\n@article{p3,title={C}}\n",
        encoding="utf-8",
    )
    policy = _policy(ws)

    result = await BuildSurveyStateTool(policy).execute()
    assert result.ok
    state = json.loads((ws / "drafts" / "survey" / "survey_state.json").read_text(encoding="utf-8"))
    assert state["semantics"] == "survey_state_for_taxonomy_driven_section_writing_not_final_claims"
    assert state["sections"]["theme_1"]["title"] == "Perturbation Mechanisms"
    assert state["sections"]["theme_3"]["status"] == "skipped"
    assert (ws / "drafts" / "survey" / "section_outlines" / "theme_1.md").exists()

    sections_dir = ws / "drafts" / "survey" / "sections"
    sections_dir.mkdir(parents=True)
    section_text = {
        "background": "\\section{Background and Scope}\nThis survey defines scope using prior work \\citep{p1}.",
        "taxonomy": "\\section{Taxonomy}\nThe taxonomy separates perturbation and routing mechanisms \\citep{p1,p2}.",
        "theme_1": "\\section{Perturbation Mechanisms}\nThis theme compares perturbation mechanisms across papers \\citep{p1,p2}.",
        "theme_2": "\\section{Routing Mechanisms}\nThis theme compares routing mechanisms and their evidence \\citep{p3}.",
        "comparison": "\\section{Comparative Analysis}\nComparative analysis identifies cross-paper tensions \\citep{p1,p3}.",
        "challenges": "\\section{Open Challenges}\nOpen Challenge: robustness remains hard under distribution shift \\citep{p2}.",
        "future": "\\section{Future Directions}\nFuture directions include adjacent transfers and better evaluation \\citep{p3}.",
        "introduction": "\\section{Introduction}\nThis survey motivates a taxonomy-driven reading of the field \\citep{p1}.",
        "conclusion": "\\section{Conclusion}\nThe survey concludes with open challenges and future directions.",
        "abstract": "\\section*{Abstract}\nA taxonomy-driven survey of mechanisms.",
    }
    for section_id, text in section_text.items():
        (sections_dir / f"{section_id}.tex").write_text(text, encoding="utf-8")
        result = await UpdateSurveySectionStateTool(policy).execute(section_id=section_id)
        assert result.ok

    result = await AssembleSurveyTool(policy).execute()
    assert result.ok
    tex = (ws / "drafts" / "survey" / "survey.tex").read_text(encoding="utf-8")
    assert "\\documentclass" in tex
    assert "Perturbation Mechanisms" in tex
    assert (ws / "drafts" / "survey" / "references.bib").exists()

    result = await AuditSurveyCoverageTool(policy).execute()
    assert result.ok
    audit = json.loads((ws / "drafts" / "survey" / "survey_audit.json").read_text(encoding="utf-8"))
    assert audit["passed"] is True

    result = await ExportSurveyForIdeationTool(policy).execute()
    assert result.ok
    insights = json.loads((ws / "ideation" / "survey_insights.json").read_text(encoding="utf-8"))
    assert insights["semantics"] == "survey_insights_optional_ideation_fuel_not_gate"
    assert insights["taxonomy"]["dimension"] == "mechanism families"


def test_survey_writer_registry_and_phase():
    agent = get_agent_by_id("survey_writer", mode="survey_section")
    assert isinstance(agent, SurveyWriterAgent)
    assert agent._mode == "survey_section"


def test_survey_writer_compile_validation_accepts_success_report(tmp_path: Path):
    ws = tmp_path
    (ws / "drafts" / "survey").mkdir(parents=True)
    (ws / "drafts" / "survey" / "survey.pdf").write_bytes(b"%PDF-1.4\n")
    (ws / "drafts" / "survey" / "survey.log").write_text("ok", encoding="utf-8")
    _write_json(
        ws / "drafts" / "survey" / "survey_compile_report.json",
        {
            "semantics": "latex_compile_attempt_report",
            "tex_path": "drafts/survey/survey.tex",
            "success": True,
            "pdf_path": "drafts/survey/survey.pdf",
            "log_path": "drafts/survey/survey.log",
        },
    )
    agent = SurveyWriterAgent(mode="survey_compile")
    ctx = type("Ctx", (), {"workspace_dir": ws, "mode": "survey_compile", "extra": {}})()
    ok, err = agent.validate_outputs(ctx)
    assert ok, err


def test_t36_compile_artifact_checker_requires_compile_report(tmp_path: Path):
    ws = tmp_path
    (ws / "drafts" / "survey").mkdir(parents=True)
    (ws / "drafts" / "survey" / "survey.pdf").write_bytes(b"%PDF-1.4\n")
    (ws / "drafts" / "survey" / "survey.log").write_text("ok", encoding="utf-8")

    ok, err = validate_task_artifacts(ws, "T3.6-COMPILE")

    assert not ok
    assert err is not None
    assert "survey_compile_report" in err


def test_survey_writer_review_validation_accepts_complete_review(tmp_path: Path):
    ws = tmp_path
    (ws / "drafts" / "survey").mkdir(parents=True)
    (ws / "drafts" / "survey" / "survey_review.md").write_text(
        "\n".join(
            [
                "## Taxonomy Review",
                "The taxonomy dimension is coherent and separates the main mechanism families clearly.",
                "## Coverage Review",
                "Coverage includes the planned core classes and flags abstract-only evidence boundaries.",
                "## Comparative Fairness Review",
                "Comparative analysis distinguishes settings before comparing evidence strength.",
                "## Challenges Review",
                "Challenges are tied to documented cross-paper tensions.",
                "## Future Directions Review",
                "Future directions use adjacent transfers without overstating them.",
                "## Scope And Craft Review",
                "Scope is stated honestly and craft issues have been checked.",
                "## Remaining Risks",
                "No blocking issue remains; minor citation polish may still be useful.",
            ]
        ),
        encoding="utf-8",
    )
    _write_json(
        ws / "drafts" / "survey" / "survey_review_actions.json",
        {
            "semantics": "llm_survey_review_and_section_revision_plan",
            "review_target": "taxonomy_driven_survey",
            "blocking_issues_remaining": False,
            "section_actions": [
                {
                    "section_id": "comparison",
                    "severity": "medium",
                    "issue": "Clarify incomparable settings.",
                    "action_taken": "revised",
                    "evidence": "comparison.tex revised",
                }
            ],
            "audit_after_review": {"survey_audit_passed": True},
        },
    )
    agent = SurveyWriterAgent(mode="survey_review")
    ctx = type("Ctx", (), {"workspace_dir": ws, "mode": "survey_review", "extra": {}})()
    ok, err = agent.validate_outputs(ctx)
    assert ok, err


def test_t36_state_machine_routes_survey_yes_no_and_corpus_scope(tmp_path: Path):
    sm_config = {
        "initial_state": "T3.6-GATE-SURVEY",
        "states": {
            "T3.6-GATE-SURVEY": {
                "agent": "survey_writer",
                "mode": "survey_gate",
                "outputs": {"survey_decision": "drafts/survey/decision.json"},
                "next_on_success": "__parse_from_output__",
            },
            "T3.6-PLAN": {"agent": "survey_writer", "mode": "survey_plan", "outputs": {"survey_plan": "drafts/survey/survey_plan.json"}, "next_on_success": "done"},
            "T3.6-GATE-CORPUS": {
                "agent": "survey_writer",
                "mode": "corpus_gate",
                "outputs": {"corpus_decision": "drafts/survey/corpus_decision.json"},
                "next_on_success": "__parse_from_output__",
            },
            "T3.6-EXPAND": {"agent": "survey_writer", "mode": "survey_expand", "outputs": {"survey_expansion": "drafts/survey/survey_expansion.json"}, "next_on_success": "done"},
            "T3.6-STATE": {"agent": "survey_writer", "mode": "survey_state", "outputs": {"survey_state": "drafts/survey/survey_state.json"}, "next_on_success": "done"},
            "T3.6-COMPILE": {"agent": "survey_writer", "mode": "survey_compile", "outputs": {"survey_pdf": "drafts/survey/survey.pdf"}, "next_on_success": "done"},
            "T4": {"agent": "ideation", "outputs": {"hypotheses": "ideation/hypotheses.md"}, "next_on_success": "done"},
            "done": {"terminal": True},
            "failed": {"terminal": True},
        },
    }
    config = tmp_path / "state_machine.yaml"
    config.write_text(yaml.safe_dump(sm_config), encoding="utf-8")
    sm = StateMachine(config)
    (tmp_path / "drafts" / "survey").mkdir(parents=True)

    _write_json(tmp_path / "drafts" / "survey" / "decision.json", {"write_survey": True})
    assert sm._parse_t36_survey_decision(tmp_path) == "T3.6-PLAN"
    _write_json(tmp_path / "drafts" / "survey" / "decision.json", {"write_survey": False})
    assert sm._parse_t36_survey_decision(tmp_path) == "T4"
    _write_json(tmp_path / "drafts" / "survey" / "corpus_decision.json", {"scope": "complete"})
    assert sm._parse_t36_corpus_decision(tmp_path) == "T3.6-EXPAND"
    _write_json(tmp_path / "drafts" / "survey" / "corpus_decision.json", {"scope": "conservative"})
    assert sm._parse_t36_corpus_decision(tmp_path) == "T3.6-STATE"


def test_t45_reframe_and_drop_pause_for_human_gate(tmp_path: Path):
    sm_config = {
        "initial_state": "T4.5",
        "states": {
            "T4": {"agent": "ideation", "outputs": {"hypotheses": "ideation/hypotheses.md"}, "next_on_success": "T4.5"},
            "T4.5": {
                "agent": "novelty_auditor",
                "outputs": {"novelty_audit": "ideation/novelty_audit.md"},
                "next_on_success": "__parse_from_output__",
            },
            "T4.5-HUMAN-REVIEW": {
                "agent": "novelty_auditor",
                "mode": "human_review",
                "extra": {"immediate_gate": True},
                "inputs": {"novelty_audit": "ideation/novelty_audit.md"},
                "outputs": {"novelty_human_review": "ideation/novelty_human_review.json"},
                "gate": {"type": "t45_human_review_gate"},
            },
            "T7": {"agent": "experimenter", "mode": "full", "outputs": {"results": "experiments/results_summary.json"}, "next_on_success": "done"},
            "done": {"terminal": True},
            "failed": {"terminal": True},
        },
    }
    gates = {
        "gates": {
            "t45_human_review_gate": {
                "title": "T4.5 review",
                "presentation": {"audit": {"from_file": "ideation/novelty_audit.md", "max_chars": 1000}},
                "options": [
                    {"id": "continue_to_t7", "label": "继续进入外部实验链", "next": "T5-HANDOFF"},
                    {"id": "return_to_t4", "label": "回到 T4", "next": "T4"},
                    {"id": "stop_project", "label": "结束", "next": "done"},
                ],
            }
        }
    }
    config = tmp_path / "state_machine.yaml"
    gates_path = tmp_path / "gates.yaml"
    config.write_text(yaml.safe_dump(sm_config), encoding="utf-8")
    gates_path.write_text(yaml.safe_dump(gates), encoding="utf-8")
    sm = StateMachine(config, gates_path)
    (tmp_path / "ideation").mkdir()
    (tmp_path / "ideation" / "novelty_audit.md").write_text(
        "Final Gate Verdict: return_to_T4_reframe\n",
        encoding="utf-8",
    )
    assert sm._parse_t45_verdict(tmp_path) == "T4.5-HUMAN-REVIEW"

    state = sm.start_task(sm.create_initial_state("p1"), "run_t45")
    result = AgentResult(
        ok=True,
        message="done",
        outputs_produced={},
        steps_used=1,
        tokens_in=0,
        tokens_out=0,
        cost_usd=0,
        duration_seconds=0,
        stop_reason=AgentResult.STOP_FINISHED,
    )
    state = sm.advance(state, result, workspace_dir=tmp_path)
    assert state.current_task == "T4.5-HUMAN-REVIEW"
    assert sm.should_pause_for_immediate_gate(state) is True
    state = sm.pause_for_immediate_gate(state, workspace_dir=tmp_path)
    assert state.status == "WAITING_HUMAN"
    assert state.pending_gate is not None
    state = sm.resolve_pending_gate(state, {"option_id": "return_to_t4", "captured": {"note": "reframe"}}, workspace_dir=tmp_path)
    assert state.current_task == "T4"
    decision = json.loads((tmp_path / "ideation" / "novelty_human_review.json").read_text(encoding="utf-8"))
    assert decision["semantics"] == "human_decision_over_agent_recommendation"
    assert decision["selected_option"] == "return_to_t4"
