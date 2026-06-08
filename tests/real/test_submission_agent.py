"""Submission Agent Integration Tests.

测试论文提交 Agent（T9）。
注意：submission 需要 Docker（LaTeX 编译）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from researchos.agents.submission import SubmissionAgent
from researchos.tools.latex_compile import _compile_dependency_fingerprint
from researchos.tools.manuscript import craft_audit_input_fingerprints


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_passing_craft_audit(workspace: Path) -> None:
    drafts = workspace / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    (drafts / "sections").mkdir(parents=True, exist_ok=True)
    if not (drafts / "paper_state.json").exists():
        (drafts / "paper_state.json").write_text(
            json.dumps(
                {
                    "semantics": "shared_state_for_section_by_section_writing_not_final_claims",
                    "sections": {},
                    "shared_facts": {"bib_keys": [], "result_metrics": [], "alignment_matrix": []},
                }
            ),
            encoding="utf-8",
        )
    if not (drafts / "alignment_matrix.json").exists():
        (drafts / "alignment_matrix.json").write_text(
            '{"semantics":"alignment_matrix_seed_not_final_scientific_judgment","rows":[]}\n',
            encoding="utf-8",
        )
    if not (drafts / "cdr_claim_ledger.json").exists():
        (drafts / "cdr_claim_ledger.json").write_text(
            '{"semantics":"cdr_claim_ledger_seed_not_final_scientific_judgment","contribution_chains":[]}\n',
            encoding="utf-8",
        )
    checks = [
        {"name": "matrix_row_count", "level": "PASS", "passed": True},
        {"name": "intro_contribution_count", "level": "PASS", "passed": True},
        {"name": "abstract_no_cite", "level": "PASS", "passed": True},
        {"name": "abstract_no_section_heading", "level": "PASS", "passed": True},
        {"name": "no_internal_label_leakage", "level": "PASS", "passed": True},
        {"name": "no_placeholder_tokens", "level": "PASS", "passed": True},
        {"name": "number_traceability", "level": "PASS", "passed": True},
        {"name": "no_standalone_limitations", "level": "PASS", "passed": True},
        {"name": "conclusion_has_limitations_subsection", "level": "PASS", "passed": True},
    ]
    (drafts / "craft_audit.md").write_text("# Writing Craft And Alignment Audit\n- [x] ok\n", encoding="utf-8")
    (drafts / "craft_audit.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "deterministic_writing_craft_audit_not_scientific_judgment",
                "input_fingerprints": craft_audit_input_fingerprints(workspace),
                "checks": checks,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class TestSubmissionAgent:
    """Submission Agent 测试套件。"""

    def test_agent_initialization(self):
        """测试 Agent 初始化。"""
        agent = SubmissionAgent()
        assert agent is not None
        assert agent.spec.name == "submission"

    def test_agent_has_required_tools(self):
        """测试 Agent 有必需的工具。"""
        agent = SubmissionAgent()
        # submission agent 需要的工具
        assert "read_file" in agent.spec.tool_names
        assert "write_file" in agent.spec.tool_names
        assert "finish_task" in agent.spec.tool_names

    def test_agent_has_docker_exec(self):
        """测试 submission agent 有 docker_exec 工具（LaTeX 编译）。"""
        agent = SubmissionAgent()
        # submission agent 需要 docker_exec（因为编译 LaTeX）
        assert "docker_exec" in agent.spec.tool_names

    def test_agent_system_prompt(self, standard_workspace: Path, project_yaml: Path):
        """测试 system prompt 生成。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.md
        paper = standard_workspace / "drafts" / "paper.md"
        paper.write_text("# Paper\n\nContent...", encoding="utf-8")

        agent = SubmissionAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="submission",
            run_id="submission_run",
            task_id="T9",
            mode=None,
            extra={},
        )
        prompt = agent.system_prompt(ctx)
        assert prompt is not None
        assert len(prompt) > 0

    def test_agent_initial_user_message(self, standard_workspace: Path, project_yaml: Path):
        """测试初始用户消息。"""
        from researchos.runtime.agent import ExecutionContext

        agent = SubmissionAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="submission",
            run_id="submission_run",
            task_id="T9",
            mode=None,
            extra={},
        )
        msg = agent.initial_user_message(ctx)
        assert msg is not None
        assert "submission" in msg.lower() or "提交" in msg


class TestSubmissionAgentValidateOutputs:
    """Submission Agent 输出验证测试。"""

    def test_validate_outputs_no_files(self, standard_workspace: Path, project_yaml: Path):
        """测试无文件时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.md
        paper = standard_workspace / "drafts" / "paper.md"
        paper.write_text("# Paper\n\nContent..." * 100, encoding="utf-8")

        # 创建 project.yaml
        project = standard_workspace / "project.yaml"
        project.write_text(
            "name: test\n"
            "target_venue: neurips2026\n",
            encoding="utf-8",
        )

        agent = SubmissionAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="submission",
            run_id="submission_run",
            task_id="T9",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False

    def test_validate_outputs_missing_bib(self, standard_workspace: Path, project_yaml: Path):
        """测试缺少 bib 文件时的验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.md
        paper = standard_workspace / "drafts" / "paper.md"
        paper.write_text("# Paper\n\nContent..." * 100, encoding="utf-8")

        # 创建 project.yaml
        project = standard_workspace / "project.yaml"
        project.write_text(
            "name: test\n"
            "target_venue: neurips2026\n",
            encoding="utf-8",
        )

        # 创建 bundle 目录（缺少 main.tex）
        bundle = standard_workspace / "submission" / "bundle"
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "main.pdf").write_text("%PDF-1.4", encoding="utf-8")

        agent = SubmissionAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="submission",
            run_id="submission_run",
            task_id="T9",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is False
        assert "main.tex" in err or "bib" in err.lower()

    def test_validate_outputs_success(self, standard_workspace: Path, project_yaml: Path):
        """测试成功验证。"""
        from researchos.runtime.agent import ExecutionContext

        # 创建 paper.md
        paper = standard_workspace / "drafts" / "paper.md"
        paper.write_text("# Paper\n\nContent..." * 100, encoding="utf-8")

        # 创建 project.yaml
        project = standard_workspace / "project.yaml"
        project.write_text(
            "name: test\n"
            "target_venue: neurips2026\n",
            encoding="utf-8",
        )

        # 创建完整的 bundle
        bundle = standard_workspace / "submission" / "bundle"
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "main.tex").write_text(
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "Test\n"
            "\\bibliographystyle{plain}\n"
            "\\bibliography{references}\n"
            "\\end{document}",
            encoding="utf-8",
        )
        (bundle / "references.bib").write_text(
            "@article{test,\n  title={Test}\n}",
            encoding="utf-8",
        )
        (bundle / "main.pdf").write_bytes(b"%PDF-1.4\n% ResearchOS test PDF placeholder\n1 0 obj\n<<>>\nendobj\n%%EOF\n")
        (bundle / "main.log").write_text(
            "This is pdfTeX, Version test\nOutput written on main.pdf (1 page).\n",
            encoding="utf-8",
        )
        source_paper = standard_workspace / "drafts" / "paper.tex"
        source_paper.write_text((bundle / "main.tex").read_text(encoding="utf-8"), encoding="utf-8")
        source_bib = standard_workspace / "literature" / "related_work.bib"
        source_bib.write_text((bundle / "references.bib").read_text(encoding="utf-8"), encoding="utf-8")
        main_tex = bundle / "main.tex"
        main_pdf = bundle / "main.pdf"
        main_log = bundle / "main.log"
        (bundle / "bundle_manifest.json").write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "semantics": "submission_bundle_source_fingerprint",
                    "source": {
                        "paper_path": "drafts/paper.tex",
                        "paper_sha256": _sha256_file(source_paper),
                        "bib_path": "literature/related_work.bib",
                        "bib_sha256": _sha256_file(source_bib),
                    },
                    "bundle": {
                        "main_tex_path": "submission/bundle/main.tex",
                        "main_tex_sha256": _sha256_file(main_tex),
                        "references_bib_path": "submission/bundle/references.bib",
                        "references_bib_sha256": _sha256_file(bundle / "references.bib"),
                        "copied_figures": [],
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        compile_report = standard_workspace / "submission" / "compile_report.json"
        dependency_fingerprint = _compile_dependency_fingerprint(standard_workspace, main_tex)
        compile_report.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "semantics": "latex_compile_attempt_report",
                    "success": True,
                    "tex_path": "submission/bundle/main.tex",
                    "pdf_path": "submission/bundle/main.pdf",
                    "log_path": "submission/bundle/main.log",
                    "main_tex_sha256": _sha256_file(main_tex),
                    "pdf_sha256": _sha256_file(main_pdf),
                    "log_sha256": _sha256_file(main_log),
                    "dependency_fingerprint": dependency_fingerprint,
                    "pdf_mtime": main_pdf.stat().st_mtime,
                    "log_mtime": main_log.stat().st_mtime,
                    "pdf_size": main_pdf.stat().st_size,
                    "log_size": main_log.stat().st_size,
                    "attempts": [
                        {
                            "success": True,
                            "exit_code": 0,
                            "dependency_fingerprint_hash": dependency_fingerprint["hash"],
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        _write_passing_craft_audit(standard_workspace)

        # 创建 migration_report.md（SubmissionAgent 必需）
        report = standard_workspace / "submission" / "migration_report.md"
        report.write_text(
            "# Migration Report\n\n"
            "## 迁移状态\n\n"
            "所有文件已成功迁移。\n\n"
            "## 编译状态\n\n"
            "编译状态: 成功\n\n"
            "## 匿名化检查\n\n"
            "已完成匿名化处理。\n\n"
            "This is sufficient content for the migration report to pass validation.",
            encoding="utf-8",
        )

        agent = SubmissionAgent()
        ctx = ExecutionContext(
            workspace_dir=standard_workspace,
            project_id="submission",
            run_id="submission_run",
            task_id="T9",
            mode=None,
            extra={},
        )

        ok, err = agent.validate_outputs(ctx)
        assert ok is True, err


class TestSubmissionAgentDockerDependency:
    """Submission Agent Docker 依赖测试。"""

    def test_submission_docker_boundary(self):
        """测试 submission 在 Docker 边界内。"""
        agent = SubmissionAgent()
        # submission 需要 Docker 执行 LaTeX 编译
        assert "docker_exec" in agent.spec.tool_names

    def test_only_experimenter_and_submission_require_docker(self):
        """测试只有 experimenter 和 submission 需要 Docker。"""
        from researchos.agents.hello import HelloAgent
        from researchos.agents.pi import PIAgent
        from researchos.agents.scout import ScoutAgent
        from researchos.agents.reader import ReaderAgent
        from researchos.agents.ideation import IdeationAgent
        from researchos.agents.novelty import NoveltyAgent
        from researchos.agents.novelty_auditor import NoveltyAuditorAgent
        from researchos.agents.writer import WriterAgent
        from researchos.agents.reviewer import ReviewerAgent

        # 确认其他 agent 不需要 docker_exec
        non_docker_agents = [
            HelloAgent(),
            PIAgent(),
            ScoutAgent(),
            ReaderAgent(),
            IdeationAgent(),
            NoveltyAuditorAgent(),
            NoveltyAgent(),
            WriterAgent(),
            ReviewerAgent(),
        ]

        for agent in non_docker_agents:
            assert (
                "docker_exec" not in agent.spec.tool_names
            ), f"{agent.spec.name} should not require docker_exec"
