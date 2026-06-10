from __future__ import annotations

"""T3.6 Survey Writer Agent.

This optional branch writes a professional taxonomy-driven survey paper after
T3.5. It is not a converter from synthesis.md to TeX.
"""

import json
import re
from pathlib import Path
from difflib import SequenceMatcher

import yaml

from ..runtime.agent import Agent, ExecutionContext
from ..runtime.agent_params import build_agent_spec
from ..runtime.prompts import render_prompt
from ..tools.latex_compile import _compile_dependency_fingerprint
from ..literature_identity import is_paper_note_file, is_placeholder_text
from ..tools.manuscript import _extract_latex_cites, _extract_bib_keys, has_formal_citation
from ..tools.survey_tools import (
    _SURVEY_MIN_PLAIN_CHARS,
    SURVEY_SECTION_MIN_CITATIONS,
    SURVEY_SECTION_SEQUENCE,
    SURVEY_SECTION_TITLES,
    _language_profile,
    _plain_latex_text,
    _SURVEY_RUNTIME_PROCESS_RE,
    _survey_section_id_for_heading,
    _survey_state_writing_language,
    _survey_internal_alignment_hits,
    _survey_section_quality_issues,
)
from ._common import ensure_seed_outline_profile, load_jsonl, load_project, prepend_resume_prefix, read_text_file


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
                        "bind_survey_review",
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
                        "user_seeds/",
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
        ensure_seed_outline_profile(ws)
        project = load_project(ctx)
        phase = self._phase(ctx)
        section_id = self._section_id(ctx)
        section_outline = (
            read_text_file(ws / "drafts" / "survey" / "section_outlines" / f"{section_id}.md", default="")
            if section_id
            else ""
        )
        seed_outline_profile = read_text_file(ws / "user_seeds" / "seed_outline_profile.json", default="")
        seed_ideas = read_text_file(ws / "user_seeds" / "seed_ideas.md", default="")
        seed_constraints = read_text_file(ws / "user_seeds" / "seed_constraints.md", default="")
        if is_placeholder_text(seed_ideas):
            seed_ideas = ""
        if is_placeholder_text(seed_constraints):
            seed_constraints = ""
        seed_papers = load_jsonl(ws / "user_seeds" / "seed_papers.jsonl")
        external_resources = load_jsonl(ws / "user_seeds" / "seed_external_resources.jsonl")
        related_work_bib_path = ws / "literature" / "related_work.bib"
        related_work_keys = _extract_bib_keys(related_work_bib_path)
        citation_pool_preview = _citation_pool_preview(ws, related_work_keys)
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
            writing_template_preview=read_text_file(ws / "drafts" / "survey" / "writing_template.json", default="")[:2000],
            survey_state_preview=read_text_file(ws / "drafts" / "survey" / "survey_state.json", default="")[:7000],
            corpus_decision_preview=read_text_file(ws / "drafts" / "survey" / "corpus_decision.json", default="")[:2000],
            survey_audit_preview=read_text_file(ws / "drafts" / "survey" / "survey_audit.md", default="")[:3000],
            section_outline_preview=section_outline[:5000],
            related_work_preview=read_text_file(related_work_bib_path, default="")[:3000],
            related_work_keys=related_work_keys,
            related_work_key_count=len(related_work_keys),
            citation_pool_preview=citation_pool_preview,
            seed_outline_profile_preview=seed_outline_profile[:7000],
            has_seed_outline_profile=bool(seed_outline_profile.strip()),
            seed_ideas_preview=seed_ideas[:3000],
            has_seed_ideas=bool(seed_ideas.strip()),
            seed_constraints_preview=seed_constraints[:2000],
            has_seed_constraints=bool(seed_constraints.strip()),
            seed_papers_preview=seed_papers[:10],
            seed_paper_count=len(seed_papers),
            external_resources_preview=external_resources[:12],
            external_resource_count=len(external_resources),
            has_external_resources=bool(external_resources),
        )

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        phase = self._phase(ctx)
        if phase == "survey_gate":
            message = (
                "请执行 T3.6 Gate-1：询问用户是否撰写综述论文。"
                "必须调用 ask_human；若没有人工输入，暂停等待 resume，不要写伪默认值。"
                "结果写入 drafts/survey/decision.json。"
            )
        elif phase == "template_gate":
            message = (
                "请执行 T3.6 Template Gate：询问用户选择综述写作语言与 LaTeX 模板。"
                "选项包括 basic_zh、basic_en、ccf(默认 neurips)、utd(默认 informs) 或 other。"
                "结果写入 drafts/survey/writing_template.json；没有真实人工输入时暂停等待 resume。"
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
                "请执行 T3.6-COMPILE：根据 survey_state 写作语言调用 latex_compile 编译 survey PDF；"
                "中文稿使用 engine=\"xelatex\"，英文稿使用 engine=\"pdflatex\"。latex_compile 会自动写 "
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
        if phase == "template_gate":
            data, err = _load_json(ws / "drafts" / "survey" / "writing_template.json")
            if err:
                return False, err
            template_err = _validate_survey_template_selection(data)
            if template_err:
                return False, template_err
            interaction_id = str(data.get("human_interaction_id") or "").strip()
            if not interaction_id:
                return False, "writing_template.json 必须包含 ask_human 返回的 human_interaction_id"
            if not _human_interaction_exists(ws, interaction_id):
                return False, "writing_template.json human_interaction_id 未在 _runtime/human_interactions.jsonl 中找到"
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
            assembly_manifest, err = _load_json(ws / "drafts" / "survey" / "survey_assembly_manifest.json")
            if err:
                return False, err
            if assembly_manifest.get("semantics") != "survey_assembly_input_fingerprints":
                return False, "survey_assembly_manifest.json semantics 不正确"
            ok, err = _validate_fingerprint_map(ws, assembly_manifest.get("input_fingerprints"), "survey_assembly_manifest.json")
            if not ok:
                return False, err
            audit, err = _load_json(ws / "drafts" / "survey" / "survey_audit.json")
            if err:
                return False, err
            if audit.get("semantics") != "deterministic_survey_coverage_audit_not_scientific_judgment":
                return False, "survey_audit.json semantics 不正确"
            ok, err = _validate_fingerprint_map(ws, audit.get("input_fingerprints"), "survey_audit.json")
            if not ok:
                return False, err
            required_checks = {
                "section_level_citation_density",
                "no_runtime_process_prose",
                "bibliography_quality",
            }
            present_checks = {
                str(item.get("name") or "")
                for item in audit.get("checks") or []
                if isinstance(item, dict)
            }
            missing_checks = sorted(required_checks - present_checks)
            if missing_checks:
                return False, "survey_audit.json 缺少新增质量检查，请重新运行 audit_survey_coverage: " + ", ".join(missing_checks)
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
                "Review Contribution",
                "Language",
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
            audit, audit_err = _load_json(ws / "drafts" / "survey" / "survey_audit.json")
            if audit_err:
                return False, audit_err
            if audit.get("passed") is not True:
                return False, "survey_review 不能通过：survey_audit.json 仍未通过"
            language_review_err = _validate_survey_review_language_gate(review, actions)
            if language_review_err:
                return False, language_review_err
            ok, err = _validate_fingerprint_map(ws, actions.get("input_fingerprints"), "survey_review_actions.json")
            if not ok:
                return False, err
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
            for key in ("main_tex_sha256", "pdf_sha256", "log_sha256"):
                if not str(report.get(key) or "").strip():
                    return False, f"survey_compile_report 缺少 {key}，需重新编译"
            if report.get("main_tex_sha256") != _sha256_file(ws / "drafts" / "survey" / "survey.tex"):
                return False, "survey_compile_report.main_tex_sha256 与当前 survey.tex 不一致，需重新编译"
            if report.get("pdf_sha256") != _sha256_file(pdf_path):
                return False, "survey_compile_report.pdf_sha256 与当前 survey.pdf 不一致"
            if report.get("log_sha256") != _sha256_file(log_path):
                return False, "survey_compile_report.log_sha256 与当前 survey.log 不一致"
            dependency = _compile_dependency_fingerprint(ws, ws / "drafts" / "survey" / "survey.tex")
            report_dependency = report.get("dependency_fingerprint") if isinstance(report.get("dependency_fingerprint"), dict) else {}
            report_dependency_hash = str(report_dependency.get("hash") or report.get("dependency_fingerprint_hash") or "").strip()
            if not report_dependency_hash:
                return False, "survey_compile_report.dependency_fingerprint 缺失，需重新编译"
            if report_dependency_hash != dependency.get("hash"):
                return False, "survey_compile_report.dependency_fingerprint 与当前 survey 依赖不一致，需重新编译"
            attempts = report.get("attempts")
            if isinstance(attempts, list) and attempts:
                last_attempt = attempts[-1] if isinstance(attempts[-1], dict) else {}
                attempt_dependency_hash = str(last_attempt.get("dependency_fingerprint_hash") or "").strip()
                if attempt_dependency_hash and attempt_dependency_hash != dependency.get("hash"):
                    return False, "survey_compile_report 最后一次 attempt 的 dependency_fingerprint_hash 过期，需重新编译"
            if float(report.get("pdf_mtime") or 0) <= 0:
                return False, "survey_compile_report 缺少 pdf_mtime，需重新编译"
            if float(report.get("pdf_mtime") or 0) < (ws / "drafts" / "survey" / "survey.tex").stat().st_mtime:
                return False, "survey_compile_report.pdf_mtime 早于当前 survey.tex，需重新编译"
            ok, err = _validate_current_survey_audit(ws)
            if not ok:
                return False, err
            ok, err = _validate_survey_compile_log(log_path)
            if not ok:
                return False, err
            ok, err = _validate_survey_pdf(pdf_path)
            if not ok:
                return False, err
            return True, None
        if phase == "survey_feed":
            data, err = _load_json(ws / "ideation" / "survey_insights.json")
            if err:
                return False, err
            if data.get("semantics") != "survey_insights_optional_ideation_fuel_not_gate":
                return False, "survey_insights.json semantics 不正确"
            if ((data.get("audit_summary") or {}).get("passed")) is not True:
                return False, "survey_insights.json 只能从已通过 audit 的 survey 导出"
            ok, err = _validate_survey_insights_fingerprints(ws, data)
            if not ok:
                return False, err
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


def _validate_survey_template_selection(data: dict) -> str | None:
    family = str(data.get("template_family") or data.get("template_type") or "").strip().lower()
    template_id = str(data.get("template_id") or "").strip().lower()
    language = str(data.get("writing_language") or "").strip().lower()
    if family not in {"basic_zh", "basic_en", "ccf", "utd", "other"}:
        return "writing_template.json template_family 必须是 basic_zh/basic_en/ccf/utd/other"
    if language not in {"zh", "en"}:
        return "writing_template.json writing_language 必须是 zh 或 en"
    if not template_id:
        return "writing_template.json 必须包含 template_id"
    if family == "ccf" and template_id == "auto":
        return "writing_template.json CCF 模板需明确 template_id，默认应为 neurips"
    if family == "utd" and template_id == "auto":
        return "writing_template.json UTD 模板需明确 template_id，默认应为 informs"
    return None


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


def _citation_pool_preview(ws: Path, related_work_keys: list[str], *, max_items: int = 80) -> str:
    bib_entries = _parse_bib_preview(ws / "literature" / "related_work.bib")
    quality_by_id = _notes_quality_by_id(ws / "literature" / "notes_manifest.json")
    plan, _ = _load_json(ws / "drafts" / "survey" / "survey_plan.json")
    section_paper_ids = _survey_plan_paper_ids(plan)
    lines = [
        "# Citation Pool",
        "",
        "Use exact BibTeX keys. Prefer entries marked core/supporting and avoid weak/do_not_cite entries for mechanism claims.",
    ]
    for idx, key in enumerate(related_work_keys[:max_items], start=1):
        meta = bib_entries.get(key, {})
        quality = quality_by_id.get(key) or quality_by_id.get(str(meta.get("title", "")).casefold()) or {}
        title = str(meta.get("title") or "").strip() or "title unavailable"
        year = str(meta.get("year") or "").strip() or "year?"
        venue = str(meta.get("venue") or "").strip()
        flags: list[str] = []
        use = str(quality.get("citation_use") or "").strip()
        score = quality.get("citation_quality_score")
        if use:
            flags.append(f"use={use}")
        if score not in (None, ""):
            flags.append(f"score={score}")
        for section_id, paper_ids in section_paper_ids.items():
            if key in paper_ids:
                flags.append(f"planned_for={section_id}")
        if "abstract-only" in str(meta.get("note") or "").casefold() or "metadata-only" in str(meta.get("note") or "").casefold():
            flags.append("weak_context_only")
        suffix = f" ({'; '.join(flags)})" if flags else ""
        venue_part = f", {venue}" if venue else ""
        lines.append(f"{idx}. `{key}`: {title} ({year}{venue_part}){suffix}")
    if len(related_work_keys) > max_items:
        lines.append(f"... {len(related_work_keys) - max_items} more keys omitted from preview; read related_work.bib if needed.")
    return "\n".join(lines)


def _parse_bib_preview(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    entries: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"@(\w+)\s*\{\s*([^,\s{}]+)\s*,(.*?)(?=^@\w+\s*\{|\Z)", text, flags=re.DOTALL | re.MULTILINE):
        key = match.group(2).strip()
        body = match.group(3)
        fields = {
            field.group(1).lower(): re.sub(r"\s+", " ", field.group(2)).strip()
            for field in re.finditer(r"(?ims)^\s*([A-Za-z][A-Za-z0-9_-]*)\s*=\s*\{(.*?)\}\s*,?", body)
        }
        venue = fields.get("journal") or fields.get("booktitle") or fields.get("publisher") or ""
        entries[key] = {
            "title": fields.get("title", ""),
            "year": fields.get("year", ""),
            "venue": venue,
            "note": fields.get("note", ""),
        }
    return entries


def _notes_quality_by_id(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for item in data.get("entries") or [] if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        for key in (
            item.get("canonical_id"),
            item.get("paper_id"),
            item.get("bib_key"),
            str(item.get("title") or "").casefold(),
        ):
            key_str = str(key or "").strip()
            if key_str:
                out[key_str] = item
    return out


def _survey_plan_paper_ids(plan: dict) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for item in plan.get("outline") or [] if isinstance(plan, dict) else []:
        if not isinstance(item, dict):
            continue
        section_id = str(item.get("section_id") or "").strip()
        if not section_id:
            continue
        out.setdefault(section_id, set()).update(str(pid).strip() for pid in item.get("paper_ids") or [] if str(pid).strip())
    taxonomy = plan.get("taxonomy") if isinstance(plan.get("taxonomy"), dict) else {}
    for item in taxonomy.get("tree") or []:
        if not isinstance(item, dict):
            continue
        for pid in item.get("paper_ids") or []:
            pid_str = str(pid).strip()
            if pid_str:
                out.setdefault("taxonomy", set()).add(pid_str)
                out.setdefault("comparison", set()).add(pid_str)
    return out


def _validate_survey_plan(path: Path) -> tuple[bool, str | None]:
    data, err = _load_json(path)
    if err:
        return False, err
    ws = path.parents[2] if len(path.parents) >= 3 else path.parent
    if data.get("semantics") != "llm_authored_taxonomy_driven_survey_plan":
        return False, "survey_plan.json semantics 必须是 llm_authored_taxonomy_driven_survey_plan"
    writing_language = str(data.get("writing_language") or "").strip().lower()
    if writing_language not in {"zh", "en"}:
        return False, "survey_plan.json 顶层 writing_language 必须是 zh 或 en；双语输入不等于混合正文"
    template_path = path.parent / "writing_template.json"
    if template_path.exists():
        template, template_err = _load_json(template_path)
        if template_err:
            return False, template_err
        selected_language = str(template.get("writing_language") or "").strip().lower()
        if selected_language in {"zh", "en"} and writing_language != selected_language:
            return False, "survey_plan.json writing_language 必须与 writing_template.json 一致"
        template_selection = data.get("template_selection")
        if not isinstance(template_selection, dict) or _validate_survey_template_selection({**template, **template_selection}):
            return False, "survey_plan.json 必须包含与 writing_template.json 一致的 template_selection"
        for field in ("template_family", "template_id", "writing_language"):
            if str(template_selection.get(field) or "").strip().lower() != str(template.get(field) or "").strip().lower():
                return False, f"survey_plan.json template_selection.{field} 必须与 writing_template.json 一致"
    central_question = str(data.get("central_question") or data.get("review_question") or "").strip()
    if len(central_question) < 20:
        return False, "survey_plan.json 必须包含明确 central_question/review_question，不能只写主题名"
    scope = data.get("scope_boundaries")
    if not isinstance(scope, dict) or not (
        scope.get("included") or scope.get("include") or scope.get("excluded") or scope.get("exclude")
    ):
        return False, "survey_plan.json 必须包含 scope_boundaries，说明纳入与排除边界"
    contribution = str(data.get("review_contribution") or data.get("theoretical_contribution") or "").strip()
    quality_plan = data.get("quality_plan") if isinstance(data.get("quality_plan"), dict) else {}
    if len(contribution) < 20 and not quality_plan.get("theoretical_lift"):
        return False, "survey_plan.json 必须说明 review_contribution 或 quality_plan.theoretical_lift"
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
    ok, sectioning_err = _validate_plan_sectioning_policy(data)
    if not ok:
        return False, sectioning_err
    section_ids = {str(item.get("section_id") or "").lower() for item in outline if isinstance(item, dict)}
    for required in ("background", "taxonomy", "comparison"):
        if required not in section_ids and not any(required in sid for sid in section_ids):
            return False, f"survey_plan.json outline 缺少 {required}"
    weak_outline = []
    for item in outline:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("section_id") or "").strip()
        if sid not in {"background", "taxonomy", "comparison", "challenges", "future"}:
            continue
        section_argument = " ".join(
            str(item.get(key) or "")
            for key in ("section_argument", "reader_question", "function", "covers_rationale", "rationale")
        ).strip()
        if len(section_argument) < 20:
            weak_outline.append(sid)
    if weak_outline:
        return False, "survey_plan.json outline 中这些章节缺少 section_argument/reader_question: " + ", ".join(weak_outline)
    selfcheck = data.get("coverage_selfcheck")
    if not isinstance(selfcheck, dict):
        return False, "survey_plan.json 缺少 coverage_selfcheck"
    return True, None


def _validate_plan_sectioning_policy(data: dict) -> tuple[bool, str | None]:
    raw_policy = data.get("sectioning_policy")
    outline = data.get("outline") if isinstance(data.get("outline"), list) else []
    theme_sections = [
        str(item.get("section_id") or "").strip()
        for item in outline
        if isinstance(item, dict)
        and (
            str(item.get("section_id") or "").strip().lower().startswith("theme")
            or "theme" in str(item.get("section_id") or "").strip().lower()
        )
    ]
    if raw_policy is None:
        return False, (
            "survey_plan.json 缺少 sectioning_policy。默认应写 compact，并把 taxonomy 类放在 "
            "Taxonomy/Comparative Analysis 内部；只有用户明确要求长综述时才允许 standalone theme 章。"
        )
    mode = ""
    max_theme_sections = 0
    rationale = ""
    if isinstance(raw_policy, str):
        mode = raw_policy.strip().lower()
    elif isinstance(raw_policy, dict):
        mode = str(raw_policy.get("mode") or raw_policy.get("sectioning_policy") or "").strip().lower()
        rationale = str(raw_policy.get("rationale") or "").strip()
        try:
            max_theme_sections = int(raw_policy.get("max_theme_sections") or raw_policy.get("theme_section_limit") or 0)
        except (TypeError, ValueError):
            max_theme_sections = 0
    else:
        return False, "survey_plan.json sectioning_policy 必须是字符串或对象"

    compact_modes = {
        "compact",
        "compact_survey",
        "compact_survey_default_taxonomy_classes_inside_taxonomy_and_comparison",
    }
    standalone_modes = {
        "standalone_theme_sections",
        "standalone_theme_sections_enabled",
        "allow_theme_sections",
        "long_survey_with_theme_sections",
    }
    if mode in compact_modes:
        if theme_sections:
            return False, (
                "survey_plan.json 使用 compact sectioning_policy，但 outline 仍包含独立 theme 章节: "
                + ", ".join(theme_sections[:8])
                + "。请把它们合并进 taxonomy/comparison 的小节或段落。"
            )
        return True, None
    if mode in standalone_modes:
        if max_theme_sections < 1:
            max_theme_sections = 1
        if len(theme_sections) > max_theme_sections:
            return False, (
                f"survey_plan.json 独立 theme 章节数 {len(theme_sections)} 超过 sectioning_policy.max_theme_sections={max_theme_sections}"
            )
        if not rationale:
            return False, "启用 standalone theme sections 时 sectioning_policy.rationale 不能为空"
        return True, None
    return False, (
        "survey_plan.json sectioning_policy.mode 不清楚；请使用 compact 或 standalone_theme_sections。"
    )


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


def _validate_survey_insights_fingerprints(ws: Path, data: dict) -> tuple[bool, str | None]:
    fingerprints = data.get("input_fingerprints")
    if not isinstance(fingerprints, dict):
        return False, "survey_insights.json 缺少 input_fingerprints，必须重新导出"
    required = {
        "survey_plan": "drafts/survey/survey_plan.json",
        "survey_state": "drafts/survey/survey_state.json",
        "survey_audit": "drafts/survey/survey_audit.json",
        "survey_tex": "drafts/survey/survey.tex",
    }
    for label, default_rel in required.items():
        item = fingerprints.get(label)
        if not isinstance(item, dict):
            return False, f"survey_insights.json input_fingerprints 缺少 {label}"
        rel = str(item.get("path") or default_rel).strip()
        path = ws / rel
        if item.get("exists") is not True or not path.exists():
            return False, f"survey_insights input 不存在: {rel}"
        expected = str(item.get("sha256") or "").strip()
        if not expected:
            return False, f"survey_insights input_fingerprints.{label} 缺少 sha256"
        if path.is_file() and _sha256_file(path) != expected:
            return False, f"survey_insights 对应的 {label} 已过期，必须重新导出"
    return True, None


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _survey_weak_evidence_ids(ws: Path) -> set[str]:
    weak: set[str] = set()
    abstract_dir = ws / "literature" / "paper_notes_abstract"
    if abstract_dir.exists():
        weak.update(path.stem for path in abstract_dir.glob("*.md") if is_paper_note_file(path))
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
    ok, err = _validate_fingerprint_map(ws, data.get("input_fingerprints"), "survey_state.json")
    if not ok:
        return False, err
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
        outline_text = outline_path.read_text(encoding="utf-8", errors="replace")
        if "Section Writing Contract" not in outline_text:
            return False, f"survey section outline 缺少 Section Writing Contract: {section_id}，请重建 T3.6-STATE"
        if not isinstance(entry.get("writing_contract"), dict) or not entry.get("writing_contract"):
            return False, f"survey_state.sections.{section_id}.writing_contract 缺失，请重建 T3.6-STATE"
    shared = data.get("shared_facts")
    if not isinstance(shared, dict) or not isinstance(shared.get("taxonomy_classes"), list):
        return False, "survey_state.json shared_facts.taxonomy_classes 必须是列表"
    sectioning_policy = str(shared.get("sectioning_policy") or "")
    if sectioning_policy.startswith("compact_survey"):
        contract = shared.get("theme_coverage_contract")
        if not isinstance(contract, dict) or contract.get("mode") != "compact_theme_slots_skipped_content_must_be_absorbed":
            return False, "survey_state.json 缺少 compact theme coverage contract，请重建 T3.6-STATE"
        for section_id in ("taxonomy", "comparison"):
            entry = sections.get(section_id) if isinstance(sections, dict) else {}
            if not isinstance(entry, dict) or entry.get("absorbs_theme_content") is not True:
                return False, f"survey_state.sections.{section_id} 缺少 absorbs_theme_content=true，请重建 T3.6-STATE"
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
        if not section_id.startswith("theme_"):
            return False, f"survey section {section_id} 是关键章节，不能标记为 skipped"
        return True, None
    path = ws / "drafts" / "survey" / "sections" / f"{section_id}.tex"
    if not path.exists():
        return False, f"缺少章节草稿: drafts/survey/sections/{section_id}.tex"
    text = path.read_text(encoding="utf-8", errors="replace")
    if "\\documentclass" in text or "\\begin{document}" in text or "\\end{document}" in text:
        return False, f"survey section {section_id} 不能包含完整 LaTeX wrapper"
    if section_id != "abstract" and not _has_current_section_heading(text, section_id, state):
        return False, f"survey section {section_id} 缺少本节 \\section{{...}} 标题"
    placeholder_hits = _placeholder_hits(text)
    if placeholder_hits:
        return False, f"survey section {section_id} 仍包含 planning placeholder: {', '.join(placeholder_hits[:8])}"
    internal_hits = _survey_internal_alignment_hits(text)
    if internal_hits:
        return False, (
            f"survey section {section_id} 暴露内部 ResearchOS/CID 标记: "
            + ", ".join(internal_hits[:8])
        )
    if section_id == "abstract" and has_formal_citation(text):
        return False, "survey abstract 不应包含正式引用命令、作者-年份括号引用或数字引用"
    if section_id == "abstract" and re.search(r"\\(?:begin|end)\{abstract\}", text, flags=re.IGNORECASE):
        return False, "survey abstract 文件应只包含摘要正文，不应包含 \\begin{abstract} 或 \\end{abstract}"
    if section_id == "abstract" and re.search(r"\\(?:section|subsection)\*?\{", text, flags=re.IGNORECASE):
        return False, "survey abstract 文件应只包含摘要正文，不应包含 \\section 或 \\subsection 标题"
    process_hits = _survey_runtime_process_hits(text)
    if process_hits:
        return False, (
            f"survey section {section_id} 暴露内部检索/运行过程术语: "
            + ", ".join(process_hits[:8])
        )
    language = _survey_state_writing_language(state, ws)
    lang_err = _validate_section_language_and_depth(section_id, text, language)
    if lang_err:
        return False, lang_err
    bib_keys = set(_extract_bib_keys(ws / "literature" / "related_work.bib"))
    cited = _extract_latex_cites(text)
    if cited and bib_keys:
        missing_cites = sorted(cited - bib_keys)
        if missing_cites:
            repaired = _repair_near_miss_citation_keys(path, missing_cites, bib_keys)
            if repaired:
                text = path.read_text(encoding="utf-8", errors="replace")
                cited = _extract_latex_cites(text)
                missing_cites = sorted(cited - bib_keys)
            if missing_cites:
                suggestions = _citation_key_suggestions(missing_cites, bib_keys)
                suffix = f"；可能候选: {suggestions}" if suggestions else ""
                return False, f"survey section {section_id} 引用了 related_work.bib 不存在的 key: {missing_cites[:8]}{suffix}"
    elif cited and not bib_keys:
        return False, f"survey section {section_id} 含引用但 literature/related_work.bib 缺失或无 BibTeX key"
    citation_err = _validate_section_citation_density(section_id, cited)
    if citation_err:
        return False, citation_err
    foreign = _foreign_survey_headers(text, section_id, state)
    if foreign:
        return False, f"survey section {section_id} 夹带其它章节: {', '.join(foreign[:5])}"
    if section_id != "abstract":
        craft_hits = _survey_section_craft_issues(text)
        if craft_hits:
            return False, f"survey section {section_id} 写作结构问题: {', '.join(craft_hits[:4])}"
    if isinstance(entry, dict) and entry.get("status") not in {"written", "revised"}:
        return False, f"survey_state 中 {section_id} 未标记 written/revised"
    ok, err = _validate_section_fingerprints(ws, state, section_id, entry)
    if not ok:
        return False, err
    return True, None


def _validate_section_citation_density(section_id: str, cited: set[str]) -> str | None:
    if section_id in {"abstract", "conclusion"} or section_id.startswith("theme_"):
        return None
    minimum = SURVEY_SECTION_MIN_CITATIONS.get(section_id, 0)
    if minimum and len(cited) < minimum:
        return f"survey section {section_id} 引用过少: unique citations={len(cited)} < {minimum}"
    return None


def _survey_runtime_process_hits(text: str) -> list[str]:
    plain = _plain_latex_text(text)
    return sorted({match.group(0).strip() for match in _SURVEY_RUNTIME_PROCESS_RE.finditer(plain)})


def _validate_section_language_and_depth(section_id: str, text: str, language: str) -> str | None:
    profile = _language_profile(text)
    min_chars = _SURVEY_MIN_PLAIN_CHARS.get(section_id, {"en": 600, "zh": 800})
    metric = profile["cjk_chars"] if language == "zh" else len(_plain_latex_text(text))
    required = min_chars.get(language, 600)
    if metric < required:
        return f"survey section {section_id} 篇幅不足: {metric} < {required} ({language})"
    if language == "zh" and profile["latin_words"] > max(80, profile["cjk_chars"] * 0.35):
        return f"survey section {section_id} 语言不一致：中文稿中英文内容过多"
    if language == "en" and profile["cjk_chars"] > max(40, profile["latin_words"] * 1.5):
        return f"survey section {section_id} 语言不一致：英文稿中中文内容过多"
    quality_issues = _survey_section_quality_issues(section_id, text)
    if quality_issues:
        return f"survey section {section_id} 综述论证结构不足: {', '.join(quality_issues[:3])}"
    return None


def _has_current_section_heading(text: str, section_id: str, state: dict | None = None) -> bool:
    expected_ids = {section_id}
    for match in re.finditer(r"\\section\*?\{([^{}]+)\}", text or "", flags=re.IGNORECASE):
        detected = _survey_section_id_for_heading(match.group(1), state)
        if detected in expected_ids:
            return True
    return False


def _validate_survey_review_language_gate(review: str, actions: dict) -> str | None:
    lowered = review.casefold()
    if "bilingual consistency" in lowered or "语言" in review or "中英" in review:
        if re.search(r"(?is)(bilingual consistency|语言|中英)[\s\S]{0,240}\bLOW\b", review):
            return "survey_review 把语言一致性问题降为 LOW；语言混杂必须作为 blocking issue 修复"
    for item in actions.get("section_actions") or []:
        if not isinstance(item, dict):
            continue
        issue = str(item.get("issue") or "")
        evidence = str(item.get("evidence") or "")
        if re.search(r"语言|中英|bilingual|language consistency", issue + " " + evidence, flags=re.IGNORECASE):
            if str(item.get("severity") or "").lower() == "low" or str(item.get("action_taken") or "") == "no_change_needed":
                return "survey_review_actions 把语言一致性问题标为低风险/无需修改；必须修复后才能通过"
    return None


def _repair_near_miss_citation_keys(path: Path, missing: list[str], bib_keys: set[str]) -> bool:
    replacements: dict[str, str] = {}
    for key in missing:
        replacement = _unique_close_bib_key(key, bib_keys)
        if not replacement:
            return False
        replacements[key] = replacement
    original = path.read_text(encoding="utf-8", errors="replace")
    repaired = _replace_latex_cite_keys(original, replacements)
    if repaired == original:
        return False
    path.write_text(repaired, encoding="utf-8")
    return True


def _unique_close_bib_key(key: str, bib_keys: set[str]) -> str | None:
    if len(key) < 8:
        return None
    scored: list[tuple[float, str]] = []
    key_folded = key.casefold()
    for candidate in bib_keys:
        candidate_folded = candidate.casefold()
        ratio = SequenceMatcher(None, key_folded, candidate_folded).ratio()
        if ratio >= 0.90:
            scored.append((ratio, candidate))
    scored.sort(reverse=True)
    if not scored:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.025:
        return None
    return scored[0][1]


_LATEX_CITE_WITH_KEYS_RE = re.compile(
    r"(\\(?:cite|citep|citet|citealp|citealt|citeauthor|citeyear|parencite|textcite|autocite|footcite|supercite)\*?"
    r"(?:\[[^\]]*\]){0,2}\{)([^}]+)(\})",
    flags=re.IGNORECASE,
)


def _replace_latex_cite_keys(text: str, replacements: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        prefix, keys, suffix = match.groups()
        parts = [part.strip() for part in keys.split(",")]
        new_parts = [replacements.get(part, part) for part in parts]
        return prefix + ",".join(new_parts) + suffix

    return _LATEX_CITE_WITH_KEYS_RE.sub(repl, text)


def _citation_key_suggestions(missing: list[str], bib_keys: set[str]) -> dict[str, list[str]]:
    suggestions: dict[str, list[str]] = {}
    for key in missing[:8]:
        scored = sorted(
            (
                (SequenceMatcher(None, key.casefold(), candidate.casefold()).ratio(), candidate)
                for candidate in bib_keys
            ),
            reverse=True,
        )
        close = [candidate for score, candidate in scored[:3] if score >= 0.78]
        if close:
            suggestions[key] = close
    return suggestions


def _validate_section_fingerprints(
    ws: Path,
    state: dict,
    section_id: str,
    entry: dict,
) -> tuple[bool, str | None]:
    fingerprints = entry.get("input_fingerprints")
    if not isinstance(fingerprints, dict):
        return False, f"survey_state.sections.{section_id} 缺少 input_fingerprints，必须重新生成"
    outline = fingerprints.get("section_outline")
    if isinstance(outline, dict):
        ok, err = _validate_fingerprint_map(
            ws,
            {"section_outline": outline},
            f"survey_state.sections.{section_id}",
        )
        if not ok:
            return False, err
    section_file = fingerprints.get("section_file")
    if isinstance(section_file, dict):
        ok, err = _validate_fingerprint_map(
            ws,
            {"section_file": section_file},
            f"survey_state.sections.{section_id}",
        )
        if not ok:
            if _refresh_section_file_fingerprint(ws, state, section_id, section_file):
                return True, None
            return False, err
    return True, None


def _refresh_section_file_fingerprint(
    ws: Path,
    state: dict,
    section_id: str,
    item: dict,
) -> bool:
    rel = str(item.get("path") or f"drafts/survey/sections/{section_id}.tex").strip()
    path = ws / rel
    if not path.exists() or not path.is_file():
        return False
    sections = state.get("sections")
    if not isinstance(sections, dict) or not isinstance(sections.get(section_id), dict):
        return False
    fingerprints = sections[section_id].setdefault("input_fingerprints", {})
    if not isinstance(fingerprints, dict):
        return False
    fingerprints["section_file"] = {
        "path": rel,
        "exists": True,
        "sha256": _sha256_file(path),
        "kind": "file",
    }
    state_path = ws / "drafts" / "survey" / "survey_state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


def _placeholder_hits(text: str) -> list[str]:
    return sorted(set(re.findall(r"\b(?:TODO|TBD|LLM_REVIEW_REQUIRED|PLACEHOLDER)\b", text or "", flags=re.IGNORECASE)))


def _survey_section_craft_issues(text: str) -> list[str]:
    issues: list[str] = []
    lowered = text.lower()
    if lowered.count("this section reviews") + lowered.count("this section discusses") >= 2:
        issues.append("重复空泛 section 开场")
    sentences = re.split(r"(?<=[.!?])\s+", text)
    etal_sentences = sum(1 for sentence in sentences if re.search(r"\bet al\.", sentence))
    comparison_signals = len(
        re.findall(
            r"\b(whereas|however|contrast|tension|boundary|mechanism|taxonomy|challenge|future direction)\b|"
            r"相比|然而|机制|边界|分类|张力|挑战|方向",
            text,
            flags=re.IGNORECASE,
        )
    )
    if etal_sentences >= 5 and comparison_signals < 2:
        issues.append("疑似逐篇论文流水账")
    if len(re.findall(r"\\subsubsection\*?\{", text)) > 0:
        issues.append("综述 section 不应使用过细 subsubsection")
    return issues


def _validate_fingerprint_map(ws: Path, fingerprints: object, label: str) -> tuple[bool, str | None]:
    if not isinstance(fingerprints, dict):
        return False, f"{label} 缺少 input_fingerprints，必须重新生成"
    for key, item in fingerprints.items():
        if not isinstance(item, dict):
            return False, f"{label}.input_fingerprints.{key} 必须是对象"
        rel = str(item.get("path") or "").strip()
        if not rel:
            return False, f"{label}.input_fingerprints.{key} 缺少 path"
        path = ws / rel
        expected_exists = bool(item.get("exists"))
        if expected_exists != path.exists():
            return False, f"{label} 对应输入存在性已变化: {rel}"
        if not expected_exists:
            continue
        if item.get("kind") == "dir" or path.is_dir():
            expected_count = item.get("file_count")
            if expected_count is not None and int(expected_count) != len([child for child in path.rglob("*") if child.is_file()]):
                return False, f"{label} 对应目录文件数已变化: {rel}"
            expected_sha = str(item.get("sha256") or "").strip()
            if expected_sha and _sha256_dir(path) != expected_sha:
                return False, f"{label} 对应目录内容已变化: {rel}"
            continue
        expected_sha = str(item.get("sha256") or "").strip()
        if not expected_sha:
            return False, f"{label}.input_fingerprints.{key} 缺少 sha256"
        if not path.is_file() or _sha256_file(path) != expected_sha:
            return False, f"{label} 对应输入已过期: {rel}"
    return True, None


def _sha256_dir(root: Path) -> str:
    import hashlib

    children = [child for child in root.rglob("*") if child.is_file()]
    digest = hashlib.sha256()
    for child in sorted(children, key=lambda p: p.relative_to(root).as_posix()):
        rel = child.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(str(child.stat().st_size).encode("ascii"))
            digest.update(b"\0")
            with child.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            digest.update(b"<unreadable>")
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_current_survey_audit(ws: Path) -> tuple[bool, str | None]:
    audit, err = _load_json(ws / "drafts" / "survey" / "survey_audit.json")
    if err:
        return False, "T3.6-COMPILE 前必须有当前 survey_audit.json: " + err
    if audit.get("semantics") != "deterministic_survey_coverage_audit_not_scientific_judgment":
        return False, "survey_audit.json semantics 不正确"
    if audit.get("passed") is not True:
        return False, "survey_audit.json 未通过，不能编译放行"
    ok, err = _validate_fingerprint_map(ws, audit.get("input_fingerprints"), "survey_audit.json")
    if not ok:
        return False, "survey_audit.json 已过期，需重新 audit_survey_coverage: " + (err or "")
    return True, None


def _validate_survey_compile_log(log_path: Path) -> tuple[bool, str | None]:
    text = read_text_file(log_path, default="")
    fatal_markers = [
        "Fatal error occurred",
        "! Emergency stop.",
        "==> Fatal error occurred",
        "LaTeX Warning: There were undefined references",
        "LaTeX Warning: Citation `",
        "Citation `",
        "Reference `",
        "undefined citations",
        "Undefined control sequence",
    ]
    for marker in fatal_markers:
        if marker in text:
            return False, f"survey.log 仍包含致命或未解析引用问题: {marker}"
    return True, None


def _validate_survey_pdf(pdf_path: Path) -> tuple[bool, str | None]:
    try:
        prefix = pdf_path.read_bytes()[:5]
    except Exception as exc:
        return False, f"survey.pdf 读取失败: {exc}"
    if prefix != b"%PDF-":
        return False, "survey.pdf 不是有效 PDF payload（缺少 %PDF header）"
    if pdf_path.stat().st_size < 64:
        return False, "survey.pdf 过小，疑似占位文件"
    return True, None


def _foreign_survey_headers(text: str, section_id: str, state: dict | None = None) -> list[str]:
    foreign = []
    for match in re.finditer(r"\\(?:section|subsection)\*?\{([^{}]+)\}", text):
        detected = _survey_section_id_for_heading(match.group(1), state)
        if not detected or detected == section_id:
            continue
        if detected in SURVEY_SECTION_TITLES:
            foreign.append(SURVEY_SECTION_TITLES[detected])
    return foreign


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
