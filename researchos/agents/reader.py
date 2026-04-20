"""T3/T3.5 Reader Agent - 深度阅读与综合

T3 (read模式): 逐篇精读论文，产出结构化笔记
T3.5 (synthesize模式): 综合所有笔记，产出synthesis.md

契约详见 ResearchOS_Agent_Dev_Spec.md §8
"""

from __future__ import annotations

from ..runtime.agent import Agent, AgentSpec, ExecutionContext
from ..runtime.prompts import render_prompt
from ._common import load_project, load_jsonl, read_text_file


class ReaderAgent(Agent):
    """深度阅读Agent。read (T3)逐篇精读，synthesize (T3.5)综合。"""

    def __init__(self):
        super().__init__(
            AgentSpec(
                name="reader",
                model_tier="medium",
                tool_names=[
                    "read_file", "write_file", "append_file", "list_files",
                    "fetch_paper_pdf", "extract_pdf_text", "finish_task",
                ],
                max_steps=80,
                max_tokens_total=400_000,
                max_wall_seconds=7200,
                temperature=0.5,
                allowed_read_prefixes=["", "literature/"],
                allowed_write_prefixes=["literature/"],
                prompt_template="reader.j2",
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """根据mode渲染不同的system prompt。"""
        mode = ctx.mode or "read"
        project = load_project(ctx)
        context_vars = {"project": project}

        if mode == "read":
            dedup_path = ctx.workspace_dir / "literature" / "papers_dedup.jsonl"
            dedup_papers = load_jsonl(dedup_path) if dedup_path.exists() else []
            context_vars["paper_count"] = len(dedup_papers)
            context_vars["paper_list_preview"] = dedup_papers[:5]
        elif mode == "synthesize":
            notes_dir = ctx.workspace_dir / "literature" / "paper_notes"
            note_count = len(list(notes_dir.glob("*.md"))) if notes_dir.exists() else 0
            context_vars["note_count"] = note_count
            missing_areas_path = ctx.workspace_dir / "literature" / "missing_areas.md"
            context_vars["missing_areas"] = read_text_file(missing_areas_path, default="")

        return render_prompt(self.spec.prompt_template, ctx, **context_vars)

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """根据mode返回不同的初始消息。"""
        if (ctx.mode or "read") == "read":
            return (
                "请开始T3深度阅读流程。逐篇精读literature/papers_dedup.jsonl中的论文，"
                "为每篇产出paper_notes/{id}.md，同时累积comparison_table.csv和related_work.bib。"
            )
        return (
            "请开始T3.5综合流程。综合literature/paper_notes/目录下的所有笔记，"
            "产出literature/synthesis.md，包含5个必需章节：方法家族分类、共同假设、"
            "性能-效率前沿、技术趋势、可操作研究问题。"
        )

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验输出文件。"""
        ok, err = super().validate_outputs(ctx)
        if not ok:
            return False, err

        mode = ctx.mode or "read"
        if mode == "read":
            return self._validate_read_outputs(ctx)
        elif mode == "synthesize":
            return self._validate_synthesize_outputs(ctx)
        return False, f"未知模式: {mode}"

    def _validate_read_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验T3 read模式的输出。"""
        notes_dir = ctx.workspace_dir / "literature" / "paper_notes"
        if not notes_dir.exists():
            return False, "缺少literature/paper_notes目录"

        note_files = list(notes_dir.glob("*.md"))

        # 动态确定最小笔记数：基于papers_dedup.jsonl的实际数量
        dedup_path = ctx.workspace_dir / "literature" / "papers_dedup.jsonl"
        if dedup_path.exists():
            dedup_papers = load_jsonl(dedup_path)
            expected_count = len(dedup_papers)
            min_required = max(3, int(expected_count * 0.8))  # 至少80%的论文应该有笔记，最少3篇
        else:
            min_required = 10  # 如果没有papers_dedup.jsonl，使用默认值10

        if len(note_files) < min_required:
            return False, f"paper_notes只有{len(note_files)}篇，至少需要{min_required}篇（基于{expected_count if dedup_path.exists() else '默认'}篇输入论文）"

        ct_path = ctx.workspace_dir / "literature" / "comparison_table.csv"
        if not ct_path.exists():
            return False, "缺少literature/comparison_table.csv"

        try:
            import csv
            with ct_path.open(encoding="utf-8") as f:
                if sum(1 for _ in csv.reader(f)) < 2:
                    return False, "comparison_table.csv内容过少"
        except Exception as e:
            return False, f"comparison_table.csv解析失败: {e}"

        bib_path = ctx.workspace_dir / "literature" / "related_work.bib"
        if not bib_path.exists():
            return False, "缺少literature/related_work.bib"
        if "@" not in read_text_file(bib_path):
            return False, "related_work.bib似乎为空或格式不正确"

        return True, None

    def _validate_synthesize_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验T3.5 synthesize模式的输出。"""
        syn_path = ctx.workspace_dir / "literature" / "synthesis.md"
        if not syn_path.exists():
            return False, "缺少literature/synthesis.md"

        content = read_text_file(syn_path)

        required_sections = [
            ("方法家族", "Method Families"),
            ("共同假设", "Shared Assumptions", "Assumptions"),
            ("前沿", "Frontier", "前沿工作", "Performance-Efficiency"),
            ("趋势", "Trends", "技术趋势"),
            ("研究问题", "Research Questions", "Open Questions", "Actionable"),
        ]

        missing = []
        for section_keywords in required_sections:
            if not any(kw in content for kw in section_keywords):
                missing.append(section_keywords[0])

        if missing:
            return False, f"synthesis.md缺少以下章节: {missing}"

        if len(content) < 2000:
            return False, f"synthesis.md过短({len(content)}字符)，可能没有认真综合"

        # 检查是否有论文ID引用（至少应该引用一些论文）
        import re
        paper_refs = re.findall(r'\[[\w_:]+\]', content)
        if len(paper_refs) < 5:
            return False, f"synthesis.md中论文引用过少({len(paper_refs)}个)，应该引用更多paper_notes中的论文"

        return True, None
