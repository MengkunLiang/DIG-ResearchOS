"""T8 Writer Agent — 论文写作

支持多个phase: outline/draft/self_check/revise/final
输出: drafts/outline.md, drafts/paper.tex, drafts/self_check.md
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.prompts import render_prompt
from ..tools.manuscript import (
    CORE_SECTIONS,
    SECTION_TITLES,
    SECTION_WRITING_SEQUENCE,
    _internal_label_leakages,
    _placeholder_hits,
    _extract_latex_cites,
    craft_audit_input_fingerprints,
    has_formal_citation,
    normalize_section_id,
)
from .guidance import load_agent_guidance
from ._common import load_project, prepend_resume_prefix, read_text_file


class WriterAgent(Agent):
    """论文写作Agent，支持大纲生成、初稿、自查和修订。"""

    def __init__(self, mode: str | None = None):
        super().__init__(
            build_agent_spec(
                "writer",
                mode=mode,
                defaults={
                    "model_tier": "heavy",
                    "tool_names": [
                        "ask_human",
                        "read_file",
                        "write_file",
                        "list_files",
                        "build_manuscript_resource_index",
                        "plan_manuscript_sections",
                        "plan_manuscript_evidence",
                        "build_manuscript_registries",
                        "build_alignment_matrix",
                        "initialize_manuscript_state",
                        "update_manuscript_section_state",
                        "assemble_manuscript",
                        "audit_manuscript_claims",
                        "audit_writing_craft",
                        "audit_paper_claims",
                        "build_manuscript_revision_patches",
                        "finish_task",
                    ],
                    "max_steps": 100,
                    "max_tokens_total": 400_000,
                    "max_wall_seconds": 1200,
                    "max_validation_retries": 3,
                    "temperature": 0.7,
                    "allowed_read_prefixes": [
                        "",
                        "drafts/",
                        "literature/",
                        "experiments/",
                        "ideation/",
                        "novelty/",
                        "evaluation/",
                        "pilot/",
                    ],
                    "allowed_write_prefixes": ["drafts/"],
                    "prompt_template": "writer.j2",
                },
            )
        )
        self._mode = mode

    def _phase(self, ctx: ExecutionContext) -> str:
        if ctx.mode:
            return ctx.mode
        if ctx.extra:
            phase = ctx.extra.get("phase")
            if isinstance(phase, str) and phase:
                return phase
        if self._mode:
            return self._mode
        return "draft"

    def _section_id(self, ctx: ExecutionContext) -> str | None:
        raw = None
        if ctx.extra:
            raw = ctx.extra.get("section_id") or ctx.extra.get("section")
        if raw is None:
            return None
        return normalize_section_id(str(raw))

    def _previous_section_tail(self, ctx: ExecutionContext) -> str:
        section_id = self._section_id(ctx)
        order = list(SECTION_WRITING_SEQUENCE)
        if not section_id or section_id not in order:
            return ""
        idx = order.index(section_id)
        if idx == 0:
            return ""
        previous_id = order[idx - 1]
        text = read_text_file(
            ctx.workspace_dir / "drafts" / "sections" / f"{previous_id}.tex",
            default="",
        )
        return text[-1200:]

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
        novelty_report = read_text_file(ws / "novelty" / "novelty_report.md", default="")
        novelty_audit = read_text_file(ws / "ideation" / "novelty_audit.md", default="")
        ablations = read_text_file(ws / "experiments" / "ablations.csv", default="")
        resource_index = read_text_file(ws / "drafts" / "manuscript_resource_index.json", default="")
        section_plan = read_text_file(ws / "drafts" / "section_plan.json", default="")
        evidence_plan = read_text_file(ws / "drafts" / "evidence_plan.json", default="")
        figure_table_plan = read_text_file(ws / "drafts" / "figure_table_plan.json", default="")
        cdr_claim_ledger = read_text_file(ws / "drafts" / "cdr_claim_ledger.json", default="")
        claim_ledger = read_text_file(ws / "drafts" / "claim_ledger.json", default="")
        figure_registry = read_text_file(ws / "drafts" / "figure_registry.json", default="")
        manuscript_audit = read_text_file(ws / "drafts" / "manuscript_audit.md", default="")
        craft_audit = read_text_file(ws / "drafts" / "craft_audit.md", default="")
        paper_claim_audit = read_text_file(ws / "drafts" / "paper_claim_audit.md", default="")
        experiment_evidence_pack = read_text_file(ws / "drafts" / "experiment_evidence_pack.json", default="")
        result_to_claim = read_text_file(ws / "drafts" / "result_to_claim.json", default="")
        paper_state = read_text_file(ws / "drafts" / "paper_state.json", default="")
        alignment_matrix = read_text_file(ws / "drafts" / "alignment_matrix.json", default="")
        writing_style_text = read_text_file(ws / "drafts" / "writing_style.json", default="")
        writing_style = _parse_writing_style(writing_style_text)

        # 根据phase选择不同的prompt策略
        phase = self._phase(ctx)
        outline = read_text_file(ws / "drafts" / "outline.md", default="")
        round_num = ctx.extra.get("round", 1) if ctx.extra else 1
        revision_patches = read_text_file(
            ws / "drafts" / "patches" / f"round_{round_num}_patches.json",
            default="",
        )
        section_id = self._section_id(ctx)
        section_outline = (
            read_text_file(ws / "drafts" / "section_outlines" / f"{section_id}.md", default="")
            if section_id
            else ""
        )
        section_draft = (
            read_text_file(ws / "drafts" / "sections" / f"{section_id}.tex", default="")
            if section_id
            else ""
        )
        previous_section_tail = self._previous_section_tail(ctx)
        review_report = read_text_file(
            ws / "drafts" / "review_rounds" / f"round_{round_num}.md",
            default="",
        )
        user_corrections = read_text_file(ws / "drafts" / "user_corrections.md", default="")

        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            results_summary=results_summary,
            synthesis_preview=synthesis[:6000],
            related_work_preview=related_work[:4000],
            hypotheses_preview=hypotheses[:3000],
            novelty_report_preview=novelty_report[:2000],
            novelty_audit_preview=novelty_audit[:2000],
            ablations_preview=ablations[:2000],
            resource_index_preview=resource_index[:5000],
            section_plan_preview=section_plan[:5000],
            evidence_plan_preview=evidence_plan[:5000],
            figure_table_plan_preview=figure_table_plan[:5000],
            cdr_claim_ledger_preview=cdr_claim_ledger[:5000],
            claim_ledger_preview=claim_ledger[:4000],
            figure_registry_preview=figure_registry[:4000],
            manuscript_audit_preview=manuscript_audit[:3000],
            craft_audit_preview=craft_audit[:3000],
            paper_claim_audit_preview=paper_claim_audit[:3000],
            experiment_evidence_pack_preview=experiment_evidence_pack[:4000],
            result_to_claim_preview=result_to_claim[:4000],
            paper_state_preview=paper_state[:5000],
            alignment_matrix_preview=alignment_matrix[:5000],
            writing_style=writing_style,
            suggested_style=_suggest_venue_style(project.get("target_venue", "neurips")),
            section_id=section_id,
            section_title=SECTION_TITLES.get(section_id or "", (section_id or "").replace("_", " ").title()),
            section_outline_preview=section_outline[:5000],
            section_draft_preview=section_draft[:3000],
            previous_section_tail=previous_section_tail[:1200],
            outline_preview=outline[:3000],
            review_report_preview=review_report[:3000],
            revision_patch_preview=revision_patches[:5000],
            user_corrections_preview=user_corrections[:2000],
            phase=phase,
            round_num=round_num,
            target_venue=project.get("target_venue", "neurips"),
            agent_guidance=load_agent_guidance("manuscript-writing"),
            temperature=self.spec.temperature,
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        """根据phase生成初始用户消息。"""
        phase = self._phase(ctx)

        if phase == "resource_index":
            return prepend_resume_prefix(
                ctx,
                (
                "请执行 T8 Writer Phase 0: 构建写作资源索引。\n\n"
                "调用 build_manuscript_resource_index 生成 drafts/manuscript_resource_index.json，"
                "调用 plan_manuscript_sections 生成 drafts/section_plan.json，"
                "调用 plan_manuscript_evidence 生成 drafts/evidence_plan.json 和 drafts/figure_table_plan.json，"
                "再调用 build_manuscript_registries 生成 drafts/cdr_claim_ledger.json、"
                "drafts/claim_ledger.json 和 drafts/figure_registry.json，最后调用 build_alignment_matrix "
                "生成 drafts/alignment_matrix.json。"
                ),
            )
        if phase == "style_gate":
            return prepend_resume_prefix(
                ctx,
                (
                "请执行 T8 Writer Phase -1: 写作风格确认。\n\n"
                "根据 target_venue 和系统建议，调用 ask_human 让用户在 is / ccf_a / both 中选择。"
                "如果当前运行环境不支持人工输入，runtime 会暂停等待 resume；不要写入伪造默认选择。"
                "若 drafts/writing_style.json 已存在且 venue_style 合法，可直接 finish_task。"
                "否则必须在收到真实选择后写 drafts/writing_style.json，然后 finish_task。"
                ),
            )
        if phase == "section_plan":
            return prepend_resume_prefix(
                ctx,
                (
                "请执行 T8 Writer Phase 1.5: 初始化逐章节写作状态。\n\n"
                "调用 initialize_manuscript_state 读取 drafts/outline.md、resource index、"
                "section/evidence/figure plans 和 drafts/alignment_matrix.json，生成 drafts/paper_state.json 和 "
                "drafts/section_outlines/*.md。不要写任何章节正文。"
                ),
            )
        if phase == "outline":
            return prepend_resume_prefix(
                ctx,
                (
                "请执行 T8 Writer Phase 1: 生成论文大纲。\n\n"
                "基于 drafts/manuscript_resource_index.json、drafts/section_plan.json、"
                "drafts/alignment_matrix.json、实验结果和文献综述，生成 drafts/outline.md。"
                "大纲应包含：标题候选、Abstract要点、Introduction结构、"
                "Related Work分类、Method结构、Experiments结构、Conclusion要点。"
                ),
            )
        elif phase == "section_draft":
            section_id = self._section_id(ctx) or "unknown"
            section_title = SECTION_TITLES.get(section_id, section_id.replace("_", " ").title())
            return prepend_resume_prefix(
                ctx,
                (
                f"请执行 T8 Writer Phase 2: 单章节写作 `{section_id}` ({section_title})。\n\n"
                f"只写 drafts/sections/{section_id}.tex 这一章。读取 drafts/paper_state.json、"
                f"drafts/section_outlines/{section_id}.md 和本章必需证据文件。"
                "不要写其它章节，不要生成 drafts/paper.tex，不要包含整篇 LaTeX wrapper。"
                "写完后调用 update_manuscript_section_state 记录该章节 status=written，然后 finish_task。"
                ),
            )
        elif phase == "section_drafts":
            return prepend_resume_prefix(
                ctx,
                (
                "旧入口 section_drafts 已废弃，不能在一次 Writer 调用中写多个章节。\n\n"
                "请不要生成 drafts/sections/*.tex 或 drafts/paper.tex。新版流程必须先运行 "
                "T8-SECTION-PLAN，再依次运行 T8-SEC-METHOD、T8-SEC-EXPERIMENTS、"
                "T8-SEC-RELATED、T8-SEC-ANALYSIS、T8-SEC-INTRO、"
                "T8-SEC-CONCLUSION、T8-SEC-ABSTRACT。若 paper_state.json 已存在，"
                "只需调用 finish_task 结束兼容检查。"
                ),
            )
        elif phase == "draft":
            return prepend_resume_prefix(
                ctx,
                (
                "请执行 T8 Writer Phase 3: 拼装并融合论文初稿。\n\n"
                "先调用 assemble_manuscript 将 drafts/sections/ 下的章节草稿拼装为 drafts/paper.tex，"
                "再做一致性 spot-check；如发现需要修改正文，请回改对应 drafts/sections/<section>.tex "
                "并重新 assemble，而不是一次性重写整篇 paper.tex。最后调用 audit_manuscript_claims "
                "生成 drafts/manuscript_audit.md，调用 audit_writing_craft 生成 drafts/craft_audit.md，"
                "如果 drafts/experiment_evidence_pack.json 存在，还必须调用 audit_paper_claims 生成 "
                "drafts/paper_claim_audit.md/json。"
                "**重要**: 所有实验数字必须来自 paper_state.shared_facts.result_metrics、"
                "drafts/experiment_evidence_pack.json 或 experiments/results_summary.json，"
                "所有引用必须存在于 literature/related_work.bib。"
                ),
            )
        elif phase == "self_check":
            return prepend_resume_prefix(
                ctx,
                (
                "请执行 T8 Writer Phase 4: 论文自查。\n\n"
                "读取 drafts/paper.tex，生成 drafts/self_check.md。"
                "检查内容完整性、数字准确性、引用完整性、格式规范，并参考 drafts/manuscript_audit.md "
                "和 drafts/craft_audit.md。"
                ),
            )
        elif phase == "revise":
            round_num = ctx.extra.get("round", 1) if ctx.extra else 1
            return prepend_resume_prefix(
                ctx,
                (
                f"请执行 T8 Writer Phase 5: 修订论文（第{round_num}轮）。\n\n"
                f"先调用 build_manuscript_revision_patches(round_num={round_num}) 生成 "
                f"drafts/patches/round_{round_num}_patches.json，再按 patch 定位修订对应 "
                "drafts/sections/<section>.tex。修订后调用 update_manuscript_section_state(status=\"revised\")，"
                "再 assemble_manuscript 重新拼装 drafts/paper.tex，并调用 audit_manuscript_claims 刷新 "
                f"drafts/manuscript_audit.md；如果 drafts/experiment_evidence_pack.json 存在，还必须调用 "
                f"audit_paper_claims 刷新 drafts/paper_claim_audit.md/json。最后写 "
                f"drafts/revision_response_round_{round_num}.md。"
                ),
            )
        elif phase == "paper_claim_audit":
            return prepend_resume_prefix(
                ctx,
                (
                "请执行 T8-PAPER-CLAIM-AUDIT：只做进入 T9 前的最终 claim/evidence 审计。\n\n"
                "调用 audit_paper_claims(paper_path=\"drafts/paper.tex\", "
                "evidence_pack_path=\"drafts/experiment_evidence_pack.json\", "
                "result_to_claim_path=\"drafts/result_to_claim.json\", "
                "output_path=\"drafts/paper_claim_audit.md\")，生成 drafts/paper_claim_audit.md/json。"
                "不要重写论文正文；若审计 FAIL，状态机会回到 T8-REVISE-2。"
                ),
            )
        elif phase == "final":
            return prepend_resume_prefix(
                ctx,
                (
                "请执行 T8 Writer Phase 6: 生成最终版。\n\n"
                "根据 drafts/user_corrections.md 的用户标注，"
                "生成最终版 drafts/paper.tex。"
                ),
            )
        else:
            return prepend_resume_prefix(ctx, f"请执行 T8 Writer（phase={phase}）。")

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        """校验输出文件。"""
        ws = ctx.workspace_dir
        phase = self._phase(ctx)

        if phase == "style_gate":
            style_path = ws / "drafts" / "writing_style.json"
            style, err = _load_json_file(style_path)
            if err:
                return False, err
            if style.get("venue_style") not in {"is", "ccf_a", "both"}:
                return False, "writing_style.json venue_style 必须是 is/ccf_a/both"
            interaction_id = str(style.get("human_interaction_id") or "").strip()
            if not interaction_id:
                return False, "writing_style.json 必须包含 ask_human 返回的 human_interaction_id"
            if not _human_interaction_exists(ws, interaction_id):
                return False, "writing_style.json human_interaction_id 未在 _runtime/human_interactions.jsonl 中找到"
            return True, None

        if phase == "resource_index":
            ok, err = _validate_resource_index_artifacts(ws)
            if not ok:
                return False, err
            return True, None

        if phase == "section_plan":
            ok, err = _validate_paper_state(ws)
            if not ok:
                return False, err
            for section_id in [
                "methodology",
                "experiments",
                "related_work",
                "analysis",
                "introduction",
                "conclusion",
                "abstract",
            ]:
                outline_path = ws / "drafts" / "section_outlines" / f"{section_id}.md"
                if not outline_path.exists() or len(read_text_file(outline_path, default="").strip()) < 120:
                    return False, f"缺少或过短的章节大纲: drafts/section_outlines/{section_id}.md"
            return True, None

        if phase == "outline":
            outline = read_text_file(ws / "drafts" / "outline.md", default="")
            if len(outline) < 100:
                return False, f"outline.md 过短({len(outline)}字符)"
            if "## " not in outline:
                return False, "outline.md 必须包含章节结构（## 标题）"
            for required in ("Introduction", "Related Work", "Method", "Experiments"):
                if required.lower() not in outline.lower():
                    return False, f"outline.md 缺少必要章节: {required}"
            ok, err = _validate_alignment_matrix(ws)
            if not ok:
                return False, err
            return True, None

        elif phase == "section_draft":
            section_id = self._section_id(ctx)
            if not section_id:
                return False, "section_draft phase 缺少 extra.section_id"
            return _validate_single_section(ws, section_id)

        elif phase == "section_drafts":
            return _validate_paper_state(ws)

        elif phase == "paper_claim_audit":
            return _validate_paper_claim_audit_if_needed(ws)

        elif phase in ("draft", "revise", "final"):
            if phase in {"draft", "revise"}:
                ok, err = _validate_paper_state(ws)
                if not ok:
                    return False, err
                for section_id in SECTION_WRITING_SEQUENCE:
                    ok, err = _validate_single_section(ws, section_id)
                    if not ok:
                        return False, err
                if phase == "revise":
                    round_num = ctx.extra.get("round", 1) if ctx.extra else 1
                    ok, err = _validate_revision_artifacts(ws, int(round_num))
                    if not ok:
                        return False, err

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

            for required in ("Introduction", "Related Work", "Method", "Experiments"):
                if required not in paper:
                    return False, f"paper.tex 缺少必要章节: {required}"

            # 验证引用（如果存在related_work.bib）
            bib_path = ws / "literature" / "related_work.bib"
            if bib_path.exists():
                bib_text = bib_path.read_text(encoding="utf-8", errors="replace")
                bib_keys = set(re.findall(r"@\w+\{([^,\s]+)", bib_text))
                cited = _extract_latex_cites(paper)
                missing_cites = cited - bib_keys
                if missing_cites:
                    return False, f"paper.tex 引用了不存在的BibTeX key: {missing_cites}"

            audit_path = ws / "drafts" / "manuscript_audit.md"
            if phase in {"draft", "revise"} and not audit_path.exists():
                return False, f"{phase} phase 必须生成 drafts/manuscript_audit.md"
            craft_audit_path = ws / "drafts" / "craft_audit.md"
            if phase in {"draft", "revise"} and not craft_audit_path.exists():
                return False, f"{phase} phase 必须生成 drafts/craft_audit.md"
            craft_json_path = ws / "drafts" / "craft_audit.json"
            if phase in {"draft", "revise"} and not craft_json_path.exists():
                return False, f"{phase} phase 必须生成 drafts/craft_audit.json"
            if phase in {"draft", "revise"}:
                ok, err = _validate_required_craft_checks(ws)
                if not ok:
                    return False, err
                ok, err = _validate_paper_claim_audit_if_needed(ws)
                if not ok:
                    return False, err
                style = _parse_writing_style(read_text_file(ws / "drafts" / "writing_style.json", default=""))
                if style.get("venue_style") == "both":
                    ok, err = _validate_style_variants(ws)
                    if not ok:
                        return False, err
                    for style_id in ("is", "ccf_a"):
                        variant_paper = ws / "drafts" / style_id / "paper.tex"
                        variant_audit = ws / "drafts" / style_id / "craft_audit.json"
                        if not variant_paper.exists():
                            return False, f"venue_style=both 必须生成 drafts/{style_id}/paper.tex"
                        if not variant_audit.exists():
                            return False, f"venue_style=both 必须生成 drafts/{style_id}/craft_audit.json"

            return True, None

        elif phase == "self_check":
            check = read_text_file(ws / "drafts" / "self_check.md", default="")
            if len(check) < 200:
                return False, f"self_check.md 过短({len(check)}字符)"
            lowered = check.lower()
            required_topics = {
                "number": ("number", "数字"),
                "citation": ("citation", "引用"),
                "claim": ("claim", "证据", "主张"),
                "revision": ("revision", "todo", "修订", "待办"),
            }
            missing_topics = [
                label
                for label, tokens in required_topics.items()
                if not any(token in lowered or token in check for token in tokens)
            ]
            if missing_topics:
                return False, "self_check.md 必须覆盖 number/citation/claim/revision 自查主题: " + ", ".join(missing_topics)
            return True, None

        return True, None


def _load_json_file(path: Path) -> tuple[dict | None, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, f"缺少文件: {path.name}"
    except Exception as exc:
        return None, f"读取失败 {path}: {exc}"
    if not text.strip():
        return None, f"文件为空: {path.name}"
    try:
        data = json.loads(text)
    except Exception as exc:
        return None, f"{path.name} 不是合法 JSON: {exc}"
    if not isinstance(data, dict):
        return None, f"{path.name} 顶层必须是对象"
    return data, None


def _validate_resource_index_artifacts(ws: Path) -> tuple[bool, str | None]:
    index, err = _load_json_file(ws / "drafts" / "manuscript_resource_index.json")
    if err:
        return False, err
    plan, err = _load_json_file(ws / "drafts" / "section_plan.json")
    if err:
        return False, err
    evidence, err = _load_json_file(ws / "drafts" / "evidence_plan.json")
    if err:
        return False, err
    figures, err = _load_json_file(ws / "drafts" / "figure_table_plan.json")
    if err:
        return False, err
    cdr_ledger, err = _load_json_file(ws / "drafts" / "cdr_claim_ledger.json")
    if err:
        return False, err
    claim_ledger, err = _load_json_file(ws / "drafts" / "claim_ledger.json")
    if err:
        return False, err
    figure_registry, err = _load_json_file(ws / "drafts" / "figure_registry.json")
    if err:
        return False, err
    alignment_matrix, err = _load_json_file(ws / "drafts" / "alignment_matrix.json")
    if err:
        return False, err

    if not isinstance(index.get("artifacts"), list):
        return False, "manuscript_resource_index.json 缺少 artifacts 列表"
    sections = plan.get("sections")
    if not isinstance(sections, list):
        return False, "section_plan.json 缺少 sections 列表"
    section_ids = {str(item.get("id")) for item in sections if isinstance(item, dict)}
    for required in ("introduction", "related_work", "methodology", "experiments"):
        if required not in section_ids:
            return False, f"section_plan.json 缺少章节计划: {required}"
    claim_slots = evidence.get("claim_slots")
    if not isinstance(claim_slots, list):
        return False, "evidence_plan.json 缺少 claim_slots 列表"
    slot_ids = {str(item.get("slot_id")) for item in claim_slots if isinstance(item, dict)}
    for required in ("intro_problem_gap", "experiments_main_result"):
        if required not in slot_ids:
            return False, f"evidence_plan.json 缺少证据槽: {required}"
    planned_visuals = figures.get("planned_visuals")
    if not isinstance(planned_visuals, list):
        return False, "figure_table_plan.json 缺少 planned_visuals 列表"
    visual_ids = {
        str(item.get("figure_id") or item.get("table_id"))
        for item in planned_visuals
        if isinstance(item, dict)
    }
    for required in ("fig:main_results", "tab:main_results"):
        if required not in visual_ids:
            return False, f"figure_table_plan.json 缺少图表计划: {required}"
    if cdr_ledger.get("semantics") != "cdr_claim_ledger_seed_not_final_scientific_judgment":
        return False, "cdr_claim_ledger.json semantics 不正确"
    cdr_tuple = cdr_ledger.get("cdr_tuple")
    if not isinstance(cdr_tuple, dict):
        return False, "cdr_claim_ledger.json 缺少 cdr_tuple"
    contribution_claims = cdr_ledger.get("contribution_claims")
    if not isinstance(contribution_claims, list) or not contribution_claims:
        return False, "cdr_claim_ledger.json 缺少 contribution_claims"
    for item in contribution_claims:
        if not isinstance(item, dict):
            return False, "cdr_claim_ledger.json contribution_claims 必须是对象列表"
        if not item.get("cdr_field") or not isinstance(item.get("required_section"), list):
            return False, "cdr_claim_ledger.json 每条 claim 必须包含 cdr_field 和 required_section"
    if claim_ledger.get("semantics") != "mechanical_claim_ledger_seed_not_final_scientific_judgment":
        return False, "claim_ledger.json semantics 不正确"
    if not isinstance(claim_ledger.get("claims"), list) or not claim_ledger.get("claims"):
        return False, "claim_ledger.json 缺少 claims"
    if figure_registry.get("semantics") != "mechanical_figure_registry_seed_not_visual_generation":
        return False, "figure_registry.json semantics 不正确"
    if not isinstance(figure_registry.get("visuals"), list) or not figure_registry.get("visuals"):
        return False, "figure_registry.json 缺少 visuals"
    if alignment_matrix.get("semantics") != "alignment_matrix_seed_not_final_scientific_judgment":
        return False, "alignment_matrix.json semantics 不正确"
    if not isinstance(alignment_matrix.get("rows"), list) or not alignment_matrix.get("rows"):
        return False, "alignment_matrix.json 缺少 rows"
    return True, None


def _validate_paper_state(ws: Path) -> tuple[bool, str | None]:
    state, err = _load_json_file(ws / "drafts" / "paper_state.json")
    if err:
        return False, err
    if state.get("semantics") != "shared_state_for_section_by_section_writing_not_final_claims":
        return False, "paper_state.json semantics 不正确"
    sections = state.get("sections")
    if not isinstance(sections, dict):
        return False, "paper_state.json 缺少 sections 对象"
    for section_id in [
        "methodology",
        "experiments",
        "related_work",
        "analysis",
        "introduction",
        "conclusion",
        "abstract",
    ]:
        entry = sections.get(section_id)
        if not isinstance(entry, dict):
            return False, f"paper_state.json 缺少 section: {section_id}"
        if entry.get("file") != f"drafts/sections/{section_id}.tex":
            return False, f"paper_state.json section file 不正确: {section_id}"
    shared = state.get("shared_facts")
    if not isinstance(shared, dict):
        return False, "paper_state.json 缺少 shared_facts"
    if not isinstance(shared.get("bib_keys"), list):
        return False, "paper_state.json shared_facts.bib_keys 必须是列表"
    if not isinstance(shared.get("result_metrics"), list):
        return False, "paper_state.json shared_facts.result_metrics 必须是列表"
    if not isinstance(shared.get("alignment_matrix"), list):
        return False, "paper_state.json shared_facts.alignment_matrix 必须是列表"
    ok, err = _validate_paper_state_input_fingerprints(ws, state)
    if not ok:
        return False, err
    return True, None


def _validate_paper_state_input_fingerprints(ws: Path, state: dict) -> tuple[bool, str | None]:
    fingerprints = state.get("input_fingerprints")
    if not isinstance(fingerprints, dict) or not fingerprints:
        return False, "paper_state.json 缺少 input_fingerprints，必须重新初始化 manuscript state"
    for label, item in fingerprints.items():
        if not isinstance(item, dict):
            return False, f"paper_state.json input_fingerprints.{label} 必须是对象"
        rel = str(item.get("path") or "").strip()
        if not rel:
            return False, f"paper_state.json input_fingerprints.{label} 缺少 path"
        expected_exists = bool(item.get("exists"))
        path = ws / rel
        if expected_exists and not path.exists():
            return False, f"paper_state input 已不存在: {rel}"
        if not expected_exists:
            # Optional artifacts such as related_work.bib or evidence packs may
            # be created after a lightweight test/helper state. They should be
            # picked up by later validators instead of blocking unrelated
            # checks. Inputs that existed when paper_state was created are
            # still hash-bound below.
            continue
        expected_hash = str(item.get("sha256") or "").strip()
        if not expected_hash:
            return False, f"paper_state input_fingerprints.{label} 缺少 sha256"
        if path.is_file() and _sha256_file(path) != expected_hash:
            return False, f"paper_state 对应的 {label} 已过期，必须重新初始化 manuscript state"
    return True, None


def _validate_single_section(ws: Path, section_id: str) -> tuple[bool, str | None]:
    section_id = normalize_section_id(section_id)
    if section_id not in CORE_SECTIONS:
        return False, f"未知章节: {section_id}"
    path = ws / "drafts" / "sections" / f"{section_id}.tex"
    if not path.exists():
        return False, f"缺少章节草稿: drafts/sections/{section_id}.tex"
    text = read_text_file(path, default="")
    min_chars = 60 if section_id == "abstract" else 100
    if len(text.strip()) < min_chars:
        return False, f"章节草稿过短: {section_id}"
    if "\\documentclass" in text or "\\begin{document}" in text or "\\end{document}" in text:
        return False, f"章节草稿不能包含整篇LaTeX wrapper: {section_id}"
    placeholder_hits = _placeholder_hits(text)
    if placeholder_hits:
        return False, f"章节草稿 {section_id} 仍包含 planning placeholder: {', '.join(placeholder_hits[:8])}"
    cids = _known_alignment_cids(ws)
    internal_hits = _internal_label_leakages(text, cids)
    if internal_hits:
        return False, (
            f"章节草稿 {section_id} 暴露内部 alignment/CID 标记: "
            + ", ".join(internal_hits[:8])
        )
    if section_id == "abstract" and has_formal_citation(text):
        return False, "Abstract 不应包含正式引用；请把作者-年份、数字引用或 LaTeX citation command 放到 Introduction 或 Related Work"
    if section_id == "abstract" and re.search(r"\\(?:begin|end)\{abstract\}", text, flags=re.IGNORECASE):
        return False, "Abstract 章节文件应只包含摘要正文，不应包含 \\begin{abstract} 或 \\end{abstract}"
    if section_id == "abstract" and re.search(r"\\(?:section|subsection)\*?\{", text, flags=re.IGNORECASE):
        return False, "Abstract 章节文件应只包含摘要正文，不应包含 \\section 或 \\subsection 标题"
    foreign_headers = _find_foreign_section_headers(text, section_id)
    if foreign_headers:
        return False, (
            f"章节草稿 {section_id} 夹带了其他章节标题: "
            + ", ".join(foreign_headers[:5])
        )
    state_path = ws / "drafts" / "paper_state.json"
    if state_path.exists():
        state, err = _load_json_file(state_path)
        if err:
            return False, err
        entry = ((state or {}).get("sections") or {}).get(section_id)
        if not isinstance(entry, dict):
            return False, f"paper_state.json 缺少 section 状态: {section_id}"
        if entry.get("status") not in {"written", "revised"}:
            return False, f"paper_state.json 中 {section_id} 尚未标记为 written/revised"
    return True, None


def _known_alignment_cids(ws: Path) -> list[str]:
    cids: list[str] = []
    for rel in ("drafts/alignment_matrix.json", "drafts/paper_state.json"):
        data, err = _load_json_file(ws / rel)
        if err or not isinstance(data, dict):
            continue
        rows = data.get("rows")
        if not isinstance(rows, list):
            shared = data.get("shared_facts") if isinstance(data.get("shared_facts"), dict) else {}
            rows = shared.get("alignment_matrix") if isinstance(shared, dict) else []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("cid") or "").strip().upper()
            if re.fullmatch(r"C\d+", cid) and cid not in cids:
                cids.append(cid)
    return cids


def _find_foreign_section_headers(text: str, section_id: str) -> list[str]:
    """Detect section files that try to draft multiple paper sections at once."""

    current_title = SECTION_TITLES.get(section_id, section_id).casefold()
    foreign: list[str] = []
    for match in re.finditer(r"\\(?:section|subsection)\*?\{([^{}]+)\}", text):
        title = re.sub(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?", "", match.group(1))
        normalized = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
        if not normalized:
            continue
        if normalized == re.sub(r"[^a-z0-9]+", " ", current_title).strip():
            continue
        for other_id, other_title in SECTION_TITLES.items():
            if other_id == section_id:
                continue
            other_norm = re.sub(r"[^a-z0-9]+", " ", other_title.casefold()).strip()
            if normalized == other_norm:
                foreign.append(other_title)
                break
    return foreign


def _validate_revision_artifacts(ws: Path, round_num: int) -> tuple[bool, str | None]:
    patch_path = ws / "drafts" / "patches" / f"round_{round_num}_patches.json"
    patches, err = _load_json_file(patch_path)
    if err:
        return False, f"revise phase 必须生成 patch list: {err}"
    if patches.get("semantics") != "mechanical_review_issue_locations_not_final_revision_decisions":
        return False, f"{patch_path.relative_to(ws)} semantics 不正确"
    patch_items = patches.get("patches")
    if not isinstance(patch_items, list):
        return False, f"{patch_path.relative_to(ws)} 缺少 patches 列表"

    response_path = ws / "drafts" / f"revision_response_round_{round_num}.md"
    response = read_text_file(response_path, default="")
    if len(response.strip()) < 80:
        return False, f"revise phase 必须生成非空 revision response: {response_path.relative_to(ws)}"
    if "resolved" not in response.lower() and "已解决" not in response and "未解决" not in response:
        return False, f"{response_path.relative_to(ws)} 必须记录 resolved/unresolved 修订状态"
    response_lower = response.lower()
    required_patch_ids = [
        str(item.get("patch_id") or item.get("id") or "").strip()
        for item in patch_items
        if isinstance(item, dict)
        and str(item.get("severity") or "").strip().lower() in {"high", "medium"}
    ]
    missing_patch_ids = [patch_id for patch_id in required_patch_ids if patch_id and patch_id.lower() not in response_lower]
    if missing_patch_ids:
        return False, f"{response_path.relative_to(ws)} 必须逐条回应 high/medium patch_id: {missing_patch_ids}"
    return True, None


def _validate_alignment_matrix(ws: Path) -> tuple[bool, str | None]:
    matrix, err = _load_json_file(ws / "drafts" / "alignment_matrix.json")
    if err:
        return False, err
    if matrix.get("semantics") != "alignment_matrix_seed_not_final_scientific_judgment":
        return False, "alignment_matrix.json semantics 不正确"
    rows = matrix.get("rows")
    if not isinstance(rows, list) or not rows:
        return False, "alignment_matrix.json 缺少 rows"
    if len(rows) < 3 or len(rows) > 5:
        return False, "alignment_matrix.json 应包含 3-5 条 contribution alignment rows"
    required_fields = [
        "cid",
        "motivation",
        "contribution",
        "related_gap",
        "counterfactual",
        "nearest_prior_work",
        "novelty_signal",
        "design_choice",
        "experiment",
        "analysis",
    ]
    placeholder_tokens = {"LLM_REVIEW_REQUIRED", "TODO", "TBD"}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            return False, f"alignment_matrix row #{index} 必须是对象"
        for field in required_fields:
            value = row.get(field)
            if value in (None, "", [], {}):
                return False, f"alignment_matrix row {row.get('cid') or index} 缺少 {field}"
            if field in {
                "motivation",
                "contribution",
                "counterfactual",
                "novelty_signal",
                "design_choice",
                "analysis",
            }:
                text = str(value).strip()
                if text in placeholder_tokens or text.startswith("LLM_REVIEW_REQUIRED"):
                    return False, f"alignment_matrix row {row.get('cid') or index} 的 {field} 仍是 LLM_REVIEW_REQUIRED/TODO"
        related_gap = row.get("related_gap")
        if isinstance(related_gap, dict):
            tension = str(related_gap.get("tension") or "").strip()
            if tension in placeholder_tokens or tension.startswith("LLM_REVIEW_REQUIRED"):
                return False, f"alignment_matrix row {row.get('cid') or index} 的 related_gap.tension 仍是 LLM_REVIEW_REQUIRED/TODO"
            nearest = related_gap.get("nearest_prior_work") or row.get("nearest_prior_work")
        else:
            nearest = row.get("nearest_prior_work")
        if not isinstance(nearest, dict):
            return False, f"alignment_matrix row {row.get('cid') or index} nearest_prior_work 必须是对象"
        distance = str(nearest.get("distance") or "").strip()
        if distance and distance not in {"very_close", "moderate", "distant", "none_found"}:
            return False, f"alignment_matrix row {row.get('cid') or index} nearest_prior_work.distance 无效: {distance}"
    return True, None


def _validate_required_craft_checks(ws: Path) -> tuple[bool, str | None]:
    craft, err = _load_json_file(ws / "drafts" / "craft_audit.json")
    if err:
        return False, err
    if craft.get("semantics") != "deterministic_writing_craft_audit_not_scientific_judgment":
        return False, "craft_audit.json semantics 不正确"
    ok, err = _validate_craft_audit_fingerprints(ws, craft)
    if not ok:
        return False, err
    checks = craft.get("checks")
    if not isinstance(checks, list):
        return False, "craft_audit.json 缺少 checks 列表"
    by_name = {str(item.get("name")): item for item in checks if isinstance(item, dict)}
    required_names = {
        "matrix_row_count",
        "intro_contribution_count",
        "abstract_no_cite",
        "abstract_no_section_heading",
        "no_internal_label_leakage",
        "no_placeholder_tokens",
        "number_traceability",
        "no_standalone_limitations",
        "conclusion_has_limitations_subsection",
    }
    missing = sorted(required_names - set(by_name))
    if missing:
        return False, "craft_audit.json 缺少关键检查: " + ", ".join(missing)
    soft_legacy_failures = {"intro_contribution_count"}
    fail_items = [
        name
        for name, item in by_name.items()
        if item.get("level") == "FAIL"
        and item.get("passed") is False
        and name not in soft_legacy_failures
    ]
    if fail_items:
        return False, "craft_audit.json 存在 FAIL: " + ", ".join(fail_items[:8])
    return True, None


def _validate_paper_claim_audit_if_needed(ws: Path) -> tuple[bool, str | None]:
    ok, err = _validate_required_craft_checks(ws)
    if not ok:
        return False, "paper_claim_audit 前必须先通过当前稿件的 craft audit: " + (err or "")
    pack_path = ws / "drafts" / "experiment_evidence_pack.json"
    if not pack_path.exists():
        return True, None
    audit_md = ws / "drafts" / "paper_claim_audit.md"
    audit_json = ws / "drafts" / "paper_claim_audit.json"
    if not audit_md.exists():
        return False, "存在 experiment_evidence_pack 时必须生成 drafts/paper_claim_audit.md"
    audit, err = _load_json_file(audit_json)
    if err:
        return False, f"存在 experiment_evidence_pack 时必须生成合法 paper_claim_audit.json: {err}"
    if audit.get("semantics") != "paper_claim_audit_against_experiment_evidence_pack":
        return False, "paper_claim_audit.json semantics 不正确"
    ok, err = _validate_paper_claim_audit_fingerprints(ws, audit)
    if not ok:
        return False, err
    summary = audit.get("summary")
    if not isinstance(summary, dict):
        return False, "paper_claim_audit.json 缺少 summary"
    if "fail_count" not in summary or "warn_count" not in summary:
        return False, "paper_claim_audit.json summary 必须包含 fail_count/warn_count"
    if int(summary.get("fail_count") or 0) > 0:
        return False, "paper_claim_audit.json 存在 FAIL，必须先回 T8-REVISE-2 修订"
    unsupported = audit.get("unsupported_strong_claims")
    if unsupported:
        return False, "paper_claim_audit.json 存在 unsupported_strong_claims，必须先修订"
    forbidden = audit.get("forbidden_wording_violations")
    if forbidden:
        return False, "paper_claim_audit.json 存在 forbidden_wording_violations，必须先修订"
    if not isinstance(audit.get("issues"), list):
        return False, "paper_claim_audit.json issues 必须是列表"
    return True, None


def _validate_craft_audit_fingerprints(ws: Path, craft: dict) -> tuple[bool, str | None]:
    fingerprints = craft.get("input_fingerprints")
    if not isinstance(fingerprints, dict):
        return False, "craft_audit.json 缺少 input_fingerprints，必须重新运行 audit_writing_craft"
    current = craft_audit_input_fingerprints(ws)
    for label, item in current.items():
        previous = fingerprints.get(label)
        if not isinstance(previous, dict):
            return False, f"craft_audit.json input_fingerprints 缺少 {label}"
        rel = str(previous.get("path") or item.get("path") or "").strip()
        if rel != str(item.get("path") or ""):
            return False, f"craft_audit.json input_fingerprints.{label}.path 不匹配"
        if bool(previous.get("exists")) != bool(item.get("exists")):
            return False, f"craft_audit.json 对应输入存在性已变化: {rel}"
        if not item.get("exists"):
            continue
        if item.get("kind") == "dir":
            if str(previous.get("sha256") or "") != str(item.get("sha256") or ""):
                return False, f"craft_audit.json 对应目录已变化: {rel}"
            previous_count = previous.get("file_count")
            current_count = item.get("file_count")
            if previous_count is None or current_count is None:
                return False, f"craft_audit.json 对应目录缺少文件计数: {rel}"
            if int(previous_count) != int(current_count):
                return False, f"craft_audit.json 对应目录文件数已变化: {rel}"
            continue
        if str(previous.get("sha256") or "") != str(item.get("sha256") or ""):
            return False, f"craft_audit.json 对应输入已过期: {rel}"
    return True, None


def _validate_paper_claim_audit_fingerprints(ws: Path, audit: dict) -> tuple[bool, str | None]:
    fingerprints = audit.get("input_fingerprints")
    if not isinstance(fingerprints, dict):
        return False, "paper_claim_audit.json 缺少 input_fingerprints，必须刷新 claim audit"
    checks = [
        ("paper", "paper_path", "paper_sha256", "drafts/paper.tex", "text"),
        ("evidence_pack", "evidence_pack_path", "evidence_pack_sha256", "drafts/experiment_evidence_pack.json", "json"),
        ("result_to_claim", "result_to_claim_path", "result_to_claim_sha256", "drafts/result_to_claim.json", "json"),
    ]
    for label, path_key, hash_key, default_rel, mode in checks:
        rel = str(fingerprints.get(path_key) or default_rel).strip()
        expected = str(fingerprints.get(hash_key) or "").strip()
        if not expected:
            return False, f"paper_claim_audit.json 缺少 {hash_key}，必须刷新 claim audit"
        path = ws / rel
        if not path.exists():
            return False, f"paper_claim_audit input 不存在: {rel}"
        if mode == "json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                return False, f"paper_claim_audit input JSON 无效: {rel}: {exc}"
            actual = _sha256_json(data)
        else:
            actual = _sha256_text(path.read_text(encoding="utf-8", errors="replace"))
        if actual != expected:
            return False, f"paper_claim_audit.json 对应的 {label} 已过期，必须重新运行 audit_paper_claims"
    return True, None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_text(path.read_text(encoding="utf-8", errors="replace"))


def _sha256_json(data) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(payload)


def _parse_writing_style(text: str) -> dict[str, str]:
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items() if value is not None}


def _human_interaction_exists(ws: Path, interaction_id: str) -> bool:
    if not interaction_id:
        return False
    log_path = ws / "_runtime" / "human_interactions.jsonl"
    if not log_path.exists():
        return False
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        if str(record.get("interaction_id") or "") == interaction_id:
            return True
    return False


def _normalize_style_variant_text(text: str) -> str:
    lines = [
        line
        for line in text.splitlines()
        if not line.startswith("% ResearchOS style variant:")
        and not line.startswith("% Target venue:")
        and not line.startswith("% This variant shares")
        and not line.startswith("% ResearchOS venue_style:")
        and not line.startswith("% ResearchOS target_venue:")
    ]
    return re.sub(r"\s+", " ", "\n".join(lines)).strip()


def _validate_style_variants(ws: Path) -> tuple[bool, str | None]:
    main_text = read_text_file(ws / "drafts" / "paper.tex", default="")
    main_norm = _normalize_style_variant_text(main_text)
    for style_id in ("is", "ccf_a"):
        variant_path = ws / "drafts" / style_id / "paper.tex"
        if not variant_path.exists():
            return False, f"venue_style=both 必须生成 drafts/{style_id}/paper.tex"
        variant_text = read_text_file(variant_path, default="")
        if not variant_text.strip():
            return False, f"drafts/{style_id}/paper.tex 为空"
        variant_norm = _normalize_style_variant_text(variant_text)
        if variant_norm == main_norm:
            return False, (
                f"venue_style=both 的 drafts/{style_id}/paper.tex 不能只是主稿加注释；"
                f"Writer 必须基于 LLM 判断完成 {style_id} 风格化改写"
            )
        note_path = ws / "drafts" / style_id / "style_revision_notes.md"
        notes = read_text_file(note_path, default="")
        if len(notes.strip()) < 80:
            return False, f"venue_style=both 必须生成 drafts/{style_id}/style_revision_notes.md 说明风格化改写取舍"
    return True, None


def _suggest_venue_style(target_venue: object) -> str:
    venue = str(target_venue or "").lower()
    config_path = Path(__file__).resolve().parents[2] / "config" / "venue_style_map.yaml"
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    is_patterns = data.get("is", []) if isinstance(data, dict) else []
    if any(str(pattern).lower() in venue for pattern in is_patterns):
        return "is"
    if isinstance(data, dict) and data.get("ccf_a_default", True):
        return "ccf_a"
    return "ccf_a"
