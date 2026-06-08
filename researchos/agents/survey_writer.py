from __future__ import annotations

"""T3.6 Survey Writer Agent.

This optional branch writes a professional taxonomy-driven survey paper after
T3.5. It is not a converter from synthesis.md to TeX.
"""

import json
import re
from pathlib import Path

import yaml

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.prompts import render_prompt
from ..tools.survey_tools import SURVEY_SECTION_SEQUENCE, SURVEY_SECTION_TITLES
from ._common import load_project, prepend_resume_prefix, read_text_file


class SurveyWriterAgent(Agent):
    """Survey branch agent with gate, plan, section, assemble, and feed phases."""

    def __init__(self, mode: str | None = None):
        super().__init__(
            build_agent_spec(
                "survey_writer",
                mode=mode,
                defaults={
                    "model_tier": "heavy",
                    "tool_names": [
                        "ask_human",
                        "read_file",
                        "write_file",
                        "list_files",
                        "expand_corpus_for_survey",
                        "build_survey_state",
                        "update_survey_section_state",
                        "assemble_survey",
                        "audit_survey_coverage",
                        "export_survey_for_ideation",
                        "latex_compile",
                        "finish_task",
                    ],
                    "max_steps": 120,
                    "max_tokens_total": 500_000,
                    "max_wall_seconds": 1800,
                    "max_validation_retries": 3,
                    "temperature": 0.55,
                    "allowed_read_prefixes": [
                        "",
                        "literature/",
                        "drafts/survey/",
                        "ideation/",
                        "_runtime/resume/",
                    ],
                    "allowed_write_prefixes": ["drafts/survey/", "ideation/"],
                    "prompt_template": "survey_writer.j2",
                },
            )
        )
        self._mode = mode

    def _phase(self, ctx: ExecutionContext) -> str:
        return ctx.mode or str(ctx.extra.get("phase") or self._mode or "survey_plan")

    def _section_id(self, ctx: ExecutionContext) -> str:
        return str(ctx.extra.get("section_id") or ctx.extra.get("section") or "").strip()

    def system_prompt(self, ctx: ExecutionContext) -> str:
        ws = ctx.workspace_dir
        project = load_project(ctx)
        phase = self._phase(ctx)
        section_id = self._section_id(ctx)
        section_outline = (
            read_text_file(ws / "drafts" / "survey" / "section_outlines" / f"{section_id}.md", default="")
            if section_id
            else ""
        )
        return render_prompt(
            self.spec.prompt_template,
            ctx,
            project=project,
            phase=phase,
            section_id=section_id,
            section_title=SURVEY_SECTION_TITLES.get(section_id, section_id.replace("_", " ").title()),
            synthesis_preview=read_text_file(ws / "literature" / "synthesis.md", default="")[:6000],
            synthesis_workbench_preview=read_text_file(ws / "literature" / "synthesis_workbench.json", default="")[:6000],
            domain_map_preview=read_text_file(ws / "literature" / "domain_map.json", default="")[:5000],
            comparison_table_preview=read_text_file(ws / "literature" / "comparison_table.csv", default="")[:4000],
            survey_plan_preview=read_text_file(ws / "drafts" / "survey" / "survey_plan.json", default="")[:7000],
            survey_state_preview=read_text_file(ws / "drafts" / "survey" / "survey_state.json", default="")[:7000],
            corpus_decision_preview=read_text_file(ws / "drafts" / "survey" / "corpus_decision.json", default="")[:2000],
            survey_audit_preview=read_text_file(ws / "drafts" / "survey" / "survey_audit.md", default="")[:3000],
            section_outline_preview=section_outline[:5000],
            related_work_preview=read_text_file(ws / "literature" / "related_work.bib", default="")[:3000],
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        phase = self._phase(ctx)
        if phase == "survey_gate":
            message = (
                "请执行 T3.6 Gate-1：询问用户是否撰写综述论文。"
                "必须调用 ask_human；若没有人工输入，暂停等待 resume，不要写伪默认值。"
                "结果写入 drafts/survey/decision.json。"
            )
        elif phase == "survey_plan":
            message = (
                "请执行 T3.6-PLAN：基于 literature/synthesis.md、synthesis_workbench.json、"
                "domain_map.json、comparison_table.csv 和 paper_notes 规划 taxonomy-driven survey。"
                "写 drafts/survey/survey_plan.json；不要写正文。"
            )
        elif phase == "outline_gate":
            message = (
                "请执行 T3.6 Gate-2：把 survey_plan.json 中的 taxonomy 和 outline 展示给用户，"
                "询问 approve/adjust。若用户要求调整，就地修订 survey_plan.json 并记录 outline_decision.json。"
            )
        elif phase == "corpus_gate":
            message = (
                "请执行 T3.6 Gate-3：询问用户选择 conservative 或 complete 素材范围，"
                "写 drafts/survey/corpus_decision.json。"
            )
        elif phase == "survey_expand":
            message = (
                "请执行 T3.6-EXPAND：调用 expand_corpus_for_survey 生成一次性定向补检计划。"
                "这不是回到 T2，也不是 idea 阶段循环。"
            )
        elif phase == "survey_state":
            message = (
                "请执行 T3.6-STATE：调用 build_survey_state 初始化 survey_state.json 和逐 section outline。"
                "不要写正文。"
            )
        elif phase == "survey_section":
            section_id = self._section_id(ctx)
            message = (
                f"请执行 T3.6-SEC：只写 `{section_id}` 这一节到 "
                f"drafts/survey/sections/{section_id}.tex。"
                "不能写其它 section，不能生成整篇 wrapper。写完后调用 update_survey_section_state。"
            )
        elif phase == "survey_assemble":
            message = (
                "请执行 T3.6-ASSEMBLE：调用 assemble_survey 拼装 survey.tex，"
                "再调用 audit_survey_coverage 生成 survey_audit.md/json。"
            )
        elif phase == "survey_review":
            message = (
                "请执行 T3.6-REVIEW：逐 section 审阅 survey.tex 的 taxonomy 合理性、覆盖、公允比较、"
                "challenges/future 质量、scope 诚实性和写作 craft。需要修订时只改对应 section 文件，"
                "重新 assemble/audit，并写 drafts/survey/survey_review.md 与 survey_review_actions.json。"
            )
        elif phase == "survey_compile":
            message = (
                "请执行 T3.6-COMPILE：调用 latex_compile(tex_path=\"drafts/survey/survey.tex\") "
                "编译 survey PDF。latex_compile 会自动写 "
                "drafts/survey/survey_compile_report.json；不要伪造或手抄 report。"
                "若环境缺失或编译失败，按工具结果暂停/修复后 resume。"
            )
        elif phase == "survey_feed":
            message = (
                "请执行 T3.6-FEED：调用 export_survey_for_ideation 导出 ideation/survey_insights.json，"
                "供 T4 作为可选 idea fuel。"
            )
        else:
            message = f"请执行 T3.6 survey_writer phase={phase}。"
        return prepend_resume_prefix(ctx, message)

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        ws = ctx.workspace_dir
        phase = self._phase(ctx)
        if phase == "survey_gate":
            data, err = _load_json(ws / "drafts" / "survey" / "decision.json")
            if err:
                return False, err
            if not isinstance(data.get("write_survey"), bool):
                return False, "decision.json 必须包含布尔字段 write_survey"
            return True, None
        if phase == "survey_plan":
            return _validate_survey_plan(ws / "drafts" / "survey" / "survey_plan.json")
        if phase == "outline_gate":
            ok, err = _validate_survey_plan(ws / "drafts" / "survey" / "survey_plan.json")
            if not ok:
                return False, err
            decision, err = _load_json(ws / "drafts" / "survey" / "outline_decision.json")
            if err:
                return False, err
            if decision.get("decision") not in {"approve", "adjust"}:
                return False, "outline_decision.json decision 必须是 approve/adjust"
            return True, None
        if phase == "corpus_gate":
            data, err = _load_json(ws / "drafts" / "survey" / "corpus_decision.json")
            if err:
                return False, err
            if data.get("scope") not in {"conservative", "complete"}:
                return False, "corpus_decision.json scope 必须是 conservative/complete"
            return True, None
        if phase == "survey_expand":
            data, err = _load_json(ws / "drafts" / "survey" / "survey_expansion.json")
            if err:
                return False, err
            if data.get("semantics") != "one_shot_survey_corpus_expansion_plan_not_ideation_loop":
                return False, "survey_expansion.json semantics 不正确"
            return True, None
        if phase == "survey_state":
            return _validate_survey_state(ws)
        if phase == "survey_section":
            return _validate_survey_section(ws, self._section_id(ctx))
        if phase == "survey_assemble":
            tex = read_text_file(ws / "drafts" / "survey" / "survey.tex", default="")
            if "\\documentclass" not in tex or "\\begin{document}" not in tex or "\\end{document}" not in tex:
                return False, "survey.tex 缺少完整 LaTeX wrapper"
            audit, err = _load_json(ws / "drafts" / "survey" / "survey_audit.json")
            if err:
                return False, err
            if audit.get("semantics") != "deterministic_survey_coverage_audit_not_scientific_judgment":
                return False, "survey_audit.json semantics 不正确"
            fail_checks = [
                item.get("name")
                for item in audit.get("checks") or []
                if isinstance(item, dict) and item.get("level") == "FAIL" and item.get("passed") is False
            ]
            if fail_checks:
                return False, "survey_audit.json 存在 FAIL: " + ", ".join(str(x) for x in fail_checks[:6])
            return True, None
        if phase == "survey_review":
            review_path = ws / "drafts" / "survey" / "survey_review.md"
            review = read_text_file(review_path, default="")
            if len(review.strip()) < 300:
                return False, "survey_review.md 过短，必须包含 taxonomy/coverage/fairness/challenges/future/scope 审阅"
            required_markers = [
                "Taxonomy",
                "Coverage",
                "Comparative",
                "Challenges",
                "Future",
                "Scope",
            ]
            missing = [marker for marker in required_markers if marker.casefold() not in review.casefold()]
            if missing:
                return False, "survey_review.md 缺少审阅维度: " + ", ".join(missing)
            actions, err = _load_json(ws / "drafts" / "survey" / "survey_review_actions.json")
            if err:
                return False, err
            if actions.get("semantics") != "llm_survey_review_and_section_revision_plan":
                return False, "survey_review_actions.json semantics 不正确"
            if actions.get("review_target") != "taxonomy_driven_survey":
                return False, "survey_review_actions.json review_target 必须是 taxonomy_driven_survey"
            if actions.get("blocking_issues_remaining") is True:
                return False, "survey_review_actions.json 仍标记存在 blocking issues"
            if not isinstance(actions.get("section_actions"), list):
                return False, "survey_review_actions.json section_actions 必须是列表"
            return True, None
        if phase == "survey_compile":
            pdf_path = ws / "drafts" / "survey" / "survey.pdf"
            log_path = ws / "drafts" / "survey" / "survey.log"
            report, err = _load_json(ws / "drafts" / "survey" / "survey_compile_report.json")
            if err:
                return False, err
            if report.get("semantics") != "latex_compile_attempt_report":
                return False, "survey_compile_report.json semantics 不正确"
            if report.get("tex_path") != "drafts/survey/survey.tex":
                return False, "survey_compile_report.tex_path 必须是 drafts/survey/survey.tex"
            if report.get("success") is not True:
                return False, "survey_compile_report 未记录编译成功"
            if not pdf_path.exists() or pdf_path.stat().st_size <= 0:
                return False, "缺少 drafts/survey/survey.pdf"
            if not log_path.exists() or log_path.stat().st_size <= 0:
                return False, "缺少 drafts/survey/survey.log"
            return True, None
        if phase == "survey_feed":
            data, err = _load_json(ws / "ideation" / "survey_insights.json")
            if err:
                return False, err
            if data.get("semantics") != "survey_insights_optional_ideation_fuel_not_gate":
                return False, "survey_insights.json semantics 不正确"
            summary = read_text_file(ws / "drafts" / "survey" / "survey_summary.md", default="")
            if len(summary.strip()) < 80:
                return False, "survey_summary.md 过短"
            return True, None
        return True, None


def _load_json(path: Path) -> tuple[dict, str | None]:
    if not path.exists():
        return {}, f"缺少文件: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {}, f"{path} JSON 解析失败: {exc}"
    if not isinstance(data, dict):
        return {}, f"{path} 顶层必须是对象"
    return data, None


def _validate_survey_plan(path: Path) -> tuple[bool, str | None]:
    data, err = _load_json(path)
    if err:
        return False, err
    ws = path.parents[2] if len(path.parents) >= 3 else path.parent
    taxonomy = data.get("taxonomy")
    if not isinstance(taxonomy, dict):
        return False, "survey_plan.json 缺少 taxonomy 对象"
    tree = taxonomy.get("tree")
    if not isinstance(tree, list) or not tree:
        return False, "survey_plan.json taxonomy.tree 必须是非空数组"
    ok, evidence_err = _validate_survey_plan_evidence_strength(ws, data)
    if not ok:
        return False, evidence_err
    outline = data.get("outline")
    if not isinstance(outline, list) or len(outline) < 5:
        return False, "survey_plan.json outline 至少需要 5 个章节"
    section_ids = {str(item.get("section_id") or "").lower() for item in outline if isinstance(item, dict)}
    for required in ("background", "taxonomy", "comparison"):
        if required not in section_ids and not any(required in sid for sid in section_ids):
            return False, f"survey_plan.json outline 缺少 {required}"
    selfcheck = data.get("coverage_selfcheck")
    if not isinstance(selfcheck, dict):
        return False, "survey_plan.json 缺少 coverage_selfcheck"
    return True, None


def _validate_survey_plan_evidence_strength(ws: Path, data: dict) -> tuple[bool, str | None]:
    weak_ids = _survey_weak_evidence_ids(ws)
    if not weak_ids:
        return True, None
    upgrade_topics = {
        str(item.get("paper_or_topic") or item.get("paper_id") or item.get("topic") or "").strip()
        for item in data.get("resource_upgrade_needs") or []
        if isinstance(item, dict)
    }
    used_ids: set[str] = set()
    taxonomy = data.get("taxonomy") if isinstance(data.get("taxonomy"), dict) else {}
    for item in taxonomy.get("tree") or []:
        if isinstance(item, dict):
            used_ids.update(str(pid).strip() for pid in item.get("paper_ids") or [] if str(pid).strip())
    for item in data.get("outline") or []:
        if isinstance(item, dict):
            used_ids.update(str(pid).strip() for pid in item.get("paper_ids") or [] if str(pid).strip())
    illegal = sorted(pid for pid in used_ids if pid in weak_ids and pid not in upgrade_topics)
    if illegal:
        return False, (
            "survey_plan.json 把 abstract-only/metadata-only 材料挂为 taxonomy/section 核心 paper_ids；"
            "这些 ID 必须先移入 resource_upgrade_needs，不能作为综述核心证据: "
            f"{illegal}"
        )
    return True, None


def _survey_weak_evidence_ids(ws: Path) -> set[str]:
    weak: set[str] = set()
    abstract_dir = ws / "literature" / "paper_notes_abstract"
    if abstract_dir.exists():
        weak.update(path.stem for path in abstract_dir.glob("*.md") if path.is_file())
    metadata_triage = ws / "literature" / "metadata_triage.md"
    if metadata_triage.exists():
        text = metadata_triage.read_text(encoding="utf-8", errors="replace")
        weak.update(match.group(1).strip() for match in re.finditer(r"`([^`]+)`", text) if match.group(1).strip())
    return weak


def _validate_survey_state(ws: Path) -> tuple[bool, str | None]:
    data, err = _load_json(ws / "drafts" / "survey" / "survey_state.json")
    if err:
        return False, err
    if data.get("semantics") != "survey_state_for_taxonomy_driven_section_writing_not_final_claims":
        return False, "survey_state.json semantics 不正确"
    sections = data.get("sections")
    if not isinstance(sections, dict):
        return False, "survey_state.json 缺少 sections"
    for section_id in SURVEY_SECTION_SEQUENCE:
        entry = sections.get(section_id)
        if not isinstance(entry, dict):
            return False, f"survey_state.json 缺少 section: {section_id}"
        outline_path = ws / "drafts" / "survey" / "section_outlines" / f"{section_id}.md"
        if not outline_path.exists():
            return False, f"缺少 survey section outline: {section_id}"
    shared = data.get("shared_facts")
    if not isinstance(shared, dict) or not isinstance(shared.get("taxonomy_classes"), list):
        return False, "survey_state.json shared_facts.taxonomy_classes 必须是列表"
    return True, None


def _validate_survey_section(ws: Path, section_id: str) -> tuple[bool, str | None]:
    section_id = section_id.strip()
    if not section_id:
        return False, "survey_section 缺少 section_id"
    state, err = _load_json(ws / "drafts" / "survey" / "survey_state.json")
    if err:
        return False, err
    entry = ((state.get("sections") or {}).get(section_id) or {})
    if isinstance(entry, dict) and entry.get("status") == "skipped":
        return True, None
    path = ws / "drafts" / "survey" / "sections" / f"{section_id}.tex"
    if not path.exists():
        return False, f"缺少章节草稿: drafts/survey/sections/{section_id}.tex"
    text = path.read_text(encoding="utf-8", errors="replace")
    min_chars = 50 if section_id == "abstract" else 120
    if len(text.strip()) < min_chars:
        return False, f"survey section {section_id} 过短"
    if "\\documentclass" in text or "\\begin{document}" in text or "\\end{document}" in text:
        return False, f"survey section {section_id} 不能包含完整 LaTeX wrapper"
    foreign = _foreign_survey_headers(text, section_id)
    if foreign:
        return False, f"survey section {section_id} 夹带其它章节: {', '.join(foreign[:5])}"
    if isinstance(entry, dict) and entry.get("status") not in {"written", "revised"}:
        return False, f"survey_state 中 {section_id} 未标记 written/revised"
    return True, None


def _foreign_survey_headers(text: str, section_id: str) -> list[str]:
    current = _normalize_title(SURVEY_SECTION_TITLES.get(section_id, section_id))
    foreign = []
    for match in re.finditer(r"\\(?:section|subsection)\*?\{([^{}]+)\}", text):
        title = _normalize_title(match.group(1))
        if not title or title == current:
            continue
        for other_id, other_title in SURVEY_SECTION_TITLES.items():
            if other_id == section_id:
                continue
            if title == _normalize_title(other_title):
                foreign.append(other_title)
                break
    return foreign


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
