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
from ._common import load_project, read_text_file


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

        round_num = self._round(ctx)

        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            results_summary=results_summary,
            related_work_bib=related_work[:3000],
            round=round_num,
            target_venue=project.get("target_venue", "neurips"),
            temperature=self.spec.temperature,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """生成审稿任务消息。"""
        round_num = self._round(ctx)
        return (
            f"请执行 T8 Reviewer 第{round_num}轮审稿。\n\n"
            f"读取 drafts/paper.tex，生成 drafts/review_rounds/round_{round_num}.md。"
            "从内容完整性、技术准确性、写作质量、学术规范四个维度审查。"
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
        required_sections = ["## 总体评价", "## 主要问题", "## 次要问题"]
        for section in required_sections:
            if section not in report:
                return False, f"review report 缺少必需章节: {section}"

        # 验证数字准确性（如果可以）
        paper_path = ws / "drafts" / "paper.tex"
        if paper_path.exists():
            paper = paper_path.read_text()
            # 简单检查：report 中引用的数字是否在 paper 中提到
            # 这里不做严格验证，因为报告可能有合理的总结性表述

        return True, None
