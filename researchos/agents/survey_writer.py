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
from ..tools.latex_compile import _compile_dependency_fingerprint
from ..literature_identity import is_placeholder_text
from ..tools.manuscript import _extract_latex_cites, _extract_bib_keys, has_formal_citation
from ..tools.survey_tools import SURVEY_SECTION_SEQUENCE, SURVEY_SECTION_TITLES
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
        if not section_id.startswith("theme_"):
            return False, f"survey section {section_id} 是关键章节，不能标记为 skipped"
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
    placeholder_hits = _placeholder_hits(text)
    if placeholder_hits:
        return False, f"survey section {section_id} 仍包含 planning placeholder: {', '.join(placeholder_hits[:8])}"
    if section_id == "abstract" and has_formal_citation(text):
        return False, "survey abstract 不应包含正式引用命令、作者-年份括号引用或数字引用"
    bib_keys = set(_extract_bib_keys(ws / "literature" / "related_work.bib"))
    cited = _extract_latex_cites(text)
    if cited and bib_keys:
        missing_cites = sorted(cited - bib_keys)
        if missing_cites:
            return False, f"survey section {section_id} 引用了 related_work.bib 不存在的 key: {missing_cites[:8]}"
    elif cited and not bib_keys:
        return False, f"survey section {section_id} 含引用但 literature/related_work.bib 缺失或无 BibTeX key"
    foreign = _foreign_survey_headers(text, section_id)
    if foreign:
        return False, f"survey section {section_id} 夹带其它章节: {', '.join(foreign[:5])}"
    if isinstance(entry, dict) and entry.get("status") not in {"written", "revised"}:
        return False, f"survey_state 中 {section_id} 未标记 written/revised"
    ok, err = _validate_fingerprint_map(ws, entry.get("input_fingerprints"), f"survey_state.sections.{section_id}")
    if not ok:
        return False, err
    return True, None


def _placeholder_hits(text: str) -> list[str]:
    return sorted(set(re.findall(r"\b(?:TODO|TBD|LLM_REVIEW_REQUIRED|PLACEHOLDER)\b", text or "", flags=re.IGNORECASE)))


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
