"""T8 Writer Agent — 论文写作

支持多个phase: outline/draft/self_check/revise/final
输出: drafts/outline.md, drafts/paper.tex, drafts/self_check.md
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.prompts import render_prompt
from ..tools.manuscript import CORE_SECTIONS, SECTION_TITLES, SECTION_WRITING_SEQUENCE, normalize_section_id
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
                        "read_file",
                        "write_file",
                        "list_files",
                        "build_manuscript_resource_index",
                        "plan_manuscript_sections",
                        "plan_manuscript_evidence",
                        "initialize_manuscript_state",
                        "update_manuscript_section_state",
                        "assemble_manuscript",
                        "audit_manuscript_claims",
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
        order = [
            "methodology",
            "experiments",
            "related_work",
            "analysis",
            "introduction",
            "limitations",
            "conclusion",
            "abstract",
        ]
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
        manuscript_audit = read_text_file(ws / "drafts" / "manuscript_audit.md", default="")
        paper_state = read_text_file(ws / "drafts" / "paper_state.json", default="")

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
            manuscript_audit_preview=manuscript_audit[:3000],
            paper_state_preview=paper_state[:5000],
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
                "再调用 plan_manuscript_evidence 生成 drafts/evidence_plan.json 和 drafts/figure_table_plan.json。"
                ),
            )
        if phase == "section_plan":
            return prepend_resume_prefix(
                ctx,
                (
                "请执行 T8 Writer Phase 1.5: 初始化逐章节写作状态。\n\n"
                "调用 initialize_manuscript_state 读取 drafts/outline.md、resource index、"
                "section/evidence/figure plans，生成 drafts/paper_state.json 和 "
                "drafts/section_outlines/*.md。不要写任何章节正文。"
                ),
            )
        if phase == "outline":
            return prepend_resume_prefix(
                ctx,
                (
                "请执行 T8 Writer Phase 1: 生成论文大纲。\n\n"
                "基于 drafts/manuscript_resource_index.json、drafts/section_plan.json、实验结果和文献综述，生成 drafts/outline.md。"
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
                "T8-SEC-RELATED、T8-SEC-ANALYSIS、T8-SEC-INTRO、T8-SEC-LIMITATIONS、"
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
                "生成 drafts/manuscript_audit.md。"
                "**重要**: 所有实验数字必须来自 experiments/results_summary.json，"
                "所有引用必须存在于 literature/related_work.bib。"
                ),
            )
        elif phase == "self_check":
            return prepend_resume_prefix(
                ctx,
                (
                "请执行 T8 Writer Phase 4: 论文自查。\n\n"
                "读取 drafts/paper.tex，生成 drafts/self_check.md。"
                "检查内容完整性、数字准确性、引用完整性、格式规范，并参考 drafts/manuscript_audit.md。"
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
                f"drafts/manuscript_audit.md。最后写 drafts/revision_response_round_{round_num}.md。"
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
                "limitations",
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
            return True, None

        elif phase == "section_draft":
            section_id = self._section_id(ctx)
            if not section_id:
                return False, "section_draft phase 缺少 extra.section_id"
            return _validate_single_section(ws, section_id)

        elif phase == "section_drafts":
            return _validate_paper_state(ws)

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
                cited = set(
                    re.findall(
                        r"\\(?:cite|citep|citet|citealp|citealt|citeauthor|citeyear|parencite|textcite)\{([^}]+)\}",
                        paper,
                    )
                )
                cited = {k.strip() for chunk in cited for k in chunk.split(",")}
                missing_cites = cited - bib_keys
                if missing_cites:
                    return False, f"paper.tex 引用了不存在的BibTeX key: {missing_cites}"

            audit_path = ws / "drafts" / "manuscript_audit.md"
            if phase in {"draft", "revise"} and not audit_path.exists():
                return False, f"{phase} phase 必须生成 drafts/manuscript_audit.md"

            return True, None

        elif phase == "self_check":
            check = read_text_file(ws / "drafts" / "self_check.md", default="")
            if len(check) < 200:
                return False, f"self_check.md 过短({len(check)}字符)"
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
        "limitations",
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
    return True, None
