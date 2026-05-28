"""T8 Reviewer Agent — 论文审稿

输入: drafts/paper.tex, experiments/results_summary.json, literature/related_work.bib
输出: drafts/review_rounds/round_N.md
"""

from __future__ import annotations

import re
from pathlib import Path

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.prompts import render_prompt
from ..tools.manuscript import CORE_SECTIONS
from ._common import load_project, prepend_resume_prefix, read_text_file


class ReviewerAgent(Agent):
    """论文审稿Agent，提供结构化审稿意见。"""

    def __init__(self):
        super().__init__(
            build_agent_spec(
                "reviewer",
                defaults={
                    "model_tier": "heavy",
                    "tool_names": [
                        "read_file",
                        "list_files",
                        "write_file",
                        "finish_task",
                    ],
                    "max_steps": 60,
                    "max_tokens_total": 200_000,
                    "max_wall_seconds": 600,
                    "max_validation_retries": 3,
                    "temperature": 0.3,
                    "allowed_read_prefixes": [
                        "",
                        "drafts/",
                        "literature/",
                        "experiments/",
                    ],
                    "allowed_write_prefixes": ["drafts/review_rounds/"],
                    "prompt_template": "reviewer.j2",
                },
            )
        )

    @staticmethod
    def _round(ctx: ExecutionContext) -> int:
        if ctx.extra:
            round_num = ctx.extra.get("round")
            if isinstance(round_num, int):
                return round_num
        return 1

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """渲染system prompt。"""
        project = load_project(ctx)
        ws = ctx.workspace_dir

        # 读取实验结果用于验证
        results_summary = read_text_file(
            ws / "experiments" / "results_summary.json", default="{}"
        )
        related_work = read_text_file(ws / "literature" / "related_work.bib", default="")
        manuscript_audit = read_text_file(ws / "drafts" / "manuscript_audit.md", default="")
        self_check = read_text_file(ws / "drafts" / "self_check.md", default="")
        cdr_claim_ledger = read_text_file(ws / "drafts" / "cdr_claim_ledger.json", default="")

        round_num = self._round(ctx)
        previous_review = (
            read_text_file(
                ws / "drafts" / "review_rounds" / f"round_{round_num - 1}.md",
                default="",
            )
            if round_num > 1
            else ""
        )

        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            results_summary=results_summary,
            related_work_bib=related_work[:3000],
            manuscript_audit_preview=manuscript_audit[:3000],
            self_check_preview=self_check[:3000],
            cdr_claim_ledger_preview=cdr_claim_ledger[:5000],
            previous_review_preview=previous_review[:3000],
            round=round_num,
            target_venue=project.get("target_venue", "neurips"),
            temperature=self.spec.temperature,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """生成审稿任务消息。"""
        round_num = self._round(ctx)
        return prepend_resume_prefix(
            ctx,
            (
            f"请执行 T8 Reviewer 第{round_num}轮审稿。\n\n"
            "读取 drafts/paper.tex、drafts/manuscript_audit.md、drafts/self_check.md"
            f"{'、drafts/review_rounds/round_' + str(round_num - 1) + '.md' if round_num > 1 else ''}，"
            f"先逐章生成 drafts/review_rounds/round_{round_num}_sections/*.md，"
            f"再综合生成 drafts/review_rounds/round_{round_num}.md。"
            "从内容完整性、技术准确性、写作质量、学术规范和 CDR 贡献兑现五个维度审查，"
            "并检查上一轮问题是否闭环。"
            ),
        )

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验审稿报告。"""
        ws = ctx.workspace_dir
        round_num = self._round(ctx)

        report_path = ws / "drafts" / "review_rounds" / f"round_{round_num}.md"
        report = read_text_file(report_path, default="")

        if len(report) < 50:
            return False, f"review report 过短({len(report)}字符)"

        # 检查报告结构
        required_sections = ["## 总体评价", "## 主要问题", "## 次要问题", "## CDR Contribution Verdict"]
        for section in required_sections:
            if section not in report:
                return False, f"review report 缺少必需章节: {section}"
        for marker in [
            "Problem frame clarity",
            "Design rationale support",
            "Contribution type credibility",
            "Evidence alignment",
            "Boundary condition honesty",
            "Verdict",
        ]:
            if marker not in report:
                return False, f"CDR Contribution Verdict 缺少字段: {marker}"

        section_dir = ws / "drafts" / "review_rounds" / f"round_{round_num}_sections"
        if not section_dir.exists():
            return False, f"缺少逐章节审稿目录: drafts/review_rounds/round_{round_num}_sections"
        for section_id in CORE_SECTIONS:
            section_report = section_dir / f"{section_id}.md"
            text = read_text_file(section_report, default="")
            if len(text.strip()) < 80:
                return False, f"逐章节审稿过短或缺失: {section_report.relative_to(ws)}"
            if "##" not in text:
                return False, f"逐章节审稿缺少结构化标题: {section_report.relative_to(ws)}"
            if "## CDR Alignment Check" not in text:
                return False, f"逐章节审稿缺少 CDR Alignment Check: {section_report.relative_to(ws)}"

        return True, None
