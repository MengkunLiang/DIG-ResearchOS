"""T3/T3.5 Reader Agent - 深度阅读与综合

T3 (read模式): 逐篇精读论文，产出结构化笔记
T3.5 (synthesize模式): 综合所有笔记，产出synthesis.md

契约详见 ResearchOS_Agent_Dev_Spec.md §8
"""

from __future__ import annotations

import math
from pathlib import Path
import re

from ..literature_identity import (
    add_identity_key_variants,
    display_record_key,
    is_paper_note_file,
    is_placeholder_text,
    paper_note_match_keys,
    paper_record_match_keys,
    record_is_covered,
)
from ..runtime.t3_notes_manifest import (
    build_t3_notes_manifest,
    format_completion_diagnostics,
    target_entries,
)
from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec, get_agent_mode_params
from ..runtime.prompts import render_prompt
from ..runtime.t2_config import get_effective_reader_read_params, load_deep_read_queue_config
from ._common import (
    cdr_schema_prompt_summary,
    ensure_seed_outline_profile,
    load_project,
    load_jsonl,
    normalize_text_key,
    prepend_resume_prefix,
    read_text_file,
)
from .guidance import load_agent_guidance


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
                        "lookup_paper_record",
                        "fetch_paper_pdf",
                        "extract_paper_sections",
                        "extract_pdf_text",
                        "save_paper_note",
                        "build_synthesis_workbench",
                        "finish_task",
                    ],
                    "max_steps": 100,
                    "max_tokens_total": 300_000,
                    "max_wall_seconds": 1200,
                    "max_validation_retries": 3,
                    "temperature": 0.5,
                    "allowed_read_prefixes": ["", "literature/", "user_seeds/", "_runtime/resume/"],
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
        ensure_seed_outline_profile(ctx.workspace_dir)
        seed_outline_profile = read_text_file(
            ctx.workspace_dir / "user_seeds" / "seed_outline_profile.json",
            default="",
        )
        seed_constraints = read_text_file(
            ctx.workspace_dir / "user_seeds" / "seed_constraints.md",
            default="",
        )
        if is_placeholder_text(seed_constraints):
            seed_constraints = ""
        queue_config = load_deep_read_queue_config(ctx.workspace_dir)
        context_vars = {
            "project": project,
            "seed_outline_profile_preview": seed_outline_profile[:6000],
            "has_seed_outline_profile": bool(seed_outline_profile.strip()),
            "seed_constraints_preview": seed_constraints[:1500],
            "has_seed_constraints": bool(seed_constraints.strip()),
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
            "deep_read_min": queue_config.deep_read_min,
            "deep_read_target": queue_config.deep_read_target,
            "deep_read_max": queue_config.deep_read_max,
            "probe_pool": queue_config.probe_pool,
            "queue_count": 0,
            "queue_preview": [],
            "resume_queue_path": "",
            "resume_queue_count": 0,
            "resume_mode": bool(ctx.extra.get("is_resume")),
            "resume_reason": str(ctx.extra.get("resume_reason", "")),
            "cdr_schema_summary": cdr_schema_prompt_summary(),
            "domain_map_exists": (ctx.workspace_dir / "literature" / "domain_map.json").exists(),
        }

        if mode == "read":
            dedup_path = ctx.workspace_dir / "literature" / "papers_dedup.jsonl"
            dedup_papers = load_jsonl(dedup_path) if dedup_path.exists() else []
            verified_path = ctx.workspace_dir / "literature" / "papers_verified.jsonl"
            verified_papers = load_jsonl(verified_path) if verified_path.exists() else []
            queue_path = ctx.workspace_dir / "literature" / "deep_read_queue.jsonl"
            queue_papers = load_jsonl(queue_path) if queue_path.exists() else []
            pending_queue_path = ctx.workspace_dir / "literature" / "deep_read_queue_pending.jsonl"
            pending_queue_papers = load_jsonl(pending_queue_path) if pending_queue_path.exists() else []
            seed_path = ctx.workspace_dir / "user_seeds" / "seed_papers.jsonl"
            seed_papers = load_jsonl(seed_path) if seed_path.exists() else []
            existing_note_paths = _iter_paper_note_paths(ctx.workspace_dir / "literature")
            existing_notes = sorted(path.stem for path in existing_note_paths)
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
            seed_in_dedup_count = sum(1 for seed in seed_papers if _paper_match_keys(seed) & dedup_keys)
            seed_missing_count = max(0, len(seed_titles) - seed_in_dedup_count)
            context_vars["paper_count"] = len(trust_pool)
            context_vars["paper_list_preview"] = trust_pool[:5]
            context_vars["verified_paper_count"] = len(verified_papers)
            context_vars["verified_paper_preview"] = verified_papers[:5]
            context_vars["existing_note_count"] = len(existing_notes)
            context_vars["existing_note_preview"] = existing_notes[:20]
            context_vars["existing_comparison_row_count"] = comparison_row_count
            context_vars["existing_bib_entry_count"] = bib_entry_count
            context_vars["deep_read_min"] = queue_config.deep_read_min
            context_vars["deep_read_target"] = queue_config.deep_read_target
            context_vars["deep_read_max"] = queue_config.deep_read_max
            context_vars["probe_pool"] = queue_config.probe_pool
            # pending queue 是恢复运行时真正还需要处理的工作清单；只要文件存在，就优先信任它。
            active_queue = pending_queue_papers if pending_queue_path.exists() else queue_papers
            context_vars["queue_count"] = len(active_queue)
            context_vars["queue_preview"] = active_queue[:10]
            context_vars["resume_queue_path"] = str(ctx.extra.get("resume_queue_path", "")).strip()
            context_vars["resume_queue_count"] = int(ctx.extra.get("resume_queue_count", len(active_queue)) or 0)
            queue_base_count = len(active_queue) if active_queue else len(dedup_papers)
            context_vars["remaining_paper_count"] = queue_base_count if active_queue else max(0, queue_base_count - len(existing_notes))
            context_vars["seed_paper_count"] = len(seed_papers)
            context_vars["seed_priority_titles"] = seed_titles[:10]
            context_vars["seed_papers_in_dedup_count"] = seed_in_dedup_count
            context_vars["seed_papers_missing_from_dedup_count"] = seed_missing_count
            context_vars["resume_mode"] = context_vars["resume_mode"] or bool(existing_notes)
        elif mode == "synthesize":
            note_files = _iter_paper_note_paths(ctx.workspace_dir / "literature")
            note_count = len(note_files)
            context_vars["note_count"] = note_count
            context_vars["note_id_preview"] = [path.stem for path in note_files[:30]]
            abstract_dir = ctx.workspace_dir / "literature" / "paper_notes_abstract"
            abstract_count = (
                len([path for path in abstract_dir.glob("*.md") if is_paper_note_file(path)])
                if abstract_dir.exists()
                else 0
            )
            context_vars["abstract_note_count"] = abstract_count
            missing_areas_path = ctx.workspace_dir / "literature" / "missing_areas.md"
            context_vars["missing_areas"] = read_text_file(missing_areas_path, default="")
            comparison_table_path = ctx.workspace_dir / "literature" / "comparison_table.csv"
            context_vars["comparison_table_preview"] = read_text_file(
                comparison_table_path,
                default="",
            )[:1200]
            metadata_triage_path = ctx.workspace_dir / "literature" / "metadata_triage.md"
            context_vars["metadata_triage_preview"] = read_text_file(
                metadata_triage_path,
                default="",
            )[:1200]
            context_vars["agent_guidance"] = load_agent_guidance("literature-synthesis")

        return render_prompt(self.spec.prompt_template, ctx, **context_vars)

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """根据mode返回不同的初始消息。"""
        if (ctx.mode or "read") == "read":
            existing_note_count = len(_iter_paper_note_paths(ctx.workspace_dir / "literature"))
            if existing_note_count > 0 or ctx.extra.get("is_resume"):
                return prepend_resume_prefix(
                    ctx,
                    (
                    "请继续T3深度阅读流程。先扫描literature/paper_notes/、comparison_table.csv和"
                    "related_work.bib中的现有进度，先补齐已有笔记缺失的表格/Bib条目，再只处理"
                    "尚未完成的论文。若存在 literature/deep_read_queue_pending.jsonl，"
                    "优先按这个剩余队列执行。用户提供的 seed papers 必须最高优先级；如果它们已在"
                    "deep_read_queue、papers_verified 或 papers_dedup 里，必须先读；如果缺失，也要明确记录这个缺口。"
                    "凡是能拿到PDF的论文，必须用extract_pdf_text覆盖到最后一页，并在Reading Coverage记录页码范围。"
                    ),
                )
            return prepend_resume_prefix(
                ctx,
                (
                "请开始T3深度阅读流程。优先按 literature/deep_read_queue.jsonl 执行；如果该文件不存在，"
                "先回退到 literature/papers_verified.jsonl，再回退到 literature/papers_dedup.jsonl。"
                "为每篇产出paper_notes/{id}.md，同时累积comparison_table.csv和related_work.bib。"
                "用户提供的 seed papers 必须最高优先级。凡是能拿到PDF的论文，必须全文读到最后一页，"
                "不能只读前几页；如果只读到部分页面，Status必须写PARTIAL-TEXT。"
                ),
            )
        return prepend_resume_prefix(
            ctx,
            (
            "请开始T3.5综合流程。综合literature/paper_notes/目录下的所有笔记，"
            "先用你的LLM能力分析方法家族、共同假设、趋势和问题，再调用 build_synthesis_workbench "
            "生成结构化证据、outline和写作指导。工具产物不是最终结论；你必须审阅后亲自写出"
            "literature/synthesis.md，包含5个必需章节：方法家族分类、共同假设、"
            "贡献空间地图、跨论文矛盾/张力、技术趋势、可操作研究问题。"
            ),
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
        literature_dir = ctx.workspace_dir / "literature"
        notes_dir = literature_dir / "paper_notes"
        bridge_notes_dir = literature_dir / "paper_notes_bridge"
        if not notes_dir.exists() and not bridge_notes_dir.exists():
            return False, "缺少 literature/paper_notes 或 literature/paper_notes_bridge 目录"

        note_files = _iter_paper_note_paths(literature_dir)
        valid_note_files: list[Path] = []
        invalid_note_files: list[tuple[Path, str]] = []
        for note_path in note_files:
            ok, err = _validate_note_structure(note_path)
            if not ok:
                invalid_note_files.append((note_path, err or "结构不完整"))
                continue
            valid_note_files.append(note_path)
        completed_note_keys: set[str] = set()
        for note_path in valid_note_files:
            completed_note_keys.update(_paper_note_match_keys(note_path))

        queue_config = load_deep_read_queue_config(ctx.workspace_dir)
        min_required = queue_config.deep_read_min
        target_required = queue_config.deep_read_target
        queue_path = ctx.workspace_dir / "literature" / "deep_read_queue.jsonl"
        queue_records = load_jsonl(queue_path) if queue_path.exists() else []
        queue_count = len(queue_records)

        if queue_records:
            manifest = build_t3_notes_manifest(
                ctx.workspace_dir,
                queue_records=queue_records,
                source_queue="literature/deep_read_queue.jsonl",
                write=True,
            )
            manifest_entries = target_entries(manifest)
            queue_count_for_completion = len(manifest_entries) or queue_count
            entry_by_rank = {
                int(entry.get("queue_rank") or -1): entry
                for entry in manifest_entries
            }
            missing_seed_notes = [
                _format_manifest_entry_for_error(entry_by_rank.get(int(item.get("queue_rank") or index)))
                or display_record_key(item)
                for index, item in enumerate(queue_records, start=1)
                if item.get("seed_priority")
                and not bool(item.get("triaged_out"))
                and not bool(entry_by_rank.get(int(item.get("queue_rank") or index), {}).get("status") == "complete")
            ]
            if missing_seed_notes:
                return False, (
                    "seed papers 尚未全部完成或对应 note 结构不合格: "
                    + ", ".join(missing_seed_notes[:5])
                    + _manifest_diagnostic_suffix(manifest_entries)
                )

            covered_queue_count = sum(1 for entry in manifest_entries if entry.get("status") == "complete")
            min_required = min(queue_count_for_completion, min_required)

            if covered_queue_count < min_required:
                return False, (
                    f"deep_read_queue 仅完成 {covered_queue_count}/{queue_count_for_completion} 篇，"
                    f"至少需要完成 {min_required} 篇队列论文；当前目标阅读数为 {target_required}。"
                    + _manifest_diagnostic_suffix(manifest_entries)
                )

            missing_protected_notes = [
                _format_manifest_entry_for_error(entry_by_rank.get(int(item.get("queue_rank") or index)))
                or display_record_key(item)
                for index, item in enumerate(queue_records, start=1)
                if _is_protected_queue_record(item)
                and not bool(item.get("triaged_out"))
                and str(item.get("target_bucket") or "") != "overflow"
                and not bool(entry_by_rank.get(int(item.get("queue_rank") or index), {}).get("status") == "complete")
            ]
            if missing_protected_notes:
                return False, (
                    "deep_read_queue 中 semantic_screen 允许的 protected-slot 论文尚未完成或结构不合格: "
                    + ", ".join(missing_protected_notes[:6])
                    + _manifest_diagnostic_suffix(manifest_entries)
                )

        # 动态确定最小笔记数：优先围绕 deep_read_queue，其次回退到 verified/dedup。
        # 默认 expected_notes_ratio=1.0；旧 workspace 没有 queue 时也不能再按 80% 静默放过。
        dedup_path = ctx.workspace_dir / "literature" / "papers_dedup.jsonl"
        verified_path = ctx.workspace_dir / "literature" / "papers_verified.jsonl"
        mode_params = get_effective_reader_read_params(ctx.workspace_dir)
        expected_notes_ratio = _expected_notes_ratio(mode_params.get("expected_notes_ratio", 1.0))
        if not queue_count and verified_path.exists():
            verified_papers = load_jsonl(verified_path)
            expected_count = len(verified_papers)
            min_required = _required_note_count(expected_count, expected_notes_ratio)
        elif not queue_count and dedup_path.exists():
            dedup_papers = load_jsonl(dedup_path)
            expected_count = len(dedup_papers)
            min_required = _required_note_count(expected_count, expected_notes_ratio)
        else:
            expected_count = 0

        if len(valid_note_files) < min_required:
            if queue_count:
                return False, (
                    f"paper_notes只有{len(valid_note_files)}篇结构合格笔记，至少需要{min_required}篇；"
                    f"当前 deep_read_queue 有 {queue_count} 篇，目标阅读数为 {target_required}。"
                    + _invalid_note_summary(invalid_note_files)
                )
            return False, (
                f"paper_notes只有{len(valid_note_files)}篇结构合格笔记，至少需要{min_required}篇"
                f"（基于{expected_count if dedup_path.exists() else '默认'}篇输入论文）"
                + _invalid_note_summary(invalid_note_files)
            )

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

        # 校验 abstract sweep notes（可选目录）
        abstract_dir = ctx.workspace_dir / "literature" / "paper_notes_abstract"
        if abstract_dir.exists():
            from ..runtime.abstract_sweep import repair_abstract_sweep_notes

            repair_abstract_sweep_notes(ctx.workspace_dir)
            for note_path in sorted(path for path in abstract_dir.glob("*.md") if is_paper_note_file(path)):
                ok, err = _validate_abstract_note_structure(note_path)
                if not ok:
                    return False, f"[abstract sweep] {err}"

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
            ("贡献空间", "Contribution-Space", "Contribution Space", "贡献空间地图"),
            ("跨论文矛盾", "Cross-Paper", "Contradictions", "张力"),
            ("趋势", "Trends", "技术趋势"),
            ("研究问题", "Research Questions", "Open Questions", "Actionable"),
        ]
        if (ctx.workspace_dir / "literature" / "domain_map.json").exists() or (
            ctx.workspace_dir / "literature" / "synthesis_workbench.json"
        ).exists():
            required_sections.append(
                ("邻接领域可迁移机制", "Adjacent Transfers", "Transferable Mechanisms", "邻接迁移")
            )

        missing = []
        for section_keywords in required_sections:
            if not any(kw in content for kw in section_keywords):
                missing.append(section_keywords[0])

        if missing:
            return False, f"synthesis.md缺少以下章节: {missing}"

        synth_params = _reader_synthesize_params()
        expected_length_min = _safe_int(synth_params.get("expected_length_min"), 2000, minimum=0)
        if len(content) < expected_length_min:
            return False, f"synthesis.md过短({len(content)}字符)，至少需要{expected_length_min}字符，可能没有认真综合"

        workbench_path = ctx.workspace_dir / "literature" / "synthesis_workbench.json"
        domain_map_exists = (ctx.workspace_dir / "literature" / "domain_map.json").exists()
        if workbench_path.exists():
            try:
                import json
                workbench = json.loads(workbench_path.read_text(encoding="utf-8"))
            except Exception as exc:
                return False, f"synthesis_workbench.json 解析失败: {exc}"
            if "adjacent_transfers" not in workbench and domain_map_exists:
                return False, "synthesis_workbench.json 缺少 adjacent_transfers"
            if "adjacent_transfers" in workbench and not isinstance(workbench.get("adjacent_transfers"), list):
                return False, "synthesis_workbench.json adjacent_transfers 必须是数组"
        elif domain_map_exists:
            return False, "缺少 literature/synthesis_workbench.json"

        note_ids = _paper_note_reference_ids(ctx.workspace_dir / "literature")
        known_refs = _known_note_refs_in_content(content, note_ids)
        if note_ids and len(known_refs) < min(5, len(note_ids)):
            return False, (
                f"synthesis.md中真实paper_notes引用过少({len(known_refs)}个)，"
                "应引用更多已读论文"
            )

        # 兼容没有 paper_notes 白名单的旧测试/workspace，仍接受规范论文ID样式。
        if note_ids:
            return True, None

        import re
        paper_refs = re.findall(
        r'\[(?:arxiv|doi|paper|10\.)[A-Za-z0-9_:.\/-]+\]',
            content,
            flags=re.IGNORECASE,
        )
        expected_citations_min = _safe_int(synth_params.get("expected_citations_min"), 5, minimum=0)
        if len(paper_refs) < expected_citations_min:
            return False, f"synthesis.md中论文引用过少({len(paper_refs)}个)，至少需要{expected_citations_min}个paper_notes引用"

        return True, None


def _add_note_key_variants(keys: set[str], value: str) -> None:
    add_identity_key_variants(keys, value)


def _paper_note_match_keys(note_path: Path) -> set[str]:
    return paper_note_match_keys(note_path)


def _invalid_note_summary(invalid_note_files: list[tuple[Path, str]]) -> str:
    if not invalid_note_files:
        return ""
    examples = ", ".join(f"{path.name}: {err}" for path, err in invalid_note_files[:3])
    return f"；另有 {len(invalid_note_files)} 个不合格/重复 note 未计入完成数: {examples}"


def _manifest_diagnostic_suffix(entries: list[dict[str, object]]) -> str:
    diagnostic = format_completion_diagnostics(entries)
    return f"；{diagnostic}" if diagnostic else ""


def _format_manifest_entry_for_error(entry: dict[str, object] | None) -> str:
    if not entry:
        return ""
    status = str(entry.get("status") or "")
    note_path = str(entry.get("note_path") or "")
    key = str(entry.get("record_display_key") or "")
    if status == "incomplete":
        err = str(entry.get("validation_error") or "结构不合格")
        return f"{key} ({note_path} 结构不合格: {err})"
    if status == "missing":
        return f"{key} (未找到 note)"
    return key


def _paper_match_keys(paper: dict[str, object]) -> set[str]:
    return paper_record_match_keys(paper)


def _is_protected_queue_record(record: dict[str, object]) -> bool:
    """Return true for LLM-screened bridge/theory queue entries that must be read."""

    if bool(record.get("protected_slot")):
        return True
    if bool(record.get("citation_hub_protected_slot")):
        return True
    protected_relations = {
        "mechanism_bridge",
        "method_transfer",
        "evaluation_or_metric_bridge",
        "baseline_or_dataset_relevance",
    }
    screen = record.get("semantic_screen")
    if not isinstance(screen, dict):
        return False
    relation = str(screen.get("relation_to_project") or record.get("relation_to_project") or "").strip()
    role = str(screen.get("role") or record.get("semantic_role") or "").strip()
    retrieval_intent = str(record.get("retrieval_intent") or "").strip()
    return (
        bool(screen.get("can_enter_deep_read"))
        and relation in protected_relations
        and (role == "theory_bridge" or retrieval_intent == "cross_domain_bridge")
    )


def _iter_paper_note_paths(literature_dir: Path) -> list[Path]:
    paths: list[Path] = []
    notes_dir = literature_dir / "paper_notes"
    if notes_dir.exists():
        paths.extend(path for path in notes_dir.glob("*.md") if is_paper_note_file(path))
    bridge_dir = literature_dir / "paper_notes_bridge"
    if bridge_dir.exists():
        paths.extend(path for path in bridge_dir.glob("**/*.md") if is_paper_note_file(path))
    return sorted(paths)


def _paper_note_reference_ids(literature_dir: Path) -> set[str]:
    ids = {path.stem for path in _iter_paper_note_paths(literature_dir)}
    normalized: set[str] = set()
    for paper_id in ids:
        normalized.add(paper_id)
        normalized.add(paper_id.replace(":", "_").replace("/", "_"))
        normalized.add(normalize_text_key(paper_id))
    return {item for item in normalized if item}


def _known_note_refs_in_content(content: str, note_ids: set[str]) -> set[str]:
    import re

    normalized_note_ids = {normalize_text_key(note_id) for note_id in note_ids if note_id}
    found: set[str] = set()
    for raw_ref in re.findall(r"\[([^\[\]]+)\]", content):
        normalized = normalize_text_key(raw_ref.replace(":", "_").replace("/", "_"))
        if normalized in normalized_note_ids:
            found.add(normalized)
    return found


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
        "## 12. Reading Coverage",
        "## 13. Mechanism Claim",
        "## 14. Design Rationale",
        "## 15. Artifact & Design Principles",
        "## 16. Data View & Evaluation Mode",
        "## 17. Contribution Type",
        "## 18. Boundary Conditions",
        "## 19. Cross-Paper Tension",
    ]
    for marker in required_markers:
        if not _has_required_marker(content, marker):
            return False, f"{note_path.name} 缺少必要结构: {marker}"

    ok, err = _validate_key_results_evidence(note_path, content)
    if not ok:
        return False, err

    # 旧格式 note 允许没有 Verification 字段；但全文类 note 至少要有证据锚点痕迹。
    status_text = content.partition("- **Status**:")[2].splitlines()[0] if "- **Status**:" in content else ""
    is_abstract_only = "ABSTRACT-ONLY" in status_text
    if is_abstract_only:
        for marker in ("## A. 核心做法/视角", "## B. 桥接点"):
            if not _has_required_marker(content, marker):
                return False, f"{note_path.name} ABSTRACT-ONLY note 缺少必要轻字段: {marker}"
    if not is_abstract_only and "Evidence Source" not in content and "| Claim | Evidence | Strength |" not in content:
        return False, f"{note_path.name} 缺少 evidence 锚点，无法支撑全文类结论"

    ok, err = _validate_reading_coverage(note_path, content, status_text)
    if not ok:
        return False, err

    ok, err = _validate_mechanism_claim(note_path, content)
    if not ok:
        return False, err

    ok, err = _validate_cdr_note_fields(note_path, content, abstract_only=False)
    if not ok:
        return False, err

    return True, None


def _expected_notes_ratio(raw: object) -> float:
    try:
        ratio = float(raw)
    except (TypeError, ValueError):
        return 1.0
    if ratio <= 0:
        return 1.0
    return min(1.0, ratio)


def _required_note_count(expected_count: int, ratio: float) -> int:
    if expected_count <= 0:
        return 0
    return min(expected_count, max(1, int(math.ceil(expected_count * ratio))))


def _validate_reading_coverage(
    note_path: Path,
    content: str,
    status_text: str,
) -> tuple[bool, str | None]:
    """校验 T3 note 是否记录了 PDF 阅读覆盖范围。"""

    import re

    section_match = re.search(
        r"(?ms)^## 12\. Reading Coverage\s*(?P<section>.*?)(?=^##\s+\d+\.|\Z)",
        content,
    )
    if section_match is None:
        return False, f"{note_path.name} 缺少 Reading Coverage 章节"

    section = section_match.group("section")
    required_fields = [
        "- **PDF source**:",
        "- **Pages read**:",
        "- **Extraction calls**:",
        "- **Truncation**:",
        "- **Status rationale**:",
    ]
    for field in required_fields:
        if field not in section:
            return False, f"{note_path.name} Reading Coverage 缺少字段: {field}"

    pages_line = _extract_markdown_field(section, "Pages read")
    truncation_line = _extract_markdown_field(section, "Truncation")
    if not pages_line:
        return False, f"{note_path.name} Reading Coverage 的 Pages read 不能为空"
    if not truncation_line:
        return False, f"{note_path.name} Reading Coverage 的 Truncation 不能为空"

    if "FULL-TEXT" in status_text:
        page_coverage_complete = _pages_read_covers_full_pdf(pages_line)
        if not page_coverage_complete:
            return False, (
                f"{note_path.name} 标记为 FULL-TEXT，但 Pages read 未说明完整页码覆盖，"
                "应类似 `1-12 / 12` 或 `1-4, 5-8, 9-12 / 12`"
            )

        if not _truncation_indicates_no_final_truncation(truncation_line):
            return False, (
                f"{note_path.name} 标记为 FULL-TEXT，但 Truncation 未明确为 none/无: {truncation_line}"
            )

    return True, None


def _validate_mechanism_claim(note_path: Path, content: str) -> tuple[bool, str | None]:
    """校验 §13 Mechanism Claim 存在且三个 bullet 非空。"""

    import re

    section_match = re.search(
        r"(?ms)^## 13\. Mechanism Claim\s*(?P<section>.*?)(?=^##\s+\d+\.|\Z)",
        content,
    )
    if section_match is None:
        return False, f"{note_path.name} 缺少 ## 13. Mechanism Claim 章节"

    section = section_match.group("section")
    required_fields = [
        "- **Stated mechanism**:",
        "- **Evidence type**:",
        "- **Supporting artifact**:",
    ]
    for field in required_fields:
        if field not in section:
            return False, f"{note_path.name} Mechanism Claim 缺少字段: {field}"

    # 检查每个字段的 value 非空
    for field_name in ("Stated mechanism", "Evidence type", "Supporting artifact"):
        value = _extract_markdown_field(section, field_name)
        if not value:
            return False, f"{note_path.name} Mechanism Claim 的 {field_name} 不能为空"

    return True, None


def _validate_abstract_note_structure(note_path: Path) -> tuple[bool, str | None]:
    """校验 abstract sweep note 的最小结构（精简版，无 §12）。"""

    content = note_path.read_text(encoding="utf-8")
    required_markers = [
        "- **Status**:",
        "## 1. Problem & Motivation",
        "## 2. Method Summary",
        "## A. 核心做法/视角",
        "## B. 桥接点",
        "## 3. Key Claimed Results",
        "## 13. Mechanism Claim",
        "## Source",
    ]
    for marker in required_markers:
        if not _has_required_marker(content, marker):
            return False, f"{note_path.name} 缺少必要结构: {marker}"

    # Status 必须是 ABSTRACT-ONLY
    if "ABSTRACT-ONLY" not in content:
        return False, f"{note_path.name} Status 必须标记为 [ABSTRACT-ONLY]"

    # §13 Evidence type 必须明确标为 abstract-only hint，避免把摘要片段
    # 伪装成已验证机制证据。兼容旧的 claimed_untested abstract notes。
    ok, err = _validate_mechanism_claim(note_path, content)
    if not ok:
        return False, err

    evidence_match = re.search(r"- \*\*Evidence type\*\*:\s*(.+)", content)
    if evidence_match:
        evidence_val = evidence_match.group(1).strip().lower()
        if "abstract_claim_hint" not in evidence_val and "claimed_untested" not in evidence_val:
            return False, f"{note_path.name} abstract note 的 Evidence type 必须为 abstract_claim_hint"

    return True, None


def _has_required_marker(content: str, marker: str) -> bool:
    """Check required markers without treating deeper headings as valid."""

    if marker.startswith("## "):
        return re.search(rf"(?m)^{re.escape(marker)}\s*$", content) is not None
    return marker in content


def _validate_cdr_note_fields(
    note_path: Path,
    content: str,
    *,
    abstract_only: bool,
) -> tuple[bool, str | None]:
    """Validate CDR note extensions without judging domain correctness."""

    required_sections = [
        ("14. Design Rationale", ["Rationale", "Rationale evidence", "Rationale weakness"]),
        ("15. Artifact & Design Principles", ["Artifact type", "Artifact description", "Design principles"]),
        ("16. Data View & Evaluation Mode", ["Data view", "Evaluation mode", "Validity concern"]),
        ("17. Contribution Type", ["Contribution type", "Contribution character", "Why not routine"]),
        ("18. Boundary Conditions", ["Works when", "May fail when", "Untested boundary"]),
        ("19. Cross-Paper Tension", ["Tension", "Competing rationale", "Idea fuel"]),
    ]
    for heading, fields in required_sections:
        section_match = re.search(
            rf"(?ms)^##\s+{re.escape(heading)}\s*(?P<section>.*?)(?=^##\s+\d+\.|\Z)",
            content,
        )
        if section_match is None:
            if abstract_only:
                continue
            return False, f"{note_path.name} 缺少 ## {heading} 章节"
        section = section_match.group("section")
        for field in fields:
            marker = f"- **{field}**:"
            if marker not in section:
                if abstract_only:
                    continue
                return False, f"{note_path.name} ## {heading} 缺少字段: {marker}"
            value = _extract_markdown_field(section, field)
            if not value and not abstract_only:
                return False, f"{note_path.name} ## {heading} 的 {field} 不能为空"

    contribution_section = re.search(
        r"(?ms)^##\s+17\. Contribution Type\s*(?P<section>.*?)(?=^##\s+\d+\.|\Z)",
        content,
    )
    contribution_value = _extract_markdown_field(
        contribution_section.group("section") if contribution_section else "",
        "Contribution type",
    )
    ok, err = _validate_contribution_type_field(note_path, contribution_value)
    if not ok:
        return False, err

    if not abstract_only:
        tension_section = re.search(
            r"(?ms)^##\s+19\. Cross-Paper Tension\s*(?P<section>.*?)(?=^##\s+\d+\.|\Z)",
            content,
        )
        tension = _extract_markdown_field(
            tension_section.group("section") if tension_section else "",
            "Tension",
        )
        if not tension:
            return False, f"{note_path.name} Cross-Paper Tension 不能为空"
        if tension.strip().lower() in {"none", "n/a", "无", "暂无", "no tension"}:
            reason = _extract_markdown_field(
                tension_section.group("section") if tension_section else "",
                "Idea fuel",
            ) or _extract_markdown_field(
                tension_section.group("section") if tension_section else "",
                "Competing rationale",
            )
            if not reason:
                return False, f"{note_path.name} Cross-Paper Tension 为 none 时必须说明无张力原因"

    return True, None


def _validate_contribution_type_field(note_path: Path, value: str) -> tuple[bool, str | None]:
    """Validate contribution-type signal without hard-coding away LLM judgment.

    The prompt asks for invention/improvement/exaptation/routine, but real notes
    often add a short parenthetical explanation. That is useful knowledge for
    T3.5 and should not make the note invalid. We only require one recognizable
    top-level label and reject empty or placeholder values.
    """

    normalized = value.strip().lower()
    if not normalized:
        return False, f"{note_path.name} Contribution type 不能为空"
    placeholders = {"unknown", "n/a", "na", "none", "无", "暂无", "unclear", "not sure"}
    if normalized in placeholders:
        return False, f"{note_path.name} Contribution type 不能是占位值: {value}"
    if not re.search(r"\b(invention|improvement|exaptation|routine)\b", normalized):
        return False, (
            f"{note_path.name} Contribution type 需要包含 invention/improvement/"
            "exaptation/routine 之一，并可附加解释"
        )
    return True, None


def _pages_read_covers_full_pdf(pages_line: str) -> bool:
    """Return True when a Pages read line covers page 1 through the final page.

    The validator accepts both compact ranges (`1-12 / 12`) and chunked rereads
    (`1-4, 5-8, 9-12 / 12`) because long PDFs often need multiple tool calls.
    """

    import re

    normalized = pages_line.strip()
    lowered = normalized.lower()
    negative_tokens = ("partial", "incomplete", "部分", "未完成", "未覆盖", "不完整")
    if any(token in lowered or token in normalized for token in negative_tokens):
        return False

    total_page = _extract_total_page_count(normalized)
    if total_page is None or total_page <= 0:
        return False

    ranges = _extract_page_ranges_before_total(normalized)
    if not ranges:
        return False
    return _ranges_cover_pages(ranges, first_page=1, last_page=total_page)


def _extract_total_page_count(pages_line: str) -> int | None:
    import re

    patterns = [
        r"/\s*(\d+)\b",
        r"\bof\s+(\d+)\b",
        r"\btotal(?:_pages)?\s*[:=]\s*(\d+)\b",
        r"共\s*(\d+)\s*页",
        r"总(?:页数)?\s*[:：]?\s*(\d+)\s*页?",
    ]
    for pattern in patterns:
        match = re.search(pattern, pages_line, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _extract_page_ranges_before_total(pages_line: str) -> list[tuple[int, int]]:
    import re

    prefix = re.split(r"/|\bof\b|\btotal(?:_pages)?\b|共|总", pages_line, maxsplit=1, flags=re.IGNORECASE)[0]
    ranges: list[tuple[int, int]] = []

    def _range_replacer(match: re.Match[str]) -> str:
        start = int(match.group(1))
        end = int(match.group(2))
        ranges.append((min(start, end), max(start, end)))
        return " "

    without_ranges = re.sub(r"\b(\d+)\s*[-–—]\s*(\d+)\b", _range_replacer, prefix)
    for value in re.findall(r"(?<![\w.])(\d+)(?![\w.])", without_ranges):
        page = int(value)
        ranges.append((page, page))
    return ranges


def _ranges_cover_pages(
    ranges: list[tuple[int, int]],
    *,
    first_page: int,
    last_page: int,
) -> bool:
    if not ranges:
        return False
    cursor = first_page
    for start, end in sorted(ranges):
        if end < cursor:
            continue
        if start > cursor:
            return False
        cursor = max(cursor, end + 1)
        if cursor > last_page:
            return True
    return cursor > last_page


def _truncation_indicates_no_final_truncation(truncation_line: str) -> bool:
    """Accept explicit no-truncation and resolved chunked reread descriptions."""

    import re

    line = truncation_line.strip()
    lowered = line.lower()
    compact_line = re.sub(r"\s+", "", line)
    unresolved_patterns = (
        "still truncated",
        "still partial",
        "incomplete",
        "not reread",
        "not re-read",
        "remaining truncation",
        "仍被截断",
        "仍然截断",
        "未完成",
        "未覆盖",
        "不完整",
        "缺失",
    )
    if any(pattern in lowered or pattern in line for pattern in unresolved_patterns):
        return False

    direct_no_truncation = (
        bool(
            re.search(
                r"\bnone\b|\bno\s+(?:preview\s+)?truncation\b|\bnot\s+truncated\b|"
                r"\bwithout\s+truncation\b|\buntruncated\b",
                lowered,
            )
        )
        or compact_line in {"无", "没有", "无截断", "未截断", "没有截断"}
        or any(token in line for token in ("无截断", "未截断", "没有截断"))
    )
    if direct_no_truncation:
        return True

    has_historical_truncation = "truncated" in lowered or "截断" in line
    has_reread_resolution = (
        any(token in lowered for token in ("final", "resolved", "after", "reread", "re-read", "chunked", "covered"))
        or any(token in line for token in ("最终", "最后", "重读", "分块", "覆盖", "通过"))
    )
    has_complete_coverage = (
        any(token in lowered for token in ("all pages", "full", "complete", "covered all"))
        or any(token in line for token in ("全部", "完整", "全篇", "所有页面"))
    )
    return has_historical_truncation and has_reread_resolution and has_complete_coverage


def _extract_markdown_field(section: str, field_name: str) -> str:
    """从 `- **Field**: value` 形式的 markdown 字段中取 value。"""

    import re

    pattern = re.compile(rf"(?m)^-\s+\*\*{re.escape(field_name)}\*\*:\s*(?P<value>.*)$")
    match = pattern.search(section)
    return match.group("value").strip() if match else ""


def _validate_key_results_evidence(note_path: Path, content: str) -> tuple[bool, str | None]:
    """要求 Key Results 中的数字结果在同一行带 `[Evidence: ...]`。"""

    import re

    section_match = re.search(
        r"(?ms)^## 3\. Key Results\s*(?P<section>.*?)^## 4\. Claims vs Evidence",
        content,
    )
    if section_match is None:
        # 结构缺失会由上层 marker 校验兜底；这里不重复报错。
        return True, None

    evidence_marker = re.compile(r"\[\s*Evidence\s*:\s*[^\]]+\]")
    # 识别独立数字、百分比、小数、倍数等，避开 AI2-THOR / 3D 这类标识符。
    numeric_value = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?(?:%|x|×)?(?![A-Za-z])")
    model_version = re.compile(
        r"\b(?:GPT|Claude|Sonnet|Haiku|Opus|Qwen|Llama|Mistral|Gemini|"
        r"DeepSeek|GLM|Mixtral|Phi|BERT|RoBERTa|T5)"
        r"(?:[-_\s]?[A-Za-z]+){0,2}[-_\s]*\d+(?:\.\d+)?(?:[-_.]?\w+)*",
        re.IGNORECASE,
    )

    in_fence = False
    section_start_line = content[: section_match.start("section")].count("\n") + 1
    for offset, raw_line in enumerate(section_match.group("section").splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        if stripped.startswith("|---") or stripped.startswith("|==="):
            continue
        if stripped.endswith((':', '：')):
            # Treat metric group headings such as
            # "- **Efficiency (throughput with Llama-3.1-8b)**:" as labels,
            # not numeric results that need their own evidence marker.
            continue

        line_without_list_marker = re.sub(r"^\s*(?:[-*+]\s*)?(?:\d+[\.)]\s*)?", "", raw_line)
        line_for_numeric_check = model_version.sub("", line_without_list_marker)
        if not numeric_value.search(line_for_numeric_check):
            continue
        if evidence_marker.search(raw_line):
            continue

        line_no = section_start_line + offset - 1
        preview = stripped[:120]
        return (
            False,
            f"{note_path.name} 的 Key Results 第 {line_no} 行含数字但缺少 `[Evidence: ...]`: {preview}",
        )

    return True, None


def _reader_synthesize_params() -> dict:
    try:
        return get_agent_mode_params("reader", "synthesize")
    except Exception:
        return {}


def _safe_int(value, default: int, *, minimum: int | None = None) -> int:
    try:
        result = int(float(str(value).strip())) if value not in (None, "", [], {}) else int(default)
    except (TypeError, ValueError):
        result = int(default)
    if minimum is not None:
        result = max(minimum, result)
    return result
