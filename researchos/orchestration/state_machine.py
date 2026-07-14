from __future__ import annotations

"""ResearchOS 状态机解释器。

本模块负责三件事：
1. 把 `config/system_config/state_machine.yaml` 解析成 task 节点；
2. 基于 `AgentResult` 推进 `state.yaml`；
3. 在 gate / resume / iteration 这些跨 task 语义上做统一处理。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid
from typing import Any

import yaml

from ..pydantic_compat import model_dump
from ..runtime.agent import (
    AgentResult,
    BudgetOverride,
    ExecutionContext,
    LLMConfigOverride,
    ToolPolicyOverride,
)
from ..runtime.task_recovery import prepare_task_resume_artifacts
from ..runtime.artifact_fingerprints import (
    build_input_fingerprints,
    validate_input_fingerprints,
    validate_t45_fingerprint_report,
)
from ..writing_profiles import resolve_venue_writing_profile
from ..schemas.state import BudgetCumulative, GateState, StateYaml, TaskHistoryEntry
from .gate_presenter import build_presentation
from .task_io_contract import get_task_io, task_io_contract_source
from ..tools.external_experiment import (
    build_executor_selection_payload,
    patch_external_executor_files_with_selection,
    validate_external_executor_ready,
)
from ..ideation.config import load_t4_evolution_settings
from ..ideation.prerun import (
    default_run_config,
    has_current_t4_prerun_confirmation,
    inspect_t4_inputs,
    parse_t4_prerun_intent,
)
from ..ideation.target_profile import parse_target_profile_instruction, suggest_target_profile
from ..ideation.selected_compilation import (
    compile_pre_novelty_hypothesis_brief,
    selected_candidate_id_from_gate_input,
)
from ..ideation.directives import (
    current_population_context,
    parse_idea_directive,
    persist_idea_directive_confirmation,
    persist_idea_directive,
)
from ..ideation.models import CandidateDossier, IdeaDirective, PopulationSnapshot, RouteGenerationResult, ScoreReport
from ..ideation.population import build_idea_families, select_portfolio
from ..ideation.legacy_projection import project_gate1_population
from ..ideation.state import T4ArtifactStore, run_config_fingerprint


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _NativeLooseArtifact:
    """Typed-read adapter for small T4 envelope artifacts with flexible bodies."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    @classmethod
    def model_validate(cls, value: Any) -> "_NativeLooseArtifact":
        if not isinstance(value, dict):
            raise ValueError("T4 envelope artifact must be an object")
        return cls(value)


def _native_t4_action_description(directive: IdeaDirective) -> dict[str, str]:
    """Explain confirmed Gate1 operations in researcher-facing language."""

    action = directive.action
    descriptions = {
        "select_candidate": {
            "title": "Proceed with Candidate",
            "what_happens": "The selected complete Candidate is frozen as a Pre-Novelty brief. T4.5 will then check novelty and collisions before formal hypotheses or an experiment plan are compiled.",
            "estimated_time": "Usually under one minute for local compilation; T4.5 runs separately.",
            "version_policy": "The current Population, Parents, Children, and Archive are retained. You can return to this Gate later.",
            "next_stage": "Pre-Novelty compilation, then T4.5.",
        },
        "continue_evolution": {
            "title": "Run another Generation",
            "what_happens": "ResearchOS uses the active Population as Parents, creates plan-bounded Mutation/Crossover Children, scores the union independently, and writes a new Population snapshot.",
            "estimated_time": "A model-backed round; duration depends on the configured provider and Population size.",
            "version_policy": "The active Population becomes a preserved prior version and remains available through Rollback.",
            "next_stage": "Return to Gate1 with a new Portfolio; T4.5 is not entered.",
        },
        "focus_candidate": {
            "title": "Focus Evolution",
            "what_happens": "ResearchOS creates a plan-bounded Mutation Child for the selected Candidate and independently scores it against the active Population.",
            "estimated_time": "A focused model-backed operation, typically shorter than a full Generation.",
            "version_policy": "Other Candidates and all historical versions remain preserved.",
            "next_stage": "Return to Gate1 with the updated Population; T4.5 is not entered.",
        },
        "merge_candidates": {
            "title": "Create a Crossover",
            "what_happens": "ResearchOS first performs a Compatibility Check. A Child is generated only when an approved Gene Donor Map supports one coherent thesis.",
            "estimated_time": "A model-backed compatibility review and, only if approved, one child-generation and scoring pass.",
            "version_policy": "Both Parent Candidates remain unchanged and recoverable.",
            "next_stage": "Return to Gate1; T4.5 is not entered automatically.",
        },
        "compose_from_components": {
            "title": "Compose selected components",
            "what_happens": "ResearchOS checks whether the chosen hypotheses, contributions, or genes can form one coherent Candidate. It never concatenates them into a final hypothesis file.",
            "estimated_time": "A model-backed Compatibility Check; a second confirmation is required before any new Candidate is generated.",
            "version_policy": "Every source Candidate and its original components remain preserved.",
            "next_stage": "Return to Gate1 with a Compatibility Report.",
        },
        "keep_parallel": {
            "title": "Keep Ideas in parallel",
            "what_happens": "The selected complete Candidates are recorded as separate directions. No mechanism, contribution, or hypothesis is merged.",
            "estimated_time": "Local artifact update only; no model call.",
            "version_policy": "All selected and unselected Candidates remain unchanged.",
            "next_stage": "Return to Gate1; choose a direction when you are ready for a Pre-Novelty brief.",
        },
        "regenerate_route": {
            "title": "Regenerate a Route",
            "what_happens": "ResearchOS creates a new route run while preserving the prior route artifacts and Population history.",
            "estimated_time": "A model-backed generation and independent scoring operation.",
            "version_policy": "Existing route output and all Candidates remain recoverable.",
            "next_stage": "Return to Gate1 with a new Population snapshot.",
        },
        "change_target_profile": {
            "title": "Change Publication Orientation",
            "what_happens": "ResearchOS preserves the active Candidate Population and five-dimension Core Scientific Score, independently reassesses Profile Fit, and writes a new profile-revision Population snapshot.",
            "estimated_time": "One model-backed Profile Fit assessment across the active Population.",
            "version_policy": "The previous Population, score batch, Candidates, and lineage remain preserved and can be inspected or restored.",
            "next_stage": "Return to Gate1 with a Portfolio ordered for the new orientation.",
        },
        "rollback": {
            "title": "Rollback",
            "what_happens": "The active Population pointer moves to the preceding Generation and the compatibility view is rebuilt from its persisted Candidate and score artifacts.",
            "estimated_time": "Local artifact update only; no model call.",
            "version_policy": "Later Generations are not deleted and can be revisited later.",
            "next_stage": "Return to Gate1 at the restored Generation.",
        },
    }
    return descriptions.get(
        action,
        {
            "title": action.replace("_", " ").title(),
            "what_happens": "ResearchOS will preserve the request and validate its artifact boundary before changing the active Population.",
            "estimated_time": "Depends on the requested operation.",
            "version_policy": "Existing Candidate and Population artifacts remain preserved.",
            "next_stage": "Return to Gate1.",
        },
    )


def _latest_native_t4_operation_result(workspace_dir: Path) -> dict[str, Any] | None:
    """Load the compact public outcome of the most recent T4 Gate operation."""

    path = Path(workspace_dir) / "ideation" / "evolution" / "latest_operation_result.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("semantics") != "t4_native_operation_result":
        return None
    summary = " ".join(str(payload.get("summary") or "").split())
    if not summary:
        return None
    result: dict[str, Any] = {
        "title": "Latest T4 operation",
        "summary": summary,
        "kind": str(payload.get("status") or "completed"),
        "artifact": "ideation/evolution/latest_operation_result.json",
    }
    details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
    report_rel = str(details.get("compatibility_report") or "")
    if report_rel:
        try:
            report = json.loads((Path(workspace_dir) / report_rel).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = {}
        if isinstance(report, dict):
            result["composition"] = {
                "composition_id": str(details.get("composition_id") or report.get("composition_id") or ""),
                "composition_type": str(report.get("composition_type") or ""),
                "recommended_action": str(report.get("recommended_action") or ""),
                "explanation": str(report.get("explanation_for_user") or ""),
                "required_repairs": report.get("required_repairs") if isinstance(report.get("required_repairs"), list) else [],
                "gene_donor_map": (report.get("gene_donor_map") or {}).get("donors") if isinstance(report.get("gene_donor_map"), dict) else {},
                "report_path": report_rel,
            }
    return result


def _pending_native_t4_composition(workspace_dir: Path) -> dict[str, str] | None:
    """Find the newest composable plan that still awaits the second confirmation."""

    root = Path(workspace_dir) / "ideation" / "human_compositions"
    candidates: list[tuple[float, Path, dict[str, Any]]] = []
    if not root.is_dir():
        return None
    for path in root.glob("*/composition_plan.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("semantics") != "t4_human_composition_plan":
            continue
        if payload.get("status") != "awaiting_human_confirmation":
            continue
        composition_id = str(payload.get("composition_id") or "").strip()
        if composition_id:
            candidates.append((path.stat().st_mtime, path, payload))
    if not candidates:
        return None
    _mtime, path, payload = max(candidates, key=lambda item: item[0])
    return {
        "composition_id": str(payload.get("composition_id") or ""),
        "composition_plan_path": path.relative_to(workspace_dir).as_posix(),
        "compatibility_report": str(payload.get("compatibility_report") or ""),
        "population_id": str(payload.get("population_id") or ""),
    }


def _stable_json_fingerprint(payload: dict[str, Any]) -> str:
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_T36_SURVEY_GATE_INPUT_PATHS = {
    "project": "project.yaml",
    "synthesis": "literature/synthesis.md",
    "synthesis_workbench": "literature/synthesis_workbench.json",
    "domain_map": "literature/domain_map.json",
    "comparison_table": "literature/comparison_table.csv",
    "seed_outline_profile": "user_seeds/seed_outline_profile.json",
    "seed_ideas": "user_seeds/seed_ideas.md",
    "seed_constraints": "user_seeds/seed_constraints.md",
    "seed_external_resources": "user_seeds/seed_external_resources.jsonl",
}


_T36_CORPUS_GATE_INPUT_PATHS = {
    "survey_plan": "drafts/survey/survey_plan.json",
    "survey_state": "drafts/survey/survey_state.json",
    "synthesis": "literature/synthesis.md",
    "synthesis_workbench": "literature/synthesis_workbench.json",
    "domain_map": "literature/domain_map.json",
    "comparison_table": "literature/comparison_table.csv",
    "deep_read_notes": "literature/deep_read_notes",
    "shallow_read_notes": "literature/shallow_read_notes",
    "metadata_triage": "literature/metadata_triage.md",
    "related_work_bib": "literature/related_work.bib",
}


_T36_POST_SURVEY_GATE_INPUT_PATHS = {
    "project": "project.yaml",
    "survey_summary": "drafts/survey/survey_summary.md",
    "survey_compile_report": "drafts/survey/survey_compile_report.json",
    "survey_insights": "ideation/survey_insights.json",
}


_TEMPLATE_GATE_INPUT_PATHS = {
    "project": "project.yaml",
    "seed_outline_profile": "user_seeds/seed_outline_profile.json",
    "seed_ideas": "user_seeds/seed_ideas.md",
    "seed_constraints": "user_seeds/seed_constraints.md",
}


_TEMPLATE_GATE_DEFAULTS: dict[str, dict[str, str]] = {
    "basic_en": {"template_family": "basic_en", "template_id": "basic_en", "writing_language": "en"},
    "basic_zh": {"template_family": "basic_zh", "template_id": "basic_zh", "writing_language": "zh"},
    "ccf_neurips": {"template_family": "ccf", "template_id": "neurips", "writing_language": "en"},
    "ccf_iclr": {"template_family": "ccf", "template_id": "iclr", "writing_language": "en"},
    "ccf_icml": {"template_family": "ccf", "template_id": "icml", "writing_language": "en"},
    "ccf_kdd": {"template_family": "ccf", "template_id": "kdd", "writing_language": "en"},
    "utd_informs": {"template_family": "utd", "template_id": "informs", "writing_language": "en"},
    "is_informs": {
        "venue_style": "is",
        "template_family": "utd",
        "template_id": "informs",
        "writing_language": "en",
    },
    "both_basic_en": {
        "venue_style": "both",
        "template_family": "basic_en",
        "template_id": "basic_en",
        "writing_language": "en",
    },
}


_SUPPORTED_RUNTIME_TEMPLATE_IDS = {
    "basic_zh",
    "basic_en",
    "neurips",
    "iclr",
    "icml",
    "kdd",
    "informs",
}


_T2_LITERATURE_PARAM_GATE_INPUT_PATHS = {
    "project": "project.yaml",
    "seed_outline_profile": "user_seeds/seed_outline_profile.json",
    "bridge_domain_plan": "literature/bridge_domain_plan.json",
}

_T2_LITERATURE_PARAM_CONFIRM_GATE_INPUT_PATHS = {
    "project": "project.yaml",
    "literature_params": "literature/literature_params.json",
    "seed_outline_profile": "user_seeds/seed_outline_profile.json",
    "bridge_domain_plan": "literature/bridge_domain_plan.json",
}

_T2_COVERAGE_GATE_INPUT_PATHS = {
    "papers_raw": "literature/papers_raw.jsonl",
    "search_log": "literature/search_log.md",
    "missing_areas": "literature/missing_areas.md",
    "domain_map": "literature/domain_map.json",
    "access_audit": "literature/access_audit.md",
    "deep_read_queue": "literature/deep_read_queue.jsonl",
    "papers_verified": "literature/papers_verified.jsonl",
    "papers_dedup": "literature/papers_dedup.jsonl",
    "literature_params": "literature/literature_params.json",
}


_LITERATURE_PARAM_PRESETS: dict[str, dict[str, Any]] = {
    "standard_research": {
        "profile": "research_article",
        "t2_finalize": {"active_pool_max": 120},
        "reader": {
            "deep_read_min": 35,
            "deep_read_target": 35,
            "deep_read_max": 45,
            "require_deep_read_target": True,
            "abstract_sweep": {
                "lite_paper_num": 120,
                "sources": ["papers_verified", "papers_dedup"],
                "include_metadata_only": True,
                "metadata_replacement_policy": "replace_metadata_only_with_readable_backlog_when_available",
            },
        },
    },
    "survey_balanced": {
        "profile": "survey",
        "t2_finalize": {"active_pool_max": 180},
        "reader": {
            "deep_read_min": 50,
            "deep_read_target": 60,
            "deep_read_max": 70,
            "require_deep_read_target": True,
            "abstract_sweep": {
                "lite_paper_num": 120,
                "sources": ["papers_verified", "papers_dedup", "papers_backlog"],
                "include_metadata_only": True,
                "metadata_replacement_policy": "replace_metadata_only_with_readable_backlog_when_available",
            },
        },
    },
    "survey_exhaustive": {
        "profile": "survey",
        "t2_finalize": {"active_pool_max": 240},
        "reader": {
            "deep_read_min": 70,
            "deep_read_target": 80,
            "deep_read_max": 95,
            "require_deep_read_target": True,
            "abstract_sweep": {
                "lite_paper_num": 180,
                "sources": ["papers_verified", "papers_dedup", "papers_backlog"],
                "include_metadata_only": True,
                "metadata_replacement_policy": "replace_metadata_only_with_readable_backlog_when_available",
            },
        },
    },
}


_LITERATURE_PARAM_PRESET_LABELS = {
    "standard_research": "标准研究论文覆盖",
    "survey_balanced": "综述均衡覆盖",
    "survey_exhaustive": "综述强覆盖",
    "custom": "自定义关键数字",
}


_LITERATURE_PARAM_PRESET_NOTES = {
    "standard_research": "适合 research article：候选池和轻读覆盖较克制，精读目标 35 篇。",
    "survey_balanced": "适合一般综述：保留候选 180 篇，精读目标 60 篇，摘要轻读最多 120 篇。",
    "survey_exhaustive": "适合正式综述/展示型综述：保留候选 240 篇，精读目标 80 篇，摘要轻读最多 180 篇，运行时间和 LLM 成本更高。",
    "custom": "只改覆盖目标；网络补资源仍由系统自动尽量执行。",
}


_LITERATURE_PARAM_SHORT_MEANINGS = {
    "active_pool_max": "保留候选数：T2 留给后续处置的候选上限；不是精读篇数，也不是最终引用数。",
    "deep_read": "精读 min/target/max：T3 的最低完成线、正常目标和硬上限。",
    "require_target": "是否必须读满 target：true 表示未达到精读目标不进入 T3.5。",
    "abstract_sweep": "摘要轻读：T3 后对 active/retained 中未精读但有摘要的候选做 LLM 轻读；all_readable 表示保留候选内不设上限。",
    "language": "稿件语言：影响 query 语言、中文文献准入和后续引用策略。",
}


def _clone_literature_param_preset(option: str) -> dict[str, Any]:
    return json.loads(json.dumps(_LITERATURE_PARAM_PRESETS[option], ensure_ascii=False))


def _recommended_literature_param_option(workspace_dir: Path | None = None) -> str:
    if workspace_dir is None:
        return "standard_research"
    detected_profile = _detect_literature_profile_hint(workspace_dir)
    return "survey_balanced" if detected_profile == "survey" else "standard_research"


def _literature_param_summary_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    reader = payload.get("reader") if isinstance(payload.get("reader"), dict) else {}
    abstract_sweep = reader.get("abstract_sweep") if isinstance(reader.get("abstract_sweep"), dict) else {}
    literature_quality = payload.get("literature_quality") if isinstance(payload.get("literature_quality"), dict) else {}
    return {
        "profile": payload.get("profile"),
        "active_pool_max": (payload.get("t2_finalize") or {}).get("active_pool_max"),
        "deep_read_min": reader.get("deep_read_min"),
        "deep_read_target": reader.get("deep_read_target"),
        "deep_read_max": reader.get("deep_read_max"),
        "require_deep_read_target": reader.get("require_deep_read_target"),
        "abstract_sweep_target": abstract_sweep.get("lite_paper_num"),
        "abstract_sweep_sources": abstract_sweep.get("sources"),
        "metadata_replacement_policy": abstract_sweep.get("metadata_replacement_policy"),
        "manuscript_language": literature_quality.get("manuscript_language", "en"),
        "include_chinese_literature": literature_quality.get("include_chinese_literature", "false"),
        "chinese_literature_policy": literature_quality.get("chinese_literature_policy", "review_flag_only"),
    }


def _summary_total_read_target(summary: dict[str, Any]) -> int | str | None:
    active = summary.get("active_pool_max")
    deep = summary.get("deep_read_target")
    abstract_target = summary.get("abstract_sweep_target")
    if str(abstract_target).strip().casefold() in {"all", "all_readable", "unlimited", "全部"}:
        return active
    try:
        return int(deep or 0) + int(abstract_target or 0)
    except (TypeError, ValueError):
        return active


def _literature_param_explained_preview(summary: dict[str, Any]) -> str:
    """Compact human-facing explanation for T2 coverage parameters."""

    return "\n".join(_literature_param_explained_preview_lines(summary))


def _literature_param_compact_preview(summary: dict[str, Any]) -> str:
    """One-line preset comparison for an interactive gate.

    The detailed explanation remains available in the saved parameter file.  At
    the first gate users need to compare the quantities that change between
    presets, rather than read the same seven fields four times.
    """

    total = _summary_total_read_target(summary)
    target = summary.get("deep_read_target")
    sweep = summary.get("abstract_sweep_target")
    pool = summary.get("active_pool_max")
    require = "读满目标" if summary.get("require_deep_read_target") else "达到最低线可继续"
    return f"候选 {pool} | 精读 {target} | 摘要轻读 {sweep} | 总覆盖约 {total} | {require}"


def _literature_param_explained_preview_lines(summary: dict[str, Any]) -> list[str]:
    deep_min = summary.get("deep_read_min")
    deep_target = summary.get("deep_read_target")
    deep_max = summary.get("deep_read_max")
    require = summary.get("require_deep_read_target")
    require_text = "未达目标不进入 T3.5" if require else "达到最低线即可继续"
    total_target = _summary_total_read_target(summary)
    return [
        f"总阅读覆盖：约 {total_target} 篇（total=deep_read_target+abstract_sweep；可自定义，如 total=30）",
        f"保留候选：{summary.get('active_pool_max')} 篇（active_pool_max={summary.get('active_pool_max')}；可选：120/180/240 或自定义）",
        f"深入阅读：目标 {deep_target} 篇（deep_read={deep_min}/{deep_target}/{deep_max}；格式：min/target/max）",
        f"读满目标门槛：{require_text}（require_target={require}；可选：true/false）",
        f"摘要轻读：{summary.get('abstract_sweep_target')} 篇（abstract_sweep={summary.get('abstract_sweep_target')}；别名：粗读/略读/rough；可选：数字或 all_readable）",
        f"稿件语言：{summary.get('manuscript_language')}（language={summary.get('manuscript_language')}；可选：auto/en/zh/mixed）",
        f"中文文献：{summary.get('include_chinese_literature')}（include_zh={summary.get('include_chinese_literature')}；可选：auto/true/false）",
    ]


def build_literature_param_gate_preview(workspace_dir: Path | None = None) -> dict[str, Any]:
    """Return human-readable current T2/T3 coverage presets for gate display."""

    detected_profile = _detect_literature_profile_hint(workspace_dir) if workspace_dir is not None else "research_article"
    recommended_option = _recommended_literature_param_option(workspace_dir)
    options: dict[str, Any] = {}
    for option_id, payload in _LITERATURE_PARAM_PRESETS.items():
        options[option_id] = {
            "label": _LITERATURE_PARAM_PRESET_LABELS[option_id],
            "recommended": option_id == recommended_option,
            "summary": _literature_param_summary_from_payload(payload),
            "compact_preview": _literature_param_compact_preview(
                _literature_param_summary_from_payload(payload),
            ),
            "explained_preview": _literature_param_explained_preview(
                _literature_param_summary_from_payload(payload),
            ),
            "will_do": _LITERATURE_PARAM_PRESET_NOTES[option_id],
        }
    recommended_summary = options[recommended_option]["summary"]
    return {
        "detected_profile": detected_profile,
        "recommended_option": recommended_option,
        "recommended_label": _LITERATURE_PARAM_PRESET_LABELS[recommended_option],
        "recommended_summary": recommended_summary,
        "recommended_human_summary": _literature_param_sentence(
            _LITERATURE_PARAM_PRESET_LABELS[recommended_option],
            recommended_summary,
        ),
        "current_default_if_enter": recommended_option,
        "question": (
            "先确认稿件类型和语言，再选择覆盖强度。默认项会直接写入 "
            "literature/literature_params.json；自定义只需填想改的字段，空字段沿用推荐档位。"
        ),
        "parameter_meanings_short": _LITERATURE_PARAM_SHORT_MEANINGS,
        "options": options,
        "custom_input_examples": {
            "coverage_total": "例如 total=30 或 总共30；表示本轮阅读覆盖约 30 篇，通常等于精读 + 摘要轻读",
            "active_pool_max": "例如 180；表示 T2 保留 180 篇进入阅读处置，超额进 papers_backlog.jsonl",
            "deep_read_target": "例如 60；表示 T3 正常应完成 60 篇结构化精读笔记；也可输入 deep_read=35/35/45 一次指定 min/target/max",
            "deep_read_min": "可选；例如 35。留空则沿用所选基础档位并不超过 target",
            "deep_read_max": "可选；例如 45。留空则按所选基础档位或 target 自动设置",
            "abstract_sweep_target": "例如 15、rough=15、粗读15 或 all_readable；表示 T3 后 LLM 摘要轻读多少篇；all_readable 只覆盖保留候选，不全读 backlog",
            "require_deep_read_target": "true/false；true 表示未读满 deep_read_target 不放行到 T3.5",
            "manuscript_language": "en/zh/mixed/auto；英文稿默认不检索也不引用中文非 seed 论文",
            "include_chinese_literature": "auto/false/true；false 表示不要中文论文，true 表示允许中文候选并标记权威性复核状态",
        },
    }


def enrich_literature_param_gate_options(options: list[dict[str, Any]], workspace_dir: Path | None = None) -> list[dict[str, Any]]:
    """Attach actual preset values to T2 coverage gate options shown in CLI."""

    preview = build_literature_param_gate_preview(workspace_dir)
    recommended = preview["recommended_option"]
    enriched: list[dict[str, Any]] = []
    for option in options:
        item = dict(option)
        option_id = str(item.get("id") or item.get("key") or "")
        if option_id in _LITERATURE_PARAM_PRESETS:
            summary = _literature_param_summary_from_payload(_LITERATURE_PARAM_PRESETS[option_id])
            item["is_default"] = option_id == recommended
            if item["is_default"] and "（推荐" not in str(item.get("label", "")):
                item["label"] = f"{item.get('label', option_id)}（当前推荐）"
            item["description"] = _LITERATURE_PARAM_PRESET_NOTES[option_id]
            item["parameter_preview"] = _literature_param_compact_preview(summary)
        elif option_id == "custom":
            item["description"] = _LITERATURE_PARAM_PRESET_NOTES["custom"]
            item["parameter_preview"] = (
                "一次输入数字、稿件语言与中文文献策略；LLM 解释意图后由本地规则校验，未提到的字段沿用当前推荐。"
            )
            # CLIHumanInterface collects one natural-language line for this
            # option.  Keeping the individual fields out of the option avoids
            # a tedious question-by-question form while retaining the same
            # payload contract for build_literature_param_payload().
            item.pop("collect_input", None)
            item["single_input_examples"] = preview["custom_input_examples"]
        enriched.append(item)
    return enriched


def _t4_gate1_candidate_pool_fingerprints(workspace_dir: Path) -> dict[str, dict[str, Any]]:
    paths = {
        "pass1_forward_candidates": "ideation/_pass1_forward_candidates.json",
        "pass2_grounding_review": "ideation/_pass2_grounding_review.json",
        "candidate_directions": "ideation/_candidate_directions.json",
        "gate1_candidate_cards": "ideation/_gate1_candidate_cards.md",
        "gate1_selection_brief": "ideation/_gate1_selection_brief.md",
        "bridge_coverage_review": "ideation/bridge_coverage_review.json",
    }
    fingerprints: dict[str, dict[str, Any]] = {}
    for label, rel in paths.items():
        path = workspace_dir / rel
        item: dict[str, Any] = {"path": rel, "exists": path.exists()}
        if path.exists() and path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            item["sha256"] = digest.hexdigest()
            item["size"] = path.stat().st_size
        fingerprints[label] = item
    return fingerprints


def _t4_basis_summary_for_gate(candidate: dict[str, Any]) -> str:
    """Return the model-authored evidence synthesis without runtime rewriting."""

    return str(candidate.get("basis_summary_zh") or candidate.get("basis_summary") or "").strip()


def _t4_short_display_title(candidate: dict[str, Any], fallback: str) -> str:
    """Use an authored short title when available without eliding the content."""

    text = str(
        candidate.get("display_title")
        or candidate.get("title_short_zh")
        or candidate.get("short_title")
        or fallback
        or "未命名候选"
    ).strip()
    return text


def _t4_candidate_hypotheses(candidate: dict[str, Any], candidate_id: str) -> list[dict[str, str]]:
    """Project authored hypotheses only; never synthesize H1/H2/H3 in the UI."""

    raw = candidate.get("candidate_hypotheses")
    result: list[dict[str, str]] = []
    if isinstance(raw, list):
        for index, item in enumerate(raw[:3], start=1):
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "id": str(item.get("id") or f"{candidate_id}-H{index}"),
                    "statement": str(item.get("statement") or item.get("hypothesis") or "").strip(),
                    "mechanism": str(item.get("mechanism") or "").strip(),
                    "prediction": str(item.get("observable_prediction") or item.get("prediction") or "").strip(),
                    "test": str(item.get("discriminating_test") or item.get("test") or "").strip(),
                    "evidence_status": str(item.get("evidence_status") or "").strip(),
                }
            )
    return result[:3]


def _t4_candidate_innovation(candidate: dict[str, Any]) -> dict[str, str]:
    raw = candidate.get("innovation") if isinstance(candidate.get("innovation"), dict) else {}
    return {
        "summary": str(raw.get("summary") or "").strip(),
        "type": str(raw.get("type") or "").strip(),
        "delta": str(raw.get("novelty_delta") or "").strip(),
        "non_incremental": str(raw.get("non_incremental_reason") or "").strip(),
    }


def _t4_merge_opportunities(candidate: dict[str, Any]) -> list[dict[str, str]]:
    raw = candidate.get("merge_opportunities")
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for item in raw[:4]:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "with": str(item.get("with_candidate") or item.get("candidate_id") or "未指定"),
                "combine": str(item.get("combine") or "").strip(),
                "rationale": str(item.get("rationale") or "").strip(),
            }
        )
    return result


def _t4_gate1_candidate_overview(workspace_dir: Path) -> dict[str, Any]:
    """Build a complete, Chinese-first, auditable Gate1 candidate deck.

    The display intentionally exposes candidate claims and durable evidence
    paths, but never internal model reasoning, provider exceptions, or hashes.
    It is a human decision surface, not a chain-of-thought transcript.
    """

    candidate_path = workspace_dir / "ideation" / "_candidate_directions.json"
    candidates: list[dict[str, Any]] = []
    try:
        raw = json.loads(candidate_path.read_text(encoding="utf-8"))
        raw_candidates = raw.get("candidates") if isinstance(raw, dict) else []
    except Exception:
        raw_candidates = []
    if not isinstance(raw_candidates, list):
        raw_candidates = []

    portfolio_ids: list[str] = []
    try:
        portfolio = json.loads((workspace_dir / "ideation" / "portfolio.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        portfolio = {}
    if isinstance(portfolio, dict):
        portfolio_ids = [
            str(candidate_id).strip()
            for candidate_id in [
                portfolio.get("lead_id"),
                *(portfolio.get("alternative_ids") if isinstance(portfolio.get("alternative_ids"), list) else []),
                *(portfolio.get("high_upside_ids") if isinstance(portfolio.get("high_upside_ids"), list) else []),
            ]
            if str(candidate_id or "").strip()
        ]
    portfolio_id_set = set(portfolio_ids)
    all_candidate_count = len([item for item in raw_candidates if isinstance(item, dict)])

    for candidate in raw_candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("id") or candidate.get("idea_id") or "").strip()
        if not candidate_id:
            continue
        if portfolio_id_set and candidate_id not in portfolio_id_set:
            continue
        source_title = str(candidate.get("title") or "").strip()
        full_title = str(candidate.get("title_zh") or source_title).strip()
        title = _t4_short_display_title(candidate, full_title)
        gate1_card = candidate.get("gate1_card") if isinstance(candidate.get("gate1_card"), dict) else {}
        value = str(candidate.get("pitch_zh") or candidate.get("pitch") or candidate.get("core_claim") or "").strip()
        mechanism = str(candidate.get("mechanism_zh") or candidate.get("mechanism") or "").strip()
        minimum = candidate.get("minimum_experiment") if isinstance(candidate.get("minimum_experiment"), dict) else {}
        metrics = minimum.get("metric") or minimum.get("metrics") or ""
        if isinstance(metrics, list):
            metrics = "、".join(str(item).strip() for item in metrics[:3] if str(item).strip())
        else:
            metrics = str(metrics).strip()
        prediction = str(candidate.get("prediction_zh") or candidate.get("prediction") or "").strip()
        counterfactual = str(candidate.get("counterfactual_zh") or candidate.get("counterfactual") or "").strip()
        score = candidate.get("scores") if isinstance(candidate.get("scores"), dict) else {}
        score_rationale = (
            candidate.get("score_rationale")
            if isinstance(candidate.get("score_rationale"), dict)
            else {}
        )
        evolution_score = candidate.get("evolution_score") if isinstance(candidate.get("evolution_score"), dict) else {}
        support = candidate.get("supporting_papers") if isinstance(candidate.get("supporting_papers"), list) else []
        basis_sources = candidate.get("basis_sources") if isinstance(candidate.get("basis_sources"), list) else []
        evidence_levels = {
            str(item.get("evidence_level") or "").upper()
            for item in support
            if isinstance(item, dict) and str(item.get("evidence_level") or "").strip()
        }
        evidence = "；".join(sorted(evidence_levels)) if evidence_levels else "需回查对应文献笔记 section"
        pass2 = candidate.get("pass2_screening") if isinstance(candidate.get("pass2_screening"), dict) else {}
        warning = str(pass2.get("selection_warning") or candidate.get("selection_warning") or "").strip()
        generated_by = str(candidate.get("generated_by") or candidate.get("generation_stage") or "").strip()
        is_recovery_candidate = generated_by.startswith("deterministic_recovery") or "deterministic_t4_gate1_recovery" in generated_by
        lane = {
            "mainline": "主方向",
            "bridge": "桥接方向",
            "supplement": "消融补充",
            "not_supported_by_current_evidence": "证据待补",
        }.get(str(candidate.get("constraint_status") or "").lower(), "候选方向")
        candidates.append(
            {
                "id": candidate_id,
                "lane": lane,
                "title": title,
                "full_title": full_title,
                "original_title": source_title if full_title != source_title else "",
                "origin": str(candidate.get("idea_origin") or "").strip(),
                "mechanism_family": str(candidate.get("mechanism_family") or "").strip(),
                "target_problem": str(candidate.get("target_problem") or "").strip(),
                "value": value,
                "mechanism": mechanism,
                "prediction": prediction,
                "counterfactual": counterfactual,
                "practical_implication": str(candidate.get("practical_implication_zh") or candidate.get("practical_implication") or "").strip(),
                "minimum_validation": {
                    "dataset": str(minimum.get("dataset") or "").strip(),
                    "baseline": str(minimum.get("baseline") or "").strip(),
                    "metric": str(metrics),
                    "expected_signal": str(minimum.get("expected_signal") or "").strip(),
                    "evidence_status": str(minimum.get("evidence_status") or "unknown"),
                    "source_refs": [
                        str(reference).strip()
                        for reference in minimum.get("source_refs", [])
                        if str(reference).strip()
                    ]
                    if isinstance(minimum.get("source_refs"), list)
                    else [],
                },
                "evidence": evidence,
                "support_count": len(support),
                "basis_summary": _t4_basis_summary_for_gate(candidate),
                "evidence_chain": [
                    {
                        "ref": str(item.get("ref") or item.get("source_file") or item.get("type") or "").strip(),
                        "observation": str(item.get("claim") or item.get("observation") or "").strip(),
                        "implication": str(item.get("implication") or "").strip(),
                        "evidence_level": str(item.get("evidence_level") or "").strip(),
                    }
                    for item in basis_sources
                    if isinstance(item, dict)
                ],
                "supporting_papers": [
                    {
                        "title": str(item.get("title") or "未命名论文"),
                        "citation": str(item.get("ref") or "未提供引用键"),
                        "note_path": str(item.get("source_file") or "未提供笔记路径"),
                        "evidence_level": str(item.get("evidence_level") or "未标注"),
                        "claim_used": str(item.get("claim_used") or item.get("claim") or "").strip(),
                    }
                    for item in support
                    if isinstance(item, dict)
                ],
                "scores": {
                    key: score.get(key)
                    for key in (
                        "novelty",
                        "feasibility",
                        "impact",
                        "evaluability",
                        "differentiation",
                        "cost",
                        "contribution_strength",
                    )
                    if score.get(key) is not None
                },
                "evolution_score": {
                    "overall_readiness": evolution_score.get("overall_readiness"),
                    "uncertainty": evolution_score.get("uncertainty"),
                    "dimensions": evolution_score.get("dimensions") if isinstance(evolution_score.get("dimensions"), dict) else {},
                    "rationales": evolution_score.get("rationales") if isinstance(evolution_score.get("rationales"), dict) else {},
                    "dominant_strength": str(evolution_score.get("dominant_strength") or "").strip(),
                    "dominant_bottleneck": str(evolution_score.get("dominant_bottleneck") or "").strip(),
                    "profile_fit": evolution_score.get("profile_fit") if isinstance(evolution_score.get("profile_fit"), dict) else {},
                },
                "final_idea_card": candidate.get("final_idea_card") if isinstance(candidate.get("final_idea_card"), dict) else {},
                "maturity": str(candidate.get("maturity") or "").strip(),
                "evidence_composition": candidate.get("evidence_composition") if isinstance(candidate.get("evidence_composition"), dict) else {},
                "artifact_paths": [str(path).strip() for path in candidate.get("artifact_paths", []) if str(path).strip()] if isinstance(candidate.get("artifact_paths"), list) else [],
                "selection_recommendation": str(pass2.get("screening_recommendation") or "").strip(),
                "counterfactual_check": str(pass2.get("counterfactual_check") or "").strip(),
                "nearest_prior_work": str(
                    (pass2.get("nearest_prior_work") or candidate.get("nearest_prior_work") or {}).get("work")
                    if isinstance(pass2.get("nearest_prior_work") or candidate.get("nearest_prior_work"), dict)
                    else ""
                ).strip(),
                "novelty_signal": str(pass2.get("novelty_signal") or candidate.get("novelty_signal") or "").strip(),
                "warning": warning,
                "presentation_status": "legacy_recovery_requires_llm_reanalysis" if is_recovery_candidate else "llm_authored_candidate",
                "gate1_card": {
                    "role_summary": str(gate1_card.get("role_summary") or "").strip(),
                    "evidence_interpretation": str(gate1_card.get("evidence_interpretation") or "").strip(),
                    "selection_advice": str(gate1_card.get("selection_advice") or "").strip(),
                    "risk_summary": str(gate1_card.get("risk_summary") or "").strip(),
                    "user_edit_hint": str(gate1_card.get("user_edit_hint") or "").strip(),
                },
                "innovation": _t4_candidate_innovation(candidate),
                "candidate_hypotheses": _t4_candidate_hypotheses(candidate, candidate_id),
                "merge_opportunities": _t4_merge_opportunities(candidate),
                "score_rationale": {str(key): str(reason).strip() for key, reason in score_rationale.items()},
            }
        )

    return {
        "language": "zh",
        "candidates": candidates,
        "active_candidate_count": all_candidate_count,
        "remaining_candidate_count": max(0, all_candidate_count - len(candidates)),
        "input_hint": "选择一个完整 Candidate 可进入 Pre-Novelty review；选择多个方向时请明确“并行保留”或“构建新 Candidate”。也可以输入“查看剩余 Population”“查看 I1 的评分/证据/谱系”“再进化一轮”或“回到上一代”。",
        "detail_path": "ideation/_gate1_candidate_cards.md",
        "file_navigation": [
            {"path": "ideation/_gate1_candidate_cards.md", "purpose": "人工阅读版完整候选卡片，适合逐项比较。"},
            {"path": "ideation/_gate1_selection_brief.md", "purpose": "候选池、合并建议、风险提示和选择顺序的简报。"},
            {"path": "ideation/_candidate_directions.json", "purpose": "机器可读的完整候选结构、评分、实验和支撑论文数据。"},
            {"path": "ideation/_pass1_forward_candidates.json", "purpose": "Pass 1 发散产生的原始候选池，用于检查覆盖范围。"},
            {"path": "ideation/_pass2_grounding_review.json", "purpose": "Pass 2 对每个候选的文献接地、风险和上桌建议。"},
            {"path": "ideation/bridge_coverage_review.json", "purpose": "桥接领域候选为何展示、暂缓或需要补证据的审计记录。"},
        ],
    }


def validate_t4_gate1_selection_file(workspace_dir: Path) -> tuple[bool, str | None]:
    """Validate that the formal T4 Gate1 user selection is usable for resume."""

    path = workspace_dir / "ideation" / "_gate1_user_selection.json"
    if not path.exists() or path.stat().st_size <= 0:
        return False, "missing ideation/_gate1_user_selection.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"_gate1_user_selection.json parse failed: {exc}"
    if not isinstance(data, dict):
        return False, "_gate1_user_selection.json top-level value must be an object"
    if data.get("semantics") != "t4_gate1_user_selection_for_candidate_pool":
        return False, "_gate1_user_selection.json semantics is invalid"
    if data.get("task_id") != "T4-GATE1":
        return False, "_gate1_user_selection.json task_id must be T4-GATE1"
    if data.get("gate_id") != "t4_gate1_selection_gate":
        return False, "_gate1_user_selection.json gate_id must be t4_gate1_selection_gate"
    option_id = str(data.get("selected_option") or "").strip()
    if not option_id:
        return False, "_gate1_user_selection.json missing selected_option"
    captured = data.get("captured")
    if not isinstance(captured, dict):
        return False, "_gate1_user_selection.json captured must be an object"
    captured_text = " ".join(str(value).strip() for value in captured.values() if str(value).strip())
    if option_id in {"select_or_reframe", "merge", "new_idea", "reanalyze"} and not captured_text:
        return False, "_gate1_user_selection.json captured selection text is empty"
    if not str(data.get("selection_fingerprint") or "").strip():
        return False, "_gate1_user_selection.json missing selection_fingerprint"
    changed = _gate1_pool_fingerprint_changed(
        data.get("candidate_pool_fingerprints"),
        _t4_gate1_candidate_pool_fingerprints(workspace_dir),
    )
    if changed:
        return False, "Gate1 selection is stale: " + ", ".join(changed[:8])
    return True, None


def _write_t4_selected_idea_brief_stub(
    workspace_dir: Path,
    *,
    gate_id: str,
    option_id: str,
    captured: dict[str, Any],
    selection_fingerprint: str,
    next_task: str,
) -> None:
    """Write a human-readable confirmation immediately after Gate1 selection.

    The T4 post-gate agent is expected to replace or extend this file with the
    final technical mechanism, practical implication, paper dependencies, and
    hypothesis scope. This stub gives users a stable place to inspect what was
    recorded right after the gate.
    """

    path = workspace_dir / "ideation" / "selected_idea_brief.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    captured_lines = []
    for key, value in sorted((captured or {}).items()):
        captured_lines.append(f"- **{key}**: {value if value not in (None, '') else '(empty)'}")
    if not captured_lines:
        captured_lines.append("- (No additional free-text input was captured.)")
    cards_path = workspace_dir / "ideation" / "_gate1_candidate_cards.md"
    selection_brief_path = workspace_dir / "ideation" / "_gate1_selection_brief.md"
    content = f"""# Selected Idea Brief

## Gate1 用户选择
- **Gate ID**: {gate_id}
- **Selected option**: {option_id}
- **Selection fingerprint**: {selection_fingerprint}
- **Next task**: {next_task}

## Captured feedback
{chr(10).join(captured_lines)}

## Final selected idea
- **Idea IDs**: 待 T4 后半段根据用户选择确认；如用户输入了 `selection`、`merge_plan` 或 `new_idea`，以上 captured feedback 是当前来源。
- **One-line hypothesis**: T4 后半段会把最终假设写到 `ideation/hypotheses.md`，这里应与 H1/H2/H3 对齐。
- **Technical mechanism**: T4 后半段会从候选机制、prediction 和 counterfactual 中收敛出可证伪表述。
- **Practical / managerial / business implication**: T4 后半段会补全现实、管理、商业或部署意义。
- **Core paper dependencies**: T4 后半段会从 `idea_scorecard.yaml` / `idea_rationales.json` / paper notes 中确认，不把 weak-only 线索当强证据。
- **Score rationale**: T4 后半段会引用候选评分和用户选择理由。

## Hypothesis scope
- T4 后半段必须把最终范围写入 `ideation/hypotheses.md`，并在该文件补充 H1/H2/H3 对应关系。

## Source files
- Candidate cards: `{cards_path.relative_to(workspace_dir).as_posix() if cards_path.exists() else 'ideation/_gate1_candidate_cards.md'}`
- Gate1 selection brief: `{selection_brief_path.relative_to(workspace_dir).as_posix() if selection_brief_path.exists() else 'ideation/_gate1_selection_brief.md'}`
- Machine selection record: `ideation/_gate1_user_selection.json`

## Rejected, deferred, or merged alternatives
- 待 T4 后半段写入 `ideation/rejected_ideas.md`，并在此处同步摘要。
"""
    path.write_text(content, encoding="utf-8")


def _gate1_pool_fingerprint_changed(
    stored: object,
    current: dict[str, dict[str, Any]],
) -> list[str]:
    if not isinstance(stored, dict):
        return []
    changed: list[str] = []
    for label, item in current.items():
        previous = stored.get(label)
        if not isinstance(previous, dict):
            changed.append(label)
            continue
        if bool(previous.get("exists")) != bool(item.get("exists")):
            changed.append(label)
            continue
        if item.get("exists") and str(previous.get("sha256") or "") != str(item.get("sha256") or ""):
            changed.append(label)
    return changed


def build_literature_param_payload(
    *,
    selected_option: str,
    captured: dict[str, Any] | None = None,
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    """Build the workspace-local literature coverage parameters for T2/T3."""

    option = _normalize_literature_param_option(selected_option)
    payload = _clone_literature_param_preset(option if option in _LITERATURE_PARAM_PRESETS else "survey_balanced")
    captured = captured or {}
    _apply_literature_quality_overrides(payload, captured, workspace_dir=workspace_dir)
    if option == "custom":
        base_option = _normalize_literature_param_option(
            captured.get("base_option") or captured.get("_base_option") or _recommended_literature_param_option(workspace_dir)
        )
        if base_option not in _LITERATURE_PARAM_PRESETS:
            base_option = "survey_balanced"
        base_payload = _clone_literature_param_preset(base_option)
        base_summary = _literature_param_summary_from_payload(base_payload)
        deep_target = _safe_int(
            captured.get("deep_read_target"),
            default=int(base_summary.get("deep_read_target") or 60),
            minimum=1,
        )
        abstract_target_raw: str | int = str(
            captured.get("abstract_sweep_target") or base_summary.get("abstract_sweep_target") or "all_readable"
        ).strip()
        if abstract_target_raw.casefold() not in {"all", "all_readable", "unlimited", "全部"}:
            abstract_target: str | int = _safe_int(
                abstract_target_raw,
                default=int(base_summary.get("abstract_sweep_target") or 0),
                minimum=0,
            )
        else:
            abstract_target = "all_readable"
        coverage_total = _safe_optional_int(captured.get("coverage_total") or captured.get("total") or captured.get("reading_total"), minimum=1)
        active_default = int(base_summary.get("active_pool_max") or 180)
        if coverage_total is not None:
            active_default = coverage_total
        elif captured.get("abstract_sweep_target") not in (None, "") and isinstance(abstract_target, int):
            active_default = deep_target + abstract_target
        active_pool = _safe_int(
            captured.get("active_pool_max"),
            default=active_default,
            minimum=1,
        )
        if captured.get("active_pool_max") in (None, ""):
            if isinstance(abstract_target, int):
                active_pool = max(active_pool, deep_target + abstract_target)
            elif coverage_total is not None:
                active_pool = max(active_pool, coverage_total)
        if coverage_total is not None and captured.get("abstract_sweep_target") in (None, ""):
            abstract_target = max(0, coverage_total - deep_target)
        if captured.get("deep_read_min") not in (None, ""):
            deep_min = _safe_int(captured.get("deep_read_min"), default=max(1, int(round(deep_target * 0.8))), minimum=1)
            deep_min = min(deep_min, deep_target)
        else:
            base_deep_min = int(base_summary.get("deep_read_min") or max(1, int(round(deep_target * 0.8))))
            deep_min = min(base_deep_min, deep_target)
        base_deep_max = int(base_summary.get("deep_read_max") or max(deep_target, int(round(deep_target * 1.15))))
        if captured.get("deep_read_max") not in (None, ""):
            deep_max = _safe_int(captured.get("deep_read_max"), default=base_deep_max, minimum=deep_target)
        else:
            deep_max = max(
                deep_target,
                min(
                    active_pool,
                    base_deep_max
                    if deep_target <= int(base_summary.get("deep_read_target") or deep_target)
                    else int(round(deep_target * 1.15)),
                ),
            )
        require_target = _safe_bool(
            captured.get("require_deep_read_target"),
            default=bool(base_summary.get("require_deep_read_target")),
        )
        abstract_sources = base_summary.get("abstract_sweep_sources") or ["papers_verified", "papers_dedup", "papers_backlog"]
        payload = {
            "profile": "custom",
            "t2_finalize": {"active_pool_max": active_pool},
            "reader": {
                "deep_read_min": deep_min,
                "deep_read_target": deep_target,
                "deep_read_max": deep_max,
                "require_deep_read_target": require_target,
                "abstract_sweep": {
                    "lite_paper_num": abstract_target,
                    "sources": abstract_sources,
                    "include_metadata_only": True,
                    "metadata_replacement_policy": "replace_metadata_only_with_readable_backlog_when_available",
                },
            },
            "base_option": base_option,
        }
        _apply_literature_quality_overrides(payload, captured, workspace_dir=workspace_dir)

    payload.update(
        {
            "semantics": "workspace_literature_coverage_parameters_for_t2_t3",
            "selected_option": option,
            "selected_label": _LITERATURE_PARAM_PRESET_LABELS.get(option, option),
            "selected_summary": _literature_param_summary_from_payload(payload),
            "confirmation_summary": _literature_param_sentence(
                _LITERATURE_PARAM_PRESET_LABELS.get(option, option),
                _literature_param_summary_from_payload(payload),
            ),
            "captured": captured,
            "resource_backfill_policy": {
                "retained_candidates": "attempt all reasonable metadata, abstract, DOI, OpenAlex, Crossref, Semantic Scholar, arXiv, and PDF-hint backfill",
                "user_visible_budget_semantics": "coverage targets, not network attempt caps",
                "metadata_only": (
                    "metadata-only records receive batch LLM triage but do not count as abstract-note evidence; "
                    "when possible, readable backlog records should replace metadata-only slots for coverage."
                ),
            },
            "parameter_meanings": {
                "active_pool_max": "保留候选数：T2 从检索结果里保留多少篇进入后续阅读处置；不是精读篇数，也不是最终引用篇数。",
                "deep_read_target": "精读目标：正常完成 T3 前应完成多少篇结构化深读笔记。",
                "deep_read_min": "最低精读：预算或资源异常时的最低可接受线；正常运行由 require_deep_read_target 决定是否必须读满 target。",
                "abstract_sweep.lite_paper_num": "摘要轻读数量：T3 后对 active/retained 中未精读但有摘要的论文做 LLM 摘要级轻读；all_readable 表示保留候选内不设上限，backlog 只作数值预算不足时的可读补位。",
                "metadata_replacement_policy": "metadata-only 只做批量 triage，并尽量用 backlog 中有摘要/PDF 的候选补足可读覆盖。",
                "literature_quality.manuscript_language": "写作语言：auto/en/zh/mixed；英文稿默认不搜索、不主动引用中文非 seed 论文。",
                "literature_quality.include_chinese_literature": "是否允许中文论文进入候选池：auto/false/true；允许时不再因缺少权威标签硬过滤，但会标记 authority_review_needed。",
                "literature_quality.chinese_literature_policy": "中文论文来源策略：默认 review_flag_only，只做权威性复核标记；英文稿且明确排除中文时仍不纳入非 seed 中文文献。",
                "literature_quality.effective_non_seed_chinese_action": "生效的非 seed 中文文献动作：英文稿固定为 exclude；中文、双语或自动稿件按中文文献设置决定准入与复核。",
            },
        }
    )
    if workspace_dir is not None:
        payload["detected_profile_before_gate"] = _detect_literature_profile_hint(workspace_dir)
    return payload


def _apply_literature_quality_overrides(
    payload: dict[str, Any],
    captured: dict[str, Any],
    *,
    workspace_dir: Path | None = None,
) -> None:
    """Attach workspace-local language/source-quality decisions to T2/T3 params."""

    literature_quality = dict(payload.get("literature_quality") or {})
    inferred_language = _infer_gate_manuscript_language(workspace_dir)

    manuscript_language = str(
        captured.get("manuscript_language")
        or captured.get("language")
        or captured.get("writing_language")
        or literature_quality.get("manuscript_language")
        or inferred_language
        or "en"
    ).strip().lower()
    manuscript_language = {
        "english": "en",
        "英文": "en",
        "chinese": "zh",
        "中文": "zh",
        "bilingual": "mixed",
        "双语": "mixed",
        "zh-en": "mixed",
        "zh_en": "mixed",
    }.get(manuscript_language, manuscript_language)
    if manuscript_language not in {"auto", "en", "zh", "mixed"}:
        manuscript_language = inferred_language or "en"

    include_raw = captured.get("include_chinese_literature")
    if include_raw in (None, ""):
        include_raw = captured.get("include_zh") or captured.get("chinese_literature")
    if include_raw in (None, ""):
        include_raw = literature_quality.get("include_chinese_literature", "auto")
    include_chinese = _normalize_include_chinese_value(include_raw)
    # English manuscripts deliberately keep non-seed Chinese literature out of
    # retrieval and citation candidates.  A user may still provide a Chinese
    # seed as context, but "include_zh=true" must not silently expand an
    # English-language search after the user selected English.
    if manuscript_language == "en":
        include_chinese = "false"
    default_enabled = _safe_bool(literature_quality.get("enabled"), default=True)
    default_seed_override = _safe_bool(literature_quality.get("allow_user_seed_override"), default=True)

    literature_quality.update(
        {
            "enabled": _safe_bool(captured.get("literature_quality_enabled"), default=default_enabled),
            "manuscript_language": manuscript_language,
            "include_chinese_literature": include_chinese,
            "english_manuscript_policy": str(
                literature_quality.get("english_manuscript_policy") or "exclude_non_seed_chinese"
            ),
            "effective_non_seed_chinese_action": (
                "exclude" if manuscript_language == "en" else "allow_or_review_by_setting"
            ),
            "chinese_literature_policy": str(
                captured.get("chinese_literature_policy")
                or literature_quality.get("chinese_literature_policy")
                or "review_flag_only"
            ),
            "allow_user_seed_override": _safe_bool(
                captured.get("allow_user_seed_override"),
                default=default_seed_override,
            ),
        }
    )
    payload["literature_quality"] = literature_quality


def _normalize_include_chinese_value(value: Any) -> str:
    text = str(value if value is not None else "auto").strip().casefold().replace("-", "_")
    if text in {"true", "yes", "y", "1", "include", "允许", "是", "需要", "zh", "中文"}:
        return "true"
    if text in {"false", "no", "n", "0", "exclude", "不", "不要", "否", "英文", "english_only", "en_only"}:
        return "false"
    return "auto"


def _infer_gate_manuscript_language(workspace_dir: Path | None) -> str:
    if workspace_dir is None:
        return "en"
    try:
        from ..runtime.literature_quality import infer_manuscript_language

        return infer_manuscript_language(workspace_dir, "auto")
    except Exception:
        return "en"


def _normalize_literature_param_option(option: str) -> str:
    normalized = str(option or "").strip().casefold()
    aliases = {
        "standard": "standard_research",
        "research": "standard_research",
        "默认": "standard_research",
        "研究": "standard_research",
        "survey": "survey_balanced",
        "review": "survey_balanced",
        "综述": "survey_balanced",
        "均衡": "survey_balanced",
        "exhaustive": "survey_exhaustive",
        "full": "survey_exhaustive",
        "全量": "survey_exhaustive",
        "强覆盖": "survey_exhaustive",
        "自定义": "custom",
    }
    return aliases.get(normalized, normalized if normalized in {"standard_research", "survey_balanced", "survey_exhaustive", "custom"} else "survey_balanced")


def _safe_int(value: Any, *, default: int, minimum: int) -> int:
    try:
        result = int(float(str(value).strip()))
    except (TypeError, ValueError):
        result = default
    return max(minimum, result)


def _safe_optional_int(value: Any, *, minimum: int | None = None) -> int | None:
    if value in (None, ""):
        return None
    try:
        result = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    if minimum is not None:
        result = max(minimum, result)
    return result


def _safe_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "y", "是", "需要", "require", "target"}:
        return True
    if text in {"0", "false", "no", "n", "否", "不", "min"}:
        return False
    return default


def _literature_param_sentence(label: str, summary: dict[str, Any]) -> str:
    return f"{label}: " + _literature_param_explained_preview(summary)


def _coverage_gate_summary(workspace_dir: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    jsonl_paths = {
        "papers_verified_count": workspace_dir / "literature" / "papers_verified.jsonl",
        "papers_dedup_count": workspace_dir / "literature" / "papers_dedup.jsonl",
        "deep_read_queue_count": workspace_dir / "literature" / "deep_read_queue.jsonl",
    }
    for key, path in jsonl_paths.items():
        if not path.exists():
            summary[key] = 0
            continue
        try:
            summary[key] = sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
        except Exception:
            summary[key] = 0
    params_path = workspace_dir / "literature" / "literature_params.json"
    if params_path.exists():
        try:
            params = json.loads(params_path.read_text(encoding="utf-8"))
        except Exception:
            params = {}
        if isinstance(params, dict):
            summary["literature_params_summary"] = params.get("confirmation_summary") or params.get("selected_summary")
    missing_path = workspace_dir / "literature" / "missing_areas.md"
    if missing_path.exists():
        text = missing_path.read_text(encoding="utf-8", errors="replace")
        summary["missing_area_signal_present"] = bool(re.search(r"(?i)missing|gap|coverage|缺|不足|补", text))
    return summary


def _detect_literature_profile_hint(workspace_dir: Path) -> str:
    texts: list[str] = []
    for rel in ("project.yaml", "user_seeds/seed_outline_profile.json"):
        path = workspace_dir / rel
        if path.exists():
            texts.append(path.read_text(encoding="utf-8", errors="replace")[:4000])
    joined = " ".join(texts).casefold()
    if any(token in joined for token in ("survey", "综述", "review", "taxonomy-driven")):
        return "survey"
    return "research_article"


def _file_newer_than_existing_inputs(output: Path, inputs: list[Path]) -> bool:
    """Return true when an output can safely route against existing inputs."""

    if not output.exists() or output.stat().st_size <= 0:
        return False
    output_mtime = output.stat().st_mtime
    for path in inputs:
        if path.exists() and path.stat().st_size > 0 and path.stat().st_mtime > output_mtime:
            return False
    return True


def _normalized_tags(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list | tuple | set):
        values = list(value)
    else:
        return set()
    return {str(item).strip().lower().replace("-", "_") for item in values if str(item).strip()}


def _config_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_")
        if normalized in {"1", "true", "yes", "y", "on", "unlimited", "unlimited_budget"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "limited", ""}:
            return False
    return bool(value)


def _budget_has_unlimited_tag(
    budget_block: dict[str, Any],
    node_tags: list[str] | None = None,
) -> bool | None:
    """Return explicit unlimited budget override from state-machine config.

    `None` means the node did not express an override, so the AgentSpec default
    should continue to apply.
    """

    if "unlimited_budget" in budget_block:
        return _config_bool(budget_block.get("unlimited_budget"))
    tags = (
        _normalized_tags(budget_block.get("tags"))
        | _normalized_tags(budget_block.get("budget_tags"))
        | _normalized_tags(node_tags)
    )
    if {"unlimited_budget", "unlimited"} & tags:
        return True
    return None


def _valid_writing_style_file(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(data, dict) or data.get("venue_style") not in {"is", "ccf_a", "both"}:
        return False
    if not _recorded_human_interaction_exists(path.parent.parent, str(data.get("human_interaction_id") or "")):
        return False
    return _valid_template_selection_dict(data)


def _valid_template_selection_file(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return (
        isinstance(data, dict)
        and _valid_template_selection_dict(data)
        and _recorded_human_interaction_exists(path.parents[2], str(data.get("human_interaction_id") or ""))
    )


def _valid_template_selection_dict(data: dict[str, Any]) -> bool:
    family = str(data.get("template_family") or data.get("template_type") or "").strip().lower()
    template_id = str(data.get("template_id") or "").strip().lower()
    language = str(data.get("writing_language") or "").strip().lower()
    if family not in {"basic_zh", "basic_en", "ccf", "utd", "other"} or not template_id or language not in {"zh", "en"}:
        return False
    if family in {"ccf", "utd"} and template_id == "auto":
        return False
    return True


def _recorded_human_interaction_exists(workspace_dir: Path, interaction_id: str) -> bool:
    interaction_id = str(interaction_id or "").strip()
    if not interaction_id:
        return False
    path = workspace_dir / "_runtime" / "human_interactions.jsonl"
    if not path.exists():
        return False
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except Exception:
                continue
            if isinstance(record, dict) and str(record.get("interaction_id") or "").strip() == interaction_id:
                return True
    except Exception:
        return False
    return False


def _record_runtime_gate_interaction(
    workspace_dir: Path,
    *,
    interaction_id: str,
    task_id: str,
    gate_id: str,
    selected_option: str,
    captured: dict[str, Any],
) -> None:
    path = workspace_dir / "_runtime" / "human_interactions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "interaction_id": interaction_id,
        "kind": "runtime_gate",
        "task_id": task_id,
        "gate_id": gate_id,
        "selected_option": selected_option,
        "captured": captured,
        "created_at": _now_iso(),
    }
    path.open("a", encoding="utf-8").write(json.dumps(record, ensure_ascii=False) + "\n")


def _interaction_id_for_gate_result(
    *,
    task_id: str,
    gate_id: str,
    selected_option: str,
    captured: dict[str, Any],
) -> str:
    explicit = str(captured.get("human_interaction_id") or "").strip()
    if explicit:
        return explicit
    fingerprint = _stable_json_fingerprint(
        {
            "task_id": task_id,
            "gate_id": gate_id,
            "selected_option": selected_option,
            "captured": captured,
        }
    )
    return f"gate_{task_id.lower().replace('.', '_').replace('-', '_')}_{fingerprint[:12]}"


def _template_selection_from_gate(
    *,
    task_id: str,
    gate_id: str,
    option_id: str,
    gate_result: dict[str, Any],
    next_task: str,
    workspace_dir: Path,
) -> dict[str, Any]:
    raw_captured = gate_result.get("captured") or {}
    captured = raw_captured if isinstance(raw_captured, dict) else {}
    defaults = dict(_TEMPLATE_GATE_DEFAULTS.get(option_id) or {})
    defaults.update({str(k): str(v) for k, v in captured.items() if v not in (None, "")})
    family = _normalize_template_family(defaults.get("template_family") or defaults.get("template_type") or option_id)
    template_id = _normalize_template_id(defaults.get("template_id") or defaults.get("template") or family)
    language = _normalize_writing_language(defaults.get("writing_language") or defaults.get("language") or "")
    if family == "ccf" and template_id in {"", "auto", "ccf", "ccf_neurips"}:
        template_id = "neurips"
    if family == "utd" and template_id in {"", "auto", "utd", "is_informs", "utd_informs"}:
        template_id = "informs"
    if family == "basic_zh":
        template_id = "basic_zh"
        language = "zh"
    if family == "basic_en":
        template_id = "basic_en"
        language = "en"
    if not language:
        language = "zh" if family == "basic_zh" else "en"
    venue_style = _normalize_venue_style(
        defaults.get("venue_style")
        or defaults.get("style")
        or ("is" if family in {"utd", "basic_zh"} else "ccf_a")
    )
    warning = ""
    if template_id not in _SUPPORTED_RUNTIME_TEMPLATE_IDS:
        warning = (
            f"template_id={template_id} is not a known local compile-ready entry; "
            "assembly may fall back to a basic template unless this template is added."
        )
    human_interaction_id = _interaction_id_for_gate_result(
        task_id=task_id,
        gate_id=gate_id,
        selected_option=option_id,
        captured=captured,
    )
    _record_runtime_gate_interaction(
        workspace_dir,
        interaction_id=human_interaction_id,
        task_id=task_id,
        gate_id=gate_id,
        selected_option=option_id,
        captured=captured,
    )
    payload = {
        "semantics": "human_confirmed_writing_template_selection",
        "task_id": task_id,
        "gate_id": gate_id,
        "selected_option": option_id,
        "template_family": family,
        "template_id": template_id,
        "writing_language": language,
        "human_interaction_id": human_interaction_id,
        "captured": captured,
        "user_answer": option_id,
        "next_task": next_task,
        "note": "runtime immediate gate selection before writing",
        "decided_at": _now_iso(),
        "input_fingerprints": build_input_fingerprints(workspace_dir, _TEMPLATE_GATE_INPUT_PATHS),
    }
    if task_id == "T8-STYLE-GATE":
        payload["venue_style"] = venue_style
        payload["venue_profile"] = resolve_venue_writing_profile("", payload).get("id", "")
        payload["venue_profile_note"] = (
            "Internal drafting profile only; verify current official venue page limits, template, and submission rules separately."
        )
    if warning:
        payload["template_warning"] = warning
    return payload


def _normalize_template_family(value: Any) -> str:
    text = str(value or "").strip().casefold().replace("-", "_")
    aliases = {
        "zh": "basic_zh",
        "chinese": "basic_zh",
        "中文": "basic_zh",
        "en": "basic_en",
        "english": "basic_en",
        "英文": "basic_en",
        "informs": "utd",
        "is": "utd",
        "misq": "utd",
        "cds": "utd",
        "commerce_data_science": "utd",
        "informs_journal_on_data_science": "utd",
        "informs_journal_on_data_science_and_analytics": "utd",
        "management_science": "utd",
        "ccf_a": "ccf",
        "ccf-a": "ccf",
        "neurips": "ccf",
        "iclr": "ccf",
        "iclr2026": "ccf",
        "icml": "ccf",
        "icml2026": "ccf",
        "kdd": "ccf",
        "sigkdd": "ccf",
    }
    text = aliases.get(text, text)
    return text if text in {"basic_zh", "basic_en", "ccf", "utd", "other"} else "basic_en"


def _normalize_template_id(value: Any) -> str:
    text = str(value or "").strip().casefold().replace("-", "_")
    aliases = {
        "zh": "basic_zh",
        "chinese": "basic_zh",
        "中文": "basic_zh",
        "en": "basic_en",
        "english": "basic_en",
        "英文": "basic_en",
        "nips": "neurips",
        "neurips2026": "neurips",
        "neurips_2026": "neurips",
        "iclr2026": "iclr",
        "iclr_2026": "iclr",
        "iclr_conference": "iclr",
        "iclr2026_conference": "iclr",
        "icml2026": "icml",
        "icml_2026": "icml",
        "sigkdd": "kdd",
        "kdd2026": "kdd",
        "kdd_2026": "kdd",
        "mnsc": "informs",
        "isr": "informs",
        "isre": "informs",
        "management_science": "informs",
        "cds": "informs",
        "commerce_data_science": "informs",
        "informs_journal_on_data_science": "informs",
        "informs_journal_on_data_science_and_analytics": "informs",
    }
    return aliases.get(text, text)


def _normalize_writing_language(value: Any) -> str:
    text = str(value or "").strip().casefold().replace("-", "_")
    aliases = {
        "english": "en",
        "英文": "en",
        "chinese": "zh",
        "中文": "zh",
    }
    text = aliases.get(text, text)
    return text if text in {"zh", "en"} else ""


def _normalize_venue_style(value: Any) -> str:
    text = str(value or "").strip().casefold().replace("-", "_")
    aliases = {
        "ccf": "ccf_a",
        "ccf-a": "ccf_a",
        "conference": "ccf_a",
        "utd": "is",
        "informs": "is",
        "misq": "is",
        "cds": "is",
        "commerce_data_science": "is",
        "informs_journal_on_data_science": "is",
        "informs_journal_on_data_science_and_analytics": "is",
    }
    text = aliases.get(text, text)
    return text if text in {"is", "ccf_a", "both"} else "ccf_a"


def _extract_t45_final_gate_verdict(text: str) -> str:
    """Extract the T4.5 Final Gate Verdict without interpreting scientific quality."""

    match = re.search(
        r"(?im)^\s*(?:#+\s*)?(?:\*\*)?\s*Final\s+Gate\s+Verdict\s*(?:\*\*)?\s*[:：]\s*(.+?)\s*$",
        text,
    )
    if match:
        return match.group(1).strip()

    heading = re.search(r"(?im)^\s*#+\s*Final\s+Gate\s+Verdict\s*$", text)
    if heading:
        tail = text[heading.end() :].splitlines()
        for line in tail[:8]:
            stripped = line.strip().strip("*")
            if not stripped or stripped.startswith("#"):
                continue
            return stripped
    return ""


def _validate_t45_post_novelty_formalization(workspace_dir: Path, audit_path: Path) -> tuple[bool, str | None]:
    """Require formal T4 artifacts only after an accepted T4.5 verdict.

    The manifest makes the lifecycle explicit for downstream consumers: a
    Pre-Novelty brief is sufficient for T4.5, while T5 may only receive the
    formal bundle that was compiled against a completed audit.
    """

    manifest_path = workspace_dir / "ideation" / "post_novelty_formalization.json"
    required = {
        "hypotheses": workspace_dir / "ideation" / "hypotheses.md",
        "exp_plan": workspace_dir / "ideation" / "exp_plan.yaml",
        "contribution_hypothesis_map": workspace_dir / "ideation" / "contribution_hypothesis_map.yaml",
        "validation_map": workspace_dir / "ideation" / "validation_map.yaml",
        "kill_criteria": workspace_dir / "ideation" / "kill_criteria.yaml",
    }
    if not manifest_path.exists() or manifest_path.stat().st_size <= 0:
        return False, "missing post-novelty formalization manifest"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"post-novelty formalization manifest cannot be read: {exc}"
    if not isinstance(manifest, dict) or manifest.get("semantics") != "t45_post_novelty_formalization":
        return False, "post-novelty formalization manifest semantics is invalid"
    if manifest.get("status") != "formalized_after_novelty_pass":
        return False, "post-novelty formalization is not marked as an accepted audit result"
    missing = [name for name, path in required.items() if not path.exists() or path.stat().st_size <= 0]
    if missing:
        return False, "post-novelty formalization is missing: " + ", ".join(missing)
    too_early = [
        name
        for name, path in required.items()
        if path.stat().st_mtime < audit_path.stat().st_mtime
    ]
    if too_early:
        return False, "formal artifacts predate the novelty audit: " + ", ".join(too_early)
    listed = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    if any(str(listed.get(name) or "") != path.relative_to(workspace_dir).as_posix() for name, path in required.items()):
        return False, "post-novelty formalization manifest does not list the required artifact paths"
    return True, None


@dataclass
class TaskNode:
    """一个 FSM 节点的运行期表示。"""

    task_id: str
    agent: str | None = None
    skill: str | None = None
    description: str | None = None
    inputs: dict[str, str] | None = None
    outputs: dict[str, str] | None = None
    optional_outputs: dict[str, str] | None = None
    next_on_success: str | None = None
    next_on_failure: str | None = None
    terminal: bool = False
    llm: dict[str, Any] | None = None
    budget: dict[str, Any] | None = None
    tools: dict[str, Any] | None = None
    tags: list[str] | None = None
    mode: str | None = None
    gate: str | dict[str, Any] | None = None
    branches: dict[str, str] | None = None
    max_iterations: int | None = None
    round: int | None = None
    extra: dict[str, Any] | None = None


class StateMachine:
    """ResearchOS runtime 层的状态机。

    设计原则：
    - 状态推进要尽量声明式：节点配置决定下一跳，代码只解释语义；
    - `state.yaml` 是唯一真相来源，CLI 每次操作都可重建状态；
    - runtime 只管理“如何推进”，不管理 agent 内部如何恢复历史工作。
    """

    def __init__(self, config_path: Path, gates_config_path: Path | None = None):
        self.config_path = config_path
        if gates_config_path is None:
            candidate = config_path.parent / "gates.yaml"
            gates_config_path = candidate if candidate.exists() else None
        self.gates_config_path = gates_config_path
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        self.initial_state = raw["initial_state"]
        self.nodes = self._parse_nodes(raw)
        self.gates = self._load_gates(gates_config_path)

    def _parse_nodes(self, raw: dict[str, Any]) -> dict[str, TaskNode]:
        """同时兼容两种配置风格：

        - `states: {T1: {...}}`
        - `nodes: [{id: T1, ...}]`
        """
        source = raw.get("states") or raw.get("nodes") or {}
        if isinstance(source, list):
            return {
                item["id"]: TaskNode(
                    task_id=item["id"],
                    **{key: value for key, value in item.items() if key != "id"},
                )
                for item in source
            }
        return {task_id: TaskNode(task_id=task_id, **cfg) for task_id, cfg in source.items()}

    def _load_gates(self, gates_config_path: Path | None) -> dict[str, dict[str, Any]]:
        if gates_config_path is None or not gates_config_path.exists():
            return {}
        raw = yaml.safe_load(gates_config_path.read_text(encoding="utf-8")) or {}
        gates = raw.get("gates", raw)
        if isinstance(gates, list):
            return {gate["id"]: gate for gate in gates}
        if not isinstance(gates, dict):
            raise ValueError("gates config must be a mapping or list")
        return gates

    def create_initial_state(self, project_id: str) -> StateYaml:
        """创建项目首次运行时的状态。"""
        return StateYaml(project_id=project_id, current_task=self.initial_state)

    def validate_definition(self) -> list[str]:
        """对状态机配置做启动前静态校验。

        这里专注于 runtime 自己有能力判断的结构问题：
        - 初始节点是否存在；
        - 非 terminal 节点是否声明了且只声明了一种执行体(agent 或 skill)；
        - 所有 next/branch 是否都指向已知节点或特殊占位符；
        - gate 引用是否存在；
        - 与 task I/O 契约是否明显不一致。
        """

        errors: list[str] = []
        if self.initial_state not in self.nodes:
            errors.append(f"initial_state '{self.initial_state}' not found in state machine nodes")

        for task_id, node in self.nodes.items():
            if node.terminal:
                continue

            if bool(node.agent) == bool(node.skill):
                errors.append(
                    f"{task_id}: non-terminal node must declare exactly one of 'agent' or 'skill'"
                )

            for field_name, target in (
                ("next_on_success", node.next_on_success),
                ("next_on_failure", node.next_on_failure),
            ):
                self._validate_target(task_id, field_name, target, errors)

            self._validate_gate(task_id, node, errors)
            self._validate_task_contract(task_id, node, errors)
        return errors

    def build_execution_context(self, workspace_dir: Path, state: StateYaml) -> ExecutionContext:
        """把当前状态翻译成 AgentRunner 可执行的 ExecutionContext。"""
        node = self.nodes[state.current_task]

        # Phase 2.3: 检查迭代死锁（相同参数重复3次以上）
        self._check_iteration_deadlock(state, node, workspace_dir=workspace_dir)

        run_id = f"{state.current_task.lower()}_{uuid.uuid4().hex[:8]}"
        optional_output_names = set((node.optional_outputs or {}).keys())
        outputs = {
            name: workspace_dir / rel
            for name, rel in (node.outputs or {}).items()
            if name not in optional_output_names
        }
        inputs = {name: workspace_dir / rel for name, rel in (node.inputs or {}).items()}

        # ctx.extra 的来源分三层：
        # 1. state.task_context：上一个 gate 决策附带的上下文
        # 2. node.extra：节点自己声明的静态额外信息
        # 3. runtime 自动注入的 resume / iteration 标志
        extra = dict(state.task_context)
        extra.update(node.extra or {})
        if node.description:
            extra.setdefault("task_description", node.description)
        if node.agent:
            extra.setdefault("agent_id", node.agent)
        if node.skill:
            extra.setdefault("skill_id", node.skill)
        if node.mode is not None:
            extra.setdefault("phase", node.mode)
        if node.round is not None:
            extra.setdefault("round", node.round)

        # P0-9 修复: 设置 skill_dir（如果是 skill 节点）
        if node.skill:
            extra["skill_name"] = node.skill
            # 设置实际skill_dir路径供bash_run等工具使用
            if node.skill == "project-skill-specialization":
                skill_dir = Path(__file__).resolve().parents[2] / "skills" / node.skill
            else:
                skill_dir = workspace_dir / "skills" / node.skill
            extra["skill_dir"] = str(skill_dir)

        iteration = state.iteration_count.get(state.current_task, 0)
        if iteration:
            extra["iteration_count"] = iteration

        # P0-2 修复: 检测 resume 场景并设置 extra 字段
        resumed_from = None
        resume_reason = None

        for history in reversed(state.history):
            if history.task != state.current_task:
                continue

            # 场景1: INTERRUPTED（用户Ctrl+C）
            if history.status == "INTERRUPTED":
                resumed_from = history.run_id
                resume_reason = "interrupted"
                break

            # 场景2: FAILED重试（验证失败，将重试）
            if history.status == "FAILED":
                # 检查是否为重试场景（非终止失败）
                # 注意：STOP_HUMAN_REJECT表示用户拒绝，不应重试
                if hasattr(history, 'stop_reason') and history.stop_reason == "human_reject":
                    break
                # 检查是否配置了失败后重试
                if node.next_on_failure and node.next_on_failure == state.current_task:
                    resumed_from = history.run_id
                    resume_reason = "retry_after_failure"
                    break

            # 场景3: 迭代（通过gate返回同一任务）
            if history.status == "DONE" and iteration > 0:
                resumed_from = history.run_id
                resume_reason = "iteration"
                break

            # 只检查该任务的最近一次运行
            break

        if resumed_from:
            # 设计文档 §13.5 要求的字段
            extra["resumed_from_run_id"] = resumed_from
            extra["resume_mode"] = True
            extra["resume_reason"] = resume_reason
            # 保留旧字段以兼容现有代码
            extra["is_resume"] = True
            extra["resumed_from"] = resumed_from

        # 所有 task 都统一生成恢复快照，让 pipeline resume 与单任务续跑共享同一语义。
        recovery_info = prepare_task_resume_artifacts(
            workspace_dir,
            task_id=node.task_id,
            outputs_expected=outputs,
            base_extra=extra,
        )
        extra.update(recovery_info)

        ctx = ExecutionContext(
            workspace_dir=workspace_dir,
            project_id=state.project_id,
            task_id=node.task_id,
            run_id=run_id,
            inputs=inputs,
            outputs_expected=outputs,
            mode=node.mode,
            extra=extra,
        )
        llm_ov, budget_ov, tool_ov = self._build_overrides(node)
        ctx.llm_override = llm_ov
        ctx.budget_override = budget_ov
        ctx.tool_policy_override = tool_ov
        return ctx

    def should_pause_for_immediate_gate(self, state: StateYaml, *, workspace_dir: Path | None = None) -> bool:
        """Return true when the current node is a gate-only node that should not run an LLM."""

        if state.current_task == "T4" and "T4-GATE1" in self.nodes and workspace_dir is not None:
            if isinstance(state.task_context.get("t4_operation_request"), dict):
                return False
            if self._t4_gate1_ready_without_selection(workspace_dir):
                return True
            return self._t4_prerun_confirmation_required(workspace_dir)
        node = self.nodes[state.current_task]
        return bool(node.gate and (node.extra or {}).get("immediate_gate"))

    def pause_for_immediate_gate(
        self,
        state: StateYaml,
        *,
        workspace_dir: Path | None = None,
    ) -> StateYaml:
        """Present a gate-only node directly and pause without starting an agent run."""

        if state.current_task == "T4" and workspace_dir is not None:
            if self._t4_gate1_ready_without_selection(workspace_dir):
                state.current_task = "T4-GATE1"
            elif self._t4_prerun_confirmation_required(workspace_dir):
                return self._pause_for_t4_prerun_gate(state, workspace_dir)
        node = self.nodes[state.current_task]
        if not node.gate:
            raise ValueError(f"{state.current_task} has no gate")
        gate_id = self._gate_id_for_node(node)
        gate_spec = self._find_gate(gate_id)
        presentation = build_presentation(
            gate_spec,
            model_dump(state, mode="json"),
            workspace_dir or Path("."),
        )
        options = list(gate_spec.get("options", []))
        if node.task_id == "T2-PARAM-GATE":
            presentation["current_parameter_preview"] = build_literature_param_gate_preview(workspace_dir)
            options = enrich_literature_param_gate_options(options, workspace_dir)
        if node.task_id == "T4-GATE1" and workspace_dir is not None:
            presentation["candidate_overview"] = _t4_gate1_candidate_overview(workspace_dir)
            presentation["candidate_pool_fingerprints"] = _t4_gate1_candidate_pool_fingerprints(workspace_dir)
            operation_result = _latest_native_t4_operation_result(workspace_dir)
            if operation_result:
                presentation["t4_directive_result"] = operation_result
            pending_composition = _pending_native_t4_composition(workspace_dir)
            if pending_composition:
                options.insert(
                    0,
                    {
                        "id": "confirm_composition",
                        "label": "Confirm Human-composed Candidate",
                        "description": "Generate one new Candidate from the reviewed Gene Donor Map and independently score it; source Candidates remain preserved.",
                    },
                )
        state.pending_gate = GateState(
            gate_id=gate_id,
            presented_at=_now_iso(),
            presentation=presentation,
            options=options,
        )
        state.status = "WAITING_HUMAN"
        state.paused_at = _now_iso()
        return state

    def _pause_for_t4_prerun_gate(self, state: StateYaml, workspace_dir: Path) -> StateYaml:
        """Pause inside T4 for configuration without adding an external FSM node."""

        inspection = inspect_t4_inputs(workspace_dir)
        store = T4ArtifactStore(workspace_dir)
        try:
            config = store.read_run_config()
        except ValueError:
            config = default_run_config(
                load_t4_evolution_settings(),
                target_profile=suggest_target_profile(workspace_dir),
            )
        if not config.target_profile.confirmed_by_user:
            config = default_run_config(
                load_t4_evolution_settings(),
                target_profile=suggest_target_profile(workspace_dir),
            )
        gate_spec = self._find_gate("t4_prerun_gate")
        presentation = {
            "_title": str(gate_spec.get("title") or "T4 run confirmation"),
            "_description": str(gate_spec.get("description") or "Confirm how T4 should form and evolve research ideas."),
            "t4_prerun": {
                "inspection": model_dump(inspection, mode="json"),
                "run_config": model_dump(config, mode="json"),
            },
        }
        state.pending_gate = GateState(
            gate_id="t4_prerun_gate",
            presented_at=_now_iso(),
            presentation=presentation,
            options=list(gate_spec.get("options", [])),
        )
        state.status = "WAITING_HUMAN"
        state.paused_at = _now_iso()
        return state

    def t4_gate1_ready_without_selection(self, workspace_dir: Path) -> bool:
        """Public helper for runners/tests: T4 has candidate artifacts ready but no Gate1 choice."""

        return self._t4_gate1_ready_without_selection(workspace_dir)

    def refresh_pending_gate_presentation(
        self,
        state: StateYaml,
        *,
        workspace_dir: Path | None = None,
    ) -> StateYaml:
        """Refresh dynamic decision panels when a waiting workspace is resumed.

        Gate state is deliberately persisted so a process can stop at a human
        decision.  Dynamic panels must nevertheless reflect newer renderer
        code and current artifacts when that workspace is resumed.
        """

        if state.pending_gate is None:
            return state
        node = self.nodes.get(state.current_task)
        if node is None:
            return state
        presentation = dict(state.pending_gate.presentation or {})
        options = list(state.pending_gate.options or [])
        gate_spec = self._find_gate(state.pending_gate.gate_id)
        if gate_spec.get("title"):
            presentation["_title"] = gate_spec["title"]
        if gate_spec.get("description"):
            presentation["_description"] = gate_spec["description"]
        if state.pending_gate.gate_id == "t4_prerun_gate" and workspace_dir is not None:
            return self._pause_for_t4_prerun_gate(state, workspace_dir)
        if node.task_id == "T2-PARAM-GATE":
            presentation["current_parameter_preview"] = build_literature_param_gate_preview(workspace_dir)
            options = enrich_literature_param_gate_options(options, workspace_dir)
        elif node.task_id == "T4-GATE1" and workspace_dir is not None:
            presentation["candidate_overview"] = _t4_gate1_candidate_overview(workspace_dir)
            presentation["candidate_pool_fingerprints"] = _t4_gate1_candidate_pool_fingerprints(workspace_dir)
            operation_result = _latest_native_t4_operation_result(workspace_dir)
            if operation_result:
                presentation["t4_directive_result"] = operation_result
            pending_composition = _pending_native_t4_composition(workspace_dir)
            if pending_composition:
                options.insert(
                    0,
                    {
                        "id": "confirm_composition",
                        "label": "Confirm Human-composed Candidate",
                        "description": "Generate one new Candidate from the reviewed Gene Donor Map and independently score it; source Candidates remain preserved.",
                    },
                )
        else:
            return state
        state.pending_gate.presentation = presentation
        state.pending_gate.options = options
        return state

    @staticmethod
    def _t4_gate1_ready_without_selection(workspace_dir: Path) -> bool:
        if validate_t4_gate1_selection_file(workspace_dir)[0]:
            return False
        try:
            from ..agents.ideation import validate_t4_gate1_ready

            ok, _err = validate_t4_gate1_ready(workspace_dir)
            return bool(ok)
        except Exception:
            return False

    @staticmethod
    def _t4_prerun_confirmation_required(workspace_dir: Path) -> bool:
        """Return whether the current scientific inputs require a T4 confirmation.

        A valid confirmed configuration is reusable across resume. Any upstream
        scientific input change invalidates only the pre-run confirmation; it
        never deletes prior populations or candidate artifacts.
        """

        return not has_current_t4_prerun_confirmation(Path(workspace_dir))

    def start_task(self, state: StateYaml, run_id: str, *, workspace_dir: Path | None = None) -> StateYaml:
        """task 开始执行前，先写入一条 RUNNING history。"""
        state.status = "RUNNING"
        state.pending_gate = None
        state.paused_at = None
        # A previous recoverable pause is preserved in history and events.  It
        # must not remain as the current error after a new task actually starts.
        state.last_error = None
        state.history.append(
            TaskHistoryEntry(
                task=state.current_task,
                run_id=run_id,
                status="RUNNING",
                started_at=_now_iso(),
            )
        )

        # Phase 2.3: 记录迭代历史（用于死锁检测）
        self._record_iteration_attempt(state, self.nodes[state.current_task], workspace_dir=workspace_dir)

        return state

    def mark_interrupted(self, state: StateYaml, *, reason: str | None = None) -> StateYaml:
        """收到 SIGINT / SIGTERM 后，把项目置为 PAUSED。"""
        if state.history:
            if state.history[-1].status not in {"DONE", "FAILED", "INTERRUPTED"}:
                state.history[-1].status = "INTERRUPTED"
            state.history[-1].finished_at = _now_iso()
            state.history[-1].stop_reason = state.history[-1].stop_reason or AgentResult.STOP_INTERRUPTED
            if reason and not state.history[-1].error:
                state.history[-1].error = reason
        state.status = "PAUSED"
        state.paused_at = _now_iso()
        return state

    def advance(
        self,
        state: StateYaml,
        result: AgentResult,
        *,
        workspace_dir: Path | None = None,
    ) -> StateYaml:
        """根据一次 agent run 的结果推进状态机。"""
        history = state.history[-1]
        history.finished_at = _now_iso()
        history.stop_reason = result.stop_reason
        history.tokens = result.tokens_in + result.tokens_out
        history.tokens_in = result.tokens_in
        history.tokens_out = result.tokens_out
        history.cost_usd = result.cost_usd
        history.llm_profile = result.llm_profile
        history.llm_tier = result.llm_tier
        history.llm_model = result.llm_model_used
        history.llm_endpoint = result.llm_endpoint_used
        history.completion_mode = (result.metadata or {}).get("completion_mode")
        history.error = result.error
        recoverable_pause = result.stop_reason in {
            AgentResult.STOP_INTERRUPTED,
            AgentResult.STOP_MAX_STEPS,
            AgentResult.STOP_BUDGET,
        }
        history.status = "DONE" if result.ok else "INTERRUPTED" if recoverable_pause else "FAILED"

        state.budget_cumulative = BudgetCumulative(
            tokens_total=state.budget_cumulative.tokens_total + history.tokens,
            cost_usd_total=state.budget_cumulative.cost_usd_total + result.cost_usd,
            gpu_hours_used=state.budget_cumulative.gpu_hours_used,
        )

        # Budget drift warning (§7.1)
        if workspace_dir:
            self._check_budget_drift(state, workspace_dir)

        if result.stop_reason in {
            AgentResult.STOP_INTERRUPTED,
            AgentResult.STOP_MAX_STEPS,
            AgentResult.STOP_BUDGET,
        }:
            state.last_error = result.error
            return self.mark_interrupted(state)

        node = self.nodes[state.current_task]
        if not result.ok:
            state.last_error = result.error
            next_task = node.next_on_failure
            if next_task and next_task in self.nodes and not self.nodes[next_task].terminal:
                state.current_task = next_task
                state.status = "RUNNING"
            else:
                if next_task and next_task in self.nodes:
                    state.current_task = next_task
                state.status = "FAILED"
            return state

        if (
            state.current_task == "T4"
            and (result.metadata or {}).get("completion_mode") == "t4_gate1_ready"
            and "T4-GATE1" in self.nodes
        ):
            state.task_context.pop("t4_operation_request", None)
            return self._transition_to_next(state, "T4-GATE1", workspace_dir=workspace_dir)

        if (
            state.current_task == "T4"
            and (result.metadata or {}).get("completion_mode") == "t4_pre_novelty_ready"
            and "T4.5" in self.nodes
        ):
            return self._transition_to_next(state, "T4.5", workspace_dir=workspace_dir)

        human_directive = state.task_context.get("human_iteration_directive")
        if isinstance(human_directive, dict) and human_directive.get("target_task") == state.current_task:
            # A human-directed return applies to exactly one successful run.
            # Leaving it behind would make later resumes look like fresh user
            # decisions and defeat normal idempotent recovery.
            state.task_context.pop("human_iteration_directive", None)

        if state.current_task == "T2" and bool(state.task_context.get("t2_user_requested_expansion")):
            # This gate flag is a one-round instruction.  Retaining it after a
            # successful supplement would make later resumes look like another
            # expansion and suppress the normal existing-output fast path.
            state.task_context.pop("t2_user_requested_expansion", None)
            state.task_context.pop("allow_t2_failure_recovery", None)

        if node.gate:
            gate_id = self._gate_id_for_node(node)
            gate_spec = self._find_gate(gate_id)
            presentation = build_presentation(
                gate_spec,
                model_dump(state, mode="json"),
                workspace_dir or Path("."),
            )
            options = list(gate_spec.get("options", []))
            if state.current_task == "T2-PARAM-GATE":
                presentation["current_parameter_preview"] = build_literature_param_gate_preview(workspace_dir)
                options = enrich_literature_param_gate_options(options, workspace_dir)
            if state.current_task == "T4-GATE1" and workspace_dir is not None:
                presentation["candidate_overview"] = _t4_gate1_candidate_overview(workspace_dir)
                presentation["candidate_pool_fingerprints"] = _t4_gate1_candidate_pool_fingerprints(workspace_dir)
                operation_result = _latest_native_t4_operation_result(workspace_dir)
                if operation_result:
                    presentation["t4_directive_result"] = operation_result
            state.pending_gate = GateState(
                gate_id=gate_id,
                presented_at=_now_iso(),
                presentation=presentation,
                options=options,
            )
            state.status = "WAITING_HUMAN"
            return state

        return self._transition_to_next(state, node.next_on_success, workspace_dir=workspace_dir)

    def resolve_pending_gate(
        self,
        state: StateYaml,
        gate_result: dict[str, Any],
        *,
        workspace_dir: Path | None = None,
    ) -> StateYaml:
        """处理一个已挂起 gate 的用户选择。"""
        if state.pending_gate is None:
            raise ValueError("No pending gate to resolve")
        if state.pending_gate.gate_id == "t4_prerun_gate":
            if workspace_dir is None:
                raise ValueError("T4 pre-run gate requires a workspace")
            return self._resolve_t4_prerun_gate(state, gate_result, workspace_dir)
        node = self.nodes[state.current_task]
        if (
            node.task_id == "T4-GATE1"
            and workspace_dir is not None
            and validate_t4_gate1_selection_file(workspace_dir)[0]
        ):
            state.pending_gate = None
            return self._transition_to_next(state, "T4", workspace_dir=workspace_dir)
        if node.task_id == "T4-GATE1" and workspace_dir is not None:
            if isinstance(state.task_context.get("t4_pending_directive"), dict):
                return self._resolve_native_t4_gate1(state, gate_result, workspace_dir)
            current_pool = _t4_gate1_candidate_pool_fingerprints(workspace_dir)
            previous_pool = (state.pending_gate.presentation or {}).get("candidate_pool_fingerprints")
            changed = _gate1_pool_fingerprint_changed(previous_pool, current_pool)
            if changed:
                gate_spec = self._find_gate(self._gate_id_for_node(node))
                presentation = build_presentation(
                    gate_spec,
                    model_dump(state, mode="json"),
                    workspace_dir,
                )
                presentation["candidate_overview"] = _t4_gate1_candidate_overview(workspace_dir)
                presentation["candidate_pool_fingerprints"] = current_pool
                presentation["stale_reason"] = (
                    "T4-GATE1 candidate pool changed while waiting for human selection: "
                    + ", ".join(changed[:8])
                )
                state.pending_gate = GateState(
                    gate_id=self._gate_id_for_node(node),
                    presented_at=_now_iso(),
                    presentation=presentation,
                    options=list(gate_spec.get("options", [])),
                )
                state.status = "WAITING_HUMAN"
                state.paused_at = _now_iso()
                state.last_error = presentation["stale_reason"]
                return state
            if self._has_native_t4_population(workspace_dir):
                return self._resolve_native_t4_gate1(state, gate_result, workspace_dir)
        next_task = self._resolve_branch(node, gate_result, state, workspace_dir=workspace_dir)
        self._persist_immediate_gate_result(node, gate_result, next_task, workspace_dir)
        if node.task_id == "T5-EXPR-MATERIAL-GATE" and next_task == "T5-EXPR-MATERIAL-GATE":
            state.pending_gate = None
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            state.last_error = (
                "WAITING_MATERIALS: place baseline models, datasets, repositories, weights, "
                "and notes under external_executor/expr/, then resume."
            )
            return state
        if node.task_id == "T5-EXTERNAL-WAIT" and workspace_dir is not None and next_task == "T7-INGEST":
            readiness = validate_external_executor_ready(
                workspace_dir,
                "external_executor/result_pack.json",
                "external_executor/executor_status.json",
            )
            if not readiness.get("ok"):
                if state.pending_gate is not None:
                    state.pending_gate.presentation["external_executor_wait_status"] = readiness.get("message")
                state.status = "WAITING_HUMAN"
                state.paused_at = _now_iso()
                state.last_error = str(readiness.get("message") or "external executor result is not ready")
                return state
        state.pending_gate = None
        return self._transition_to_next(state, next_task, workspace_dir=workspace_dir)

    @staticmethod
    def _has_native_t4_population(workspace_dir: Path) -> bool:
        """Return whether Gate1 is backed by the typed evolutionary population."""

        try:
            population, _dossiers = current_population_context(workspace_dir)
        except (OSError, ValueError):
            return False
        return bool(population.active_candidate_ids)

    def _resolve_native_t4_gate1(
        self,
        state: StateYaml,
        gate_result: dict[str, Any],
        workspace_dir: Path,
    ) -> StateYaml:
        """Resolve an Evolution-native Gate1 operation.

        The retained legacy Gate1 files remain the compatibility surface.  This
        method adds the native directive layer behind it: every meaningful
        action gets an immutable fingerprint-bound record, confirmation is
        explicit, and only a queued operation may re-enter T4.
        """

        pending = state.task_context.get("t4_pending_directive")
        option_id = str(gate_result.get("option_id") or gate_result.get("key") or "").strip()
        captured = gate_result.get("captured") if isinstance(gate_result.get("captured"), dict) else {}
        if isinstance(pending, dict):
            if option_id in {"confirm", "proceed", "yes"}:
                raw = pending.get("directive")
                if not isinstance(raw, dict):
                    raise ValueError("T4 confirmation is missing its persisted Directive")
                directive = IdeaDirective.model_validate(raw)
                state.task_context.pop("t4_pending_directive", None)
                persist_idea_directive_confirmation(
                    workspace_dir,
                    directive=directive,
                    directive_path=str(pending.get("directive_path") or ""),
                    accepted=True,
                    outcome="confirmed_for_execution",
                )
                return self._apply_native_t4_directive(
                    state,
                    directive=directive,
                    directive_path=str(pending.get("directive_path") or ""),
                    workspace_dir=workspace_dir,
                )
            if option_id in {"cancel", "pause", "no"}:
                raw = pending.get("directive") if isinstance(pending.get("directive"), dict) else {}
                directive = IdeaDirective.model_validate(raw)
                persist_idea_directive_confirmation(
                    workspace_dir,
                    directive=directive,
                    directive_path=str(pending.get("directive_path") or ""),
                    accepted=False,
                    outcome="cancelled_before_execution",
                )
                state.task_context.pop("t4_pending_directive", None)
                return self._reopen_native_t4_gate(
                    state,
                    workspace_dir,
                    result={
                        "title": "Operation cancelled",
                        "summary": "No Candidate, Population, or historical version was changed.",
                        "kind": "cancelled",
                    },
                )
            return self._native_t4_confirmation_gate(state, workspace_dir, pending)

        pending_composition = _pending_native_t4_composition(workspace_dir)
        inline_text = self._native_t4_directive_text(option_id=option_id, captured=captured).casefold()
        if pending_composition and (
            option_id == "confirm_composition"
            or any(token in inline_text for token in ("confirm composition", "确认组合", "确认生成", "生成组合"))
        ):
            return self._queue_confirmed_native_t4_composition(
                state,
                workspace_dir,
                pending_composition,
            )

        population, dossiers = current_population_context(workspace_dir)
        raw = self._native_t4_directive_text(option_id=option_id, captured=captured)
        try:
            directive = parse_idea_directive(
                raw,
                candidate_ids=set(dossiers),
                option_id=option_id,
                llm_payload=captured.get("parsed_directive") if isinstance(captured.get("parsed_directive"), dict) else None,
            )
        except ValueError as exc:
            return self._reopen_native_t4_gate(
                state,
                workspace_dir,
                result={
                    "title": "More detail is needed",
                    "summary": str(exc),
                    "kind": "needs_clarification",
                },
            )
        directive_path = persist_idea_directive(workspace_dir, directive=directive, population=population)
        if directive.confirmation_required:
            pending_payload = {
                "directive": model_dump(directive, mode="json"),
                "directive_path": directive_path,
                "population_id": population.population_id,
                "population_generation": population.generation,
            }
            state.task_context["t4_pending_directive"] = pending_payload
            return self._native_t4_confirmation_gate(state, workspace_dir, pending_payload)
        return self._apply_native_t4_directive(
            state,
            directive=directive,
            directive_path=directive_path,
            workspace_dir=workspace_dir,
        )

    def _queue_confirmed_native_t4_composition(
        self,
        state: StateYaml,
        workspace_dir: Path,
        pending_composition: dict[str, str],
    ) -> StateYaml:
        """Queue the second-confirmed Human-composed Candidate generation."""

        population, _dossiers = current_population_context(workspace_dir)
        if pending_composition.get("population_id") != population.population_id:
            return self._reopen_native_t4_gate(
                state,
                workspace_dir,
                result={
                    "title": "Composition plan is stale",
                    "summary": "The active Population changed after the Compatibility Check. Re-run the component selection so ResearchOS can evaluate the current Candidate context.",
                    "kind": "composition_stale",
                },
            )
        composition_id = str(pending_composition.get("composition_id") or "")
        operation = {
            "schema_version": "1.0.0",
            "semantics": "t4_native_operation_request",
            "action": "execute_human_composition",
            "composition_id": composition_id,
            "composition_plan_path": str(pending_composition.get("composition_plan_path") or ""),
            "requested_from_population": population.population_id,
            "queued_at": _now_iso(),
        }
        operation_path = f"ideation/evolution/operations/{composition_id}_confirmed.json"
        T4ArtifactStore(workspace_dir).write_json(operation_path, operation)
        state.task_context["t4_operation_request"] = {**operation, "path": operation_path}
        state.task_context["human_iteration_directive"] = {
            "decision_id": composition_id,
            "gate_id": "t4_gate1_selection_gate",
            "source_task": "T4-GATE1",
            "target_task": "T4",
            "option_id": "execute_human_composition",
        }
        state.iteration_count["T4"] = state.iteration_count.get("T4", 0) + 1
        state.pending_gate = None
        state.current_task = "T4"
        state.status = "RUNNING"
        state.paused_at = None
        state.last_error = None
        return state

    @staticmethod
    def _native_t4_directive_text(*, option_id: str, captured: dict[str, Any]) -> str:
        """Use the user's own wording as the semantic input to the parser."""

        for key in ("directive", "selection", "merge_plan", "new_idea", "feedback", "route"):
            value = captured.get(key)
            if str(value or "").strip():
                return str(value).strip()
        return option_id or "show_population"

    def _native_t4_confirmation_gate(
        self,
        state: StateYaml,
        workspace_dir: Path,
        pending: dict[str, Any],
    ) -> StateYaml:
        """Render the second, action-specific confirmation without raw JSON."""

        raw = pending.get("directive") if isinstance(pending.get("directive"), dict) else {}
        directive = IdeaDirective.model_validate(raw)
        action = _native_t4_action_description(directive)
        presentation = {
            "_title": "Confirm T4 operation",
            "_description": "Review the planned operation before ResearchOS changes the active Population or calls a model.",
            "t4_directive_confirmation": {
                "action": action["title"],
                "what_happens": action["what_happens"],
                "estimated_time": action["estimated_time"],
                "version_policy": action["version_policy"],
                "next_stage": action["next_stage"],
                "candidate_ids": directive.target_candidate_ids,
                "component_refs": directive.component_refs,
                "directive_path": str(pending.get("directive_path") or ""),
            },
        }
        state.pending_gate = GateState(
            gate_id="t4_gate1_selection_gate",
            presented_at=_now_iso(),
            presentation=presentation,
            options=[
                {"id": "confirm", "label": "Confirm and continue", "description": "Apply the operation exactly as shown."},
                {"id": "cancel", "label": "Cancel", "description": "Keep the current Population and return to the decision panel."},
            ],
        )
        state.current_task = "T4-GATE1"
        state.status = "WAITING_HUMAN"
        state.paused_at = _now_iso()
        return state

    def _apply_native_t4_directive(
        self,
        state: StateYaml,
        *,
        directive: IdeaDirective,
        directive_path: str,
        workspace_dir: Path,
    ) -> StateYaml:
        """Apply a confirmed directive or queue its model-backed T4 operation."""

        if directive.action in {
            "show_more",
            "show_archive",
            "inspect_score",
            "inspect_evidence",
            "inspect_lineage",
            "inspect_hypotheses",
            "inspect_contributions",
            "inspect_genome",
        }:
            return self._reopen_native_t4_gate(
                state,
                workspace_dir,
                result=self._native_t4_readonly_result(workspace_dir, directive),
            )
        if directive.action == "pause":
            state.pending_gate = None
            state.current_task = "T4-GATE1"
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            state.last_error = "T4 is paused at the research-idea decision panel. Resume returns here without repeating a model call."
            return state
        if directive.action == "rollback":
            return self._rollback_native_t4_population(state, workspace_dir, directive, directive_path)
        if directive.action == "select_candidate":
            return self._select_native_t4_candidate(state, workspace_dir, directive, directive_path)
        if directive.action == "keep_parallel":
            return self._stage_native_t4_parallel_selection(state, workspace_dir, directive, directive_path)
        if directive.action == "change_target_profile":
            return self._stage_native_t4_profile_revision(state, workspace_dir, directive, directive_path)
        if directive.action in {"continue_evolution", "focus_candidate", "merge_candidates", "compose_from_components", "regenerate_route", "refine_candidate"}:
            operation = {
                "schema_version": "1.0.0",
                "semantics": "t4_native_operation_request",
                "action": directive.action,
                "directive_path": directive_path,
                "directive": model_dump(directive, mode="json"),
                "requested_from_population": current_population_context(workspace_dir)[0].population_id,
                "queued_at": _now_iso(),
            }
            store = T4ArtifactStore(workspace_dir)
            operation_path = f"ideation/evolution/operations/{directive.directive_id}.json"
            store.write_json(operation_path, operation)
            state.task_context["t4_operation_request"] = {**operation, "path": operation_path}
            state.task_context["human_iteration_directive"] = {
                "decision_id": directive.directive_id,
                "gate_id": "t4_gate1_selection_gate",
                "source_task": "T4-GATE1",
                "target_task": "T4",
                "option_id": directive.action,
            }
            state.iteration_count["T4"] = state.iteration_count.get("T4", 0) + 1
            state.pending_gate = None
            state.current_task = "T4"
            state.status = "RUNNING"
            state.paused_at = None
            state.last_error = None
            return state
        return self._reopen_native_t4_gate(
            state,
            workspace_dir,
            result={"title": "Operation is not available", "summary": f"The requested action '{directive.action}' is not available for this Population.", "kind": "unsupported"},
        )

    def _stage_native_t4_profile_revision(
        self,
        state: StateYaml,
        workspace_dir: Path,
        directive: IdeaDirective,
        directive_path: str,
    ) -> StateYaml:
        """Persist a confirmed orientation change before the reprofile operation."""

        store = T4ArtifactStore(workspace_dir)
        current_config = store.read_run_config()
        target_profile = parse_target_profile_instruction(
            directive.raw_user_input,
            suggested=current_config.target_profile,
        )
        revised_config = current_config.model_copy(update={"target_profile": target_profile})
        store.write_run_config(revised_config)
        store.write_json(
            "ideation/t4_target_profile.json",
            {"schema_version": "1.0.0", "semantics": "t4_target_profile", **model_dump(target_profile, mode="json")},
        )
        inspection = inspect_t4_inputs(workspace_dir)
        store.write_json(
            "ideation/evolution/pre_run_confirmation.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_pre_run_confirmation",
                "input_fingerprint": inspection.input_fingerprint,
                "run_config_fingerprint": run_config_fingerprint(revised_config),
                "selected_option": "profile_revision",
                "captured": {"publication_orientation": directive.raw_user_input},
                "target_profile": model_dump(target_profile, mode="json"),
                "inspection_status": inspection.status,
                "confirmed_at": _now_iso(),
            },
        )
        population, _dossiers = current_population_context(workspace_dir)
        operation = {
            "schema_version": "1.0.0",
            "semantics": "t4_native_operation_request",
            "action": "change_target_profile",
            "directive_path": directive_path,
            "directive": model_dump(directive, mode="json"),
            "requested_from_population": population.population_id,
            "target_profile": model_dump(target_profile, mode="json"),
            "queued_at": _now_iso(),
        }
        operation_path = f"ideation/evolution/operations/{directive.directive_id}.json"
        store.write_json(operation_path, operation)
        state.task_context["t4_operation_request"] = {**operation, "path": operation_path}
        state.task_context["t4_target_profile_path"] = "ideation/t4_target_profile.json"
        state.iteration_count["T4"] = state.iteration_count.get("T4", 0) + 1
        state.pending_gate = None
        state.current_task = "T4"
        state.status = "RUNNING"
        state.paused_at = None
        state.last_error = None
        return state

    def _select_native_t4_candidate(
        self,
        state: StateYaml,
        workspace_dir: Path,
        directive: IdeaDirective,
        directive_path: str,
    ) -> StateYaml:
        """Create the formal Gate1 selection and its Pre-Novelty briefing files."""

        population, _dossiers = current_population_context(workspace_dir)
        selected_candidate_id = directive.target_candidate_ids[0]
        pool_fingerprints = _t4_gate1_candidate_pool_fingerprints(workspace_dir)
        fingerprint_payload = {
            "semantics": "t4_gate1_selection_fingerprint",
            "gate_id": "t4_gate1_selection_gate",
            "selected_option": "proceed_candidate",
            "directive_path": directive_path,
            "selected_candidate_id": selected_candidate_id,
            "candidate_pool_fingerprints": pool_fingerprints,
            "population_id": population.population_id,
        }
        selection_fingerprint = _stable_json_fingerprint(fingerprint_payload)
        payload = {
            "semantics": "t4_gate1_user_selection_for_candidate_pool",
            "task_id": "T4-GATE1",
            "gate_id": "t4_gate1_selection_gate",
            "selected_option": "proceed_candidate",
            "captured": {"directive": directive.raw_user_input},
            "directive_path": directive_path,
            "selected_candidate_id": selected_candidate_id,
            "population_id": population.population_id,
            "candidate_pool_fingerprints": pool_fingerprints,
            "selection_fingerprint": selection_fingerprint,
            "next_task": "T4",
            "decided_at": _now_iso(),
        }
        payload["pre_novelty_artifacts"] = compile_pre_novelty_hypothesis_brief(
            workspace_dir,
            selection_fingerprint=selection_fingerprint,
            selected_candidate_id=selected_candidate_id,
        )
        T4ArtifactStore(workspace_dir).write_json("ideation/_gate1_user_selection.json", payload)
        _write_t4_selected_idea_brief_stub(
            workspace_dir,
            gate_id="t4_gate1_selection_gate",
            option_id="proceed_candidate",
            captured={"directive": directive.raw_user_input, "selected_candidate_id": selected_candidate_id},
            selection_fingerprint=selection_fingerprint,
            next_task="T4.5",
        )
        state.pending_gate = None
        state.current_task = "T4"
        state.status = "RUNNING"
        state.paused_at = None
        state.last_error = None
        return state

    def _stage_native_t4_parallel_selection(
        self,
        state: StateYaml,
        workspace_dir: Path,
        directive: IdeaDirective,
        directive_path: str,
    ) -> StateYaml:
        """Preserve parallel full-Candidate intent without treating it as a merge."""

        population, _dossiers = current_population_context(workspace_dir)
        payload = {
            "schema_version": "1.0.0",
            "semantics": "t4_parallel_candidate_selection",
            "candidate_ids": directive.target_candidate_ids,
            "population_id": population.population_id,
            "input_fingerprint": population.input_fingerprint,
            "run_config_fingerprint": population.run_config_fingerprint,
            "directive_path": directive_path,
            "status": "staged_for_individual_pre_novelty_review",
            "note": "The selected Candidates remain separate. No mechanism, hypothesis, or contribution has been merged.",
            "created_at": _now_iso(),
        }
        T4ArtifactStore(workspace_dir).write_json("ideation/selected/parallel_selection.json", payload)
        return self._reopen_native_t4_gate(
            state,
            workspace_dir,
            result={
                "title": "Parallel Ideas preserved",
                "summary": "The requested complete Candidates are retained as separate directions. Their source versions remain unchanged; choose one when you are ready to create a Pre-Novelty brief, or request a Compatibility Check to build a new Candidate.",
                "kind": "parallel_staged",
                "candidate_ids": directive.target_candidate_ids,
                "artifact": "ideation/selected/parallel_selection.json",
            },
        )

    def _rollback_native_t4_population(
        self,
        state: StateYaml,
        workspace_dir: Path,
        directive: IdeaDirective,
        directive_path: str,
    ) -> StateYaml:
        """Activate the prior Population without deleting later artifacts."""

        store = T4ArtifactStore(workspace_dir)
        internal = store.read_state()
        current = store.read_population(internal.current_population_id)
        target_generation = current.generation - 1
        if target_generation < 0:
            return self._reopen_native_t4_gate(
                state,
                workspace_dir,
                result={"title": "Rollback is unavailable", "summary": "P0 is already the earliest preserved Population.", "kind": "rollback_unavailable"},
            )
        target_id = f"P{target_generation}"
        target = store.read_population(target_id)
        self._archive_gate1_projection(workspace_dir, suffix=f"before_rollback_{current.population_id}")
        restored = store.activate_population(target_id)
        scores = self._native_population_scores(store, target)
        dossiers = self._native_population_dossiers(store, target)
        families = build_idea_families(
            [item.genome for item in dossiers],
            generation=target.generation,
            similarity_threshold=load_t4_evolution_settings().family_similarity_threshold,
        )
        run_config = store.read_run_config()
        portfolio = select_portfolio(
            target,
            scores,
            families,
            maximum=run_config.final_top_k,
            profile_weight=run_config.target_profile.portfolio_profile_weight,
        )
        store.write_json("ideation/portfolio.json", model_dump(portfolio, mode="json"))
        route_results = self._native_route_results(store)
        project_gate1_population(
            workspace_dir,
            population=target,
            dossiers=dossiers,
            scores=scores,
            route_results=route_results,
        )
        store.write_json(
            f"ideation/evolution/rollback_events/{directive.directive_id}.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_population_rollback",
                "directive_path": directive_path,
                "from_population": current.population_id,
                "to_population": target.population_id,
                "from_generation": current.generation,
                "to_generation": target.generation,
                "later_artifacts_preserved": True,
                "performed_at": _now_iso(),
                "state_path": "ideation/evolution/state.json",
                "restored_state_generation": restored.generation,
            },
        )
        return self._reopen_native_t4_gate(
            state,
            workspace_dir,
            result={
                "title": "Population rolled back",
                "summary": f"The active Population moved from {current.population_id} to {target.population_id}. Later Population files and Candidate versions were preserved and can be reactivated later.",
                "kind": "rollback_completed",
                "candidate_ids": target.active_candidate_ids,
                "artifact": f"ideation/evolution/rollback_events/{directive.directive_id}.json",
            },
        )

    def _reopen_native_t4_gate(
        self,
        state: StateYaml,
        workspace_dir: Path,
        *,
        result: dict[str, Any] | None = None,
    ) -> StateYaml:
        """Re-render the decision surface after a safe read-only local action."""

        node = self.nodes["T4-GATE1"]
        gate_spec = self._find_gate(self._gate_id_for_node(node))
        presentation = {
            "_title": str(gate_spec.get("title") or "Research idea decision"),
            "_description": str(gate_spec.get("description") or "Choose how to continue with the current Candidate Population."),
            "candidate_overview": _t4_gate1_candidate_overview(workspace_dir),
            "candidate_pool_fingerprints": _t4_gate1_candidate_pool_fingerprints(workspace_dir),
        }
        if result:
            presentation["t4_directive_result"] = result
        options = list(gate_spec.get("options", []))
        pending_composition = _pending_native_t4_composition(workspace_dir)
        if pending_composition:
            options.insert(
                0,
                {
                    "id": "confirm_composition",
                    "label": "Confirm Human-composed Candidate",
                    "description": "Use the reviewed Gene Donor Map to generate one new Candidate and independently score it against its source Candidates. The sources remain preserved.",
                },
            )
        state.current_task = "T4-GATE1"
        state.pending_gate = GateState(
            gate_id=self._gate_id_for_node(node),
            presented_at=_now_iso(),
            presentation=presentation,
            options=options,
        )
        state.status = "WAITING_HUMAN"
        state.paused_at = _now_iso()
        state.last_error = None
        return state

    @staticmethod
    def _native_population_dossiers(store: T4ArtifactStore, population: PopulationSnapshot) -> list[CandidateDossier]:
        dossiers: list[CandidateDossier] = []
        for candidate_id in population.active_candidate_ids:
            matches = sorted(store.path("ideation/candidates").glob(f"{candidate_id}.v*.json"))
            if not matches:
                raise ValueError(f"rollback Population is missing Candidate Dossier {candidate_id}")
            dossiers.append(store.read_model(matches[-1].relative_to(store.workspace_dir), CandidateDossier))
        return dossiers

    @staticmethod
    def _native_population_scores(store: T4ArtifactStore, population: PopulationSnapshot) -> list[ScoreReport]:
        score_population_id = "P0" if population.generation == 0 else f"U{population.generation}"
        payload = store.read_model(f"ideation/scoring/{score_population_id}.json", _NativeLooseArtifact).payload
        raw_scores = payload.get("scores") if isinstance(payload.get("scores"), list) else []
        by_id = {
            score.candidate_id: score
            for item in raw_scores
            if isinstance(item, dict)
            for score in [ScoreReport.model_validate(item)]
        }
        missing = [candidate_id for candidate_id in population.active_candidate_ids if candidate_id not in by_id]
        if missing:
            raise ValueError("rollback Population is missing independent scores: " + ", ".join(missing))
        return [by_id[candidate_id] for candidate_id in population.active_candidate_ids]

    @staticmethod
    def _native_route_results(store: T4ArtifactStore) -> list[RouteGenerationResult]:
        try:
            payload = store.read_model("ideation/evolution/routes/round_0.json", _NativeLooseArtifact).payload
        except ValueError:
            return []
        raw = payload.get("routes") if isinstance(payload.get("routes"), list) else []
        return [RouteGenerationResult.model_validate(item) for item in raw if isinstance(item, dict)]

    @staticmethod
    def _archive_gate1_projection(workspace_dir: Path, *, suffix: str) -> None:
        """Snapshot compatibility projections before replacing their active view."""

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = workspace_dir / "ideation" / "evolution" / "projection_archive" / f"{stamp}_{suffix}"
        for rel in (
            "ideation/_pass1_forward_candidates.json",
            "ideation/_pass2_grounding_review.json",
            "ideation/_candidate_directions.json",
            "ideation/_family_distribution.md",
            "ideation/_gate1_candidate_cards.md",
            "ideation/_gate1_selection_brief.md",
            "ideation/bridge_coverage_review.json",
        ):
            source = workspace_dir / rel
            if source.is_file():
                archive.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, archive / source.name)

    def _native_t4_readonly_result(self, workspace_dir: Path, directive: IdeaDirective) -> dict[str, Any]:
        """Build a compact read-only explanation from durable Candidate artifacts."""

        population, dossiers = current_population_context(workspace_dir)
        if directive.action == "show_more":
            portfolio_path = workspace_dir / "ideation" / "portfolio.json"
            try:
                portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                portfolio = {}
            displayed = {
                str(value)
                for value in [portfolio.get("lead_id"), *(portfolio.get("alternative_ids") or []), *(portfolio.get("high_upside_ids") or [])]
                if str(value or "").strip()
            }
            remaining = [candidate_id for candidate_id in population.active_candidate_ids if candidate_id not in displayed]
            return {
                "title": "Remaining active Population",
                "summary": "These Candidates remain active but are outside the current display Portfolio. Viewing them does not call a model or change any version.",
                "kind": "remaining_population",
                "candidates": [self._native_candidate_summary(dossiers[candidate_id]) for candidate_id in remaining],
                "artifact": f"ideation/populations/{population.population_id}.json",
            }
        if directive.action == "show_archive":
            return {
                "title": "Archived Candidates",
                "summary": "Archived Candidates are retained for audit and can be revisited through Rollback or a targeted Evolution request.",
                "kind": "archive",
                "candidate_ids": population.archived_candidate_ids,
                "artifact": f"ideation/populations/{population.population_id}.json",
            }
        candidate_id = directive.target_candidate_ids[0]
        candidate = dossiers[candidate_id]
        detail_by_action = {
            "inspect_score": {"label": "Score", "path": candidate.score_report_path or f"ideation/scoring/U{population.generation}.json"},
            "inspect_evidence": {"label": "Evidence", "path": "ideation/evidence/evidence_index.jsonl"},
            "inspect_lineage": {"label": "Lineage", "path": f"ideation/candidates/{candidate.candidate_id}.v{candidate.version}.json"},
            "inspect_hypotheses": {"label": "Draft hypotheses", "items": [item.statement for item in candidate.hypotheses]},
            "inspect_contributions": {"label": "Contributions", "items": [item.statement for item in candidate.contributions]},
            "inspect_genome": {"label": "Idea Genome", "path": f"ideation/candidates/{candidate.candidate_id}.v{candidate.version}.json"},
        }
        detail = detail_by_action.get(directive.action, {})
        return {
            "title": f"{detail.get('label', 'Candidate')} · {candidate_id}",
            "summary": "This is a read-only view based on the current Candidate artifacts. No model call, Population change, or merge is performed.",
            "kind": directive.action,
            "candidate": self._native_candidate_summary(candidate),
            "detail": detail,
            "artifact_paths": candidate.artifact_paths,
        }

    @staticmethod
    def _native_candidate_summary(candidate: CandidateDossier) -> dict[str, Any]:
        presentation = candidate.presentation
        return {
            "candidate_id": candidate.candidate_id,
            "title": presentation.display_title if presentation else candidate.candidate_id,
            "one_line_thesis": str(candidate.genome.core_thesis.value),
            "family_hint": str(candidate.genome.problem.value),
            "main_risk": str(candidate.genome.risks.value),
            "maturity": candidate.maturity.value,
        }

    def _resolve_t4_prerun_gate(
        self,
        state: StateYaml,
        gate_result: dict[str, Any],
        workspace_dir: Path,
    ) -> StateYaml:
        """Persist a T4 configuration or pause after a read-only preflight action."""

        option_id = str(gate_result.get("option_id") or gate_result.get("key") or "").strip()
        captured = gate_result.get("captured") if isinstance(gate_result.get("captured"), dict) else {}
        if option_id in {"pause", "inspect_materials"}:
            state.pending_gate = None
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            state.last_error = (
                "T4 input materials were inspected; resume returns to the T4 run confirmation."
                if option_id == "inspect_materials"
                else "T4 is paused before any model call; resume returns to the T4 run confirmation."
            )
            return state

        mode_by_option = {
            "start_standard": "standard",
            "start_quick": "quick",
            "start_deep": "deep",
            "start_auto": "auto",
        }
        if option_id == "adjust":
            directive = parse_t4_prerun_intent(str(captured.get("settings") or ""))
            if directive.action != "start":
                state.pending_gate = None
                state.status = "PAUSED"
                state.paused_at = _now_iso()
                state.last_error = "T4 configuration needs a start mode before the run can begin."
                return state
        elif option_id in mode_by_option:
            directive = parse_t4_prerun_intent(mode_by_option[option_id])
        else:
            raise KeyError(f"Unsupported T4 pre-run option: {option_id}")

        inspection = inspect_t4_inputs(workspace_dir)
        if inspection.status == "blocked":
            return self._pause_for_t4_prerun_gate(state, workspace_dir)
        suggested_profile = suggest_target_profile(workspace_dir)
        profile_instruction = str(captured.get("publication_orientation") or "")
        target_profile = parse_target_profile_instruction(profile_instruction, suggested=suggested_profile)
        config = default_run_config(
            load_t4_evolution_settings(),
            directive,
            target_profile=target_profile,
        )
        store = T4ArtifactStore(workspace_dir)
        store.write_run_config(config)
        store.write_json(
            "ideation/t4_target_profile.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_target_profile",
                **model_dump(target_profile, mode="json"),
            },
        )
        store.write_json(
            "ideation/evolution/pre_run_confirmation.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_pre_run_confirmation",
                "input_fingerprint": inspection.input_fingerprint,
                "run_config_fingerprint": run_config_fingerprint(config),
                "selected_option": option_id,
                "captured": captured,
                "target_profile": model_dump(target_profile, mode="json"),
                "inspection_status": inspection.status,
                "confirmed_at": _now_iso(),
            },
        )
        state.task_context["t4_run_config_path"] = "ideation/t4_run_config.json"
        state.task_context["t4_target_profile_path"] = "ideation/t4_target_profile.json"
        state.task_context["t4_input_fingerprint"] = inspection.input_fingerprint
        state.pending_gate = None
        state.status = "RUNNING"
        state.paused_at = None
        state.last_error = None
        return state

    def _transition_to_next(
        self,
        state: StateYaml,
        next_task: str | None,
        *,
        workspace_dir: Path | None = None,
    ) -> StateYaml:
        """统一处理正常 next / terminal next / 特殊占位 next。"""
        if next_task is None or next_task == "__terminal__":
            state.status = "COMPLETED"
            return state
        if next_task == "__fail__":
            state.status = "FAILED"
            return state

        next_task = self._resolve_special_target(
            current_task=state.current_task,
            next_task=next_task,
            workspace_dir=workspace_dir,
        )

        target = self.nodes[next_task]
        state.current_task = next_task
        if target.terminal:
            state.status = "FAILED" if next_task.lower().startswith("fail") else "COMPLETED"
        else:
            state.status = "RUNNING"
        return state

    def _gate_id_for_node(self, node: TaskNode) -> str:
        if isinstance(node.gate, dict):
            return str(node.gate.get("id") or node.gate.get("ref") or node.gate.get("type"))
        return str(node.gate)

    def _find_gate(self, gate_id: str) -> dict[str, Any]:
        gate = self.gates.get(gate_id)
        if gate is None:
            raise KeyError(f"Gate '{gate_id}' not found in gates config")
        return gate

    def _resolve_branch(
        self,
        node: TaskNode,
        gate_result: dict[str, Any],
        state: StateYaml,
        *,
        workspace_dir: Path | None = None,
    ) -> str:
        """根据 gate 选择计算下一跳。

        支持两类配置：
        - `gate.options[*].next`
        - `branches: {option_id: next_task}`
        """
        option_id = gate_result.get("option_id") or gate_result.get("key")
        gate_spec = self.gates.get(self._gate_id_for_node(node), {})
        option = self._find_option(gate_spec, option_id) or self._find_option_from_node(node, option_id)
        next_state = None
        if option is not None:
            next_state = option.get("next")
            if option.get("extra"):
                state.task_context.update(option["extra"])

        branches = dict(node.branches or {})
        if isinstance(node.gate, dict):
            branches.update(node.gate.get("branches", {}))
        branches.update(gate_spec.get("branches", {}))
        if next_state is None:
            next_state = branches.get(option_id)
        if next_state is None:
            raise KeyError(f"Gate option '{option_id}' has no branch mapping")

        next_state = self._resolve_special_target(
            current_task=state.current_task,
            next_task=next_state,
            workspace_dir=workspace_dir,
        )

        # Returning to a previously completed task from a human gate is a new,
        # auditable decision, not an autonomous same-parameter loop.  The
        # directive is consumed after that target task completes successfully;
        # interrupted resumes retain it and remain protected by the deadlock
        # guard for that same decision.
        human_directed_iteration = next_state in self.nodes and self._is_iteration(next_state, state)
        if human_directed_iteration:
            state.task_context["human_iteration_directive"] = {
                "decision_id": uuid.uuid4().hex,
                "gate_id": self._gate_id_for_node(node),
                "source_task": node.task_id,
                "target_task": next_state,
                "option_id": str(option_id or ""),
            }
            state.iteration_count[next_state] = state.iteration_count.get(next_state, 0) + 1

        if next_state in self.nodes:
            limit = self.nodes[next_state].max_iterations
            if (
                not human_directed_iteration
                and limit is not None
                and state.iteration_count.get(next_state, 0) >= limit
            ):
                if "ITER_LIMIT_GATE" in self.nodes:
                    return "ITER_LIMIT_GATE"
        return next_state

    def _persist_immediate_gate_result(
        self,
        node: TaskNode,
        gate_result: dict[str, Any],
        next_task: str,
        workspace_dir: Path | None,
    ) -> None:
        """Persist the user decision for gate-only nodes that declare a JSON output."""

        if workspace_dir is None or not (node.extra or {}).get("immediate_gate"):
            return
        if node.task_id == "T2-PARAM-GATE":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "survey_balanced")
            captured = gate_result.get("captured") or {}
            payload = build_literature_param_payload(
                selected_option=option_id,
                captured=captured if isinstance(captured, dict) else {},
                workspace_dir=workspace_dir,
            )
            payload["task_id"] = node.task_id
            payload["gate_id"] = self._gate_id_for_node(node)
            payload["next_task"] = next_task
            payload["input_fingerprints"] = build_input_fingerprints(
                workspace_dir,
                _T2_LITERATURE_PARAM_GATE_INPUT_PATHS,
            )
            payload["decided_at"] = _now_iso()
            path = workspace_dir / "literature" / "literature_params.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return
        if node.task_id == "T2-PARAM-CONFIRM-GATE":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "confirm_start_t2")
            captured = gate_result.get("captured") or {}
            if not isinstance(captured, dict):
                captured = {}
            params_path = workspace_dir / "literature" / "literature_params.json"
            params = self._read_json_dict(params_path) or {}
            human_interaction_id = _interaction_id_for_gate_result(
                task_id=node.task_id,
                gate_id=self._gate_id_for_node(node),
                selected_option=option_id,
                captured=captured,
            )
            _record_runtime_gate_interaction(
                workspace_dir,
                interaction_id=human_interaction_id,
                task_id=node.task_id,
                gate_id=self._gate_id_for_node(node),
                selected_option=option_id,
                captured=captured,
            )
            confirmed = option_id in {"confirm_start_t2", "confirm", "start", "continue"}
            payload = {
                "semantics": "human_final_confirmed_t2_literature_parameters_before_scout",
                "task_id": node.task_id,
                "gate_id": self._gate_id_for_node(node),
                "selected_option": option_id,
                "confirmed_to_start_t2": confirmed,
                "captured": captured,
                "next_task": next_task,
                "human_interaction_id": human_interaction_id,
                "selected_parameters_summary": params.get("selected_summary") or {},
                "confirmation_summary": params.get("confirmation_summary") or "",
                "parameter_source": "literature/literature_params.json",
                "input_fingerprints": build_input_fingerprints(
                    workspace_dir,
                    _T2_LITERATURE_PARAM_CONFIRM_GATE_INPUT_PATHS,
                ),
                "decided_at": _now_iso(),
            }
            if option_id == "revise_params":
                payload["decision_summary"] = "Return to T2-PARAM-GATE before starting T2."
            elif option_id == "stop_project":
                payload["decision_summary"] = "Stop the project before starting T2."
            else:
                payload["decision_summary"] = "Start T2 with the confirmed workspace-local literature parameters."
            path = workspace_dir / "literature" / "literature_params_confirmation.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return
        if node.task_id == "T2-COVERAGE-GATE":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "continue_to_t3")
            captured = gate_result.get("captured") or {}
            payload = {
                "semantics": "human_confirmed_t2_retrieval_coverage_before_t3",
                "task_id": node.task_id,
                "gate_id": self._gate_id_for_node(node),
                "selected_option": option_id,
                "captured": captured if isinstance(captured, dict) else {},
                "next_task": next_task,
                "coverage_summary": _coverage_gate_summary(workspace_dir),
                "input_fingerprints": build_input_fingerprints(
                    workspace_dir,
                    _T2_COVERAGE_GATE_INPUT_PATHS,
                ),
                "decided_at": _now_iso(),
            }
            if option_id == "continue_to_t3":
                payload["decision_summary"] = (
                    "Proceed to T3 with the current verified corpus and deep_read_queue; "
                    "missing_areas.md remains a retrieval coverage hint, not a final research gap."
                )
            elif option_id == "rerun_t2_expand":
                payload["decision_summary"] = "Return to T2 for user-requested expansion or query adjustment."
            else:
                payload["decision_summary"] = "Stop or pause the project after T2 coverage review."
            path = workspace_dir / "literature" / "coverage_decision.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return
        if node.task_id == "T3.6-GATE-SURVEY":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "")
            write_survey = option_id in {
                "yes",
                "yes_targeted_retrieval",
                "write_survey",
                "survey",
                "撰写综述",
            }
            captured = gate_result.get("captured") if isinstance(gate_result.get("captured"), dict) else {}
            retrieval_preference = str(captured.get("survey_retrieval_preference") or "").strip()
            payload = {
                "write_survey": write_survey,
                "user_answer": option_id,
                "selected_option": option_id,
                "survey_retrieval_preference": retrieval_preference or (
                    "targeted_supplement_before_writing" if option_id == "yes_targeted_retrieval" else "current_corpus_only"
                ),
                "note": (
                    "taxonomy-driven survey, not synthesis-to-tex; "
                    f"retrieval_preference={retrieval_preference or ('targeted_supplement_before_writing' if option_id == 'yes_targeted_retrieval' else 'current_corpus_only')}"
                    if write_survey
                    else "skip survey branch and continue T4"
                ),
                "input_fingerprints": build_input_fingerprints(workspace_dir, _T36_SURVEY_GATE_INPUT_PATHS),
                "decided_at": _now_iso(),
            }
            path = workspace_dir / "drafts" / "survey" / "decision.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return
        if node.task_id == "T3.6-TEMPLATE-GATE":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "basic_en")
            payload = _template_selection_from_gate(
                task_id=node.task_id,
                gate_id=self._gate_id_for_node(node),
                option_id=option_id,
                gate_result=gate_result,
                next_task=next_task,
                workspace_dir=workspace_dir,
            )
            payload["note"] = "T3.6 survey template/language selection before PLAN"
            path = workspace_dir / "drafts" / "survey" / "writing_template.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return
        if node.task_id == "T3.6-GATE-CORPUS":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "")
            scope = "complete" if option_id in {"complete", "full", "expand", "补检", "完整"} else "conservative"
            payload = {
                "scope": scope,
                "selected_option": option_id,
                "note": (
                    "one-shot targeted survey expansion plan"
                    if scope == "complete"
                    else "use existing T2/T3 corpus only"
                ),
                "input_fingerprints": build_input_fingerprints(workspace_dir, _T36_CORPUS_GATE_INPUT_PATHS),
                "decided_at": _now_iso(),
            }
            path = workspace_dir / "drafts" / "survey" / "corpus_decision.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return
        if node.task_id == "T3.6-POST-SURVEY-GATE":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "continue_to_t4")
            continue_to_t4 = option_id in {"continue_to_t4", "continue", "t4", "idea", "生成idea"}
            payload = {
                "semantics": "human_confirmed_post_survey_next_step",
                "task_id": node.task_id,
                "gate_id": self._gate_id_for_node(node),
                "selected_option": option_id,
                "continue_to_t4": continue_to_t4,
                "captured": gate_result.get("captured") or {},
                "next_task": next_task,
                "input_fingerprints": build_input_fingerprints(workspace_dir, _T36_POST_SURVEY_GATE_INPUT_PATHS),
                "decided_at": _now_iso(),
            }
            payload["decision_summary"] = (
                "Continue to T4 ideation using survey_insights as idea fuel."
                if continue_to_t4
                else "Finish the project after T3.6 survey outputs."
            )
            path = workspace_dir / "drafts" / "survey" / "post_survey_decision.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return
        if node.task_id == "T4-GATE1":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "")
            captured = gate_result.get("captured") or {}
            if option_id == "reanalyze":
                payload = {
                    "semantics": "t4_gate1_reanalysis_request",
                    "task_id": node.task_id,
                    "gate_id": self._gate_id_for_node(node),
                    "selected_option": option_id,
                    "captured": captured,
                    "candidate_pool_fingerprints": _t4_gate1_candidate_pool_fingerprints(workspace_dir),
                    "next_task": next_task,
                    "decided_at": _now_iso(),
                }
                path = workspace_dir / "ideation" / "_gate1_reanalysis_request.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                selection_path = workspace_dir / "ideation" / "_gate1_user_selection.json"
                if selection_path.exists():
                    selection_path.unlink()
                for rel in (
                    "ideation/_pass1_forward_candidates.json",
                    "ideation/_pass2_grounding_review.json",
                    "ideation/_candidate_directions.json",
                    "ideation/_family_distribution.md",
                    "ideation/_gate1_candidate_cards.md",
                    "ideation/_gate1_selection_brief.md",
                    "ideation/bridge_coverage_review.json",
                ):
                    artifact = workspace_dir / rel
                    if artifact.exists() and artifact.is_file():
                        archive_dir = workspace_dir / "ideation" / "_gate1_reanalysis_archive"
                        archive_dir.mkdir(parents=True, exist_ok=True)
                        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                        artifact.replace(archive_dir / f"{stamp}_{artifact.name}")
                return
            candidate_pool_fingerprints = _t4_gate1_candidate_pool_fingerprints(workspace_dir)
            fingerprint_payload = {
                "semantics": "t4_gate1_selection_fingerprint",
                "gate_id": self._gate_id_for_node(node),
                "selected_option": option_id,
                "captured": captured,
                "candidate_pool_fingerprints": candidate_pool_fingerprints,
            }
            payload = {
                "semantics": "t4_gate1_user_selection_for_candidate_pool",
                "task_id": node.task_id,
                "gate_id": self._gate_id_for_node(node),
                "selected_option": option_id,
                "captured": captured,
                "candidate_pool_fingerprints": candidate_pool_fingerprints,
                "selection_fingerprint": _stable_json_fingerprint(fingerprint_payload),
                "next_task": next_task,
                "decided_at": _now_iso(),
            }
            path = workspace_dir / "ideation" / "_gate1_user_selection.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            if option_id == "select_or_reframe":
                selected_candidate_id = selected_candidate_id_from_gate_input(workspace_dir, captured)
                if selected_candidate_id:
                    payload["selected_candidate_id"] = selected_candidate_id
                    payload["pre_novelty_artifacts"] = compile_pre_novelty_hypothesis_brief(
                        workspace_dir,
                        selection_fingerprint=payload["selection_fingerprint"],
                        selected_candidate_id=selected_candidate_id,
                    )
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _write_t4_selected_idea_brief_stub(
                workspace_dir,
                gate_id=self._gate_id_for_node(node),
                option_id=option_id,
                captured=captured,
                selection_fingerprint=payload["selection_fingerprint"],
                next_task=next_task,
            )
            return
        if node.task_id == "T8-STYLE-GATE":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "ccf_neurips")
            payload = _template_selection_from_gate(
                task_id=node.task_id,
                gate_id=self._gate_id_for_node(node),
                option_id=option_id,
                gate_result=gate_result,
                next_task=next_task,
                workspace_dir=workspace_dir,
            )
            payload["semantics"] = "human_confirmed_t8_writing_style_and_template"
            payload["note"] = "T8 writing style/language/template selection before RESOURCE"
            path = workspace_dir / "drafts" / "writing_style.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return
        if node.task_id == "T5-EXECUTOR-GATE":
            if next_task == "T5-HANDOFF":
                outputs = node.outputs or {}
                for rel_path in outputs.values():
                    path = workspace_dir / rel_path
                    if path.suffix.lower() != ".json":
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    payload = {
                        "semantics": "external_executor_selection_deferred_for_handoff_rebuild",
                        "task_id": node.task_id,
                        "gate_id": self._gate_id_for_node(node),
                        "selected_option": gate_result.get("option_id") or gate_result.get("key"),
                        "next_task": next_task,
                        "decided_at": _now_iso(),
                    }
                    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    return
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "codex_cli")
            aliases = {
                "mock": "mock_dry_run",
                "dry": "mock_dry_run",
                "dry_run": "mock_dry_run",
                "external_ready_later": "claude_code_window",
                "claude": "claude_code_window",
                "manual_external": "manual",
            }
            selected_executor = aliases.get(option_id, option_id)
            if selected_executor not in {"mock_dry_run", "codex_cli", "claude_code_window", "manual"}:
                selected_executor = "codex_cli"
            captured = gate_result.get("captured") or {}
            notes = str(captured.get("notes") or captured.get("note") or "")
            if captured.get("downgraded_from"):
                downgrade_note = (
                    f"downgraded_from={captured.get('downgraded_from')}; "
                    f"reason={captured.get('downgrade_reason') or 'not specified'}"
                )
                notes = f"{notes}; {downgrade_note}".strip("; ")
            selection = build_executor_selection_payload(
                selected_executor=selected_executor,
                selected_by="human",
                notes=notes,
            )
            selection["next_state"] = next_task
            path = workspace_dir / "external_executor" / "executor_selection.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            patch_external_executor_files_with_selection(workspace_dir, selection)
            return
        if node.task_id == "T5-EXPR-MATERIAL-GATE":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "pause_for_materials")
            captured = gate_result.get("captured") or {}
            expr_dir = workspace_dir / "external_executor" / "expr"
            expr_dir.mkdir(parents=True, exist_ok=True)
            files = []
            for path in sorted(expr_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(workspace_dir).as_posix()
                files.append(
                    {
                        "path": rel,
                        "bytes": path.stat().st_size,
                        "sha256": _sha256_file(path),
                    }
                )
            payload = {
                "version": "1.0",
                "semantics": "external_executor_expr_materials_gate_decision",
                "task_id": node.task_id,
                "gate_id": self._gate_id_for_node(node),
                "selected_option": option_id,
                "materials_ready": option_id == "materials_ready",
                "captured": captured if isinstance(captured, dict) else {},
                "next_task": next_task,
                "expr_dir": "external_executor/expr",
                "expr_snapshot": files,
                "decided_at": _now_iso(),
                "resume_instruction": "After placing materials, run: python -m researchos.cli resume --workspace <workspace>",
            }
            path = workspace_dir / "external_executor" / "expr" / "materials_gate_decision.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return
        outputs = node.outputs or {}
        for rel_path in outputs.values():
            path = workspace_dir / rel_path
            if path.suffix.lower() != ".json":
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "semantics": "human_decision_over_agent_recommendation",
                "task_id": node.task_id,
                "gate_id": self._gate_id_for_node(node),
                "selected_option": gate_result.get("option_id") or gate_result.get("key"),
                "captured": gate_result.get("captured") or {},
                "next_task": next_task,
                "decided_at": _now_iso(),
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return

    def _resolve_special_target(
        self,
        *,
        current_task: str,
        next_task: str,
        workspace_dir: Path | None,
    ) -> str:
        """解析状态机里的特殊占位目标。"""
        if next_task != "__parse_from_output__":
            return next_task
        if workspace_dir is None:
            raise ValueError("workspace_dir is required for __parse_from_output__ targets")

        if current_task == "T7.5":
            return self._parse_t75_decision(workspace_dir)
        if current_task == "T4.5":
            return self._parse_t45_verdict(workspace_dir)
        if current_task == "T3.6-GATE-SURVEY":
            return self._parse_t36_survey_decision(workspace_dir)
        if current_task == "T3.6-GATE-CORPUS":
            return self._parse_t36_corpus_decision(workspace_dir)
        if current_task == "T3.6-POST-SURVEY-GATE":
            return self._parse_t36_post_survey_decision(workspace_dir)
        if current_task == "T2-PARAM-CONFIRM-GATE":
            return self._parse_t2_param_confirmation(workspace_dir)
        if current_task == "T5-EXPR-MATERIAL-GATE":
            return self._parse_t5_expr_material_decision(workspace_dir)

        raise ValueError(f"Unsupported __parse_from_output__ task: {current_task}")

    def _parse_t45_verdict(self, workspace_dir: Path) -> str:
        """Route T4.5 according to the explicit Final Gate Verdict in novelty_audit.md."""

        human_review = "T4.5-HUMAN-REVIEW" if "T4.5-HUMAN-REVIEW" in self.nodes else "failed"
        audit_path = workspace_dir / "ideation" / "novelty_audit.md"
        if not audit_path.exists():
            return human_review
        if not _file_newer_than_existing_inputs(
            audit_path,
            [
                workspace_dir / "ideation" / "hypothesis_brief.yaml",
                workspace_dir / "ideation" / "selected" / "selected_candidate.json",
                workspace_dir / "ideation" / "selected" / "t45_search_targets.json",
                workspace_dir / "ideation" / "idea_scorecard.yaml",
                workspace_dir / "ideation" / "gate_decisions.json",
                workspace_dir / "literature" / "synthesis.md",
                workspace_dir / "literature" / "synthesis_workbench.json",
                workspace_dir / "literature" / "comparison_table.csv",
            ],
        ):
            return human_review
        ok, _err = validate_t45_fingerprint_report(workspace_dir)
        if not ok:
            return human_review

        text = audit_path.read_text(encoding="utf-8", errors="replace")
        verdict_text = _extract_t45_final_gate_verdict(text)
        if not verdict_text:
            return human_review

        normalized = verdict_text.lower().replace("-", "_").replace(" ", "_")
        if any(token in normalized for token in ("return_to_t4", "return_tot4", "reframe", "回到t4", "回退t4")):
            return human_review
        if any(token in normalized for token in ("drop_due_to_collision", "drop", "collision", "reject", "fail")):
            return human_review
        verdict_token = re.split(r"[^a-z0-9_]+", normalized, maxsplit=1)[0]
        pass_tokens = {
            "pass",
            "passed",
            "pass_to_experiment",
            "pass_with_required_baselines",
            "go_t7",
            "continue_to_t7",
            "continue_to_experiment",
        }
        if verdict_token in pass_tokens:
            formal_ok, _formal_error = _validate_t45_post_novelty_formalization(workspace_dir, audit_path)
            if not formal_ok:
                return human_review
            if "T5-REBOOST-GATE" in self.nodes:
                return "T5-REBOOST-GATE"
            if "T5-HANDOFF" in self.nodes:
                return "T5-HANDOFF"
            return "T7" if "T7" in self.nodes else "failed"
        return human_review

    def _parse_t36_survey_decision(self, workspace_dir: Path) -> str:
        """Route the optional T3.6 survey branch from drafts/survey/decision.json."""

        path = workspace_dir / "drafts" / "survey" / "decision.json"
        data = self._read_json_dict(path)
        if data is None:
            return "T3.6-GATE-SURVEY" if "T3.6-GATE-SURVEY" in self.nodes else "failed"
        fingerprints = data.get("input_fingerprints")
        if fingerprints is not None:
            ok, _ = validate_input_fingerprints(
                workspace_dir,
                fingerprints,
                _T36_SURVEY_GATE_INPUT_PATHS,
                label_for_error="T3.6 survey gate decision",
            )
            if not ok:
                return "T3.6-GATE-SURVEY" if "T3.6-GATE-SURVEY" in self.nodes else "failed"
        decision = data.get("write_survey")
        if isinstance(decision, str):
            decision = decision.strip().lower() in {
                "yes",
                "yes_targeted_retrieval",
                "true",
                "1",
                "write",
                "survey",
                "撰写",
                "是",
            }
        if decision:
            if "T3.6-TEMPLATE-GATE" in self.nodes:
                template_path = workspace_dir / "drafts" / "survey" / "writing_template.json"
                if _valid_template_selection_file(template_path):
                    return "T3.6-PLAN" if "T3.6-PLAN" in self.nodes else "T4"
                return "T3.6-TEMPLATE-GATE"
            return "T3.6-PLAN" if "T3.6-PLAN" in self.nodes else "T4"
        return "T4" if "T4" in self.nodes else "failed"

    def _parse_t36_corpus_decision(self, workspace_dir: Path) -> str:
        """Route the survey corpus-scope gate from drafts/survey/corpus_decision.json."""

        path = workspace_dir / "drafts" / "survey" / "corpus_decision.json"
        data = self._read_json_dict(path)
        if data is None:
            return "T3.6-GATE-CORPUS" if "T3.6-GATE-CORPUS" in self.nodes else "failed"
        fingerprints = data.get("input_fingerprints")
        if fingerprints is not None:
            ok, _ = validate_input_fingerprints(
                workspace_dir,
                fingerprints,
                _T36_CORPUS_GATE_INPUT_PATHS,
                label_for_error="T3.6 corpus gate decision",
            )
            if not ok:
                return "T3.6-GATE-CORPUS" if "T3.6-GATE-CORPUS" in self.nodes else "failed"
        scope = str(data.get("scope") or data.get("corpus_scope") or "").strip().lower()
        if scope in {"complete", "full", "expand", "完整", "补检", "定向补检"}:
            return "T3.6-EXPAND" if "T3.6-EXPAND" in self.nodes else "T3.6-STATE"
        return "T3.6-STATE" if "T3.6-STATE" in self.nodes else "T4"

    def _parse_t36_post_survey_decision(self, workspace_dir: Path) -> str:
        """Route after survey completion according to the explicit user decision."""

        path = workspace_dir / "drafts" / "survey" / "post_survey_decision.json"
        data = self._read_json_dict(path)
        if data is None:
            return "T3.6-POST-SURVEY-GATE" if "T3.6-POST-SURVEY-GATE" in self.nodes else "T4"
        fingerprints = data.get("input_fingerprints")
        if fingerprints is not None:
            ok, _ = validate_input_fingerprints(
                workspace_dir,
                fingerprints,
                _T36_POST_SURVEY_GATE_INPUT_PATHS,
                label_for_error="T3.6 post-survey gate decision",
            )
            if not ok:
                return "T3.6-POST-SURVEY-GATE" if "T3.6-POST-SURVEY-GATE" in self.nodes else "T4"
        selected = str(data.get("selected_option") or "").strip().lower()
        continue_to_t4 = data.get("continue_to_t4")
        if isinstance(continue_to_t4, str):
            continue_to_t4 = continue_to_t4.strip().lower() in {"true", "1", "yes", "continue", "t4"}
        if continue_to_t4 or selected in {"continue_to_t4", "continue", "t4"}:
            return "T4" if "T4" in self.nodes else "done"
        return "done" if "done" in self.nodes else "T4"

    def _parse_t2_param_confirmation(self, workspace_dir: Path) -> str:
        """Route T2 parameter confirmation from its explicit decision file."""

        path = workspace_dir / "literature" / "literature_params_confirmation.json"
        data = self._read_json_dict(path)
        if data is None:
            return "T2-PARAM-CONFIRM-GATE" if "T2-PARAM-CONFIRM-GATE" in self.nodes else "T2"
        fingerprints = data.get("input_fingerprints")
        if fingerprints is not None:
            ok, _ = validate_input_fingerprints(
                workspace_dir,
                fingerprints,
                _T2_LITERATURE_PARAM_CONFIRM_GATE_INPUT_PATHS,
                label_for_error="T2 parameter confirmation",
            )
            if not ok:
                return "T2-PARAM-CONFIRM-GATE" if "T2-PARAM-CONFIRM-GATE" in self.nodes else "T2"
        selected = str(data.get("selected_option") or "").strip().lower()
        if data.get("confirmed_to_start_t2") is True or selected in {"confirm_start_t2", "confirm", "start"}:
            return "T2"
        if selected in {"revise_params", "revise", "back"}:
            return "T2-PARAM-GATE" if "T2-PARAM-GATE" in self.nodes else "T2"
        return "done" if "done" in self.nodes else "failed"

    def _parse_t5_expr_material_decision(self, workspace_dir: Path) -> str:
        """Route the T5 experiment-material gate from its explicit decision file."""

        path = workspace_dir / "external_executor" / "expr" / "materials_gate_decision.json"
        data = self._read_json_dict(path)
        if data is None:
            return "T5-EXPR-MATERIAL-GATE"
        selected = str(data.get("selected_option") or "").strip().lower()
        if data.get("materials_ready") is True or selected in {"materials_ready", "ready", "continue", "done"}:
            return "T5-EXECUTOR-GATE"
        if selected in {"back_to_t4", "t4", "rethink"}:
            return "T4"
        if selected in {"stop_project", "stop", "done"}:
            return "done" if "done" in self.nodes else "failed"
        return "T5-EXPR-MATERIAL-GATE"

    @staticmethod
    def _read_json_dict(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _parse_t75_decision(self, workspace_dir: Path) -> str:
        """T7.5 完成后，解析 evaluation_decision.md 的推荐下一步。"""

        default_t8_entry = self._default_t8_entry(workspace_dir)
        decision_path = workspace_dir / "evaluation" / "evaluation_decision.md"
        if not decision_path.exists():
            return default_t8_entry

        text = decision_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"next_task:\s*([A-Za-z0-9_.-]+)", text, re.DOTALL)
        if match is None:
            return default_t8_entry

        raw_target = match.group(1).strip()
        aliases = {
            "T5": self._default_experiment_entry(),
            "T6": self._default_experiment_entry(),
            "T7": self._default_experiment_entry(),
            "T8": self._default_t8_entry(workspace_dir),
            "T8-WRITE": self._default_t8_entry(workspace_dir),
            "T8-SEC-LIMITATIONS": "T8-SEC-CONCLUSION",
            "terminate": "done",
            "terminal": "done",
            "stop": "done",
            "end": "done",
        }
        target = aliases.get(raw_target, raw_target)
        if target not in self.nodes and raw_target in self.nodes:
            return raw_target
        if target not in self.nodes:
            return default_t8_entry
        return target

    def _default_experiment_entry(self) -> str:
        if "T5-REBOOST-GATE" in self.nodes:
            return "T5-REBOOST-GATE"
        if "T5-HANDOFF" in self.nodes:
            return "T5-HANDOFF"
        if "T7" in self.nodes:
            return "T7"
        if "T5" in self.nodes:
            return "T5"
        return "failed"

    def _default_t8_entry(self, workspace_dir: Path | None = None) -> str:
        if "T8-STYLE-GATE" in self.nodes:
            if workspace_dir is not None and _valid_writing_style_file(workspace_dir / "drafts" / "writing_style.json"):
                if "T8-RESOURCE" in self.nodes:
                    return "T8-RESOURCE"
            return "T8-STYLE-GATE"
        if "T8-RESOURCE" in self.nodes:
            return "T8-RESOURCE"
        if "T8-WRITE" in self.nodes:
            return "T8-WRITE"
        return "done"

    @staticmethod
    def _find_option(gate_spec: dict[str, Any], option_id: str | None) -> dict[str, Any] | None:
        for option in gate_spec.get("options", []):
            key = option.get("id") or option.get("key")
            if key == option_id:
                return option
        return None

    @staticmethod
    def _find_option_from_node(node: TaskNode, option_id: str | None) -> dict[str, Any] | None:
        if not isinstance(node.gate, dict):
            return None
        for option in node.gate.get("options", []):
            key = option.get("id") or option.get("key")
            if key == option_id:
                return option
        return None

    def _is_iteration(self, next_state: str, state: StateYaml) -> bool:
        return any(history.task == next_state and history.status == "DONE" for history in state.history)

    @staticmethod
    def _build_overrides(
        node: TaskNode,
    ) -> tuple[LLMConfigOverride, BudgetOverride, ToolPolicyOverride]:
        """把节点里的 llm/budget/tools 块转换成 ExecutionContext override。"""
        llm_block = node.llm or {}
        llm_ov = LLMConfigOverride(
            profile=llm_block.get("profile"),
            tier=llm_block.get("tier"),
            model=llm_block.get("model"),
            endpoint=llm_block.get("endpoint"),
            max_context=llm_block.get("max_context"),
            temperature=llm_block.get("temperature"),
        )

        budget_block = node.budget or {}
        budget_ov = BudgetOverride(
            max_steps=budget_block.get("max_steps"),
            max_tokens=budget_block.get("max_tokens"),
            max_wall_seconds=budget_block.get("max_wall_seconds"),
            unlimited_budget=_budget_has_unlimited_tag(budget_block, node.tags),
        )

        tools_block = node.tools or {}
        tool_ov = ToolPolicyOverride(
            allowed_read_prefixes=tools_block.get("allowed_read_prefixes"),
            allowed_write_prefixes=tools_block.get("allowed_write_prefixes"),
            extra_tool_names=tools_block.get("extra_tool_names", tools_block.get("extra", [])),
        )
        return llm_ov, budget_ov, tool_ov

    def _validate_target(
        self,
        task_id: str,
        field_name: str,
        target: str | None,
        errors: list[str],
    ) -> None:
        if target is None:
            return
        if target in {"__terminal__", "__fail__", "__parse_from_output__"}:
            return
        if target not in self.nodes:
            errors.append(f"{task_id}: {field_name} points to unknown node '{target}'")

    def _validate_gate(self, task_id: str, node: TaskNode, errors: list[str]) -> None:
        if not node.gate:
            return

        gate_id = self._gate_id_for_node(node)
        inline_gate = node.gate if isinstance(node.gate, dict) else {}
        gate_spec = self.gates.get(gate_id, {})
        if gate_id not in self.gates and not inline_gate.get("options"):
            errors.append(f"{task_id}: gate '{gate_id}' not found in gates config")

        for option in list(gate_spec.get("options", [])) + list(inline_gate.get("options", [])):
            next_target = option.get("next")
            if next_target is not None:
                self._validate_target(task_id, f"gate option '{option.get('id') or option.get('key')}'", next_target, errors)

        branch_maps = [
            ("branches", node.branches or {}),
            ("gate.branches", inline_gate.get("branches", {})),
            (f"gates.{gate_id}.branches", gate_spec.get("branches", {})),
        ]
        for field_name, mapping in branch_maps:
            if not isinstance(mapping, dict):
                continue
            for option_id, target in mapping.items():
                self._validate_target(task_id, f"{field_name}.{option_id}", target, errors)

    def _validate_task_contract(self, task_id: str, node: TaskNode, errors: list[str]) -> None:
        """检查节点与 task I/O 契约是否一致。

        这里只对 ResearchOS 已定义 contract 的正式 task 生效；像 `done`/`failed` 这类
        控制节点或自定义调试节点，不做额外限制。
        """

        try:
            contract = get_task_io(task_id)
        except KeyError:
            return

        declared_inputs = dict(node.inputs or {})
        declared_outputs = dict(node.outputs or {})
        declared_outputs.update(dict(node.optional_outputs or {}))
        contract_inputs = dict(contract.get("inputs", {}))
        contract_outputs = dict(contract.get("outputs", {}))
        source_hint = (
            f" [state_machine={self.config_path.resolve()}; "
            f"task_io_contract={task_io_contract_source()}]. "
            "This often means the YAML and Python contract came from different versions; "
            "run `python -m researchos.cli validate-config` from the intended checkout."
        )

        if declared_inputs != contract_inputs:
            errors.append(self._format_contract_mismatch(
                task_id, "inputs", declared_inputs, contract_inputs, source_hint
            ))
        if declared_outputs != contract_outputs:
            errors.append(self._format_contract_mismatch(
                task_id, "outputs", declared_outputs, contract_outputs, source_hint
            ))

    @staticmethod
    def _format_contract_mismatch(
        task_id: str,
        field_name: str,
        declared: dict[str, Any],
        contract: dict[str, Any],
        source_hint: str,
    ) -> str:
        """Describe a contract drift without dumping full configuration maps."""

        missing = [
            f"{name} -> {contract[name]}"
            for name in sorted(set(contract) - set(declared))
        ]
        unexpected = sorted(set(declared) - set(contract))
        changed = [
            f"{name}: {declared[name]} -> {contract[name]}"
            for name in sorted(set(contract) & set(declared))
            if declared[name] != contract[name]
        ]
        def compact(items: list[str], *, limit: int = 6) -> str:
            if len(items) <= limit:
                return ", ".join(items)
            return ", ".join(items[:limit]) + f", +{len(items) - limit} more"

        details: list[str] = []
        if missing:
            details.append("missing " + compact(missing))
        if unexpected:
            details.append("unexpected " + compact(unexpected))
        if changed:
            details.append("path changed " + compact(changed))
        summary = "; ".join(details) or "mapping order or value type differs"
        return f"{task_id}: node.{field_name} does not match task_io_contract ({summary}){source_hint}"

    def _check_budget_drift(self, state: StateYaml, workspace_dir: Path) -> None:
        """检查预算漂移并发出警告（§7.1）。

        如果累计花费超过预算的70%，记录警告；
        如果超过90%，记录严重警告。
        """
        from ..runtime.logger import get_logger

        logger = get_logger("state_machine.budget")

        # 读取project.yaml获取预算上限
        project_file = workspace_dir / "project.yaml"
        if not project_file.exists():
            return

        try:
            project_data = yaml.safe_load(project_file.read_text(encoding="utf-8"))
            max_budget = project_data.get("constraints", {}).get("max_budget_usd")
            if max_budget is None or max_budget <= 0:
                return

            spent = state.budget_cumulative.cost_usd_total
            ratio = spent / max_budget

            if ratio >= 0.9:
                logger.warning(
                    f"预算严重超支警告: 已花费 ${spent:.2f} / ${max_budget:.2f} ({ratio*100:.1f}%)"
                )
                # 写入预算警告文件
                warning_file = workspace_dir / ".researchos" / "budget_warning.txt"
                warning_file.parent.mkdir(parents=True, exist_ok=True)
                warning_file.write_text(
                    f"预算严重超支警告 (90%+)\n"
                    f"已花费: ${spent:.2f}\n"
                    f"预算上限: ${max_budget:.2f}\n"
                    f"使用比例: {ratio*100:.1f}%\n"
                    f"当前任务: {state.current_task}\n"
                    f"时间: {_now_iso()}\n",
                    encoding="utf-8"
                )
            elif ratio >= 0.7:
                logger.warning(
                    f"预算警告: 已花费 ${spent:.2f} / ${max_budget:.2f} ({ratio*100:.1f}%)"
                )
        except Exception as e:
            logger.debug(f"预算检查失败: {e}")

    def _check_iteration_deadlock(self, state: StateYaml, node: TaskNode, workspace_dir: Path | None = None) -> None:
        """检查迭代死锁：相同参数组合尝试3次以上时快速失败。

        Phase 2.3: 防止 Agent 在相同参数上无限迭代。
        """
        from ..runtime.logger import get_logger

        logger = get_logger("state_machine.deadlock")

        task_id = state.current_task
        task_history = state.iteration_history.get(task_id, [])

        if not task_history:
            return

        # 计算当前参数哈希
        current_params = self._extract_task_params(node, state=state, workspace_dir=workspace_dir)
        current_hash = self._compute_param_hash(current_params)

        # 统计相同参数哈希出现次数
        same_param_count = sum(1 for entry in task_history if entry.get("param_hash") == current_hash)

        if same_param_count >= 3:
            error_msg = (
                f"检测到迭代死锁：任务 '{task_id}' 使用相同参数已尝试 {same_param_count} 次。"
                f"\n参数哈希: {current_hash}"
                f"\n参数内容: {current_params}"
                f"\n建议：检查任务配置或修改参数以避免无限循环。"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        if same_param_count >= 2:
            logger.warning(
                f"迭代警告：任务 '{task_id}' 使用相同参数已尝试 {same_param_count} 次，"
                f"再次尝试将触发死锁保护。"
            )

    def _record_iteration_attempt(
        self,
        state: StateYaml,
        node: TaskNode,
        *,
        workspace_dir: Path | None = None,
    ) -> None:
        """记录本次迭代尝试到 iteration_history。

        Phase 2.3: 用于后续死锁检测。
        """
        task_id = state.current_task
        params = self._extract_task_params(node, state=state, workspace_dir=workspace_dir)
        param_hash = self._compute_param_hash(params)

        if task_id not in state.iteration_history:
            state.iteration_history[task_id] = []

        state.iteration_history[task_id].append(
            {
                "param_hash": param_hash,
                "timestamp": _now_iso(),
                "params": params,
            }
        )

    @staticmethod
    def _extract_task_params(
        node: TaskNode,
        *,
        state: StateYaml | None = None,
        workspace_dir: Path | None = None,
    ) -> dict[str, Any]:
        """提取任务的关键参数用于死锁检测。

        包括：inputs, outputs, llm配置, budget配置等影响任务行为的参数。
        """
        params = {}

        if node.inputs:
            params["inputs"] = dict(node.inputs)
        if node.outputs:
            params["outputs"] = dict(node.outputs)
        if node.llm:
            params["llm"] = dict(node.llm)
        if node.budget:
            params["budget"] = dict(node.budget)
        if node.mode:
            params["mode"] = node.mode
        if node.extra:
            params["extra"] = dict(node.extra)

        if state is not None:
            directive = state.task_context.get("human_iteration_directive")
            if isinstance(directive, dict) and str(directive.get("target_task") or "") == node.task_id:
                params["human_iteration_directive"] = {
                    "decision_id": str(directive.get("decision_id") or ""),
                    "gate_id": str(directive.get("gate_id") or ""),
                    "source_task": str(directive.get("source_task") or ""),
                    "option_id": str(directive.get("option_id") or ""),
                }

        if node.task_id == "T4" and state is not None:
            selection_fingerprint = ""
            if workspace_dir is not None:
                selection_path = workspace_dir / "ideation" / "_gate1_user_selection.json"
                if selection_path.exists() and selection_path.stat().st_size > 0:
                    try:
                        data = json.loads(selection_path.read_text(encoding="utf-8"))
                        if isinstance(data, dict):
                            selection_fingerprint = str(data.get("selection_fingerprint") or "").strip()
                    except Exception:
                        selection_fingerprint = ""
            params["t4_gate_phase"] = "post_gate1" if selection_fingerprint else "pre_gate1"
            if selection_fingerprint:
                params["gate1_selection_fingerprint"] = selection_fingerprint
            operation = state.task_context.get("t4_operation_request")
            if isinstance(operation, dict):
                params["t4_native_operation"] = {
                    "action": str(operation.get("action") or ""),
                    "directive_path": str(operation.get("directive_path") or ""),
                    "requested_from_population": str(operation.get("requested_from_population") or ""),
                }

        if node.task_id == "T2" and state is not None and bool(
            state.task_context.get("t2_user_requested_expansion")
        ):
            # Returning from T2-COVERAGE-GATE is a human-directed new search
            # round, not an autonomous retry.  The coverage decision is
            # persisted before the next context is built and contains a fresh
            # timestamp plus input fingerprints for this specific request.
            # Include its file fingerprint so the deadlock guard still catches
            # automatic same-parameter loops, while allowing the user to ask
            # for a documented targeted expansion more than once.
            decision_path = (
                workspace_dir / "literature" / "coverage_decision.json"
                if workspace_dir is not None
                else None
            )
            decision_fingerprint = "missing_expansion_decision"
            if decision_path is not None and decision_path.is_file():
                try:
                    decision = json.loads(decision_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    decision = {}
                if isinstance(decision, dict) and str(decision.get("selected_option") or "") == "rerun_t2_expand":
                    decision_fingerprint = _sha256_file(decision_path)
            params["t2_run_mode"] = "user_requested_expansion"
            params["t2_expansion_decision_fingerprint"] = decision_fingerprint

        return params

    @staticmethod
    def _compute_param_hash(params: dict[str, Any]) -> str:
        """计算参数字典的哈希值。

        使用 frozenset 处理嵌套字典，确保参数顺序不影响哈希结果。
        """
        import json

        normalized = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
