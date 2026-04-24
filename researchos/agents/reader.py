"""T3/T3.5 Reader Agent - 深度阅读与综合

T3 (read模式): 逐篇精读论文，产出结构化笔记
T3.5 (synthesize模式): 综合所有笔记，产出synthesis.md

契约详见 ResearchOS_Agent_Dev_Spec.md §8
"""

from __future__ import annotations

from pathlib import Path

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec, get_agent_mode_params
from ..runtime.prompts import render_prompt
from ._common import load_project, load_jsonl, normalize_text_key, read_text_file


class ReaderAgent(Agent):
    """深度阅读Agent。read (T3)逐篇精读，synthesize (T3.5)综合。"""

    def __init__(self, mode: str | None = None):
        super().__init__(
            build_agent_spec(
                "reader",
                mode=mode,
                defaults={
                    "model_tier": "medium",
                    "tool_names": [
                        "read_file",
                        "write_file",
                        "append_file",
                        "list_files",
                        "fetch_paper_pdf",
                        "extract_paper_sections",
                        "extract_pdf_text",
                        "finish_task",
                    ],
                    "max_steps": 100,
                    "max_tokens_total": 300_000,
                    "max_wall_seconds": 1200,
                    "max_validation_retries": 3,
                    "temperature": 0.5,
                    "allowed_read_prefixes": ["", "literature/", "user_seeds/"],
                    "allowed_write_prefixes": ["literature/"],
                    "prompt_template": "reader.j2",
                },
            )
        )
        self._mode = mode

    def system_prompt(self, ctx: ExecutionContext) -> str:
        """根据mode渲染不同的system prompt。"""
        mode = ctx.mode or "read"
        project = load_project(ctx)
        context_vars = {
            "project": project,
            "verified_paper_count": 0,
            "verified_paper_preview": [],
            "existing_note_count": 0,
            "existing_note_preview": [],
            "existing_comparison_row_count": 0,
            "existing_bib_entry_count": 0,
            "remaining_paper_count": 0,
            "seed_paper_count": 0,
            "seed_priority_titles": [],
            "seed_papers_in_dedup_count": 0,
            "seed_papers_missing_from_dedup_count": 0,
            "deep_read_min": 18,
            "deep_read_target": 24,
            "deep_read_max": 30,
            "probe_pool": 45,
            "queue_count": 0,
            "queue_preview": [],
            "resume_mode": bool(ctx.extra.get("is_resume")),
            "resume_reason": str(ctx.extra.get("resume_reason", "")),
        }

        if mode == "read":
            dedup_path = ctx.workspace_dir / "literature" / "papers_dedup.jsonl"
            dedup_papers = load_jsonl(dedup_path) if dedup_path.exists() else []
            verified_path = ctx.workspace_dir / "literature" / "papers_verified.jsonl"
            verified_papers = load_jsonl(verified_path) if verified_path.exists() else []
            queue_path = ctx.workspace_dir / "literature" / "deep_read_queue.jsonl"
            queue_papers = load_jsonl(queue_path) if queue_path.exists() else []
            seed_path = ctx.workspace_dir / "user_seeds" / "seed_papers.jsonl"
            seed_papers = load_jsonl(seed_path) if seed_path.exists() else []
            mode_params = get_agent_mode_params("reader", "read")
            notes_dir = ctx.workspace_dir / "literature" / "paper_notes"
            existing_notes = sorted(path.stem for path in notes_dir.glob("*.md")) if notes_dir.exists() else []
            comparison_table_path = ctx.workspace_dir / "literature" / "comparison_table.csv"
            related_work_path = ctx.workspace_dir / "literature" / "related_work.bib"
            comparison_row_count = 0
            if comparison_table_path.exists():
                comparison_row_count = max(
                    0,
                    len(comparison_table_path.read_text(encoding="utf-8").splitlines()) - 1,
                )
            bib_entry_count = 0
            if related_work_path.exists():
                bib_entry_count = related_work_path.read_text(encoding="utf-8").count("@")
            trust_pool = verified_papers or dedup_papers
            dedup_keys: set[str] = set()
            for paper in trust_pool:
                dedup_keys.update(_paper_match_keys(paper))
            seed_titles = [str(seed.get("title", "")).strip() for seed in seed_papers if seed.get("title")]
            seed_keys: set[str] = set()
            for seed in seed_papers:
                seed_keys.update(_paper_match_keys(seed))
            seed_in_dedup_count = sum(1 for key in seed_keys if key and key in dedup_keys)
            seed_missing_count = max(0, len(seed_titles) - seed_in_dedup_count)
            context_vars["paper_count"] = len(trust_pool)
            context_vars["paper_list_preview"] = trust_pool[:5]
            context_vars["verified_paper_count"] = len(verified_papers)
            context_vars["verified_paper_preview"] = verified_papers[:5]
            context_vars["existing_note_count"] = len(existing_notes)
            context_vars["existing_note_preview"] = existing_notes[:20]
            context_vars["existing_comparison_row_count"] = comparison_row_count
            context_vars["existing_bib_entry_count"] = bib_entry_count
            context_vars["deep_read_min"] = int(mode_params.get("deep_read_min", 18))
            context_vars["deep_read_target"] = int(mode_params.get("deep_read_target", 24))
            context_vars["deep_read_max"] = int(mode_params.get("deep_read_max", 30))
            context_vars["probe_pool"] = int(mode_params.get("probe_pool", 45))
            context_vars["queue_count"] = len(queue_papers)
            context_vars["queue_preview"] = queue_papers[:10]
            queue_base_count = len(queue_papers) if queue_papers else len(dedup_papers)
            context_vars["remaining_paper_count"] = max(0, queue_base_count - len(existing_notes))
            context_vars["seed_paper_count"] = len(seed_papers)
            context_vars["seed_priority_titles"] = seed_titles[:10]
            context_vars["seed_papers_in_dedup_count"] = seed_in_dedup_count
            context_vars["seed_papers_missing_from_dedup_count"] = seed_missing_count
            context_vars["resume_mode"] = context_vars["resume_mode"] or bool(existing_notes)
        elif mode == "synthesize":
            notes_dir = ctx.workspace_dir / "literature" / "paper_notes"
            note_count = len(list(notes_dir.glob("*.md"))) if notes_dir.exists() else 0
            context_vars["note_count"] = note_count
            missing_areas_path = ctx.workspace_dir / "literature" / "missing_areas.md"
            context_vars["missing_areas"] = read_text_file(missing_areas_path, default="")
            comparison_table_path = ctx.workspace_dir / "literature" / "comparison_table.csv"
            context_vars["comparison_table_preview"] = read_text_file(
                comparison_table_path,
                default="",
            )[:1200]

        return render_prompt(self.spec.prompt_template, ctx, **context_vars)

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """根据mode返回不同的初始消息。"""
        if (ctx.mode or "read") == "read":
            notes_dir = ctx.workspace_dir / "literature" / "paper_notes"
            existing_note_count = len(list(notes_dir.glob("*.md"))) if notes_dir.exists() else 0
            if existing_note_count > 0 or ctx.extra.get("is_resume"):
                return (
                    "请继续T3深度阅读流程。先扫描literature/paper_notes/、comparison_table.csv和"
                    "related_work.bib中的现有进度，先补齐已有笔记缺失的表格/Bib条目，再只处理"
                    "尚未完成的论文。用户提供的 seed papers 必须最高优先级；如果它们已在"
                    "deep_read_queue、papers_verified 或 papers_dedup 里，必须先读；如果缺失，也要明确记录这个缺口。"
                )
            return (
                "请开始T3深度阅读流程。优先按 literature/deep_read_queue.jsonl 执行；如果该文件不存在，"
                "先回退到 literature/papers_verified.jsonl，再回退到 literature/papers_dedup.jsonl。"
                "为每篇产出paper_notes/{id}.md，同时累积comparison_table.csv和related_work.bib。"
                "用户提供的 seed papers 必须最高优先级。"
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
        completed_note_keys = {normalize_text_key(path.stem) for path in note_files}
        for note_path in note_files:
            ok, err = _validate_note_structure(note_path)
            if not ok:
                return False, err

        mode_params = get_agent_mode_params("reader", "read")
        min_required = int(mode_params.get("deep_read_min", 18))
        target_required = int(mode_params.get("deep_read_target", 24))
        queue_path = ctx.workspace_dir / "literature" / "deep_read_queue.jsonl"
        queue_records = load_jsonl(queue_path) if queue_path.exists() else []
        queue_count = len(queue_records)

        if queue_records:
            queue_keys = {
                normalize_text_key(str(item.get("normalized_id") or item.get("paper_id") or ""))
                for item in queue_records
            }
            queue_keys = {key for key in queue_keys if key}

            queued_seed_keys = {
                normalize_text_key(str(item.get("normalized_id") or item.get("paper_id") or ""))
                for item in queue_records
                if item.get("seed_priority")
            }
            queued_seed_keys = {key for key in queued_seed_keys if key}
            missing_seed_notes = sorted(queued_seed_keys - completed_note_keys)
            if missing_seed_notes:
                return False, (
                    "seed papers 尚未全部完成，缺少以下笔记: "
                    + ", ".join(missing_seed_notes[:5])
                )

            covered_queue_count = len(queue_keys & completed_note_keys)
            min_required = min(queue_count, min_required)

            if covered_queue_count < min_required:
                return False, (
                    f"deep_read_queue 仅完成 {covered_queue_count}/{queue_count} 篇，"
                    f"至少需要完成 {min_required} 篇队列论文；当前目标阅读数为 {target_required}。"
                )

        # 动态确定最小笔记数：优先围绕 deep_read_queue，其次回退到 papers_dedup
        dedup_path = ctx.workspace_dir / "literature" / "papers_dedup.jsonl"
        verified_path = ctx.workspace_dir / "literature" / "papers_verified.jsonl"
        if not queue_count and verified_path.exists():
            verified_papers = load_jsonl(verified_path)
            expected_count = len(verified_papers)
            min_required = max(3, int(expected_count * 0.8))
        elif not queue_count and dedup_path.exists():
            dedup_papers = load_jsonl(dedup_path)
            expected_count = len(dedup_papers)
            min_required = max(3, int(expected_count * 0.8))  # 兼容旧模式
        else:
            expected_count = 0

        if len(note_files) < min_required:
            if queue_count:
                return False, (
                    f"paper_notes只有{len(note_files)}篇，至少需要{min_required}篇；"
                    f"当前 deep_read_queue 有 {queue_count} 篇，目标阅读数为 {target_required}。"
                )
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


def _paper_match_keys(paper: dict[str, object]) -> set[str]:
    external_ids = paper.get("externalIds") if isinstance(paper.get("externalIds"), dict) else {}
    candidates = {
        str(paper.get("id", "")),
        str(paper.get("canonical_id", "")),
        str(paper.get("title", "")),
        str(paper.get("doi", "")),
        str(paper.get("url", "")),
        str(external_ids.get("ArXiv", "")),
        str(external_ids.get("DOI", "")),
    }
    return {normalize_text_key(candidate) for candidate in candidates if str(candidate).strip()}


def _validate_note_structure(note_path: Path) -> tuple[bool, str | None]:
    """校验单篇 note 的最小结构，防止 T3 只产出空壳摘要。"""

    content = note_path.read_text(encoding="utf-8")
    required_markers = [
        "- **Status**:",
        "## 1. Problem & Motivation",
        "## 2. Method Overview",
        "## 3. Key Results",
        "## 4. Claims vs Evidence",
        "## 5. Limitations",
        "## 6. Relevance to Our Research",
        "## 10. Key Quotes",
        "## 11. My Questions",
    ]
    for marker in required_markers:
        if marker not in content:
            return False, f"{note_path.name} 缺少必要结构: {marker}"

    # 旧格式 note 允许没有 Verification 字段；但全文类 note 至少要有证据锚点痕迹。
    status_text = content.partition("- **Status**:")[2].splitlines()[0] if "- **Status**:" in content else ""
    is_abstract_only = "ABSTRACT-ONLY" in status_text
    if not is_abstract_only and "Evidence Source" not in content and "| Claim | Evidence | Strength |" not in content:
        return False, f"{note_path.name} 缺少 evidence 锚点，无法支撑全文类结论"

    return True, None
