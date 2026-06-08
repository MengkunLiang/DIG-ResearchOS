"""Writer Agent Integration Tests.

测试论文写作 Agent（T8 outline/draft/revise 模式）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.agents.writer import WriterAgent
from researchos.tools.manuscript import build_paper_state_input_fingerprints, craft_audit_input_fingerprints


CORE_SECTIONS = [
    "methodology",
    "experiments",
    "related_work",
    "analysis",
    "introduction",
    "conclusion",
    "abstract",
]


def _write_alignment_matrix(workspace: Path) -> None:
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
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_paper_state_and_sections(workspace: Path, *, short_section: bool = False) -> None:
    sections = {}
    for section_id in CORE_SECTIONS:
        sections[section_id] = {
            "status": "written",
            "file": f"drafts/sections/{section_id}.tex",
            "outline": f"drafts/section_outlines/{section_id}.md",
        }
        outline_path = workspace / "drafts" / "section_outlines" / f"{section_id}.md"
        outline_path.parent.mkdir(parents=True, exist_ok=True)
        outline_path.write_text(
            f"# Section Outline: {section_id}\n\n## Purpose\n" + ("Detailed outline. " * 10),
            encoding="utf-8",
        )
        section_path = workspace / "drafts" / "sections" / f"{section_id}.tex"
        section_path.parent.mkdir(parents=True, exist_ok=True)
        body = "Short." if short_section and section_id == "methodology" else ("Substantive section content. " * 6)
        section_path.write_text(
            f"\\section{{{section_id.replace('_', ' ').title()}}}\n{body}",
            encoding="utf-8",
        )
    (workspace / "drafts" / "paper_state.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "shared_state_for_section_by_section_writing_not_final_claims",
                "input_fingerprints": build_paper_state_input_fingerprints(
                    workspace,
                    {
                        "outline": "drafts/outline.md",
                        "resource_index": "drafts/resource_index.json",
                        "section_plan": "drafts/section_plan.json",
                        "evidence_plan": "drafts/evidence_plan.json",
                        "figure_table_plan": "drafts/figure_table_plan.json",
                        "alignment_matrix": "drafts/alignment_matrix.json",
                        "related_work_bib": "literature/related_work.bib",
                        "experiment_evidence_pack": "drafts/experiment_evidence_pack.json",
                    },
                ),
                "section_order": CORE_SECTIONS,
                "sections": sections,
                "shared_facts": {
                    "bib_keys": ["smith2024"],
                    "result_metrics": [],
                    "alignment_matrix": [{"cid": "C1"}, {"cid": "C2"}, {"cid": "C3"}],
                },
            }
        ),
        encoding="utf-8",
    )


def _write_valid_draft_support(workspace: Path, *, short_section: bool = False) -> None:
    _write_paper_state_and_sections(workspace, short_section=short_section)
    (workspace / "literature" / "related_work.bib").write_text(
        "@article{smith2024, title={Test}, author={Smith}, year={2024}}\n",
        encoding="utf-8",
    )
    (workspace / "drafts" / "manuscript_audit.md").write_text("# Audit\n- [x] ok\n", encoding="utf-8")
    (workspace / "drafts" / "craft_audit.md").write_text("# Writing Craft And Alignment Audit\n- [x] ok\n", encoding="utf-8")
    (workspace / "drafts" / "craft_audit.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "deterministic_writing_craft_audit_not_scientific_judgment",
                "venue_style": "ccf_a",
                "alignment_cids": ["C1", "C2", "C3"],
                "checks": [
                    {"name": "matrix_row_count", "level": "PASS", "passed": True},
                    {"name": "intro_contribution_count", "level": "PASS", "passed": True},
                    {"name": "abstract_no_cite", "level": "PASS", "passed": True},
                    {"name": "no_internal_label_leakage", "level": "PASS", "passed": True},
                    {"name": "no_placeholder_tokens", "level": "PASS", "passed": True},
                    {"name": "number_traceability", "level": "PASS", "passed": True},
                    {"name": "no_standalone_limitations", "level": "PASS", "passed": True},
                    {"name": "conclusion_has_limitations_subsection", "level": "PASS", "passed": True},
                ],
                "input_fingerprints": craft_audit_input_fingerprints(workspace),
            }
        ),
        encoding="utf-8",
    )
    (workspace / "drafts" / "patches").mkdir(parents=True, exist_ok=True)
    (workspace / "drafts" / "patches" / "round_1_patches.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "mechanical_review_issue_locations_not_final_revision_decisions",
                "round": 1,
                "patches": [
                    {
                        "patch_id": "P1",
                        "target_section": "introduction",
                        "target_file": "drafts/sections/introduction.tex",
                        "severity": "low",
                        "issue_type": "clarity",
                        "specific_issue": "Improve clarity.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (workspace / "drafts" / "revision_response_round_1.md").write_text(
        "# Revision Response Round 1\n\n"
        "- P1 resolved: revised the introduction section for clarity without rewriting the whole paper.\n"
        "- Unresolved: none.\n",
        encoding="utf-8",
    )


class TestWriterAgent:
    """Writer Agent 测试套件。"""

    def test_agent_initialization(self):
        """测试 Agent 初始化。"""
        agent = WriterAgent()
        assert agent is not None
        assert agent.spec.name == "writer"

    def test_agent_has_required_tools(self):
        """测试 Agent 有必需的工具。"""
        agent = WriterAgent()
        # writer agent 需要的工具
        assert "read_file" in agent.spec.tool_names
        assert "write_file" in agent.spec.tool_names
        assert "finish_task" in agent.spec.tool_names

    def test_agent_has_no_docker_exec(self):
        """测试 writer agent 没有 docker_exec 工具。"""
        agent = WriterAgent()
        # writer agent 不需要 docker_exec
        assert "docker_exec" not in agent.spec.tool_names

    def test_agent_system_prompt_outline_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 outline 模式的 system prompt。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建必要的文件
        synthesis = standard_workspace / "literature" / "synthesis.md"
        synthesis.write_text("# Synthesis\n\nContent...", encoding="utf-8")

        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text("# Hypotheses\n\nH1: Test\n", encoding="utf-8")

        novelty_report = standard_workspace / "novelty" / "novelty_report.md"
        novelty_report.write_text("# Novelty\n\nLevel 1\n", encoding="utf-8")

        results = standard_workspace / "experiments" / "results_summary.json"
        results.write_text('{"results": []}', encoding="utf-8")

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="outline",
            extra={"phase": "outline"},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_initial_user_message_outline_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 outline 模式的初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="outline",
            extra={"phase": "outline"},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "outline" in msg.lower()

    def test_agent_initial_user_message_draft_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 draft 模式的初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="draft",
            extra={"phase": "draft"},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "draft" in msg.lower()

    def test_agent_initial_user_message_revise_mode(self, standard_workspace: Path, project_yaml: Path):
        """测试 revise 模式的初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="revise",
            extra={"phase": "revise"},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "修订" in msg


class TestWriterAgentValidateOutputs:
    """Writer Agent 输出验证测试。"""

    def test_validate_outline_no_file(self, standard_workspace: Path, project_yaml: Path):
        """测试 outline 模式无文件时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建必要的依赖文件
        synthesis = standard_workspace / "literature" / "synthesis.md"
        synthesis.write_text("# Synthesis\n\nContent...", encoding="utf-8")

        hypotheses = standard_workspace / "ideation" / "hypotheses.md"
        hypotheses.write_text("# Hypotheses\n\nH1: Test\n", encoding="utf-8")

        novelty_report = standard_workspace / "novelty" / "novelty_report.md"
        novelty_report.write_text("# Novelty\n\nLevel 1\n", encoding="utf-8")

        results = standard_workspace / "experiments" / "results_summary.json"
        results.write_text('{"results": []}', encoding="utf-8")

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="outline",
            extra={"phase": "outline"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "outline" in err.lower()

    def test_validate_outline_missing_sections(self, standard_workspace: Path, project_yaml: Path):
        """测试 outline 没有章节标记时的验证。

        注意：当前实现只检查 outline 是否包含 ## 标记，不检查特定章节。
        """
        from researchos.runtime.agent import ExecutionContext

        # 创建没有 ## 章节标记的 outline（但有足够的字符数）
        outline = standard_workspace / "drafts" / "outline.md"
        outline.write_text(
            "# Outline\n\n"
            "This is a short document without proper heading markers. "
            "It has enough characters to pass the length check but is missing "
            "the required markers that indicate proper structure. "
            "Each chapter should be marked to denote sections. "
            "Without these markers the document will not pass validation checks.\n" * 5,
            encoding="utf-8",
        )
        _write_alignment_matrix(standard_workspace)

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="outline",
            extra={"phase": "outline"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "section" in err.lower() or "章节" in err

    def test_validate_outline_success(self, standard_workspace: Path, project_yaml: Path):
        """测试 outline 模式成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建完整的 outline
        outline = standard_workspace / "drafts" / "outline.md"
        outline.write_text(
            "# Outline\n\n"
            "## Abstract\n\n"
            "A brief abstract.\n\n"
            "## 1. Introduction\n\n"
            "Introduction content.\n\n"
            "## 2. Related Work\n\n"
            "Related work content.\n\n"
            "## 3. Method\n\n"
            "Method content.\n\n"
            "## 4. Experiments\n\n"
            "Experiment content.\n\n"
            "## 5. Conclusion\n\n"
            "Conclusion content.\n\n"
            "This is a complete outline with all required sections.\n" * 5,
            encoding="utf-8",
        )
        _write_alignment_matrix(standard_workspace)

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="outline",
            extra={"phase": "outline"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True, err

    def _create_draft_paper_tex(self, workspace: Path) -> None:
        """创建测试用的 paper.tex 文件。"""
        paper = workspace / "drafts" / "paper.tex"
        paper.write_text(
            r"\documentclass{article}" + "\n"
            + r"\begin{document}" + "\n"
            + r"\section{Introduction}" + "\n"
            + "This is a test paper with enough content to pass validation.\n"
            + r"\section{Related Work}" + "\n"
            + "Related work description.\n"
            + r"\section{Method}" + "\n"
            + "Method description with sufficient text.\n"
            + r"\section{Experiments}" + "\n"
            + "Experimental results.\n"
            + r"\section{Conclusion}" + "\n"
            + r"\subsection{Limitations}" + "\n"
            + "Conclusion text.\n"
            + r"\end{document}",
            encoding="utf-8",
        )

    def test_validate_draft_no_file(self, standard_workspace: Path, project_yaml: Path):
        """测试 draft 模式无文件时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 outline
        outline = standard_workspace / "drafts" / "outline.md"
        outline.write_text("# Outline\n\nComplete outline.\n" * 10, encoding="utf-8")

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="draft",
            extra={"phase": "draft"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "paper" in err.lower() or "draft" in err.lower()

    def test_validate_draft_too_short(self, standard_workspace: Path, project_yaml: Path):
        """测试 draft 过短时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        self._create_draft_paper_tex(standard_workspace)
        _write_valid_draft_support(standard_workspace, short_section=True)

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="draft",
            extra={"phase": "draft"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "过短" in err or "too short" in err.lower()

    def test_validate_draft_success(self, standard_workspace: Path, project_yaml: Path):
        """测试 draft 模式成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建完整的 outline
        outline = standard_workspace / "drafts" / "outline.md"
        outline.write_text(
            "# Outline\n\n"
            "## Abstract\n\n"
            "A brief abstract.\n\n"
            "## 1. Introduction\n\n"
            "Introduction content.\n\n"
            "## 2. Related Work\n\n"
            "Related work content.\n\n"
            "## 3. Method\n\n"
            "Method content.\n\n"
            "## 4. Experiment\n\n"
            "Experiment content.\n\n"
            "## 5. Conclusion\n\n"
            "Conclusion content.\n\n"
            "This is a complete outline.\n",
            encoding="utf-8",
        )

        # 创建 paper.tex（使用辅助方法）
        self._create_draft_paper_tex(standard_workspace)
        _write_valid_draft_support(standard_workspace)

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="draft",
            extra={"phase": "draft"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True, f"Expected ok=True, got ok={ok}, err={err}"

    def test_validate_revise_no_feedback(self, standard_workspace: Path, project_yaml: Path):
        """测试 revise 模式无反馈时的验证。

        注意：当前实现中 revise 模式不强制要求反馈文件，只验证 paper.tex 结构。
        如果 paper.tex 结构完整，验证应该通过。
        """
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.tex（有效的 LaTeX 格式）
        self._create_draft_paper_tex(standard_workspace)
        _write_valid_draft_support(standard_workspace)

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="revise",
            extra={"phase": "revise"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True, f"Expected ok=True, got ok={ok}, err={err}"

    def test_validate_revise_success(self, standard_workspace: Path, project_yaml: Path):
        """测试 revise 模式成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.tex（有效的 LaTeX 格式）
        self._create_draft_paper_tex(standard_workspace)
        _write_valid_draft_support(standard_workspace)

        # 创建反馈文件
        feedback = standard_workspace / "drafts" / "review_rounds" / "feedback_round1.md"
        feedback.write_text(
            "# Feedback Round 1\n\n"
            "## Comments\n\n"
            "Please improve the writing.\n\n"
            "## Suggestions\n\n"
            "1. Fix grammar.\n"
            "2. Add more details.\n\n"
            "This is sufficient feedback.\n" * 5,
            encoding="utf-8",
        )

        # 创建修订后的 paper.md
        revised_paper = standard_workspace / "drafts" / "paper_revised.md"
        revised_paper.write_text(
            "# Revised Paper\n\n"
            "Content with improvements.\n" * 100,
            encoding="utf-8",
        )

        agent = WriterAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="writer",
            run_id="writer_run",
            task_id="T8",
            mode="revise",
            extra={"phase": "revise"},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True, err
