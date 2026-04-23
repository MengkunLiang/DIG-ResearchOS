"""T8 Writer Agent — 论文写作

支持多个phase: outline/draft/self_check/revise/final
输出: drafts/outline.md, drafts/paper.tex, drafts/self_check.md
"""

from __future__ import annotations

import re
from pathlib import Path

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.prompts import render_prompt
from ._common import load_project, read_text_file


class WriterAgent(Agent):
    """论文写作Agent，支持大纲生成、初稿、自查和修订。"""

    def __init__(self):
        super().__init__(
            build_agent_spec(
                "writer",
                defaults={
                    "model_tier": "heavy",
                    "tool_names": [
                        "read_file",
                        "write_file",
                        "list_files",
                        "finish_task",
                    ],
                    "max_steps": 100,
                    "max_tokens_total": 400_000,
                    "max_wall_seconds": 1200,
                    "max_validation_retries": 3,
                    "temperature": 0.7,
                    "allowed_read_prefixes": [
                        "",
                        "literature/",
                        "experiments/",
                        "ideation/",
                    ],
                    "allowed_write_prefixes": ["drafts/"],
                    "prompt_template": "writer.j2",
                },
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """渲染system prompt，传入项目配置、实验结果和文献资料。"""
        project = load_project(ctx)
        ws = ctx.workspace_dir

        # 读取实验结果
        results_summary = read_text_file(
            ws / "experiments" / "results_summary.json", default="{}"
        )
        synthesis = read_text_file(ws / "literature" / "synthesis.md", default="")
        related_work = read_text_file(ws / "literature" / "related_work.bib", default="")
        hypotheses = read_text_file(ws / "ideation" / "hypotheses.md", default="")
        ablations = read_text_file(ws / "experiments" / "ablations.csv", default="")

        # 根据phase选择不同的prompt策略
        phase = ctx.extra.get("phase", "draft") if ctx.extra else "draft"

        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            results_summary=results_summary,
            synthesis_preview=synthesis[:6000],
            related_work_preview=related_work[:4000],
            hypotheses_preview=hypotheses[:3000],
            ablations_preview=ablations[:2000],
            phase=phase,
            target_venue=project.get("target_venue", "neurips"),
            temperature=self.spec.temperature,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """根据phase生成初始用户消息。"""
        phase = ctx.extra.get("phase", "draft") if ctx.extra else "draft"

        if phase == "outline":
            return (
                "请执行 T8 Writer Phase 1: 生成论文大纲。\n\n"
                "基于实验结果和文献综述，生成 drafts/outline.md。"
                "大纲应包含：标题候选、Abstract要点、Introduction结构、"
                "Related Work分类、Method结构、Experiments结构、Conclusion要点。"
            )
        elif phase == "draft":
            return (
                "请执行 T8 Writer Phase 2: 生成论文初稿。\n\n"
                "基于 drafts/outline.md，生成 drafts/paper.tex。"
                "**重要**: 所有实验数字必须来自 experiments/results_summary.json，"
                "所有引用必须存在于 literature/related_work.bib。"
            )
        elif phase == "self_check":
            return (
                "请执行 T8 Writer Phase 3: 论文自查。\n\n"
                "读取 drafts/paper.tex，生成 drafts/self_check.md。"
                "检查内容完整性、数字准确性、引用完整性、格式规范。"
            )
        elif phase == "revise":
            round_num = ctx.extra.get("round", 1) if ctx.extra else 1
            return (
                f"请执行 T8 Writer Phase 4: 修订论文（第{round_num}轮）。\n\n"
                f"根据 drafts/review_rounds/round_{round_num}.md 的审稿意见，"
                "修订 drafts/paper.tex。"
            )
        elif phase == "final":
            return (
                "请执行 T8 Writer Phase 5: 生成最终版。\n\n"
                "根据 drafts/user_corrections.md 的用户标注，"
                "生成最终版 drafts/paper.tex。"
            )
        else:
            return f"请执行 T8 Writer（phase={phase}）。"

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验输出文件。"""
        ws = ctx.workspace_dir
        phase = ctx.extra.get("phase", "draft") if ctx.extra else "draft"

        if phase == "outline":
            outline = read_text_file(ws / "drafts" / "outline.md", default="")
            if len(outline) < 100:
                return False, f"outline.md 过短({len(outline)}字符)"
            if "## " not in outline:
                return False, "outline.md 必须包含章节结构（## 标题）"
            return True, None

        elif phase in ("draft", "revise", "final"):
            paper = read_text_file(ws / "drafts" / "paper.tex", default="")
            if len(paper) < 50:
                return False, f"paper.tex 过短({len(paper)}字符)"

            # 检查LaTeX基本结构
            if "\\documentclass" not in paper:
                return False, "paper.tex 必须包含 \\documentclass"
            if "\\begin{document}" not in paper:
                return False, "paper.tex 必须包含 \\begin{document}"
            if "\\end{document}" not in paper:
                return False, "paper.tex 必须包含 \\end{document}"

            # 检查section存在
            sections = ["\\section{", "\\section*{"]
            if not any(s in paper for s in sections):
                return False, "paper.tex 必须包含至少一个章节"

            # 验证引用（如果存在related_work.bib）
            bib_path = ws / "literature" / "related_work.bib"
            if bib_path.exists():
                bib_text = bib_path.read_text()
                bib_keys = set(re.findall(r"@\w+\{(\w+),", bib_text))
                cited = set(re.findall(r"\\cite\{([^}]+)\}", paper))
                cited = {k.strip() for chunk in cited for k in chunk.split(",")}
                missing_cites = cited - bib_keys
                if missing_cites:
                    return False, f"paper.tex 引用了不存在的BibTeX key: {missing_cites}"

            return True, None

        elif phase == "self_check":
            check = read_text_file(ws / "drafts" / "self_check.md", default="")
            if len(check) < 200:
                return False, f"self_check.md 过短({len(check)}字符)"
            return True, None

        return True, None
