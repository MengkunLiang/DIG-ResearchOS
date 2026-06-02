"""Writer/Reviewer/Submission Agent 单元测试"""

import hashlib
import json
import os
from pathlib import Path

import pytest

from researchos.agents.writer import WriterAgent
from researchos.agents.reviewer import ReviewerAgent
from researchos.agents.submission import (
    SubmissionAgent,
    check_anonymization,
    check_submission_compile_environment,
)
from researchos.runtime.agent_params import get_agent_params
from researchos.runtime.prompts import render_prompt
from researchos.tools.manuscript import CORE_SECTIONS


def _load_agent_params():
    """加载 runtime 规范化后的 agent 参数，用于测试断言。"""

    return {
        name: get_agent_params(name)
        for name in ("writer", "reviewer", "submission")
    }


class MockExecutionContext:
    """模拟 ExecutionContext"""

    def __init__(self, mode: str, workspace_dir: Path, extra: dict = None):
        self.mode = mode
        self.workspace_dir = workspace_dir
        self.project_id = "test_project"
        self.run_id = "test_run"
        self.inputs = {}
        self.outputs_expected = {}
        self.agent_name = "test"
        self.task_id = "T8-WRITE" if "write" in mode else f"T8-{mode.upper()}" if mode else "T8"
        self.extra = extra or {}
        if "phase" not in self.extra and mode:
            self.extra["phase"] = mode
        if "round" not in self.extra and "review" in mode:
            self.extra["round"] = 1


@pytest.fixture
def temp_workspace(tmp_path):
    """创建临时 workspace"""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "literature").mkdir()
    (ws / "experiments").mkdir()
    (ws / "ideation").mkdir()
    (ws / "drafts").mkdir()
    (ws / "drafts" / "figures").mkdir()
    (ws / "drafts" / "review_rounds").mkdir()
    (ws / "submission").mkdir()
    (ws / "references").mkdir()
    (ws / "references" / "venue_templates").mkdir()
    (ws / "project.yaml").write_text(
        "name: test_project\nresearch_direction: AI\ntarget_venue: neurips2026"
    )
    return ws


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_compile_report(workspace: Path, *, success: bool = True) -> None:
    bundle_dir = workspace / "submission" / "bundle"
    main_tex = bundle_dir / "main.tex"
    main_pdf = bundle_dir / "main.pdf"
    main_log = bundle_dir / "main.log"
    report = {
        "version": "1.0",
        "semantics": "latex_compile_attempt_report",
        "tex_path": "submission/bundle/main.tex",
        "requested_engine": "pdflatex",
        "bibtex": True,
        "output_dir": None,
        "started_at": "2026-05-28T00:00:00+00:00",
        "finished_at": "2026-05-28T00:00:01+00:00",
        "engine": "docker",
        "exit_code": 0 if success else 1,
        "success": success,
        "error": None if success else "nonzero_exit",
        "main_tex_sha256": _sha256_file(main_tex) if main_tex.exists() else "",
        "main_tex_mtime": main_tex.stat().st_mtime if main_tex.exists() else 0,
        "log_path": "submission/bundle/main.log",
        "log_sha256": _sha256_file(main_log) if main_log.exists() else "",
        "log_mtime": main_log.stat().st_mtime if main_log.exists() else 0,
        "log_size": main_log.stat().st_size if main_log.exists() else 0,
        "pdf_path": "submission/bundle/main.pdf",
        "pdf_sha256": _sha256_file(main_pdf) if main_pdf.exists() else "",
        "pdf_size": main_pdf.stat().st_size if main_pdf.exists() else 0,
        "pdf_mtime": main_pdf.stat().st_mtime if main_pdf.exists() else 0,
        "attempts": [
            {
                "engine": "docker",
                "exit_code": 0 if success else 1,
                "success": success,
                "started_at": "2026-05-28T00:00:00+00:00",
                "finished_at": "2026-05-28T00:00:01+00:00",
                "error": None if success else "nonzero_exit",
            }
        ],
    }
    (workspace / "submission" / "compile_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_writer_prompt_defaults_suggested_style_when_not_injected(temp_workspace):
    """writer.j2 must not crash if an older render path omits suggested_style."""

    ctx = MockExecutionContext("self_check", temp_workspace)
    prompt = render_prompt(
        "writer.j2",
        ctx,
        project={"name": "p", "research_direction": "AI"},
        target_venue="unknown",
        phase="self_check",
        writing_style={},
        agent_guidance="",
        results_summary="{}",
        synthesis_preview="",
        related_work_preview="",
        hypotheses_preview="",
        novelty_report_preview="",
        novelty_audit_preview="",
        ablations_preview="",
        resource_index_preview="",
        section_plan_preview="",
        evidence_plan_preview="",
        figure_table_plan_preview="",
        cdr_claim_ledger_preview="",
        claim_ledger_preview="",
        figure_registry_preview="",
        manuscript_audit_preview="",
        craft_audit_preview="",
        paper_state_preview="",
        alignment_matrix_preview="",
        section_id=None,
        section_title="",
        section_outline_preview="",
        section_draft_preview="",
        previous_section_tail="",
        outline_preview="",
        review_report_preview="",
        revision_patch_preview="",
        user_corrections_preview="",
        round_num=1,
        temperature=0.7,
    )

    assert "venue_style: ccf_a" in prompt


def _write_manuscript_registries(workspace: Path) -> None:
    (workspace / "drafts" / "cdr_claim_ledger.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "cdr_claim_ledger_seed_not_final_scientific_judgment",
                "cdr_tuple": {
                    "problem_frame": "test problem frame",
                    "design_rationale": "test design rationale",
                    "artifact": "test artifact",
                    "data_view": "test data view",
                    "evaluation_mode": "test evaluation mode",
                    "contribution_type": "improvement",
                    "boundary_conditions": ["synthetic boundary"],
                },
                "contribution_chains": [
                    {"cid": "C1", "hypothesis": "H1", "source_claim_ids": ["cdr_C1"], "contribution_type": "improvement"},
                    {"cid": "C2", "hypothesis": "H2", "source_claim_ids": ["cdr_C1"], "contribution_type": "improvement"},
                    {"cid": "C3", "hypothesis": "H3", "source_claim_ids": ["cdr_C1"], "contribution_type": "improvement"},
                ],
                "contribution_claims": [
                    {
                        "claim_id": "C1",
                        "claim_text": "test contribution claim",
                        "cdr_field": "design_rationale",
                        "required_section": ["introduction", "methodology"],
                        "evidence_artifacts": ["ideation/hypotheses.md"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (workspace / "drafts" / "claim_ledger.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "mechanical_claim_ledger_seed_not_final_scientific_judgment",
                "claims": [
                    {
                        "claim_id": "Q1",
                        "claim_text": "test claim",
                        "required_section": ["experiments"],
                        "evidence_artifacts": ["experiments/results_summary.json"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (workspace / "drafts" / "figure_registry.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "mechanical_figure_registry_seed_not_visual_generation",
                "visuals": [
                    {
                        "visual_id": "fig:main_results",
                        "type": "figure",
                        "source_artifacts": ["experiments/results_summary.json"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (workspace / "drafts" / "alignment_matrix.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "alignment_matrix_seed_not_final_scientific_judgment",
                "rows": [
                    {
                        "cid": "C1",
                        "hypothesis": "H1",
                        "motivation": "test motivation",
                        "contribution": "test contribution",
                        "contribution_type": "improvement",
                        "related_gap": {"papers": ["smith2024"], "tension": "test tension"},
                        "counterfactual": "independent",
                        "counterfactual_note": "test counterfactual note",
                        "nearest_prior_work": {"work": "smith2024", "distance": "moderate"},
                        "novelty_signal": "adjacent_zone",
                        "design_choice": "test design choice",
                        "experiment": {"rq": "RQ1", "result_metric": "accuracy", "table": "tab:main_results"},
                        "analysis": "test analysis",
                        "status": "seed_needs_llm_completion",
                    },
                    {
                        "cid": "C2",
                        "hypothesis": "H2",
                        "motivation": "test motivation 2",
                        "contribution": "test contribution 2",
                        "contribution_type": "improvement",
                        "related_gap": {"papers": ["smith2024"], "tension": "test tension 2"},
                        "counterfactual": "survives_weakened",
                        "counterfactual_note": "test counterfactual note 2",
                        "nearest_prior_work": {"work": "smith2024", "distance": "distant"},
                        "novelty_signal": "no_nearby_cluster",
                        "design_choice": "test design choice 2",
                        "experiment": {"rq": "RQ2", "result_metric": "accuracy", "table": "tab:main_results"},
                        "analysis": "test analysis 2",
                        "status": "seed_needs_llm_completion",
                    },
                    {
                        "cid": "C3",
                        "hypothesis": "H3",
                        "motivation": "test motivation 3",
                        "contribution": "test contribution 3",
                        "contribution_type": "improvement",
                        "related_gap": {"papers": ["smith2024"], "tension": "test tension 3"},
                        "counterfactual": "collapses",
                        "counterfactual_note": "test counterfactual note 3",
                        "nearest_prior_work": {"work": "smith2024", "distance": "very_close"},
                        "novelty_signal": "marginal_zone",
                        "design_choice": "test design choice 3",
                        "experiment": {"rq": "RQ3", "result_metric": "accuracy", "table": "tab:main_results"},
                        "analysis": "test analysis 3",
                        "status": "seed_needs_llm_completion",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_passing_craft_audit(workspace: Path) -> None:
    checks = [
        {"name": "matrix_row_count", "level": "PASS", "passed": True, "detail": "ok"},
        {"name": "intro_contribution_count", "level": "PASS", "passed": True, "detail": "ok"},
        {"name": "abstract_no_cite", "level": "PASS", "passed": True, "detail": "ok"},
        {"name": "number_traceability", "level": "PASS", "passed": True, "detail": "ok"},
        {"name": "no_standalone_limitations", "level": "PASS", "passed": True, "detail": "ok"},
        {"name": "conclusion_has_limitations_subsection", "level": "PASS", "passed": True, "detail": "ok"},
    ]
    (workspace / "drafts" / "craft_audit.md").write_text("# Writing Craft And Alignment Audit\n- [x] ok\n")
    (workspace / "drafts" / "craft_audit.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "deterministic_writing_craft_audit_not_scientific_judgment",
                "venue_style": "ccf_a",
                "alignment_cids": ["C1", "C2", "C3"],
                "checks": checks,
            }
        ),
        encoding="utf-8",
    )


def _write_valid_draft_artifacts(workspace: Path) -> None:
    _write_valid_paper_state(workspace)
    state = json.loads((workspace / "drafts" / "paper_state.json").read_text(encoding="utf-8"))
    section_dir = workspace / "drafts" / "sections"
    section_dir.mkdir(parents=True, exist_ok=True)
    for name in CORE_SECTIONS:
        state["sections"][name]["status"] = "written"
        (section_dir / f"{name}.tex").write_text(
            f"\\section{{{name.replace('_', ' ').title()}}}\n" + ("Substantive section content. " * 6),
            encoding="utf-8",
        )
    (workspace / "drafts" / "paper_state.json").write_text(json.dumps(state), encoding="utf-8")
    paper_content = r"""\documentclass{article}
\begin{document}
\begin{abstract}
This is an abstract.
\end{abstract}
\section{Introduction}
Content here.
\section{Related Work}
Related work here.
\section{Method}
Method description.
\section{Experiments}
Experimental results.
\section{Conclusion}
Conclusion.
\subsection{Limitations}
Validity boundaries.
\end{document}
"""
    (workspace / "drafts" / "paper.tex").write_text(paper_content, encoding="utf-8")
    (workspace / "drafts" / "manuscript_audit.md").write_text("# Audit\n- [x] ok\n", encoding="utf-8")
    _write_passing_craft_audit(workspace)
    (workspace / "literature" / "related_work.bib").write_text(
        "@article{test2024,\n  author={Test Author},\n  title={Test Title},\n  year={2024}\n}",
        encoding="utf-8",
    )


# ══════════════════════════════════════════════════════
# Writer Agent Tests
# ══════════════════════════════════════════════════════

def test_writer_agent_initialization():
    """测试 WriterAgent 初始化"""
    agent = WriterAgent()
    params = _load_agent_params()["writer"]
    assert agent.spec.name == "writer"
    assert agent.spec.max_steps == params["max_steps"]
    assert agent.spec.max_tokens_total == params["max_tokens_total"]
    assert "write_file" in agent.spec.tool_names
    assert "drafts/" in agent.spec.allowed_write_prefixes


def test_writer_outline_phase_initial_message(temp_workspace):
    """测试 outline 模式的初始消息"""
    agent = WriterAgent()
    ctx = MockExecutionContext("outline", temp_workspace, {"phase": "outline"})
    msg = agent.initial_user_message(ctx)

    assert "Phase 1" in msg
    assert "outline.md" in msg
    assert "标题候选" in msg


def test_writer_draft_phase_initial_message(temp_workspace):
    """测试 draft 模式的初始消息"""
    agent = WriterAgent()
    ctx = MockExecutionContext("draft", temp_workspace, {"phase": "draft"})
    msg = agent.initial_user_message(ctx)

    assert "Phase 3" in msg
    assert "paper.tex" in msg
    assert "results_summary.json" in msg


def test_writer_resource_index_phase_initial_message(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext("resource_index", temp_workspace, {"phase": "resource_index"})
    msg = agent.initial_user_message(ctx)

    assert "Phase 0" in msg
    assert "build_manuscript_resource_index" in msg
    assert "section_plan.json" in msg


def test_writer_section_drafts_phase_initial_message(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext("section_drafts", temp_workspace, {"phase": "section_drafts"})
    msg = agent.initial_user_message(ctx)

    assert "已废弃" in msg
    assert "T8-SEC-METHOD" in msg
    assert "不要生成 drafts/sections" in msg


def test_writer_section_plan_phase_initial_message(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext("section_plan", temp_workspace, {"phase": "section_plan"})
    msg = agent.initial_user_message(ctx)

    assert "Phase 1.5" in msg
    assert "initialize_manuscript_state" in msg
    assert "paper_state.json" in msg


def test_writer_single_section_phase_initial_message(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext(
        "section_draft",
        temp_workspace,
        {"phase": "section_draft", "section_id": "experiments"},
    )
    msg = agent.initial_user_message(ctx)

    assert "单章节写作" in msg
    assert "experiments.tex" in msg
    assert "update_manuscript_section_state" in msg
    assert "paper.tex" in msg


def test_writer_uses_ctx_mode_when_phase_extra_missing(temp_workspace):
    """状态机只传 mode 时，Writer 也应进入对应 phase。"""
    agent = WriterAgent(mode="outline")
    ctx = MockExecutionContext("outline", temp_workspace, {})
    ctx.extra = {}
    msg = agent.initial_user_message(ctx)

    assert "Phase 1" in msg
    assert "outline.md" in msg


def test_writer_validate_outputs_outline_success(temp_workspace):
    """测试 outline 模式验证成功"""
    agent = WriterAgent()
    ctx = MockExecutionContext("outline", temp_workspace, {"phase": "outline"})

    # 创建符合要求的 outline
    outline_content = """# 论文大纲

## 标题候选
1. Test Title 1
2. Test Title 2

## Abstract
- 问题：描述
- 方法：方案
- 结果：效果

## 1. Introduction
介绍章节内容

## 2. Related Work
相关工作章节

## 3. Method
方法章节

## 4. Experiments
实验章节
    """
    (temp_workspace / "drafts" / "outline.md").write_text(outline_content)
    _write_manuscript_registries(temp_workspace)

    ok, err = agent.validate_outputs(ctx)
    assert ok
    assert err is None


def test_writer_validate_outputs_resource_index_success(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext("resource_index", temp_workspace, {"phase": "resource_index"})
    (temp_workspace / "drafts" / "manuscript_resource_index.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "artifacts": [{"path": "experiments/results_summary.json"}],
                "bib_keys": ["smith2024"],
                "result_metrics": [],
            }
        ),
        encoding="utf-8",
    )
    (temp_workspace / "drafts" / "section_plan.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "sections": [
                    {"id": "introduction"},
                    {"id": "related_work"},
                    {"id": "methodology"},
                    {"id": "experiments"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (temp_workspace / "drafts" / "evidence_plan.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "claim_slots": [
                    {"slot_id": "intro_problem_gap"},
                    {"slot_id": "experiments_main_result"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (temp_workspace / "drafts" / "figure_table_plan.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "planned_visuals": [
                    {"figure_id": "fig:main_results"},
                    {"table_id": "tab:main_results"},
                ],
            }
        ),
        encoding="utf-8",
    )
    _write_manuscript_registries(temp_workspace)

    ok, err = agent.validate_outputs(ctx)
    assert ok
    assert err is None


def test_writer_validate_outputs_resource_index_rejects_invalid_json(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext("resource_index", temp_workspace, {"phase": "resource_index"})
    (temp_workspace / "drafts" / "manuscript_resource_index.json").write_text("{not json")
    (temp_workspace / "drafts" / "section_plan.json").write_text("{}")
    (temp_workspace / "drafts" / "evidence_plan.json").write_text("{}")
    (temp_workspace / "drafts" / "figure_table_plan.json").write_text("{}")

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "JSON" in err


def _write_valid_paper_state(workspace: Path) -> None:
    sections = {}
    for section_id in [
        "methodology",
        "experiments",
        "related_work",
        "analysis",
        "introduction",
        "conclusion",
        "abstract",
    ]:
        sections[section_id] = {
            "status": "pending",
            "file": f"drafts/sections/{section_id}.tex",
            "outline": f"drafts/section_outlines/{section_id}.md",
        }
        outline_path = workspace / "drafts" / "section_outlines" / f"{section_id}.md"
        outline_path.parent.mkdir(parents=True, exist_ok=True)
        outline_path.write_text(
            f"# Section Outline: {section_id}\n\n## Purpose\n" + ("Detailed outline. " * 10),
            encoding="utf-8",
        )
    (workspace / "drafts" / "paper_state.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "shared_state_for_section_by_section_writing_not_final_claims",
                "section_order": list(sections),
                "sections": sections,
                "shared_facts": {
                    "bib_keys": ["smith2024"],
                    "result_metrics": [],
                    "alignment_matrix": [
                        {
                            "cid": "C1",
                            "hypothesis": "H1",
                            "motivation": "test motivation",
                            "contribution": "test contribution",
                            "contribution_type": "improvement",
                            "related_gap": {"papers": ["smith2024"], "tension": "test tension"},
                            "design_choice": "test design choice",
                            "experiment": {"rq": "RQ1", "result_metric": "accuracy"},
                            "analysis": "test analysis",
                            "status": "seed_needs_llm_completion",
                        },
                        {
                            "cid": "C2",
                            "hypothesis": "H2",
                            "motivation": "test motivation 2",
                            "contribution": "test contribution 2",
                            "contribution_type": "improvement",
                            "related_gap": {"papers": ["smith2024"], "tension": "test tension 2"},
                            "design_choice": "test design choice 2",
                            "experiment": {"rq": "RQ2", "result_metric": "accuracy", "table": "tab:main_results"},
                            "analysis": "test analysis 2",
                            "status": "seed_needs_llm_completion",
                        },
                        {
                            "cid": "C3",
                            "hypothesis": "H3",
                            "motivation": "test motivation 3",
                            "contribution": "test contribution 3",
                            "contribution_type": "improvement",
                            "related_gap": {"papers": ["smith2024"], "tension": "test tension 3"},
                            "design_choice": "test design choice 3",
                            "experiment": {"rq": "RQ3", "result_metric": "accuracy", "table": "tab:main_results"},
                            "analysis": "test analysis 3",
                            "status": "seed_needs_llm_completion",
                        },
                    ],
                },
            }
        ),
        encoding="utf-8",
    )


def test_writer_validate_outputs_section_plan_success(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext("section_plan", temp_workspace, {"phase": "section_plan"})
    _write_valid_paper_state(temp_workspace)

    ok, err = agent.validate_outputs(ctx)

    assert ok, err


def test_writer_validate_outputs_single_section_success(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext(
        "section_draft",
        temp_workspace,
        {"phase": "section_draft", "section_id": "experiments"},
    )
    _write_valid_paper_state(temp_workspace)
    state = json.loads((temp_workspace / "drafts" / "paper_state.json").read_text(encoding="utf-8"))
    state["sections"]["experiments"]["status"] = "written"
    (temp_workspace / "drafts" / "paper_state.json").write_text(json.dumps(state), encoding="utf-8")
    section_dir = temp_workspace / "drafts" / "sections"
    section_dir.mkdir(parents=True, exist_ok=True)
    (section_dir / "experiments.tex").write_text(
        "\\section{Experiments}\n" + ("Experiment section grounded in 0.82 results. " * 5),
        encoding="utf-8",
    )

    ok, err = agent.validate_outputs(ctx)

    assert ok, err


def test_writer_validate_outputs_single_section_rejects_foreign_section(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext(
        "section_draft",
        temp_workspace,
        {"phase": "section_draft", "section_id": "experiments"},
    )
    _write_valid_paper_state(temp_workspace)
    state = json.loads((temp_workspace / "drafts" / "paper_state.json").read_text(encoding="utf-8"))
    state["sections"]["experiments"]["status"] = "written"
    (temp_workspace / "drafts" / "paper_state.json").write_text(json.dumps(state), encoding="utf-8")
    section_dir = temp_workspace / "drafts" / "sections"
    section_dir.mkdir(parents=True, exist_ok=True)
    (section_dir / "experiments.tex").write_text(
        "\\section{Experiments}\n"
        + ("Experiment section grounded in 0.82 results. " * 5)
        + "\n\\section{Conclusion}\nThis should not be here.",
        encoding="utf-8",
    )

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "夹带" in err


def test_writer_validate_outputs_single_section_requires_state_update(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext(
        "section_draft",
        temp_workspace,
        {"phase": "section_draft", "section_id": "experiments"},
    )
    _write_valid_paper_state(temp_workspace)
    section_dir = temp_workspace / "drafts" / "sections"
    section_dir.mkdir(parents=True, exist_ok=True)
    (section_dir / "experiments.tex").write_text(
        "\\section{Experiments}\n" + ("Experiment section grounded in 0.82 results. " * 5),
        encoding="utf-8",
    )

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "尚未标记" in err


def test_writer_validate_outputs_section_drafts_success(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext("section_drafts", temp_workspace, {"phase": "section_drafts"})
    _write_valid_paper_state(temp_workspace)

    ok, err = agent.validate_outputs(ctx)
    assert ok
    assert err is None


def test_writer_validate_outputs_section_drafts_requires_paper_state(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext("section_drafts", temp_workspace, {"phase": "section_drafts"})

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "paper_state" in err


def test_writer_validate_outputs_draft_rejects_unvalidated_section_wrapper(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext("draft", temp_workspace, {"phase": "draft"})
    _write_valid_paper_state(temp_workspace)
    state = json.loads((temp_workspace / "drafts" / "paper_state.json").read_text(encoding="utf-8"))
    section_dir = temp_workspace / "drafts" / "sections"
    section_dir.mkdir(parents=True, exist_ok=True)
    for name in [
        "abstract",
        "introduction",
        "related_work",
        "methodology",
        "experiments",
        "analysis",
        "conclusion",
    ]:
        state["sections"][name]["status"] = "written"
        body = f"\\section{{{name}}}\n" + ("Substantive section content. " * 6)
        if name == "analysis":
            body = "\\documentclass{article}\n\\begin{document}\n" + body + "\\end{document}\n"
        (section_dir / f"{name}.tex").write_text(body, encoding="utf-8")
    (temp_workspace / "drafts" / "paper_state.json").write_text(json.dumps(state), encoding="utf-8")
    (temp_workspace / "drafts" / "paper.tex").write_text(
        "\\documentclass{article}\\begin{document}\\section{Introduction}x\\section{Related Work}x"
        "\\section{Method}x\\section{Experiments}x\\section{Conclusion}x\\end{document}",
        encoding="utf-8",
    )
    (temp_workspace / "drafts" / "manuscript_audit.md").write_text("# Audit\n")

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "wrapper" in err


def test_writer_validate_outputs_outline_too_short(temp_workspace):
    """测试 outline 模式内容过短"""
    agent = WriterAgent()
    ctx = MockExecutionContext("outline", temp_workspace, {"phase": "outline"})

    (temp_workspace / "drafts" / "outline.md").write_text("Too short")

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "过短" in err


def test_writer_validate_outputs_draft_success(temp_workspace):
    """测试 draft 模式验证成功"""
    agent = WriterAgent()
    ctx = MockExecutionContext("draft", temp_workspace, {"phase": "draft"})
    _write_valid_draft_artifacts(temp_workspace)

    ok, err = agent.validate_outputs(ctx)
    assert ok
    assert err is None


def test_writer_validate_outputs_both_rejects_annotation_only_variants(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext("draft", temp_workspace, {"phase": "draft"})
    _write_valid_draft_artifacts(temp_workspace)
    main = (temp_workspace / "drafts" / "paper.tex").read_text(encoding="utf-8")
    (temp_workspace / "drafts" / "writing_style.json").write_text('{"venue_style":"both"}\n', encoding="utf-8")
    for style_id in ("is", "ccf_a"):
        style_dir = temp_workspace / "drafts" / style_id
        style_dir.mkdir(parents=True, exist_ok=True)
        (style_dir / "paper.tex").write_text(
            f"% ResearchOS style variant: {style_id}\n% Target venue: neurips2026\n" + main,
            encoding="utf-8",
        )
        (style_dir / "craft_audit.json").write_text(
            (temp_workspace / "drafts" / "craft_audit.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (style_dir / "style_revision_notes.md").write_text(
            "# Style Revision Notes\n\nThis note says the style was reviewed, but the body was not actually changed.",
            encoding="utf-8",
        )

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "不能只是主稿加注释" in err


def test_writer_validate_outputs_both_accepts_style_revised_variants(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext("draft", temp_workspace, {"phase": "draft"})
    _write_valid_draft_artifacts(temp_workspace)
    main = (temp_workspace / "drafts" / "paper.tex").read_text(encoding="utf-8")
    (temp_workspace / "drafts" / "writing_style.json").write_text('{"venue_style":"both"}\n', encoding="utf-8")
    for style_id, sentence in (
        ("is", "This IS-style revision expands the theoretical positioning and validity framing."),
        ("ccf_a", "This CCF-A revision tightens the result headline and reproducibility framing."),
    ):
        style_dir = temp_workspace / "drafts" / style_id
        style_dir.mkdir(parents=True, exist_ok=True)
        (style_dir / "paper.tex").write_text(
            main.replace("Content here.", f"Content here. {sentence}"),
            encoding="utf-8",
        )
        (style_dir / "craft_audit.json").write_text(
            (temp_workspace / "drafts" / "craft_audit.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (style_dir / "style_revision_notes.md").write_text(
            "# Style Revision Notes\n\n"
            f"The {style_id} variant was revised with venue-specific framing while preserving the same "
            "alignment matrix, result numbers, citation keys, and contribution facts.",
            encoding="utf-8",
        )

    ok, err = agent.validate_outputs(ctx)

    assert ok, err


def test_writer_validate_outputs_revise_requires_audit(temp_workspace):
    """revise 阶段不能只改 paper.tex 而不刷新 manuscript_audit.md。"""
    agent = WriterAgent()
    ctx = MockExecutionContext("revise", temp_workspace, {"phase": "revise", "round": 1})
    _write_valid_paper_state(temp_workspace)
    state = json.loads((temp_workspace / "drafts" / "paper_state.json").read_text(encoding="utf-8"))
    section_dir = temp_workspace / "drafts" / "sections"
    section_dir.mkdir(parents=True, exist_ok=True)
    for name in [
        "abstract",
        "introduction",
        "related_work",
        "methodology",
        "experiments",
        "analysis",
        "conclusion",
    ]:
        state["sections"][name]["status"] = "revised"
        (section_dir / f"{name}.tex").write_text(
            f"\\section{{{name.replace('_', ' ').title()}}}\n" + ("Substantive section content. " * 6),
            encoding="utf-8",
        )
    (temp_workspace / "drafts" / "paper_state.json").write_text(json.dumps(state), encoding="utf-8")
    (temp_workspace / "drafts" / "patches").mkdir(parents=True, exist_ok=True)
    (temp_workspace / "drafts" / "patches" / "round_1_patches.json").write_text(
        json.dumps(
            {
                "semantics": "mechanical_review_issue_locations_not_final_revision_decisions",
                "patches": [],
            }
        ),
        encoding="utf-8",
    )
    (temp_workspace / "drafts" / "revision_response_round_1.md").write_text(
        "# Revision Response\n\n"
        "- resolved: no reviewer issues in this synthetic test; this response is long enough "
        "to pass the structured revision response gate before audit validation.\n",
        encoding="utf-8",
    )

    paper_content = r"""\documentclass{article}
\begin{document}
\section{Introduction}
Content here.
\section{Related Work}
Related work here.
\section{Method}
Method description.
\section{Experiments}
Experimental results.
\section{Conclusion}
Conclusion.
\end{document}
"""
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "manuscript_audit.md" in err


def test_writer_validate_outputs_revise_requires_patch_list(temp_workspace):
    agent = WriterAgent()
    ctx = MockExecutionContext("revise", temp_workspace, {"phase": "revise", "round": 1})
    _write_valid_paper_state(temp_workspace)
    state = json.loads((temp_workspace / "drafts" / "paper_state.json").read_text(encoding="utf-8"))
    section_dir = temp_workspace / "drafts" / "sections"
    section_dir.mkdir(parents=True, exist_ok=True)
    for name in [
        "abstract",
        "introduction",
        "related_work",
        "methodology",
        "experiments",
        "analysis",
        "conclusion",
    ]:
        state["sections"][name]["status"] = "revised"
        (section_dir / f"{name}.tex").write_text(
            f"\\section{{{name.replace('_', ' ').title()}}}\n" + ("Substantive section content. " * 6),
            encoding="utf-8",
        )
    (temp_workspace / "drafts" / "paper_state.json").write_text(json.dumps(state), encoding="utf-8")
    (temp_workspace / "drafts" / "paper.tex").write_text(
        "\\documentclass{article}\\begin{document}\\section{Introduction}x\\section{Related Work}x"
        "\\section{Method}x\\section{Experiments}x\\section{Conclusion}x\\end{document}",
        encoding="utf-8",
    )
    (temp_workspace / "drafts" / "manuscript_audit.md").write_text("# Audit\n")
    (temp_workspace / "drafts" / "craft_audit.md").write_text("# Writing Craft And Alignment Audit\n")

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "patch list" in err


def test_writer_validate_outputs_draft_missing_documentclass(temp_workspace):
    """测试 draft 模式缺少 documentclass"""
    agent = WriterAgent()
    ctx = MockExecutionContext("draft", temp_workspace, {"phase": "draft"})
    _write_valid_paper_state(temp_workspace)
    state = json.loads((temp_workspace / "drafts" / "paper_state.json").read_text(encoding="utf-8"))
    section_dir = temp_workspace / "drafts" / "sections"
    section_dir.mkdir(parents=True, exist_ok=True)
    for name in [
        "abstract",
        "introduction",
        "related_work",
        "methodology",
        "experiments",
        "analysis",
        "conclusion",
    ]:
        state["sections"][name]["status"] = "written"
        (section_dir / f"{name}.tex").write_text(
            f"\\section{{{name.replace('_', ' ').title()}}}\n" + ("Substantive section content. " * 6),
            encoding="utf-8",
        )
    (temp_workspace / "drafts" / "paper_state.json").write_text(json.dumps(state), encoding="utf-8")

    # 提供完整的LaTeX结构但没有 documentclass 命令
    paper_content = (
        r"\begin{document}"
        r"\section{Test Section}"
        r"This is a test paper content."
        r"\end{document}"
    )
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "documentclass" in err


def test_writer_validate_outputs_draft_invalid_citations(temp_workspace):
    """测试 draft 模式引用不存在的 BibTeX key"""
    agent = WriterAgent()
    ctx = MockExecutionContext("draft", temp_workspace, {"phase": "draft"})
    _write_valid_paper_state(temp_workspace)
    state = json.loads((temp_workspace / "drafts" / "paper_state.json").read_text(encoding="utf-8"))
    section_dir = temp_workspace / "drafts" / "sections"
    section_dir.mkdir(parents=True, exist_ok=True)
    for name in [
        "abstract",
        "introduction",
        "related_work",
        "methodology",
        "experiments",
        "analysis",
        "conclusion",
    ]:
        state["sections"][name]["status"] = "written"
        (section_dir / f"{name}.tex").write_text(
            f"\\section{{{name.replace('_', ' ').title()}}}\n" + ("Substantive section content. " * 6),
            encoding="utf-8",
        )
    (temp_workspace / "drafts" / "paper_state.json").write_text(json.dumps(state), encoding="utf-8")

    paper_content = r"""\documentclass{article}
\begin{document}
\title{Test}
\section{Introduction}
Some text \cite{nonexistent2024}.
\section{Related Work}
Related text.
\section{Method}
More text.
\section{Experiments}
Results.
\section{Conclusion}
Done.
\end{document}
"""
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)

    # 创建空 bib 文件
    (temp_workspace / "literature" / "related_work.bib").write_text("")

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "nonexistent2024" in err


# ══════════════════════════════════════════════════════
# Reviewer Agent Tests
# ══════════════════════════════════════════════════════

def test_reviewer_agent_initialization():
    """测试 ReviewerAgent 初始化"""
    agent = ReviewerAgent()
    params = _load_agent_params()["reviewer"]
    assert agent.spec.name == "reviewer"
    assert agent.spec.max_steps == params["max_steps"]
    assert agent.spec.max_tokens_total == params["max_tokens_total"]
    assert "read_file" in agent.spec.tool_names
    assert "list_files" in agent.spec.tool_names
    assert "drafts/review_rounds/" in agent.spec.allowed_write_prefixes


def test_reviewer_initial_message(temp_workspace):
    """测试审稿初始消息"""
    agent = ReviewerAgent()
    ctx = MockExecutionContext("review", temp_workspace, {"round": 1})
    msg = agent.initial_user_message(ctx)

    assert "Reviewer" in msg
    assert "round_1.md" in msg
    assert "round_1_sections" in msg
    assert "内容完整性" in msg


def test_reviewer_round2_prompt_includes_audit_self_check_and_previous_review(temp_workspace):
    agent = ReviewerAgent()
    (temp_workspace / "drafts" / "manuscript_audit.md").write_text("# Audit\n- [ ] issue\n")
    (temp_workspace / "drafts" / "craft_audit.md").write_text("# Writing Craft And Alignment Audit\n- [ ] FAIL item\n")
    (temp_workspace / "drafts" / "alignment_matrix.json").write_text('{"rows":[{"cid":"C1"}]}\n')
    (temp_workspace / "drafts" / "self_check.md").write_text("# Self Check\nHigh TODO\n")
    (temp_workspace / "drafts" / "review_rounds" / "round_1.md").write_text("# Round 1\nFix result table.\n")
    ctx = MockExecutionContext("review", temp_workspace, {"round": 2})

    prompt = agent.system_prompt(ctx)
    msg = agent.initial_user_message(ctx)

    assert "Manuscript Audit" in prompt
    assert "Writing Craft Audit" in prompt
    assert "Alignment Matrix" in prompt
    assert "High TODO" in prompt
    assert "Previous Review" in prompt
    assert "Fix result table" in prompt
    assert "round_1.md" in msg


def test_reviewer_validate_outputs_success(temp_workspace):
    """测试审稿报告验证成功"""
    agent = ReviewerAgent()
    ctx = MockExecutionContext("review", temp_workspace, {"round": 1})

    report_content = """# 审稿报告 - Round 1

生成时间: 2024-01-25 10:30:00

## 总体评价

这篇论文整体质量良好。

**推荐**: Minor Revision

## 主要问题（Major Issues）

### 问题1: 相关工作不完整
**位置**: Section 2
**描述**: 缺少重要引用
**建议**: 补充相关工作
**严重程度**: Medium

## 次要问题（Minor Issues）

### 问题1: 格式问题
**位置**: Abstract
**描述**: 格式不规范
**建议**: 修正格式
**严重程度**: Low

## 写作范式与对齐核查

- Alignment matrix closure: Pass
- Craft audit FAIL items: none
- Craft audit WARN items: none
- Standalone Limitations section: Pass
- CID anchor coverage: Pass

## CDR Contribution Verdict

- Problem frame clarity: Clear enough for this test report.
- Design rationale support: Supported enough for this test report.
- Contribution type credibility: Improvement claim is plausible.
- Evidence alignment: Evidence issues are actionable.
- Boundary condition honesty: Boundary conditions are stated.
- Verdict: Needs minor revision.

## 总结

论文需要小修后提交。
"""
    (temp_workspace / "drafts" / "review_rounds" / "round_1.md").write_text(report_content)
    section_dir = temp_workspace / "drafts" / "review_rounds" / "round_1_sections"
    section_dir.mkdir(parents=True, exist_ok=True)
    for section_id in CORE_SECTIONS:
        (section_dir / f"{section_id}.md").write_text(
            f"# Section Review: {section_id}\n\n"
            "## Section Purpose Check\nSubstantive check.\n\n"
            "## Evidence And Number Check\nSubstantive check.\n\n"
            "## Logic And Writing Issues\nSubstantive check.\n\n"
            "## CDR Alignment Check\nProblem, rationale, evidence, and boundary alignment are checked.\n\n"
            "## Alignment Matrix Check\nCID coverage is checked.\n\n"
            "## Writing Craft Check\nCraft audit is checked.\n\n"
            "## Actionable Fixes\n- [Low] Fix wording.\n",
            encoding="utf-8",
        )

    ok, err = agent.validate_outputs(ctx)
    assert ok
    assert err is None


def test_reviewer_validate_outputs_too_short(temp_workspace):
    """测试审稿报告内容过短"""
    agent = ReviewerAgent()
    ctx = MockExecutionContext("review", temp_workspace, {"round": 1})

    (temp_workspace / "drafts" / "review_rounds" / "round_1.md").write_text("Too short")

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "过短" in err


def test_reviewer_validate_outputs_missing_sections(temp_workspace):
    """测试审稿报告缺少必需章节"""
    agent = ReviewerAgent()
    ctx = MockExecutionContext("review", temp_workspace, {"round": 1})

    report_content = """# 审稿报告 - Round 1

生成时间: 2024-01-25

## 总体评价

这篇论文整体质量良好。

## 主要问题

问题描述

"""
    (temp_workspace / "drafts" / "review_rounds" / "round_1.md").write_text(report_content)

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "次要问题" in err or "缺少必需章节" in err


# ══════════════════════════════════════════════════════
# Submission Agent Tests
# ══════════════════════════════════════════════════════

def test_submission_agent_initialization():
    """测试 SubmissionAgent 初始化"""
    agent = SubmissionAgent()
    params = _load_agent_params()["submission"]
    assert agent.spec.name == "submission"
    assert agent.spec.max_steps == params["max_steps"]
    assert agent.spec.max_tokens_total == params["max_tokens_total"]
    assert "docker_exec" in agent.spec.tool_names
    assert "latex_compile" in agent.spec.tool_names
    assert "submission/" in agent.spec.allowed_write_prefixes
    # 默认关闭匿名化前置检查，避免本地调试或非匿名投稿流程被直接拦截。
    hook_names = [h.__name__ if callable(h) else str(h) for h in agent.spec.pre_hooks]
    assert "check_anonymization" not in hook_names
    assert "check_submission_compile_environment" in hook_names


def test_submission_initial_message(temp_workspace):
    """测试投稿准备初始消息"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)
    msg = agent.initial_user_message(ctx)

    assert "Submission" in msg
    assert "neurips" in msg
    assert "迁移" in msg


def test_submission_validate_outputs_success(temp_workspace):
    """测试投稿包验证成功"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    # 创建bundle目录和必需文件
    bundle_dir = temp_workspace / "submission" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "main.tex").write_text(r"\documentclass{article}\begin{document}\end{document}")
    (bundle_dir / "references.bib").write_text("@article{test,}")
    (bundle_dir / "main.pdf").write_bytes(b"%PDF-1.4\nmock pdf body\n%%EOF")
    (bundle_dir / "main.log").write_text("This is a clean compile log.")
    _write_compile_report(temp_workspace)

    # 创建迁移报告
    report_content = """# 投稿迁移报告

生成时间: 2024-01-26 15:30:00
目标会议: neurips2026

## 迁移摘要

- 源文件: drafts/paper.tex
- 目标模板: neurips2026
- 迁移状态: 成功
- 编译状态: 成功
- 匿名化检查: 通过

## 文件清单

- main.tex
- references.bib

## 投稿检查清单

- [x] 主论文
- [x] 参考文献
"""
    (temp_workspace / "submission" / "migration_report.md").write_text(report_content)

    ok, err = agent.validate_outputs(ctx)
    assert ok
    assert err is None


def test_submission_validate_outputs_missing_bundle(temp_workspace):
    """测试投稿包缺少bundle目录"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "bundle" in err.lower()


def test_submission_validate_outputs_missing_main_tex(temp_workspace):
    """测试投稿包缺少main.tex"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    bundle_dir = temp_workspace / "submission" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "main.tex" in err


def test_submission_validate_outputs_missing_pdf(temp_workspace):
    """测试投稿包缺少编译产出的 PDF。"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    bundle_dir = temp_workspace / "submission" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "main.tex").write_text(r"\documentclass{article}\begin{document}\end{document}")
    (bundle_dir / "references.bib").write_text("@article{test,}")

    report_content = """# 投稿迁移报告

生成时间: 2024-01-26 15:30:00
目标会议: neurips2026

## 迁移摘要

- 迁移状态: 成功
- 编译状态: 成功
- 匿名化检查: 通过
"""
    (temp_workspace / "submission" / "migration_report.md").write_text(report_content)

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "main.pdf" in err


def test_submission_validate_outputs_compile_not_marked_success(temp_workspace):
    """测试报告未明确声明编译成功时应失败。"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    bundle_dir = temp_workspace / "submission" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "main.tex").write_text(r"\documentclass{article}\begin{document}\end{document}")
    (bundle_dir / "references.bib").write_text("@article{test,}")
    (bundle_dir / "main.pdf").write_bytes(b"%PDF-1.4\nmock pdf body\n%%EOF")
    (bundle_dir / "main.log").write_text("This is a clean compile log.")
    _write_compile_report(temp_workspace)

    report_content = """# 投稿迁移报告

生成时间: 2024-01-26 15:30:00
目标会议: neurips2026

## 迁移摘要

- 迁移状态: 部分成功
- 编译状态: 失败
- 匿名化检查: 通过

## 模板迁移

已完成基础迁移。
"""
    (temp_workspace / "submission" / "migration_report.md").write_text(report_content)

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "编译状态" in err


def test_submission_validate_outputs_rejects_non_pdf_payload(temp_workspace):
    """main.pdf 不能只是扩展名正确的文本文件。"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    bundle_dir = temp_workspace / "submission" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "main.tex").write_text(r"\documentclass{article}\begin{document}\end{document}")
    (bundle_dir / "references.bib").write_text("@article{test,}")
    (bundle_dir / "main.pdf").write_bytes(b"this is not a pdf even if the filename says pdf")
    (bundle_dir / "main.log").write_text("This is a clean compile log.")
    _write_compile_report(temp_workspace)
    (temp_workspace / "submission" / "migration_report.md").write_text(
        "# 投稿迁移报告\n\n"
        "## 迁移摘要\n"
        "- 迁移状态: 成功\n"
        "- 编译状态: 成功\n"
        "- 匿名化检查: 通过\n\n"
        + "details " * 30,
        encoding="utf-8",
    )

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "%PDF" in err


def test_submission_validate_outputs_rejects_tiny_pdf_placeholder(temp_workspace):
    """只有 PDF 文件头的极小占位文件不能通过。"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    bundle_dir = temp_workspace / "submission" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "main.tex").write_text(r"\documentclass{article}\begin{document}\end{document}")
    (bundle_dir / "references.bib").write_text("@article{test,}")
    (bundle_dir / "main.pdf").write_bytes(b"%PDF-1.4\n")
    (bundle_dir / "main.log").write_text("This is a clean compile log.")
    _write_compile_report(temp_workspace)
    (temp_workspace / "submission" / "migration_report.md").write_text(
        "# 投稿迁移报告\n\n"
        "## 迁移摘要\n"
        "- 迁移状态: 成功\n"
        "- 编译状态: 成功\n"
        "- 匿名化检查: 通过\n\n"
        + "details " * 30,
        encoding="utf-8",
    )

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "过小" in err


def test_submission_validate_outputs_fatal_log_detected(temp_workspace):
    """测试日志仍有 fatal error 时不能通过。"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    bundle_dir = temp_workspace / "submission" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "main.tex").write_text(r"\documentclass{article}\begin{document}\end{document}")
    (bundle_dir / "references.bib").write_text("@article{test,}")
    (bundle_dir / "main.pdf").write_bytes(b"%PDF-1.4\nmock pdf body\n%%EOF")
    (bundle_dir / "main.log").write_text("! Emergency stop.\nFatal error occurred")
    _write_compile_report(temp_workspace)

    report_content = """# 投稿迁移报告

生成时间: 2024-01-26 15:30:00
目标会议: neurips2026

## 迁移摘要

- 迁移状态: 成功
- 编译状态: 成功
- 匿名化检查: 通过

## 模板迁移

已完成基础迁移。

## 文件清单

- main.tex
- references.bib
- main.pdf
"""
    (temp_workspace / "submission" / "migration_report.md").write_text(report_content)

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "致命编译错误" in err


def test_submission_validate_outputs_undefined_reference_log_detected(temp_workspace):
    """编译日志里有 unresolved reference/citation 时不能通过。"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    bundle_dir = temp_workspace / "submission" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "main.tex").write_text(r"\documentclass{article}\begin{document}\end{document}")
    (bundle_dir / "references.bib").write_text("@article{test,}")
    (bundle_dir / "main.pdf").write_bytes(b"%PDF-1.4\nmock pdf body\n%%EOF")
    (bundle_dir / "main.log").write_text(
        "LaTeX Warning: There were undefined references.\n"
        "LaTeX Warning: Citation `missing2024' on page 1 undefined."
    )
    _write_compile_report(temp_workspace)
    (temp_workspace / "submission" / "migration_report.md").write_text(
        "# 投稿迁移报告\n\n"
        "## 迁移摘要\n"
        "- 迁移状态: 成功\n"
        "- 编译状态: 成功\n"
        "- 匿名化检查: 通过\n\n"
        + "details " * 30,
        encoding="utf-8",
    )

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "致命编译错误" in err


def test_submission_validate_outputs_should_require_compile_log_evidence(temp_workspace):
    """待修：没有编译日志或工具证据时，不应只凭 PDF 文件头和报告文字通过。"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    bundle_dir = temp_workspace / "submission" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "main.tex").write_text(r"\documentclass{article}\begin{document}\end{document}")
    (bundle_dir / "references.bib").write_text("@article{test,}")
    (bundle_dir / "main.pdf").write_bytes(b"%PDF-1.4\nmock pdf body\n%%EOF")
    (temp_workspace / "submission" / "migration_report.md").write_text(
        "# 投稿迁移报告\n\n"
        "## 迁移摘要\n"
        "- 迁移状态: 成功\n"
        "- 编译状态: 成功\n"
        "- 匿名化检查: 通过\n\n"
        + "details " * 30,
        encoding="utf-8",
    )

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "main.log" in err or "编译日志" in err or "compile_report" in err


def test_submission_validate_outputs_should_not_accept_historical_compile_success(temp_workspace):
    """待修：报告里当前状态失败但历史文本有成功时，不能误判成功。"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    bundle_dir = temp_workspace / "submission" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "main.tex").write_text(r"\documentclass{article}\begin{document}\end{document}")
    (bundle_dir / "references.bib").write_text("@article{test,}")
    (bundle_dir / "main.pdf").write_bytes(b"%PDF-1.4\nmock pdf body\n%%EOF")
    (bundle_dir / "main.log").write_text("This is a clean compile log.")
    _write_compile_report(temp_workspace)
    (temp_workspace / "submission" / "migration_report.md").write_text(
        "# 投稿迁移报告\n\n"
        "## 迁移摘要\n"
        "- 迁移状态: 成功\n"
        "- 当前编译状态: 失败\n"
        "- 匿名化检查: 通过\n\n"
        "## 历史尝试\n"
        "- 第一轮编译状态: 成功，但对应旧 main.tex。\n\n"
        + "details " * 30,
        encoding="utf-8",
    )

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "编译状态" in err


def test_submission_validate_outputs_report_too_short(temp_workspace):
    """测试迁移报告内容过短"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    # 创建bundle目录和必需文件
    bundle_dir = temp_workspace / "submission" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "main.tex").write_text(r"\documentclass{article}\begin{document}\end{document}")
    (bundle_dir / "references.bib").write_text("@article{test,}")
    (bundle_dir / "main.pdf").write_bytes(b"%PDF-1.4\nmock pdf body\n%%EOF")
    (bundle_dir / "main.log").write_text("This is a clean compile log.")
    _write_compile_report(temp_workspace)

    # 创建过短的报告
    (temp_workspace / "submission" / "migration_report.md").write_text("Too short")

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "migration_report.md" in err


def test_submission_validate_outputs_rejects_stale_pdf(temp_workspace):
    """main.tex 更新后必须重新编译 PDF。"""
    agent = SubmissionAgent()
    ctx = MockExecutionContext("submission", temp_workspace)

    bundle_dir = temp_workspace / "submission" / "bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = bundle_dir / "main.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nmock pdf body\n%%EOF")
    (bundle_dir / "main.tex").write_text(r"\documentclass{article}\begin{document}\end{document}")
    (bundle_dir / "references.bib").write_text("@article{test,}")
    (bundle_dir / "main.log").write_text("This is a clean compile log.")
    _write_compile_report(temp_workspace)
    old_time = (bundle_dir / "main.tex").stat().st_mtime - 5
    os.utime(pdf_path, (old_time, old_time))
    (temp_workspace / "submission" / "migration_report.md").write_text(
        "# 投稿迁移报告\n\n## 迁移摘要\n- 迁移状态: 成功\n- 编译状态: 成功\n- 匿名化检查: 通过\n\n"
        + "details " * 30,
        encoding="utf-8",
    )

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "早于 main.tex" in err


# ══════════════════════════════════════════════════════
# Anonymization Check Tests
# ══════════════════════════════════════════════════════

def test_check_anonymization_clean_paper(temp_workspace):
    """测试匿名化检查 - 干净的论文"""
    paper_content = r"""\documentclass{article}
\begin{document}
\title{Test Paper}
\section{Introduction}
This is a test paper about machine learning.
\section{Method}
We propose a new approach.
\section{Experiments}
Results show effectiveness.
\end{document}
"""
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = check_anonymization(ctx)
    assert ok
    assert err is None


def test_check_anonymization_email_detected(temp_workspace):
    """测试匿名化检查 - 检测到邮箱"""
    paper_content = r"""\documentclass{article}
\begin{document}
\title{Test Paper}
\author{Test Author}
\maketitle
Contact: author@example.com for questions.
\section{Method}
Our method is described here.
\end{document}
"""
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = check_anonymization(ctx)
    assert not ok
    assert "email" in err


def test_check_anonymization_github_detected(temp_workspace):
    """测试匿名化检查 - 检测到GitHub链接"""
    paper_content = r"""\documentclass{article}
\begin{document}
\title{Test Paper}
Code is available at github.com/test/project.
\section{Method}
Our implementation follows standard practices.
\end{document}
"""
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = check_anonymization(ctx)
    assert not ok
    assert "github" in err


def test_check_anonymization_url_detected(temp_workspace):
    """测试匿名化检查 - 检测到URL"""
    paper_content = r"""\documentclass{article}
\begin{document}
\title{Test Paper}
Results available at https://example.com/results
\section{Method}
Our method shows promise.
\end{document}
"""
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = check_anonymization(ctx)
    assert not ok
    assert "url" in err


def test_check_anonymization_acknowledgments_detected(temp_workspace):
    """测试匿名化检查 - 检测到致谢"""
    paper_content = r"""\documentclass{article}
\begin{document}
\title{Test Paper}
\section{Acknowledgments}
We thank the reviewers for their helpful comments.
\section{Method}
Our method is described here.
\end{document}
"""
    (temp_workspace / "drafts" / "paper.tex").write_text(paper_content)
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = check_anonymization(ctx)
    assert not ok
    assert "acknowledgments" in err or "致谢" in err


def test_check_anonymization_no_paper_file(temp_workspace):
    """测试匿名化检查 - 没有paper.tex文件"""
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = check_anonymization(ctx)
    assert ok  # 应该通过，因为没有文件时不报错


def test_submission_compile_environment_uses_native_latexmk(monkeypatch, temp_workspace):
    monkeypatch.setattr(
        "researchos.agents.submission.shutil.which",
        lambda name: "/usr/bin/latexmk" if name == "latexmk" else None,
    )
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = check_submission_compile_environment(ctx)

    assert ok
    assert err is None


def test_submission_compile_environment_pauses_without_latex_or_docker(monkeypatch, temp_workspace):
    monkeypatch.setattr("researchos.agents.submission.shutil.which", lambda _name: None)
    monkeypatch.setattr("researchos.tools.docker_exec.shutil.which", lambda _name: None)
    ctx = MockExecutionContext("submission", temp_workspace)

    ok, err = check_submission_compile_environment(ctx)

    assert not ok
    assert "WAITING_ENVIRONMENT" in err
