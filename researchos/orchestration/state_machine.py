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
import shlex
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
from ..latex_templates import (
    ccf_template_entry,
    ccf_template_entries,
    ccf_template_ids,
    ccf_template_option_id,
    normalize_ccf_template_id,
)
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
    materialize_t4_cross_domain_catalog_context,
    parse_t4_prerun_intent,
)
from ..ideation.target_profile import parse_target_profile_instruction, suggest_target_profile
from ..ideation.selected_compilation import (
    candidate_selection_readiness,
    candidate_selection_warnings_for_workspace,
    compile_pre_novelty_hypothesis_brief,
    selected_candidate_id_from_gate_input,
    validate_candidate_selection_ready,
)
from ..ideation.directives import (
    _explicit_read_only_action,
    _explicit_selection_action,
    current_population_context,
    parse_idea_directive,
    persist_idea_directive_confirmation,
    persist_idea_directive,
)
from ..ideation.final_card_readiness import validate_t4_portfolio_final_cards
from ..ideation.models import CandidateDossier, IdeaDirective, PopulationSnapshot, RouteGenerationResult, ScoreReport
from ..ideation.population import build_idea_families, select_portfolio
from ..ideation.legacy_projection import project_gate1_population
from ..ideation.evidence_display import (
    humanize_evidence_ids,
    load_evidence_display_catalog,
    referenced_evidence,
)
from ..ideation.proposal import repair_t45_proposal_manifest, validate_t45_research_proposal
from ..ideation.state import T4ArtifactStore, build_t4_input_fingerprints, run_config_fingerprint


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
            "title": "选择此 Candidate 进入 T4.5",
            "what_happens": "系统会将这个完整 Candidate 固化为 Pre-Novelty brief；随后由 T4.5 检查 novelty 与 collision，只有通过后才会编译正式 hypotheses 和 experiment plan。",
            "estimated_time": "本地整理通常少于一分钟；T4.5 会作为下一阶段单独运行。",
            "version_policy": "当前 Population、Parent、Child 和 Archive 都会保留，之后仍可回到此 Gate。",
            "next_stage": "生成 Pre-Novelty brief，然后进入 T4.5。",
        },
        "continue_evolution": {
            "title": "继续一轮 Evolution",
            "what_happens": "系统以当前 Active Population 为 Parent，按计划生成 Mutation / Crossover Child，对合并后的候选独立评分，并写入新的 Population 快照。",
            "estimated_time": "需要模型参与；耗时取决于 provider 和 Population 大小。",
            "version_policy": "当前 Active Population 会保留为历史版本，可随时 rollback。",
            "next_stage": "带着新的 Portfolio 回到 Gate1，不会进入 T4.5。",
        },
        "focus_candidate": {
            "title": "聚焦演化此 Candidate",
            "what_happens": "系统会为所选 Candidate 制定边界明确的 Mutation Child 计划，并将其与当前 Active Population 独立比较评分。",
            "estimated_time": "需要模型参与，通常短于完整的一轮 Evolution。",
            "version_policy": "其他 Candidate 与所有历史版本都会保留。",
            "next_stage": "带着更新后的 Population 回到 Gate1，不会进入 T4.5。",
        },
        "merge_candidates": {
            "title": "创建 Crossover Candidate",
            "what_happens": "系统会先进行 Compatibility Check；只有确认存在一致的 Core Thesis 和 Gene Donor Map 后，才会生成新的 Child。",
            "estimated_time": "需要模型参与的兼容性检查；通过后才会再进行一次 Child 生成和评分。",
            "version_policy": "两个 Parent Candidate 都不会被改写，且可以恢复。",
            "next_stage": "回到 Gate1；不会自动进入 T4.5。",
        },
        "compose_from_components": {
            "title": "组合选定的组成部分",
            "what_happens": "系统会检查所选 hypotheses、contributions 或 genes 能否构成一个一致的 Candidate；不会把它们直接拼接进正式假设文件。",
            "estimated_time": "需要模型参与的 Compatibility Check；生成新 Candidate 前还会进行第二次确认。",
            "version_policy": "所有来源 Candidate 及其原始组成部分都会保留。",
            "next_stage": "带着 Compatibility Report 回到 Gate1。",
        },
        "keep_parallel": {
            "title": "并行保留多个方向",
            "what_happens": "所选的完整 Candidate 会作为相互独立的研究方向记录；不会合并 mechanism、contribution 或 hypothesis。",
            "estimated_time": "仅更新本地记录，不调用模型。",
            "version_policy": "已选和未选 Candidate 都不会改变。",
            "next_stage": "回到 Gate1；准备好后再选择一个方向生成 Pre-Novelty brief。",
        },
        "regenerate_route": {
            "title": "重新生成一条 Route",
            "what_happens": "系统会重新运行指定 Route，同时保留先前的 Route 结果和 Population 历史。",
            "estimated_time": "需要模型生成与独立评分。",
            "version_policy": "现有 Route 输出和所有 Candidate 都可恢复。",
            "next_stage": "带着新的 Population 快照回到 Gate1。",
        },
        "change_target_profile": {
            "title": "调整论文取向（Publication Orientation）",
            "what_happens": "系统会保留 Active Population 与五维 Core Scientific Score，只重新独立评估 Profile Fit，并写入新的 profile-revision Population 快照。",
            "estimated_time": "需要模型对当前 Active Population 做一次 Profile Fit 评估。",
            "version_policy": "之前的 Population、评分批次、Candidate 和谱系都会保留，可查看或恢复。",
            "next_stage": "按新的论文取向排序 Portfolio 后回到 Gate1。",
        },
        "rollback": {
            "title": "回退到上一代（Rollback）",
            "what_happens": "系统会将 Active Population 指向上一代，并根据已保存的 Candidate 和评分记录重建比较视图。",
            "estimated_time": "仅更新本地记录，不调用模型。",
            "version_policy": "后续 Generation 不会删除，之后仍可重新查看。",
            "next_stage": "在恢复后的 Generation 回到 Gate1。",
        },
    }
    return descriptions.get(
        action,
        {
            "title": "T4 操作",
            "what_happens": "系统会先保留请求并校验 artifact 边界，再改变 Active Population。",
            "estimated_time": "取决于所请求的操作。",
            "version_policy": "现有 Candidate 和 Population artifact 会保留。",
            "next_stage": "回到 Gate1。",
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


_T4_IMPLEMENTATION_PATHS = (
    "researchos/ideation/evolution_controller.py",
    "researchos/ideation/llm_roles.py",
    "researchos/ideation/models.py",
    "researchos/ideation/evidence.py",
    "researchos/ideation/final_card_readiness.py",
    "researchos/ideation/final_card_diagnostics.py",
    "researchos/ideation/legacy_projection.py",
    "researchos/runtime/orchestrator.py",
    "researchos/ui/idea_evolution_renderer.py",
    "config/system_config/t4_evolution.yaml",
    "config/system_config/idea_evidence_permissions.yaml",
    "config/system_config/idea_scoring_rubric.yaml",
    "config/system_config/idea_evolution_operators.yaml",
)


def _t4_execution_implementation_fingerprint() -> str:
    """Bind T4 retry identity to the installed controller contract.

    A retry with unchanged workspace inputs should still be stopped after the
    configured deadlock threshold.  A repaired T4 runtime, however, is a new
    execution contract: treating it as the old failing attempt blocks a valid
    resume and forces users to edit unrelated project inputs.  Only the small
    set of files that define T4 formation, validation, projection, and its
    system policy participate in this fingerprint.
    """

    repository_root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for relative in _T4_IMPLEMENTATION_PATHS:
        path = repository_root / relative
        digest.update(relative.encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<missing>")
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
_TEMPLATE_GATE_DEFAULTS.update(
    {
        ccf_template_option_id(entry.template_id): {
            "template_family": "ccf",
            "template_id": entry.template_id,
            "writing_language": "en",
        }
        for entry in ccf_template_entries()
    }
)


_SUPPORTED_RUNTIME_TEMPLATE_IDS = {
    "basic_zh",
    "basic_en",
    "neurips",
    "iclr",
    "icml",
    "kdd",
    "informs",
} | ccf_template_ids()


_CCF_TEMPLATE_GATE_TASKS = {
    "T3.6-CCF-TEMPLATE-GATE": "T3.6-PLAN",
    "T8-CCF-TEMPLATE-GATE": "T8-RESOURCE",
}


def _ccf_template_gate_options(*, task_id: str) -> list[dict[str, Any]]:
    """Build the second-level CCF menu from the bundled local catalogue."""

    repo_root = Path(__file__).resolve().parents[2]
    next_task = _CCF_TEMPLATE_GATE_TASKS[task_id]
    options: list[dict[str, Any]] = []
    for entry in ccf_template_entries(repo_root=repo_root, available_only=True):
        options.append(
            {
                "id": ccf_template_option_id(entry.template_id),
                "label": entry.label,
                "aliases": [entry.template_id, entry.label.casefold()],
                "next": next_task,
                "captured_defaults": {
                    "template_family": "ccf",
                    "template_id": entry.template_id,
                    "writing_language": "en",
                    **({"venue_style": "ccf_a"} if task_id == "T8-CCF-TEMPLATE-GATE" else {}),
                },
            }
        )
    return options


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
                # The retained pool is the total distinct-paper reading
                # budget.  Deep and shallow reading are complementary, not
                # two independent pools that silently add up past it.
                "lite_paper_num": 85,
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
                "lite_paper_num": 160,
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
    try:
        if active not in (None, ""):
            return max(0, int(active))
    except (TypeError, ValueError):
        pass
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
    return f"阅读候选 {pool} 篇 = 精读 {target} + 摘要轻读 {sweep} | {require}"


def _literature_param_explained_preview_lines(summary: dict[str, Any]) -> list[str]:
    deep_min = summary.get("deep_read_min")
    deep_target = summary.get("deep_read_target")
    deep_max = summary.get("deep_read_max")
    require = summary.get("require_deep_read_target")
    require_text = "未达目标不进入 T3.5" if require else "达到最低线即可继续"
    total_target = _summary_total_read_target(summary)
    pool = summary.get("active_pool_max")
    try:
        split_total = int(deep_target or 0) + int(summary.get("abstract_sweep_target") or 0)
    except (TypeError, ValueError):
        split_total = None
    split_note = (
        f"阅读分配：{pool} 篇不同论文 = {deep_target} 篇精读 + {summary.get('abstract_sweep_target')} 篇摘要轻读。"
        if split_total is not None and str(pool) == str(split_total)
        else (
            "阅读分配：当前精读与摘要轻读目标和候选数不一致；请返回重选参数。"
            if split_total is not None and pool not in (None, "")
            else "阅读分配：摘要轻读使用 all_readable，覆盖范围由保留候选数决定。"
        )
    )
    return [
        f"本轮阅读覆盖：最多 {total_target} 篇不同论文。候选数不是额外的阅读数量。",
        f"保留候选：{pool} 篇。T2 从检索结果中保留这些论文进入本轮阅读；其余保留在后备清单，可追溯但不会默认额外阅读。",
        split_note,
        f"深入阅读：目标 {deep_target} 篇（最低 {deep_min}，最多 {deep_max}）。",
        f"读满目标门槛：{require_text}（require_target={require}；可选：true/false）",
        f"摘要轻读：目标 {summary.get('abstract_sweep_target')} 篇。仅记录题目、摘要和元数据层面的证据，不能单独支持强结论。",
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
            "coverage_total": "例如 total=30 或 总共30；表示本轮阅读的不同论文总数，系统会按精读目标自动补足摘要轻读数量",
            "active_pool_max": "例如 180；表示本轮需要覆盖的不同论文总数。系统会在其中分配精读和摘要轻读，超额论文进入后备清单",
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
    """Use an authored short title; old cards get a layout-only abbreviation."""

    text = str(
        candidate.get("display_title")
        or candidate.get("title_short_zh")
        or candidate.get("short_title")
        or fallback
        or "未命名候选"
    ).strip()
    # New FinalIdeaCard translations supply a real LLM-authored short title.
    # Historical cards only have a full pitch.  Do not pretend to invent a
    # scientific title from it: retain a compact display excerpt and leave the
    # full LLM-authored thesis in the detailed card immediately below.
    if len(text) > 38:
        boundary = min(
            [position for position in (text.find("。"), text.find("；"), text.find("，")) if position >= 12] or [38]
        )
        return text[:boundary].rstrip("，；。 ") + "…"
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


def _t4_gate_lane_label(*, portfolio_role: str, candidate: dict[str, Any]) -> str:
    """Keep decision role separate from whether the evidence still needs work."""

    role = portfolio_role.strip().lower()
    if role == "lead":
        return "主线"
    if role == "alternative":
        return "备选方向"
    if role == "high_upside":
        return "高上行方向"
    if role == "parallel":
        return "并行候选"
    origin = str(candidate.get("idea_origin") or candidate.get("origin") or "").strip().lower()
    if "bridge" in origin:
        return "跨域候选"
    if "supplement" in origin:
        return "补充候选"
    return "候选方向"


def _t4_evidence_readiness_label(
    candidate: dict[str, Any],
    references: list[dict[str, str]],
) -> str:
    """Describe the actual evidence gap without changing the Candidate's role."""

    support_count = len(candidate.get("supporting_papers") or []) if isinstance(candidate.get("supporting_papers"), list) else 0
    basis_count = len(candidate.get("basis_sources") or []) if isinstance(candidate.get("basis_sources"), list) else 0
    constrained = str(candidate.get("constraint_status") or "").strip().lower() == "not_supported_by_current_evidence"
    if references:
        reading_labels = {str(item.get("reading_label") or "") for item in references}
        if reading_labels and reading_labels <= {"仅摘要线索", "仅元数据线索"}:
            return "关键材料目前仅有摘要或元数据线索，需全文或定向章节核验后才能支撑核心机制。"
        if constrained:
            return "已定位相关材料，但尚未绑定为该 Candidate 的正式证据链；不能据此提升主张强度。"
        return "已定位可读材料；仍需按其阅读范围核对具体主张。"
    if support_count or basis_count:
        return "已绑定候选材料；仍需核对每条材料对当前机制和预测的支持范围。"
    if constrained:
        return "当前没有绑定到 Candidate 的可追溯阅读依据；应先补充或绑定关键材料。"
    return "当前没有列出专属证据材料；需在后续审计中确认依据范围。"


_T4_GATE1_ENRICHABLE_CARD_FIELDS: tuple[tuple[str, str], ...] = (
    ("role_summary", "为何入选说明"),
    ("evidence_interpretation", "证据解读"),
    ("selection_advice", "选择建议"),
    ("risk_summary", "主要风险说明"),
    ("user_edit_hint", "可调整处说明"),
)


def _t4_gate1_card_enrichment_diagnostics(gate1_card: dict[str, Any]) -> list[str]:
    """Report absent LLM card explanations without fabricating their meaning.

    Gate1 remains a comparison surface when one Candidate's presentation is
    incomplete.  These messages are operational disclosure, not substituted
    research prose: they say which model-authored explanation is unavailable
    and keep the Candidate out of direct T4.5 selection until a targeted
    enrichment pass produces it.
    """

    missing = [
        label
        for field, label in _T4_GATE1_ENRICHABLE_CARD_FIELDS
        if not " ".join(str(gate1_card.get(field) or "").split())
    ]
    if not missing:
        return []
    return [
        "LLM 展示富化未完成：缺少" + "、".join(missing) + "。"
        "当前仅展示已保存字段；请先定向富化或继续演化，不能将其它字段替代为这些解释。"
    ]


def _t4_evidence_status_for_gate(
    candidate: dict[str, Any],
    final_card: dict[str, Any],
    evidence_levels: set[str],
    basis_sources: list[dict[str, Any]],
) -> str:
    """Return only the LLM-authored evidence explanation for a Final Card.

    Evidence level labels and source paths remain available as factual metadata
    elsewhere in the view model.  They cannot stand in for the candidate-level
    explanation of what the present material supports, so a missing summary is
    deliberately left empty and routed to Final Card repair before Gate1.
    """

    del candidate, evidence_levels, basis_sources
    return str(final_card.get("evidence_status_summary") or "").strip()


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


def _t4_gate1_file_navigation(workspace_dir: Path) -> list[dict[str, str]]:
    """Describe the durable, researcher-facing T4 materials that exist now.

    These paths are deliberately relative to the workspace and exclude runtime
    traces, fingerprints, checkpoints, and raw Population internals. The goal
    is to help a researcher revisit the decision evidence without learning
    the controller's implementation vocabulary.
    """

    entries = (
        (
            "完整候选卡",
            "ideation/_gate1_candidate_cards.md",
            "逐个阅读候选的完整命题、贡献、假设、评分、证据和风险。",
            "决定前细看某个方向，或需要离线比较时打开。",
        ),
        (
            "选择简报",
            "ideation/_gate1_selection_brief.md",
            "汇总候选池、建议选择顺序、并行保留提示与主要风险。",
            "先快速比较多个方向时打开。",
        ),
        (
            "候选结构与评分",
            "ideation/_candidate_directions.json",
            "保存机器可读的候选结构、评分、验证设想和支撑论文索引。",
            "需要导出、复核字段或交给后续工具时打开。",
        ),
        (
            "文献接地复核",
            "ideation/_pass2_grounding_review.json",
            "记录每个候选的文献支撑、证据边界、主要风险和上桌判断。",
            "想追问某个方向为何被推荐或证据不足时打开。",
        ),
        (
            "第一轮探索记录",
            "ideation/_pass1_forward_candidates.json",
            "保留第一轮形成的候选范围，便于回看未进入首屏的探索方向。",
            "需要检查本轮覆盖范围或回顾早期方向时打开。",
        ),
        (
            "当前候选组合",
            "ideation/portfolio.json",
            "记录当前推荐组合、备选方向与高上行方向。",
            "需要确认本轮可选择的方向集合时打开。",
        ),
        (
            "跨域覆盖审计",
            "ideation/bridge_coverage_review.json",
            "说明跨域材料如何支持、限制或暂缓当前候选。",
            "想核验跨域论文的使用边界时打开。",
        ),
    )
    return [
        {"label": label, "path": path, "purpose": purpose, "when_to_open": when_to_open}
        for label, path, purpose, when_to_open in entries
        if (workspace_dir / path).is_file()
    ]


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
    evidence_catalog = load_evidence_display_catalog(workspace_dir)

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
    lead_id = str(portfolio.get("lead_id") or "").strip() if isinstance(portfolio, dict) else ""
    alternative_ids = set(str(item).strip() for item in (portfolio.get("alternative_ids") or []) if str(item).strip()) if isinstance(portfolio, dict) else set()
    high_upside_ids = set(str(item).strip() for item in (portfolio.get("high_upside_ids") or []) if str(item).strip()) if isinstance(portfolio, dict) else set()
    portfolio_id_set = set(portfolio_ids)
    all_candidate_count = len([item for item in raw_candidates if isinstance(item, dict)])
    raw_by_id = {
        str(item.get("id") or item.get("idea_id") or "").strip(): item
        for item in raw_candidates
        if isinstance(item, dict) and str(item.get("id") or item.get("idea_id") or "").strip()
    }
    ordered_raw_candidates = (
        [raw_by_id[candidate_id] for candidate_id in portfolio_ids if candidate_id in raw_by_id]
        if portfolio_ids
        else [item for item in raw_candidates if isinstance(item, dict)]
    )

    for candidate in ordered_raw_candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("id") or candidate.get("idea_id") or "").strip()
        if not candidate_id:
            continue
        if portfolio_id_set and candidate_id not in portfolio_id_set:
            continue
        display_candidate = humanize_evidence_ids(candidate, evidence_catalog)
        if not isinstance(display_candidate, dict):
            display_candidate = candidate
        candidate_evidence_references = referenced_evidence(candidate, evidence_catalog)
        source_title = str(display_candidate.get("title") or "").strip()
        final_translation = candidate.get("final_idea_card") if isinstance(candidate.get("final_idea_card"), dict) else {}
        # A visible Portfolio candidate must have a completed LLM Final Card.
        # Do not build a title, evidence interpretation, or recommendation from
        # legacy CandidatePresentation fields when it is absent; the recovery
        # gate owns that repair path.
        if not final_translation:
            continue
        display_final_translation = humanize_evidence_ids(final_translation, evidence_catalog)
        if not isinstance(display_final_translation, dict):
            display_final_translation = final_translation
        title = str(display_final_translation.get("short_title") or "").strip()
        if not title:
            continue
        full_title = str(display_final_translation.get("plain_language_summary") or "").strip()
        value = str(display_final_translation.get("core_thesis") or "").strip()
        mechanism = str(display_final_translation.get("scientific_technical_core") or "").strip()
        minimum = display_candidate.get("minimum_experiment") if isinstance(display_candidate.get("minimum_experiment"), dict) else {}
        metrics = minimum.get("metric") or minimum.get("metrics") or ""
        if isinstance(metrics, list):
            metrics = "、".join(str(item).strip() for item in metrics[:3] if str(item).strip())
        else:
            metrics = str(metrics).strip()
        prediction = str(display_candidate.get("prediction_zh") or display_candidate.get("prediction") or "").strip()
        counterfactual = str(display_candidate.get("counterfactual_zh") or display_candidate.get("counterfactual") or "").strip()
        score = display_candidate.get("scores") if isinstance(display_candidate.get("scores"), dict) else {}
        score_rationale = (
            display_candidate.get("score_rationale")
            if isinstance(display_candidate.get("score_rationale"), dict)
            else {}
        )
        evolution_score = display_candidate.get("evolution_score") if isinstance(display_candidate.get("evolution_score"), dict) else {}
        support = display_candidate.get("supporting_papers") if isinstance(display_candidate.get("supporting_papers"), list) else []
        basis_sources = display_candidate.get("basis_sources") if isinstance(display_candidate.get("basis_sources"), list) else []
        evidence_levels = {
            str(item.get("evidence_level") or "").upper()
            for item in support
            if isinstance(item, dict) and str(item.get("evidence_level") or "").strip()
        }
        evidence = _t4_evidence_status_for_gate(candidate, display_final_translation, evidence_levels, basis_sources)
        pass2 = candidate.get("pass2_screening") if isinstance(candidate.get("pass2_screening"), dict) else {}
        selection_enabled, selection_blocker = validate_candidate_selection_ready(candidate)
        # The Final Idea Card is the LLM-authored decision narrative for the
        # visible Portfolio. It supersedes an older CandidatePresentation that
        # may be absent in a resumed workspace. Gate1 is not opened at all
        # until the Card Compiler has produced this complete translation, so
        # no deterministic card-field fallback is needed here.
        selection_recommendation = str(pass2.get("screening_recommendation") or "").strip()
        warning = str(pass2.get("selection_warning") or candidate.get("selection_warning") or "").strip()
        generated_by = str(candidate.get("generated_by") or candidate.get("generation_stage") or "").strip()
        is_recovery_candidate = generated_by.startswith("deterministic_recovery") or "deterministic_t4_gate1_recovery" in generated_by
        presentation_status = "legacy_recovery_requires_llm_reanalysis" if is_recovery_candidate else "llm_final_card_completed"
        portfolio_role = (
            "lead" if candidate_id == lead_id else "alternative" if candidate_id in alternative_ids else "high_upside" if candidate_id in high_upside_ids else "parallel"
        )
        lane = _t4_gate_lane_label(portfolio_role=portfolio_role, candidate=candidate)
        candidates.append(
            {
                # D# is the stable researcher-facing handle for this Gate.
                # The internal lineage ID remains available in the detail view
                # and is resolved by the state machine before any operation.
                "id": f"D{len(candidates) + 1}",
                "internal_id": candidate_id,
                "portfolio_role": portfolio_role,
                "lane": lane,
                "title": title,
                "full_title": full_title,
                "original_title": source_title if full_title != source_title else "",
                "origin": str(display_candidate.get("idea_origin") or "").strip(),
                "parent_ids": [str(value).strip() for value in display_candidate.get("parent_ids") or [] if str(value).strip()] if isinstance(display_candidate.get("parent_ids"), list) else [],
                "mechanism_family": str(display_candidate.get("mechanism_family") or "").strip(),
                "target_problem": str(display_candidate.get("target_problem") or "").strip(),
                "value": value,
                "mechanism": mechanism,
                "real_world_significance": str(display_final_translation.get("real_world_significance") or "").strip(),
                "prediction": prediction,
                "counterfactual": counterfactual,
                "practical_implication": str(display_candidate.get("practical_implication_zh") or display_candidate.get("practical_implication") or "").strip(),
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
                "evidence_status_summary": str(display_final_translation.get("evidence_status_summary") or "").strip(),
                "evidence_readiness": _t4_evidence_readiness_label(candidate, candidate_evidence_references),
                "evidence_references": candidate_evidence_references,
                "support_count": len(support),
                "basis_summary": _t4_basis_summary_for_gate(display_candidate),
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
                "final_idea_card": display_final_translation,
                "maturity": str(candidate.get("maturity") or "").strip(),
                "candidate_stage": str(candidate.get("candidate_stage") or candidate.get("maturity") or "").strip(),
                "contribution_type": str(
                    display_candidate.get("contribution_type")
                    or (
                        display_candidate.get("contributions")[0].get("type")
                        if isinstance(display_candidate.get("contributions"), list)
                        and display_candidate.get("contributions")
                        and isinstance(display_candidate.get("contributions")[0], dict)
                        else ""
                    )
                ).strip(),
                "contributions": [
                    {
                        "id": str(item.get("id") or "").strip(),
                        "statement": str(item.get("statement") or "").strip(),
                        "type": str(item.get("type") or "").strip(),
                        "what_changes_if_true": str(item.get("what_changes_if_true") or "").strip(),
                    }
                    for item in display_candidate.get("contributions", [])
                    if isinstance(item, dict)
                ]
                if isinstance(display_candidate.get("contributions"), list)
                else [],
                "lineage": display_candidate.get("lineage") if isinstance(display_candidate.get("lineage"), dict) else {},
                "artifact_index": display_candidate.get("artifact_index") if isinstance(display_candidate.get("artifact_index"), dict) else {},
                "cross_domain_sources": [
                    str(value).strip()
                    for value in display_candidate.get("cross_domain_sources", [])
                    if str(value).strip()
                ]
                if isinstance(display_candidate.get("cross_domain_sources"), list)
                else [],
                "cross_domain_relation": str(display_candidate.get("cross_domain_relation_detail") or display_candidate.get("cross_domain_relation") or "").strip(),
                "projection_status": str(candidate.get("projection_status") or "complete").strip(),
                "projection_diagnostics": [
                    str(value).strip()
                    for value in candidate.get("projection_diagnostics", [])
                    if str(value).strip()
                ]
                if isinstance(candidate.get("projection_diagnostics"), list)
                else [],
                "final_card_status": str(candidate.get("final_card_status") or "").strip(),
                "final_card_diagnostic": str(candidate.get("final_card_diagnostic") or "").strip(),
                "evidence_composition": display_candidate.get("evidence_composition") if isinstance(display_candidate.get("evidence_composition"), dict) else {},
                "artifact_paths": [str(path).strip() for path in display_candidate.get("artifact_paths", []) if str(path).strip()] if isinstance(display_candidate.get("artifact_paths"), list) else [],
                "selection_recommendation": selection_recommendation,
                # This is the same structural contract used after the second
                # confirmation.  The Gate must never present a Candidate as
                # selectable and only reject it after the researcher confirms.
                "selection_enabled": selection_enabled,
                "selection_blocker": selection_blocker or "",
                "counterfactual_check": str(pass2.get("counterfactual_check") or "").strip(),
                "nearest_prior_work": str(
                    (pass2.get("nearest_prior_work") or candidate.get("nearest_prior_work") or {}).get("work")
                    if isinstance(pass2.get("nearest_prior_work") or candidate.get("nearest_prior_work"), dict)
                    else ""
                ).strip(),
                "novelty_signal": str(pass2.get("novelty_signal") or candidate.get("novelty_signal") or "").strip(),
                "warning": str(humanize_evidence_ids(warning, evidence_catalog) or "").strip(),
                "presentation_status": presentation_status,
                "enrichment_diagnostics": [],
                "innovation": _t4_candidate_innovation(candidate),
                "candidate_hypotheses": _t4_candidate_hypotheses(display_candidate, candidate_id),
                "merge_opportunities": _t4_merge_opportunities(display_candidate),
                "score_rationale": {str(key): str(reason).strip() for key, reason in score_rationale.items()},
            }
        )

    internal_to_display = {
        str(item.get("internal_id") or ""): str(item.get("id") or "")
        for item in candidates
        if str(item.get("internal_id") or "").strip()
    }
    for item in candidates:
        final_card = item.get("final_idea_card") if isinstance(item.get("final_idea_card"), dict) else {}
        dependencies = final_card.get("dependency_candidate_ids") if isinstance(final_card.get("dependency_candidate_ids"), list) else []
        item["dependency_display_ids"] = [
            internal_to_display.get(str(value), str(value))
            for value in dependencies
            if str(value).strip()
        ]
    return {
        "language": "zh",
        "candidates": candidates,
        "active_candidate_count": all_candidate_count,
        "remaining_candidate_count": max(0, all_candidate_count - len(candidates)),
        "input_hint": "选择一个完整 Candidate 可进入 Pre-Novelty review；选择多个方向时请明确“并行保留”或“构建新 Candidate”。也可以输入“查看剩余 Population”“查看 D1 的评分/证据/谱系/全部假设”“重新演化”“只优化 D2”或“回到上一代”。",
        "detail_path": "ideation/_gate1_candidate_cards.md",
        "file_navigation": _t4_gate1_file_navigation(workspace_dir),
    }


def _t4_gate1_display_id_map(workspace_dir: Path) -> dict[str, str]:
    """Return stable Gate1 D# handles for the whole current Active Population.

    The first handles always match the visible Portfolio cards.  Remaining
    active Candidates receive subsequent handles so a researcher can inspect
    or compare them after selecting ``查看其余 Population``.  Internal lineage
    identifiers remain the durable execution keys and are never inferred from
    a display handle.
    """

    overview = _t4_gate1_candidate_overview(workspace_dir)
    ordered_ids = [
        str(item.get("internal_id") or "").strip()
        for item in overview.get("candidates", [])
        if isinstance(item, dict) and str(item.get("internal_id") or "").strip()
    ]
    try:
        raw = json.loads((workspace_dir / "ideation" / "_candidate_directions.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    raw_candidates = raw.get("candidates") if isinstance(raw, dict) else []
    if isinstance(raw_candidates, list):
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            candidate_id = str(item.get("id") or item.get("idea_id") or "").strip()
            if candidate_id and candidate_id not in ordered_ids:
                ordered_ids.append(candidate_id)
    try:
        population, _dossiers = current_population_context(workspace_dir)
    except (OSError, ValueError):
        population = None
    if population is not None:
        for candidate_id in population.active_candidate_ids:
            if candidate_id not in ordered_ids:
                ordered_ids.append(candidate_id)
    return {f"D{index}": candidate_id for index, candidate_id in enumerate(ordered_ids, start=1)}


def _resolve_t4_display_ids(value: str, display_ids: dict[str, str]) -> str:
    """Replace exact public D# handles, never arbitrary substrings."""

    if not display_ids:
        return value
    return re.sub(
        r"(?<![A-Za-z0-9])D\d+(?![A-Za-z0-9])",
        lambda match: display_ids.get(match.group(0).upper(), match.group(0)),
        str(value or ""),
        flags=re.IGNORECASE,
    )


def _resolve_t4_display_ids_in_payload(value: Any, display_ids: dict[str, str]) -> Any:
    """Resolve public handles in the narrow parsed-directive payload."""

    if isinstance(value, str):
        return _resolve_t4_display_ids(value, display_ids)
    if isinstance(value, list):
        return [_resolve_t4_display_ids_in_payload(item, display_ids) for item in value]
    if isinstance(value, dict):
        return {str(key): _resolve_t4_display_ids_in_payload(item, display_ids) for key, item in value.items()}
    return value


def _t4_public_handle_tokens(value: str) -> list[str]:
    """Return bare Gate1 D# handles when the whole input is just handles."""

    text = " ".join(str(value or "").strip().split())
    if not text:
        return []
    tokens = re.findall(r"(?<![A-Za-z0-9])D\d+(?![A-Za-z0-9])", text, flags=re.IGNORECASE)
    if not tokens:
        return []
    remainder = re.sub(r"(?<![A-Za-z0-9])D\d+(?![A-Za-z0-9])", "", text, flags=re.IGNORECASE)
    remainder = re.sub(r"[\s,，、;；+&和与andAND]+", "", remainder)
    if remainder:
        return []
    return [token.upper() for token in tokens]


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
    coverage_adjustment: dict[str, Any] | None = None
    _apply_literature_quality_overrides(payload, captured, workspace_dir=workspace_dir)
    if option == "custom":
        base_option = _normalize_literature_param_option(
            captured.get("base_option") or captured.get("_base_option") or _recommended_literature_param_option(workspace_dir)
        )
        if base_option not in _LITERATURE_PARAM_PRESETS:
            base_option = "survey_balanced"
        base_payload = _clone_literature_param_preset(base_option)
        base_summary = _literature_param_summary_from_payload(base_payload)
        explicit_active_pool = captured.get("active_pool_max") not in (None, "")
        explicit_deep_target = captured.get("deep_read_target") not in (None, "")
        explicit_abstract_target = captured.get("abstract_sweep_target") not in (None, "")
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
        requested_active_pool = active_pool if explicit_active_pool else None
        active_pool = max(active_pool, deep_target)
        if coverage_total is not None:
            active_pool = max(active_pool, coverage_total)
        if coverage_total is not None and not explicit_abstract_target:
            abstract_target = max(0, coverage_total - deep_target)
        elif not explicit_abstract_target and (explicit_active_pool or explicit_deep_target):
            # A request such as "候选 20，精读 5" means a compact, complete
            # reading plan: the remaining 15 candidates receive abstract-level
            # notes.  Do not inherit an unrelated 120-paper sweep target.
            abstract_target = max(0, active_pool - deep_target)
        if isinstance(abstract_target, int):
            # Explicitly asking for more shallow notes enlarges the distinct
            # reading pool.  This keeps the persisted plan internally
            # consistent instead of creating an impossible T3 coverage gate.
            active_pool = max(active_pool, deep_target + abstract_target)
        if requested_active_pool is not None and active_pool > requested_active_pool:
            coverage_adjustment = {
                "requested_active_pool_max": requested_active_pool,
                "effective_active_pool_max": active_pool,
                "deep_read_target": deep_target,
                "abstract_sweep_target": abstract_target,
                "reason": "explicit_reading_allocation_exceeds_requested_candidate_count",
                "human_summary": (
                    f"精读 {deep_target} 篇与摘要轻读 {abstract_target} 篇合计 {active_pool} 篇，"
                    f"超过你输入的候选 {requested_active_pool} 篇；"
                    f"本轮候选已调整为 {active_pool} 篇。"
                ),
            }
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
                "active_pool_max": "保留候选数：本轮需要形成阅读记录的不同论文总数。系统会在其中分配精读和摘要轻读；它不是最终引用数。",
                "deep_read_target": "精读目标：正常完成 T3 前应完成多少篇结构化深读笔记。",
                "deep_read_min": "最低精读：预算或资源异常时的最低可接受线；正常运行由 require_deep_read_target 决定是否必须读满 target。",
                "abstract_sweep.lite_paper_num": "摘要轻读数量：T3 后对保留候选中未精读但有摘要的论文做 LLM 摘要级轻读。它与精读目标共同构成阅读覆盖；all_readable 表示覆盖全部剩余可读候选。",
                "metadata_replacement_policy": "metadata-only 只做批量 triage，并尽量用 backlog 中有摘要/PDF 的候选补足可读覆盖。",
                "literature_quality.manuscript_language": "写作语言：auto/en/zh/mixed；英文稿默认不搜索、不主动引用中文非 seed 论文。",
                "literature_quality.include_chinese_literature": "是否允许中文论文进入候选池：auto/false/true；允许时不再因缺少权威标签硬过滤，但会标记 authority_review_needed。",
                "literature_quality.chinese_literature_policy": "中文论文来源策略：默认 review_flag_only，只做权威性复核标记；英文稿且明确排除中文时仍不纳入非 seed 中文文献。",
                "literature_quality.effective_non_seed_chinese_action": "生效的非 seed 中文文献动作：英文稿固定为 exclude；中文、双语或自动稿件按中文文献设置决定准入与复核。",
            },
        }
    )
    if coverage_adjustment is not None:
        payload["coverage_adjustment"] = coverage_adjustment
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
    if family == "ccf":
        entry = ccf_template_entry(template_id)
        if entry is not None:
            payload["template_availability"] = entry.availability_label
            if not entry.has_official_entry:
                payload["template_submission_notice"] = (
                    "本地目录只有 class/style 模板包；正文会使用匿名写作外壳，投稿前必须核对并替换为该 venue 当年的官方入口文件。"
                )
    if task_id in {"T8-STYLE-GATE", "T8-CCF-TEMPLATE-GATE"}:
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
    if normalize_ccf_template_id(text) in ccf_template_ids():
        text = "ccf"
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
    return normalize_ccf_template_id(aliases.get(text, text))


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
    repair_t45_proposal_manifest(workspace_dir, audit_path)
    required = {
        "hypotheses": workspace_dir / "ideation" / "hypotheses.md",
        "research_dossier": workspace_dir / "ideation" / "research_dossier.json",
        "exp_plan": workspace_dir / "ideation" / "exp_plan.yaml",
        "contribution_hypothesis_map": workspace_dir / "ideation" / "contribution_hypothesis_map.yaml",
        "validation_map": workspace_dir / "ideation" / "validation_map.yaml",
        "kill_criteria": workspace_dir / "ideation" / "kill_criteria.yaml",
        "research_proposal": workspace_dir / "ideation" / "proposal" / "research_proposal.md",
        "proposal_manifest": workspace_dir / "ideation" / "proposal" / "proposal_manifest.json",
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
    hypotheses_text = (workspace_dir / "ideation" / "hypotheses.md").read_text(encoding="utf-8", errors="replace")
    dossier_ok, dossier_error = _validate_t45_research_dossier(workspace_dir, hypotheses_text)
    if not dossier_ok:
        return False, dossier_error
    proposal_ok, proposal_error = validate_t45_research_proposal(workspace_dir, audit_path)
    if not proposal_ok:
        return False, proposal_error
    return True, None


def _validate_t45_research_dossier(workspace_dir: Path, hypotheses_text: str) -> tuple[bool, str | None]:
    """Require a usable research dossier after T4.5 without judging its science."""

    if len(hypotheses_text.strip()) < 3_000:
        return False, "hypotheses.md is too short for the post-novelty research dossier"
    required_markers = {
        "summary": r"(?im)^#{1,3}\s*(摘要|executive summary)\b",
        "why_it_matters": r"(?im)^#{1,3}\s*(研究意义|why this matters|问题背景)",
        "contributions": r"(?im)^#{1,3}\s*(研究贡献|contributions?)\b",
        "practical_or_commercial_implications": r"(?im)^#{1,3}\s*(现实.*含义|实践.*含义|管理.*含义|商业.*含义|practical.*implications?|commercial.*implications?)",
        "evidence_or_novelty_boundary": r"(?im)^#{1,3}\s*(证据边界|新颖性约束|evidence boundary|novelty boundary)",
        "risks_or_kill_criteria": r"(?im)^#{1,3}\s*(风险.*停止|风险.*证伪|risks?.*(kill|falsification)|kill criteria)",
        "lineage": r"(?im)^#{1,3}\s*(研究谱系|可追溯性|lineage|traceability)",
    }
    missing_markers = [label for label, pattern in required_markers.items() if not re.search(pattern, hypotheses_text)]
    if missing_markers:
        return False, "hypotheses.md is missing research-dossier sections: " + ", ".join(missing_markers)
    if not re.search(r"(?im)^#{1,4}\s*H1\b", hypotheses_text):
        return False, "hypotheses.md is missing a formal H1 heading"
    dossier_path = workspace_dir / "ideation" / "research_dossier.json"
    try:
        dossier = json.loads(dossier_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"research_dossier.json cannot be read: {exc}"
    if not isinstance(dossier, dict) or dossier.get("semantics") != "t45_research_dossier":
        return False, "research_dossier.json semantics is invalid"
    if dossier.get("status") != "formalized_after_novelty_pass":
        return False, "research_dossier.json is not marked as an accepted audit result"
    required = {
        "candidate_id",
        "selection_fingerprint",
        "novelty_audit_verdict",
        "central_thesis",
        "research_problem",
        "why_it_matters",
        "contributions",
        "hypotheses",
        "evidence_boundary",
        "novelty_boundary",
        "risks_and_kill_criteria",
        "traceability",
    }
    missing = sorted(key for key in required if key not in dossier)
    if missing:
        return False, "research_dossier.json is missing fields: " + ", ".join(missing)
    why_it_matters = dossier.get("why_it_matters")
    if not isinstance(why_it_matters, dict) or not {
        "scholarly", "practical", "commercial", "stakeholders_or_processes"
    }.issubset(why_it_matters):
        return False, "research_dossier.json.why_it_matters is incomplete"
    traceability = dossier.get("traceability")
    if not isinstance(traceability, dict) or not isinstance(traceability.get("source_artifacts"), list):
        return False, "research_dossier.json.traceability.source_artifacts is invalid"
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

        # T4's run configuration is a first-class Human Gate, not a renderer
        # convenience.  A state machine that exposes T4 but omits this public
        # declaration would otherwise pass generic node validation and fail
        # only after the user starts a run.  Runtime still has a Recovery Gate
        # fallback for stale persisted workspaces, but new/custom definitions
        # must fail validation with an actionable configuration error.
        if "T4" in self.nodes and "t4_prerun_gate" not in self.gates:
            errors.append(
                "T4: missing required gate 't4_prerun_gate'; add the public T4 pre-run confirmation "
                "to gates.yaml or use config/system_config/gates.yaml"
            )
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
        runtime_recovery = extra.get("runtime_recovery")
        if isinstance(runtime_recovery, dict) and runtime_recovery.get("target_task") == node.task_id:
            # The durable directive is intentionally passed through the normal
            # context rather than reconstructed from a free-form error.  All
            # Agent families can therefore see the same bounded repair window
            # after a process restart.
            extra["resume_mode"] = True
            extra["is_resume"] = True
            extra["resume_reason"] = "runtime_recovery"

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

        # Upgrade an older paused Survey assembly failure into the current
        # durable recovery gate before another model repair window is used.
        if (
            state.current_task == "T3.6-ASSEMBLE"
            and workspace_dir is not None
            and state.status == "PAUSED"
            and self._is_t36_assemble_recoverable_error(state.last_error)
        ):
            return True
        if (
            state.current_task == "T3.6-COMPILE"
            and workspace_dir is not None
            and state.status == "PAUSED"
            and self._is_t36_compile_recoverable_error(state.last_error)
        ):
            return True
        if state.current_task == "T4" and "T4-GATE1" in self.nodes and workspace_dir is not None:
            operation = state.task_context.get("t4_operation_request")
            if isinstance(operation, dict) and not self._is_legacy_t4_advance_operation(operation):
                return False
            if isinstance(operation, dict):
                return True
            if self._t4_gate1_ready_without_selection(workspace_dir):
                return True
            return self._t4_prerun_confirmation_required(workspace_dir)
        if state.current_task == "T5-EXTERNAL-WAIT" and state.status == "PAUSED":
            # Older workspaces persisted this wait as a generic runtime pause.
            # Reopen it as the dedicated external-executor handoff panel before
            # any agent or model can run again.
            return True
        # Older configurations incorrectly routed a completed protocol-only
        # dry-run into T8. A mock result has no writer handoff and must never
        # become manuscript evidence, so reopen executor selection before a
        # T8 style gate can be shown.
        if (
            state.current_task == "T8-STYLE-GATE"
            and workspace_dir is not None
            and self._mock_dry_run_requires_real_executor(workspace_dir)
        ):
            return True
        if state.status == "PAUSED" and self._runtime_recovery_payload_from_error(state.last_error) is not None:
            return True
        node = self.nodes[state.current_task]
        return bool(node.gate and (node.extra or {}).get("immediate_gate"))

    def pause_for_immediate_gate(
        self,
        state: StateYaml,
        *,
        workspace_dir: Path | None = None,
    ) -> StateYaml:
        """Present a gate-only node directly and pause without starting an agent run."""

        if (
            state.current_task == "T8-STYLE-GATE"
            and workspace_dir is not None
            and self._mock_dry_run_requires_real_executor(workspace_dir)
        ):
            state.current_task = "T5-EXECUTOR-GATE"
            state.pending_gate = None
            state.status = "RUNNING"
            state.paused_at = None
            state.last_error = None
            receipts = state.task_context.get("state_migrations")
            history = list(receipts) if isinstance(receipts, list) else []
            history.append(
                {
                    "migration": "redirect_mock_dry_run_from_t8",
                    "from_task": "T8-STYLE-GATE",
                    "to_task": "T5-EXECUTOR-GATE",
                    "reason": "mock_only protocol outputs do not satisfy the T5-to-T8 writer handoff contract",
                    "migrated_at": _now_iso(),
                }
            )
            state.task_context["state_migrations"] = history[-20:]
            return self.pause_for_immediate_gate(state, workspace_dir=workspace_dir)

        if (
            state.current_task == "T3.6-ASSEMBLE"
            and workspace_dir is not None
            and self._is_t36_assemble_recoverable_error(state.last_error)
        ):
            return self._pause_for_t36_assemble_recovery_gate(state, state.last_error or "", workspace_dir)
        if (
            state.current_task == "T3.6-COMPILE"
            and workspace_dir is not None
            and self._is_t36_compile_recoverable_error(state.last_error)
        ):
            return self._pause_for_t36_compile_recovery_gate(state, state.last_error or "", workspace_dir)
        if state.current_task == "T4" and workspace_dir is not None:
            operation = state.task_context.get("t4_operation_request")
            if isinstance(operation, dict) and self._is_legacy_t4_advance_operation(operation):
                state.task_context.pop("t4_operation_request", None)
                state.task_context.pop("human_iteration_directive", None)
                return self._reopen_native_t4_gate(
                    state,
                    workspace_dir,
                    result={
                        "title": "已阻止旧版错误推进",
                        "summary": (
                            "当前保存的 T4 操作原本来自“推进候选”，但被旧版解析为重新演化。"
                            "系统已在模型调用前取消该操作；没有创建新 Candidate 或改变 Population。"
                            "请重新输入“推进 D1”，随后将进入 T4.5 的确认流程。"
                        ),
                        "kind": "legacy_advance_operation_repaired",
                    },
                )
            if self._t4_gate1_ready_without_selection(workspace_dir):
                state.current_task = "T4-GATE1"
            elif self._t4_prerun_confirmation_required(workspace_dir):
                # A custom/minimal state-machine can expose T4 without also
                # importing the public pre-run Gate declaration.  That is a
                # recoverable configuration incompleteness, not a reason for
                # the CLI to raise ``KeyError`` before it can show a human
                # decision.  The standard configuration still presents the
                # normal T4 pre-run chooser below.
                if "t4_prerun_gate" not in self.gates:
                    return self._pause_for_t4_recovery_gate(
                        state,
                        "T4 requires a confirmed pre-run configuration, but this state-machine does not declare t4_prerun_gate. "
                        "Add the public pre-run gate or resume with the standard ResearchOS configuration.",
                        workspace_dir,
                    )
                return self._pause_for_t4_prerun_gate(state, workspace_dir)
        if state.current_task == "T5-EXTERNAL-WAIT" and state.status == "PAUSED":
            return self._pause_for_runtime_recovery_gate(
                state,
                error=state.last_error,
                workspace_dir=workspace_dir,
                recovery={
                    "kind": "runtime",
                    "error_summary": state.last_error or "WAITING_EXTERNAL: awaiting external executor handoff.",
                    "details": {"source": "t5_external_wait_panel_upgrade"},
                },
            ) or state
        if state.status == "PAUSED":
            generic_recovery = self._pause_for_runtime_recovery_gate(
                state,
                error=state.last_error,
                workspace_dir=workspace_dir,
            )
            if generic_recovery is not None:
                return generic_recovery
        node = self.nodes[state.current_task]
        if not node.gate:
            raise ValueError(f"{state.current_task} has no gate")
        if node.task_id == "T4-GATE1" and workspace_dir is not None:
            redirected = self._redirect_incomplete_t4_gate_to_recovery(state, workspace_dir)
            if redirected is not None:
                return redirected
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
        elif node.task_id in _CCF_TEMPLATE_GATE_TASKS:
            options = _ccf_template_gate_options(task_id=node.task_id)
        elif node.task_id == "T3.6-GATE-CORPUS" and workspace_dir is not None:
            presentation["supplement_recommendation"] = _t36_supplement_recommendation(workspace_dir)
        elif node.task_id == "T5-PROTOCOL-GATE" and workspace_dir is not None:
            presentation["protocol_readiness"] = self._t5_protocol_gate_summary(workspace_dir)
        if node.task_id == "T4-GATE1" and workspace_dir is not None:
            presentation["candidate_overview"] = _t4_gate1_candidate_overview(workspace_dir)
            presentation["candidate_pool_fingerprints"] = _t4_gate1_candidate_pool_fingerprints(workspace_dir)
            presentation["t4_artifact_guide"] = _t4_gate1_file_navigation(workspace_dir)
            operation_result = _latest_native_t4_operation_result(workspace_dir)
            if operation_result:
                presentation["t4_directive_result"] = operation_result
            pending_composition = _pending_native_t4_composition(workspace_dir)
            if pending_composition:
                options.insert(
                    0,
                    {
                        "id": "confirm_composition",
                        "label": "确认生成 Human-composed Candidate",
                        "description": "根据已审核的 Gene Donor Map 生成一个新 Candidate 并独立评分；来源 Candidate 会保留。",
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

    @staticmethod
    def _is_legacy_t4_advance_operation(operation: dict[str, Any]) -> bool:
        """Identify an old queued T4 evolution created from ``推进 D#``.

        This is a resume-only compatibility guard.  Explicit optimize/evolve
        directives remain valid T4 operations; only a plainly documented
        advance request with a non-selection action is intercepted.
        """

        action = str(operation.get("action") or "").strip()
        directive = operation.get("directive") if isinstance(operation.get("directive"), dict) else {}
        raw = str(directive.get("raw_user_input") or "")
        targets = directive.get("target_candidate_ids") if isinstance(directive.get("target_candidate_ids"), list) else []
        return action != "select_candidate" and _explicit_selection_action(raw, target_count=len(targets))

    def _pause_for_t4_prerun_gate(self, state: StateYaml, workspace_dir: Path) -> StateYaml:
        """Pause inside T4 for configuration without adding an external FSM node."""

        catalog_context = materialize_t4_cross_domain_catalog_context(workspace_dir)
        inspection = inspect_t4_inputs(workspace_dir)
        if catalog_context.get("status") == "degraded":
            inspection = inspection.model_copy(
                update={
                    "warnings": [
                        *inspection.warnings,
                        "Cross-domain catalog 未能自动物化："
                        + str(catalog_context.get("warning") or "请检查 catalog 诊断；T4 仍会保留已确认方向名称作为受限创意上下文。"),
                    ]
                }
            )
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

    @staticmethod
    def _t4_recovery_presentation(
        error: str,
        *,
        has_final_card_checkpoint: bool,
        recovery_stage: str = "",
    ) -> dict[str, str]:
        """Describe the failed T4 boundary without exposing a provider traceback.

        T4 uses one recovery gate for several recoverable boundaries.  A
        compatibility-review record that needs normalizing is fundamentally
        different from a missing Final Idea Card.  Calling both of them
        ``card repair`` confused researchers into retrying an unrelated
        operation, and leaked a Pydantic implementation error into the normal
        decision surface.  The complete error remains in the runtime trace and
        ``state.last_error``; this method supplies the bounded public account.
        """

        normalized = " ".join(str(error or "").casefold().split())
        if recovery_stage == "evolution_resume":
            return {
                "kind": "evolution_resume",
                "title": "T4 演化进度恢复",
                "description": (
                    "初始 Candidate、已完成评分、路线和已完成 Child 均已保存。"
                    "尚未形成可比较的最终 Portfolio，因此恢复会只续跑缺失的演化步骤并复用已有检查点。"
                ),
                "error_summary": (
                    "当前阻塞点位于最终 Portfolio 写入之前；这不是卡片文案问题，也不会重新生成已保存的候选。"
                ),
                "retry_label": "继续剩余演化步骤",
                "retry_description": "复用已保存的路线、兼容性评审、Child 和评分检查点，只执行尚未完成的步骤。",
            }
        if recovery_stage == "source_data_missing":
            return {
                "kind": "source_data_missing",
                "title": "T4 原生产物需要修复",
                "description": (
                    "恢复检查发现最终 Portfolio 的原生输入不一致或缺失。系统不会让 LLM 猜测、补写或覆盖 Candidate。"
                ),
                "error_summary": "需要先恢复已记录的原生 Population、评分或 Portfolio 输入，之后才可以编译决策卡。",
                "retry_label": "重新检查原生 T4 产物",
                "retry_description": "只重新执行确定性一致性检查，不会调用模型或创建新的 Candidate。",
            }
        if recovery_stage == "final_card":
            return {
                "kind": "final_card",
                "title": "T4 决策卡恢复",
                "description": (
                    "当前 Population、评分、谱系和 Portfolio 已经完整保存。"
                    "恢复只会编译缺失的研究者可读 Candidate Card，并随后生成 Gate1 决策页。"
                ),
                "error_summary": "最终 Candidate Card 尚未完整生成；不会重新生成候选、评分或演化路线。",
                "retry_label": "继续决策卡恢复",
                "retry_description": "从已保存 Portfolio 恢复 Card 编译与 Gate1 投影，不重跑 Candidate Evolution。",
            }
        compatibility_tokens = (
            "crossovercompatibilitydecision",
            "crossover compatibility",
            "兼容投影",
            "兼容性评审",
        )
        final_card_tokens = (
            "final idea card",
            "portfolio idea card",
            "final card",
            "portfolio_cards.json",
        )
        if any(token in normalized for token in compatibility_tokens):
            return {
                "kind": "compatibility_record",
                "title": "T4 兼容性记录恢复",
                "description": (
                    "候选、评分、谱系和已完成演化均已保存。已保存的候选组合评审记录需要按当前版本重新读取和校验；"
                    "这不会重新生成候选，也不会把未获批准的组合变成新方向。"
                ),
                "error_summary": (
                    "候选组合的兼容性评审记录与当前恢复格式不兼容，尚未完成读取。"
                    "已保存的候选、评分和演化计划保持不变。"
                ),
                "retry_label": "继续兼容性记录恢复",
                "retry_description": "从已保存的演化计划恢复兼容性记录；已完成的候选、评分和路线不会重跑。",
            }
        if has_final_card_checkpoint or any(token in normalized for token in final_card_tokens):
            return {
                "kind": "final_card",
                "title": "T4 卡片说明恢复",
                "description": (
                    "候选、评分、谱系和已完成演化均已保存。决策页缺少部分完整的候选说明，"
                    "恢复时只会补齐该说明，不会改写候选或评分。"
                ),
                "error_summary": "候选决策卡尚未完整生成，当前不能安全打开选择页；已保存的研究结果不会丢失。",
                "retry_label": "继续卡片说明恢复",
                "retry_description": "从已保存 checkpoint 恢复，只补齐缺失的候选说明，不重跑路线、评分或 Population。",
            }
        return {
            "kind": "general",
            "title": "T4 恢复决策",
            "description": "候选、评分和已完成演化均已保存。恢复会从尚未完成的 T4 边界继续，不会删除已有研究结果。",
            "error_summary": "T4 尚未完成一项结构化恢复检查；已保存的候选、评分和演化结果保持可用。",
            "retry_label": "继续 T4 恢复",
            "retry_description": "从已保存状态继续未完成的 T4 工作，并在恢复前重新校验相关产物。",
        }

    def _pause_for_t4_recovery_gate(self, state: StateYaml, error: str, workspace_dir: Path | None) -> StateYaml:
        """Persist an actionable decision when T4 cannot safely reach Gate1."""

        ready = bool(workspace_dir and self._t4_gate1_ready_without_selection(workspace_dir))
        cards_ready = False
        card_error = ""
        repair_checkpoint: dict[str, Any] | None = None
        repair_checkpoint_error = ""
        compatibility_migration: dict[str, Any] | None = None
        recovery_stage = ""
        if workspace_dir is not None:
            operation = state.task_context.get("t4_operation_request")
            try:
                store = T4ArtifactStore(workspace_dir)
                compatibility_migration = store.migrate_crossover_compatibility_records()
                cards_ready, raw_card_error = validate_t4_portfolio_final_cards(workspace_dir)
                card_error = str(raw_card_error or "")
                if not cards_ready:
                    # Older runs can complete survival and write Portfolio
                    # before the durable pre-card receipt introduced later.
                    # Reconstruct only that receipt after proving the native
                    # Population, dossiers and independent scores agree.
                    store.ensure_final_card_checkpoint_for_completed_population(
                        operation=operation if isinstance(operation, dict) else None,
                    )
                repair_checkpoint, raw_checkpoint_error = store.current_final_card_repair_checkpoint(
                    operation=operation if isinstance(operation, dict) else None,
                )
                repair_checkpoint_error = str(raw_checkpoint_error or "")
            except (OSError, ValueError) as exc:
                repair_checkpoint_error = str(exc)
            portfolio_path = workspace_dir / "ideation" / "portfolio.json"
            if repair_checkpoint is not None:
                recovery_stage = "final_card"
            elif portfolio_path.is_file():
                # A Portfolio without cards is still a Card-only recovery
                # only if its checkpoint can be recreated on the retry.  Keep
                # the source error visible rather than calling an LLM here.
                recovery_stage = "source_data_missing"
            else:
                try:
                    internal = T4ArtifactStore(workspace_dir).read_state()
                    phase_marker = workspace_dir / "ideation" / "evolution" / "phases" / f"{internal.generation}_survival.json"
                    recovery_stage = "source_data_missing" if phase_marker.is_file() else "evolution_resume"
                except (OSError, ValueError):
                    recovery_stage = "evolution_resume"
        recovery = self._t4_recovery_presentation(
            error,
            has_final_card_checkpoint=repair_checkpoint is not None,
            recovery_stage=recovery_stage,
        )
        options = [
            {
                "id": "retry_t4",
                "label": recovery["retry_label"],
                "description": recovery["retry_description"],
            },
            {"id": "pause", "label": "暂停", "description": "保留诊断和所有 T4 artifacts，稍后 resume。"},
            {"id": "exit", "label": "结束本次运行", "description": "不删除任何 Candidate 或演化结果。"},
        ]
        if ready:
            options.insert(
                1,
                {
                    "id": "open_gate1",
                    "label": "查看完整 Portfolio Card",
                    "description": "所有可见 Candidate 已有完整 LLM Final Card；仅打开决策面板，不调用模型。",
                },
            )
        state.pending_gate = GateState(
            gate_id="t4_recovery_gate",
            presented_at=_now_iso(),
            presentation={
                "_title": recovery["title"],
                "_description": recovery["description"],
                "_recovery_kind": recovery["kind"],
                "_recovery_presentation_version": 2,
                "error_summary": recovery["error_summary"],
                "gate1_artifacts_ready": ready,
                "portfolio_final_cards_ready": cards_ready,
                "portfolio_final_cards_error": card_error[:1000],
                "final_card_repair_checkpoint": (
                    {
                        "population_id": repair_checkpoint.get("population_id"),
                        "population_generation": repair_checkpoint.get("population_generation"),
                        "operation_action": repair_checkpoint.get("operation_action"),
                        "operation_directive_id": repair_checkpoint.get("operation_directive_id"),
                        "status": repair_checkpoint.get("status"),
                    }
                    if repair_checkpoint is not None
                    else None
                ),
                "final_card_repair_checkpoint_error": repair_checkpoint_error[:1000],
                "compatibility_record_migration": (
                    {
                        "status": compatibility_migration.get("status"),
                        "migrated_decision_count": compatibility_migration.get("migrated_decision_count"),
                        "unresolved_count": len(compatibility_migration.get("unresolved") or []),
                        "receipt": "ideation/evolution/migrations/crossover_compatibility_v3.json",
                    }
                    if isinstance(compatibility_migration, dict)
                    else None
                ),
            },
            options=options,
        )
        state.status = "WAITING_HUMAN"
        state.paused_at = _now_iso()
        return state

    @staticmethod
    def _t4_has_recoverable_checkpoint(workspace_dir: Path | None) -> bool:
        """Return whether a T4 retry can retain a durable Candidate checkpoint.

        This deliberately performs only an existence check.  It must not read or
        reinterpret a potentially damaged Population while deciding whether a
        provider outage is recoverable; the normal controller/state validation
        remains responsible for that stronger integrity check on retry.
        """

        if workspace_dir is None:
            return False
        workspace = Path(workspace_dir)
        return bool(
            list((workspace / "ideation" / "populations").glob("P*.json"))
            or list((workspace / "ideation" / "candidates").glob("*.json"))
        )

    @classmethod
    def _is_t4_recoverable_runtime_failure(
        cls,
        error: str | None,
        *,
        workspace_dir: Path | None,
    ) -> bool:
        """Classify a failed native T4 run without turning a failure into success.

        T4's controller already preserves checkpoints and raw diagnostics for
        route, score, mutation, crossover, card, renderer, and compatibility
        projection failures.  Those conditions need an actionable recovery Gate
        rather than the generic `next_on_failure: failed` transition.  By
        contrast, an explicitly unsafe persistence/identity/selection state
        cannot be retried blindly and must remain a hard failure.

        Runtime currently transports many failures as text, so this is
        intentionally conservative about what is *hard*: only explicit
        integrity signatures bypass recovery.  Unknown implementation or
        provider failures remain visible in the recovery Gate with their saved
        diagnostic; they are never marked successful.
        """

        text = " ".join(str(error or "").casefold().split())
        hard_patterns = (
            r"path traversal",
            r"workspace-relative",
            r"cannot safely write",
            r"permission denied",
            r"read-only file system",
            r"unsafe artifact (?:replacement|overwrite)",
            r"legacy artifact.*overwrit",
            r"state corruption",
            r"unrecoverable state",
            r"fingerprint (?:corruption|mismatch)",
            r"stale (?:gate1 )?selection",
            r"selection is stale",
            r"candidate id .*?(?:overwrite|collision)",
            r"population id .*?(?:overwrite|collision)",
            r"forged (?:parent|child )?lineage",
        )
        if any(re.search(pattern, text) for pattern in hard_patterns):
            return False

        # A total provider outage is only a hard infrastructure failure when no
        # Population/Candidate checkpoint exists to present or resume.  A
        # partial T4 run with saved Candidates remains recoverable.
        all_providers_unavailable = bool(
            re.search(r"(?:all|every) (?:llm )?providers? (?:are )?(?:unavailable|failed)", text)
            or "no available provider" in text
        )
        if all_providers_unavailable and not cls._t4_has_recoverable_checkpoint(workspace_dir):
            return False
        return True

    def _pause_for_t4_runtime_failure(
        self,
        state: StateYaml,
        error: str | None,
        *,
        workspace_dir: Path | None,
    ) -> StateYaml | None:
        """Open the T4 recovery Gate for a non-integrity failure, if eligible."""

        if state.current_task != "T4" or not self._is_t4_recoverable_runtime_failure(
            error,
            workspace_dir=workspace_dir,
        ):
            return None
        summary = " ".join(str(error or "T4 stopped before Gate1 without a detailed runtime error.").split())
        # The task did not succeed, but it is a recoverable interruption rather
        # than a completed/failed scientific result. Keep the original error
        # for audit while making the lifecycle status truthful for resume.
        if state.history:
            state.history[-1].status = "INTERRUPTED"
            state.history[-1].error = summary
        return self._pause_for_t4_recovery_gate(state, summary, workspace_dir)

    def _pause_for_t36_assemble_recovery_gate(
        self,
        state: StateYaml,
        error: str,
        workspace_dir: Path | None,
    ) -> StateYaml:
        """Turn an exhausted Survey assembly audit into a durable decision.

        A citation-diversity FAIL is neither a bibliography fabrication request
        nor a reason to discard the assembled Survey.  The audit already
        records the exact over-concentrated keys and sections.  Preserve that
        context in a Gate so the researcher can grant another bounded repair
        window, inspect the saved diagnosis, or pause without losing prose.
        """

        repair_guidance: dict[str, Any] = {}
        audit_path = workspace_dir / "drafts" / "survey" / "survey_audit.json" if workspace_dir else None
        if audit_path and audit_path.exists():
            try:
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
                if isinstance(audit, dict):
                    guidance = audit.get("repair_guidance")
                    if isinstance(guidance, dict):
                        repair_guidance = guidance
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        state.pending_gate = GateState(
            gate_id="t36_assemble_recovery_gate",
            presented_at=_now_iso(),
            presentation={
                "_title": "综述拼装审计需要决策",
                "_description": (
                    "已保存所有 section、survey.tex 和审计结果。请选择是否追加一轮定向修复；"
                    "系统只会修复审计指出的来源文件，不会用无关引用或虚构内容强行通过。"
                ),
                "error_summary": " ".join(str(error).split())[:1000],
                "audit_path": "drafts/survey/survey_audit.json",
                "repair_guidance": repair_guidance,
            },
            options=[
                {
                    "id": "retry_survey_repair",
                    "label": "继续定向修复",
                    "description": "给予新的有界修复窗口；优先处理 audit 指出的 section，不重写整篇综述。",
                },
                {
                    "id": "pause_review",
                    "label": "暂停并检查审计",
                    "description": "保留所有已写内容和 audit，稍后 resume。",
                },
                {
                    "id": "exit",
                    "label": "结束本次综述运行",
                    "description": "不删除任何 survey artifact。",
                },
            ],
        )
        state.status = "WAITING_HUMAN"
        state.paused_at = _now_iso()
        return state

    @staticmethod
    def _is_t36_assemble_recoverable_error(error: str | None) -> bool:
        text = str(error or "").casefold()
        return bool(
            text
            and (
                "survey_audit" in text
                or "citation_diversity" in text
                or "survey audit" in text
            )
        )

    def _pause_for_t36_compile_recovery_gate(
        self,
        state: StateYaml,
        error: str,
        workspace_dir: Path | None,
    ) -> StateYaml:
        """Turn a deterministic Survey compile failure into a user decision.

        Compilation is deliberately outside the prose-writing agent loop.  A
        TeX/environment failure therefore needs an explicit choice instead of
        silently relaunching a model that cannot improve the compiler result:
        retry the deterministic command after an environment repair, return to
        Review to patch the source sections, or preserve the artifacts and
        pause.
        """

        artifacts: dict[str, bool] = {}
        if workspace_dir is not None:
            for relative_path in (
                "drafts/survey/survey.tex",
                "drafts/survey/survey.log",
                "drafts/survey/survey_compile_report.json",
                "drafts/survey/survey_audit.json",
            ):
                artifacts[relative_path] = (workspace_dir / relative_path).is_file()
        state.pending_gate = GateState(
            gate_id="t36_compile_recovery_gate",
            presented_at=_now_iso(),
            presentation={
                "_title": "综述 PDF 编译需要决策",
                "_description": (
                    "编译阶段未改写任何综述正文，已保留 TeX、section、审计和 compile report。"
                    "请选择重试确定性编译、回到 Review 修复来源文件，或暂停检查环境。"
                ),
                "error_summary": " ".join(str(error).split())[:1200],
                "compile_report_path": "drafts/survey/survey_compile_report.json",
                "artifacts_present": artifacts,
            },
            options=[
                {
                    "id": "retry_compile",
                    "label": "重试编译",
                    "description": "不调用模型，不改写正文；重新执行 latex_compile 并验证 PDF、log 和 report。",
                },
                {
                    "id": "return_to_review",
                    "label": "回到 Review 修复来源文件",
                    "description": "调用 Survey Writer 只修复 compile report 指向的 section、引用或模板来源；随后重新拼装、审计和编译。",
                },
                {
                    "id": "pause_review",
                    "label": "暂停并检查编译报告",
                    "description": "保留所有文件和诊断，稍后 resume 会再次显示此决策。",
                },
                {
                    "id": "exit",
                    "label": "结束本次综述运行",
                    "description": "停止当前运行，不删除任何 survey artifact。",
                },
            ],
        )
        state.status = "WAITING_HUMAN"
        state.paused_at = _now_iso()
        return state

    @staticmethod
    def _is_t36_compile_recoverable_error(error: str | None) -> bool:
        text = str(error or "").casefold()
        return bool(
            text
            and (
                "t3.6-compile" in text
                or "survey compile" in text
                or "compile report" in text
                or "waiting_environment" in text
                or "latex" in text
            )
        )

    @staticmethod
    def is_hard_runtime_integrity_error(error: str | None) -> bool:
        """Return whether a runtime error is unsafe to retry through a Gate.

        A generic recovery decision must never convert state corruption, unsafe
        writes, forged provenance, or an identity collision into an ordinary
        retry.  Schema shape/format issues, quality findings, provider
        outages, and exhausted budgets deliberately do *not* belong here: they
        remain repairable or degradable workflow conditions.
        """

        text = " ".join(str(error or "").casefold().split())
        hard_patterns = (
            r"path traversal",
            r"workspace-relative",
            r"cannot safely write",
            r"unsafe artifact (?:replacement|overwrite)",
            r"legacy artifact.*overwrit",
            r"state corruption",
            r"unrecoverable state",
            r"fingerprint (?:corruption|mismatch)",
            r"forged (?:parent|child )?lineage",
            r"(?:candidate|population) id .*?(?:overwrite|collision)",
            r"forged (?:citation|source|doi)",
            r"citation (?:identity|provenance) corruption",
        )
        return any(re.search(pattern, text) for pattern in hard_patterns)

    @staticmethod
    def _result_has_explicit_runtime_pause(result: AgentResult) -> bool:
        """Return whether an inline runtime prompt already received a pause choice."""

        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        raw = metadata.get("runtime_explicit_pause")
        return isinstance(raw, dict) and bool(str(raw.get("decision") or "").strip())

    @classmethod
    def _runtime_recovery_payload_from_result(cls, result: AgentResult) -> dict[str, Any] | None:
        """Normalize an AgentRunner recovery signal without trusting text alone."""

        if cls.is_hard_runtime_integrity_error(result.error):
            return None
        if cls._result_has_explicit_runtime_pause(result):
            return None
        metadata = result.metadata if isinstance(result.metadata, dict) else {}
        raw = metadata.get("runtime_recovery")
        if isinstance(raw, dict):
            kind = str(raw.get("kind") or "runtime").strip()
            if kind in {
                "validation",
                "artifact_validation",
                "budget",
                "max_steps",
                "provider",
                "runtime",
                "environment",
                "human_input",
                "survey_retrieval",
            }:
                payload = dict(raw)
                payload["kind"] = kind
                payload["error_summary"] = " ".join(
                    str(payload.get("error_summary") or result.error or "").split()
                )[:1200]
                details = payload.get("details")
                payload["details"] = dict(details) if isinstance(details, dict) else {}
                return payload

        # ``STOP_MAX_STEPS`` and ``STOP_BUDGET`` have unambiguous runtime
        # semantics even for third-party Agents that predate the structured
        # signal.  Keep plain interrupted runs conservative so Ctrl-C remains
        # an ordinary user pause.
        if result.stop_reason == AgentResult.STOP_BUDGET:
            return {"kind": "budget", "error_summary": " ".join(str(result.error or "").split())[:1200], "details": {}}
        if result.stop_reason == AgentResult.STOP_MAX_STEPS:
            return {"kind": "max_steps", "error_summary": " ".join(str(result.error or "").split())[:1200], "details": {}}
        if result.stop_reason != AgentResult.STOP_INTERRUPTED:
            return None
        text = " ".join(str(result.error or "").casefold().split())
        inferred: str | None = None
        if "validation failed" in text or "输出校验" in text or "artifact validation" in text:
            inferred = "validation"
        elif (
            "模型服务" in text
            or "llm provider" in text
            or "provider unavailable" in text
            or "provider failure" in text
            or ("llm" in text and "unavailable" in text)
        ):
            inferred = "provider"
        elif "waiting_environment" in text or "human_input_unavailable" in text or "需要用户输入" in text:
            inferred = "runtime"
        if inferred is None:
            return None
        return {"kind": inferred, "error_summary": " ".join(str(result.error or "").split())[:1200], "details": {}}

    @classmethod
    def _runtime_recovery_payload_from_error(cls, error: str | None) -> dict[str, Any] | None:
        """Upgrade legacy PAUSED states created before structured metadata."""

        synthetic = AgentResult(
            ok=False,
            message="legacy runtime recovery",
            outputs_produced={},
            steps_used=0,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            duration_seconds=0.0,
            stop_reason=AgentResult.STOP_INTERRUPTED,
            error=error,
        )
        return cls._runtime_recovery_payload_from_result(synthetic)

    @staticmethod
    def _runtime_recovery_existing_outputs(
        workspace_dir: Path | None,
        result: AgentResult | None,
        node: TaskNode | None,
    ) -> list[str]:
        """List durable artifacts for the recovery decision without inventing content."""

        if workspace_dir is None:
            return []
        workspace = Path(workspace_dir)
        values: list[Path] = []
        if result is not None:
            values.extend(path for path in result.outputs_produced.values() if isinstance(path, Path))
        if node is not None:
            values.extend(workspace / str(path) for path in (node.outputs or {}).values())
        listed: list[str] = []
        seen: set[str] = set()
        for path in values:
            try:
                relative = path.resolve().relative_to(workspace.resolve())
            except (OSError, ValueError):
                continue
            if not path.exists():
                continue
            text = relative.as_posix()
            if text not in seen:
                seen.add(text)
                listed.append(text)
        return listed[:30]

    @staticmethod
    def _t5_external_wait_launch_context(workspace_dir: Path | None) -> dict[str, Any]:
        """Build a human-operable external-executor handoff, not a raw JSON dump."""

        workspace = Path(workspace_dir).resolve() if workspace_dir is not None else None
        selection_path = "external_executor/report/executor_selection.json"
        selection: dict[str, Any] = {}
        if workspace is not None:
            path = workspace / selection_path
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                selection = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError, json.JSONDecodeError):
                selection = {}

        selected_executor = str(selection.get("selected_executor") or "unknown").strip()
        root = str(workspace) if workspace is not None else "<workspace>"
        prompt = str(selection.get("codex_user_input") or "").strip()
        if selected_executor == "codex_cli" and not prompt:
            prompt = "请读取 external_executor/AGENTS.md，并执行 external_executor/skills/research-execution/SKILL.md。"

        required_paths = [
            "external_executor/executor_research_report.md",
            "external_executor/result_pack.json",
            "external_executor/executor_status.json",
            "external_executor/report/run_manifest.json",
        ]
        artifacts = [
            {
                "path": rel_path,
                "status": "已回传" if workspace is not None and (workspace / rel_path).is_file() else "待回传",
            }
            for rel_path in required_paths
        ]
        command_lines = [f"cd {shlex.quote(root)}", "codex"] if selected_executor == "codex_cli" else []
        if selected_executor == "claude_code_window":
            launch_summary = "在当前 workspace 根目录启动 Claude Code，并发送下方执行指令。"
        elif selected_executor == "manual":
            launch_summary = "将下方执行指令交给获授权的人工或其它外部执行器，并限制其在当前 workspace 内工作。"
        elif selected_executor == "codex_cli":
            launch_summary = "在一个单独终端的当前 workspace 根目录启动 Codex CLI。"
        else:
            launch_summary = "未读取到有效执行器选择记录；先检查 external_executor/report/executor_selection.json。"

        return {
            "selected_executor": selected_executor,
            "selection_path": selection_path,
            "workspace_root": root,
            "launch_summary": launch_summary,
            "command_lines": command_lines,
            "executor_prompt": prompt,
            "required_artifacts": artifacts,
            "concurrency_boundary": (
                "外部执行期间不要在另一个终端对同一 workspace 运行 researchos resume、run-task T5 或 run-task T8。"
                "这些命令可能读取到外部执行器尚未原子写完的 result pack、状态或运行清单。"
            ),
            "completion_boundary": (
                "外部执行根 Skill 在 Writer Handoff 校验通过后会执行其 route 返回的 T8 交接命令。"
                "只有当该执行器明确报告未能启动 T8，且四项回传文件均已就绪时，才在外部执行器停止后运行 "
                f"python -m researchos.cli resume --workspace {shlex.quote(root)}。"
            ),
        }

    def _pause_for_runtime_recovery_gate(
        self,
        state: StateYaml,
        *,
        error: str | None,
        workspace_dir: Path | None,
        result: AgentResult | None = None,
        recovery: dict[str, Any] | None = None,
    ) -> StateYaml | None:
        """Persist a generic recovery decision for non-integrity interruptions.

        This is intentionally below the T4 and T3.6 specialised gates in
        ``advance``.  It covers the common runtime cases which used to become
        a context-free ``PAUSED`` state after retry exhaustion.
        """

        if self.is_hard_runtime_integrity_error(error):
            return None
        payload = dict(recovery or {})
        if not payload and result is not None:
            recovered = self._runtime_recovery_payload_from_result(result)
            payload = dict(recovered or {})
            # A completed Agent can fail for many reasons that are not a
            # recoverable runtime pause (for example a task-specific hard
            # validator).  Only its structured signal or an unambiguous
            # max-step/budget reason may open the generic Gate.  Text-only
            # inference is reserved for legacy PAUSED state files, where the
            # original AgentResult metadata no longer exists.
            if not payload:
                return None
        if not payload:
            recovered = self._runtime_recovery_payload_from_error(error)
            payload = dict(recovered or {})
        if not payload:
            return None

        node = self.nodes.get(state.current_task)
        existing_outputs = self._runtime_recovery_existing_outputs(workspace_dir, result, node)
        summary = " ".join(str(payload.get("error_summary") or error or "").split())[:1200]
        recovery_kind = str(payload.get("kind") or "runtime").strip()
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        is_literature_coverage = recovery_kind == "literature_coverage"
        is_survey_retrieval = recovery_kind == "survey_retrieval"
        is_external_wait = state.current_task == "T5-EXTERNAL-WAIT"
        payload.update(
            {
                "schema_version": "1.0.0",
                "target_task": state.current_task,
                "error_summary": summary,
                "existing_outputs": existing_outputs,
            }
        )
        if is_literature_coverage:
            reentry_target = str(details.get("return_to_task") or "T3").strip()
            if reentry_target not in self.nodes:
                reentry_target = "T3" if "T3" in self.nodes else state.current_task
            payload["return_to_task"] = reentry_target
            title = "文献阅读覆盖未完成"
            description = (
                "当前阶段需要共享文献事实源，但摘要轻读覆盖未达到本工作区已确认的目标。"
                "为避免用旧语料继续综合、生成候选或写作，系统不会重试当前下游阶段。"
                "请回到 T3 补齐可读论文的阅读笔记；随后 T3.5、T4 及后续阶段会基于更新后的同一份文献清单重新运行。"
            )
            options = [
                {
                    "id": "return_to_t3_for_reading",
                    "label": "回到 T3 补齐阅读",
                    "description": "保留现有文献、综合和候选文件；从 T3 补齐缺少的阅读覆盖，再重新完成后续综合与研究方向生成。",
                },
                {
                    "id": "inspect_then_pause",
                    "label": "检查后暂停",
                    "description": "保留诊断与所有已有文件；下次 resume 仍会展示此处，不会直接重跑下游阶段。",
                },
                {
                    "id": "exit",
                    "label": "结束本次运行",
                    "description": "本次命令停止，不删除任何 artifact；之后 resume 仍会先展示这项阅读覆盖决策。",
                },
            ]
        elif is_survey_retrieval:
            checkpoint_path = str(details.get("checkpoint_path") or "literature/survey_supplement/expansion_checkpoint.json")
            completed = details.get("completed_query_count")
            total = details.get("query_count")
            progress = f"{completed}/{total}" if completed is not None and total is not None else "部分"
            title = "综述定向补检已暂停"
            description = (
                "定向补检在完成一部分来源查询后达到本次操作预算，当前检索记录和阅读笔记检查点均已写入工作区。"
                f"已完成查询：{progress}。恢复会读取 `{checkpoint_path}`，只继续未完成的查询，"
                "不会重新检索已完成条目，也不会在没有真实文献输入时继续生成综述。"
            )
            options = [
                {
                    "id": "retry_targeted_repair",
                    "label": "继续定向补检",
                    "description": "从持久化检查点继续剩余查询，并复用已保存的检索记录、PDF 获取结果和笔记。",
                },
                {
                    "id": "inspect_then_pause",
                    "label": "检查后暂停",
                    "description": "保留检查点、来源状态和已生成笔记；下次 resume 仍从该检查点继续。",
                },
                {
                    "id": "exit",
                    "label": "结束本次运行",
                    "description": "不删除任何补检记录或笔记；之后 resume 时仍会先展示这项补检恢复决策。",
                },
            ]
        elif is_external_wait:
            title = "外部执行器正在等待或运行"
            description = (
                "ResearchOS 已完成内部交接，当前不应继续调用模型或重跑 T5。"
                "请按下方执行器说明在同一 workspace 中完成外部实验和 Writer Handoff；"
                "系统只会在四项回传文件齐备且校验通过后进入 T8。"
            )
            options = [
                {
                    "id": "retry_targeted_repair",
                    "label": "检查外部执行回传",
                    "description": "只检查 executor report、result pack、status 和 run manifest；齐备且合法时进入 T8，不会重跑 T4.5 或 T5。",
                },
                {
                    "id": "inspect_then_pause",
                    "label": "暂不检查，保持等待",
                    "description": "保留当前外部执行器选择和全部交接文件；下次 resume 仍展示启动说明与回传状态。",
                },
                {
                    "id": "exit",
                    "label": "结束本次 ResearchOS 会话",
                    "description": "不删除外部执行器任务或任何 artifact；外部执行完成后可显式 resume 回到此处检查。",
                },
            ]
        else:
            title = "运行恢复需要决策"
            description = (
                "任务在可恢复的运行时问题后暂停，已有文件与诊断均已保留。"
                "请选择定向修复、使用旧兼容修复窗口，或暂停检查；系统不会把该中断伪装成成功。"
            )
            options = [
                {
                    "id": "retry_targeted_repair",
                    "label": "继续定向修复",
                    "description": "基于当前诊断和已有产物继续；只处理实际失败点，不重写已合格内容。",
                },
                {
                    "id": "extend_recovery_window",
                    "label": "使用旧兼容修复窗口后重试",
                    "description": "仅用于旧 workspace 的 bounded-recovery 记录；当前默认运行不施加普通 step/token 上限，也不会放松证据、引用或状态完整性。",
                },
                {
                    "id": "inspect_then_pause",
                    "label": "检查后暂停",
                    "description": "保留本决策与所有诊断；下次 resume 会重新展示，不会直接重跑。",
                },
                {
                    "id": "exit",
                    "label": "结束本次运行",
                    "description": "本次命令停止，不删除任何 artifact；之后显式 resume 时仍会先显示恢复决策。",
                },
            ]
        state.pending_gate = GateState(
            gate_id="runtime_recovery_gate",
            presented_at=_now_iso(),
            presentation={
                "_title": title,
                "_description": description,
                "runtime_recovery": payload,
                "error_summary": summary,
                "existing_outputs": existing_outputs,
                "resume_state_path": "_runtime/resume/" + state.current_task.lower().replace(".", "_") + "_resume_state.json",
                **(
                    {"external_executor_launch": self._t5_external_wait_launch_context(workspace_dir)}
                    if is_external_wait
                    else {}
                ),
            },
            options=options,
        )
        state.status = "WAITING_HUMAN"
        state.paused_at = _now_iso()
        return state

    def _resolve_runtime_recovery_gate(
        self,
        state: StateYaml,
        gate_result: dict[str, Any],
        *,
        workspace_dir: Path | None,
    ) -> StateYaml:
        """Apply a researcher decision for a persisted runtime recovery Gate."""

        pending = state.pending_gate
        if pending is None:
            raise ValueError("runtime recovery gate is not pending")
        option_id = str(gate_result.get("option_id") or gate_result.get("key") or "inspect_then_pause")
        presentation = dict(pending.presentation or {})
        raw_recovery = presentation.get("runtime_recovery")
        recovery = dict(raw_recovery) if isinstance(raw_recovery, dict) else {}
        target_task = str(recovery.get("target_task") or state.current_task)
        if target_task not in self.nodes:
            # The active state is the authority for a persisted dynamic Gate.
            # A stale/malformed presentation must not redirect a resume to an
            # arbitrary task name.
            target_task = state.current_task

        if option_id == "return_to_t3_for_reading":
            details = recovery.get("details") if isinstance(recovery.get("details"), dict) else {}
            reentry_target = str(recovery.get("return_to_task") or details.get("return_to_task") or "T3").strip()
            if str(recovery.get("kind") or "") != "literature_coverage" or reentry_target not in self.nodes:
                # This option is meaningful only for the explicitly typed
                # evidence-coverage recovery.  A malformed persisted gate is
                # never allowed to redirect an unrelated task to T3.
                option_id = "inspect_then_pause"
            else:
                state.task_context["literature_coverage_reentry"] = {
                    "schema_version": "1.0.0",
                    "semantics": "literature_coverage_reentry",
                    "from_task": target_task,
                    "to_task": reentry_target,
                    "requested_at": _now_iso(),
                    "error_summary": str(recovery.get("error_summary") or presentation.get("error_summary") or "")[:1200],
                    "scope": (
                        "Resume T3 to create the missing reading coverage first. Preserve existing artifacts for audit, "
                        "but do not reuse their derived synthesis, candidate, score, or writing conclusions after the literature set changes."
                    ),
                }
                state.task_context.pop("runtime_recovery", None)
                state.pending_gate = None
                state.current_task = reentry_target
                state.status = "RUNNING"
                state.paused_at = None
                state.last_error = None
                return state

        if option_id in {"retry_targeted_repair", "extend_recovery_window"}:
            directive: dict[str, Any] = {
                "schema_version": "1.0.0",
                "semantics": "runtime_recovery_directive",
                "action": option_id,
                "target_task": target_task,
                "requested_at": _now_iso(),
                "recovery_kind": str(recovery.get("kind") or "runtime"),
                "error_summary": str(recovery.get("error_summary") or presentation.get("error_summary") or "")[:1200],
                "details": dict(recovery.get("details") or {}) if isinstance(recovery.get("details"), dict) else {},
                "existing_outputs": [str(item) for item in recovery.get("existing_outputs", [])][:30]
                if isinstance(recovery.get("existing_outputs"), list)
                else [],
                "scope": (
                    "Read the saved diagnostics and existing artifacts first. Preserve valid work and repair only the implicated files, "
                    "claims, structures, or environment preconditions. Do not fabricate evidence, citations, data, metrics, results, "
                    "or scientific explanations merely to make a validator pass."
                ),
            }
            if option_id == "extend_recovery_window":
                directive["resource_window"] = {
                    "mode": "legacy_bounded_extension_compatibility",
                    "increase_ratio": 0.25,
                    "applies_to": ["max_steps", "max_tokens", "max_wall_seconds"],
                }
            if workspace_dir is not None:
                safe_task = re.sub(r"[^A-Za-z0-9_.-]+", "_", target_task).lower()
                receipt_path = Path(workspace_dir) / "_runtime" / "recovery" / f"{safe_task}_runtime_recovery.json"
                try:
                    receipt_path.parent.mkdir(parents=True, exist_ok=True)
                    temporary_path = receipt_path.with_suffix(receipt_path.suffix + ".tmp")
                    temporary_path.write_text(
                        json.dumps(directive, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    temporary_path.replace(receipt_path)
                    directive["path"] = str(receipt_path.relative_to(workspace_dir))
                except OSError as exc:
                    # The state itself remains authoritative if a transient
                    # filesystem issue prevents the optional receipt write.
                    directive["path"] = ""
                    directive["receipt_write_error"] = str(exc)[:500]
            state.task_context["runtime_recovery"] = directive
            state.pending_gate = None
            state.current_task = target_task
            state.status = "RUNNING"
            state.paused_at = None
            state.last_error = None
            return state

        # A pause/exit is intentional.  Preserve the pending Gate so a later
        # explicit ``resume`` re-presents the same saved diagnosis instead of
        # silently launching the Agent with no researcher decision.
        presentation["last_user_action"] = option_id
        presentation["last_user_action_at"] = _now_iso()
        state.pending_gate.presentation = presentation
        state.status = "PAUSED"
        state.paused_at = _now_iso()
        state.last_error = (
            "Researcher chose to inspect the runtime recovery diagnostics before retrying."
            if option_id == "inspect_then_pause"
            else "Researcher ended this invocation at the runtime recovery decision; saved artifacts remain unchanged."
        )
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
        # A previously rendered T4 recovery panel can become obsolete without
        # another model turn: legacy Final Cards may have been migrated, or a
        # bounded repair may already have committed a complete deck before the
        # process resumed.  Do not keep asking the researcher to repair an
        # artifact which is now valid; return to the ordinary Gate1 decision
        # panel in this same invocation.
        if (
            state.pending_gate.gate_id == "t4_recovery_gate"
            and node.task_id == "T4"
            and workspace_dir is not None
            and self._t4_gate1_ready_without_selection(workspace_dir)
        ):
            state.pending_gate = None
            state.status = "RUNNING"
            state.paused_at = None
            state.last_error = None
            state.task_context["t4_recovery_resolved_at"] = _now_iso()
            state.task_context["t4_recovery_resolution"] = "final_card_artifacts_now_ready"
            return state
        if (
            state.pending_gate.gate_id == "t4_recovery_gate"
            and node.task_id == "T4"
            and workspace_dir is not None
            and state.pending_gate.presentation.get("_recovery_presentation_version") != 2
        ):
            # T4 recovery gates are durable workspace state.  Upgrade an old
            # card-repair-only presentation before the first resumed CLI view,
            # using its saved original error as the classifier input.  This is
            # display-only: it does not rerun T4, rewrite a Candidate, or
            # discard any recovery checkpoint.
            legacy_error = str(
                state.last_error
                or state.pending_gate.presentation.get("error_summary")
                or (state.history[-1].error if state.history else "")
                or "T4 interrupted before Gate1"
            )
            return self._pause_for_t4_recovery_gate(state, legacy_error, workspace_dir)
        presentation = dict(state.pending_gate.presentation or {})
        options = list(state.pending_gate.options or [])
        # Recovery gates are persisted dynamically by the runtime.  Their
        # presentation/options are already complete, so a missing optional
        # registry decoration must never crash resume.  Normal declared gates
        # still receive their configured title/description below.
        gate_spec = self.gates.get(state.pending_gate.gate_id, {})
        if gate_spec.get("title"):
            presentation["_title"] = gate_spec["title"]
        if gate_spec.get("description"):
            presentation["_description"] = gate_spec["description"]
        # T5 material/executor gates formerly required an unprompted ``notes``
        # line. Pending gates persist their options by design, so a workspace
        # paused before this fix would otherwise keep requesting a field that
        # no longer participates in the decision. This migration is limited
        # to removing that obsolete input requirement; it preserves every
        # option ID, branch, presentation, and user artifact.
        if state.pending_gate.gate_id in {"t5_expr_material_gate", "t5_executor_gate"}:
            configured_options = {
                str(item.get("id") or item.get("key") or ""): item
                for item in gate_spec.get("options", [])
                if isinstance(item, dict)
            }
            migrated_option_ids: list[str] = []
            for option in options:
                if not isinstance(option, dict):
                    continue
                option_id = str(option.get("id") or option.get("key") or "")
                configured = configured_options.get(option_id, {})
                configured_inputs = configured.get("collect_input", []) if isinstance(configured, dict) else []
                legacy_inputs = option.get("collect_input", [])
                if (
                    isinstance(legacy_inputs, list)
                    and "notes" in legacy_inputs
                    and "notes" not in configured_inputs
                ):
                    remaining_inputs = [field for field in legacy_inputs if field != "notes"]
                    if remaining_inputs:
                        option["collect_input"] = remaining_inputs
                    else:
                        option.pop("collect_input", None)
                    migrated_option_ids.append(option_id)
            if migrated_option_ids:
                receipts = state.task_context.get("pending_gate_migrations")
                history = list(receipts) if isinstance(receipts, list) else []
                history.append(
                    {
                        "gate_id": state.pending_gate.gate_id,
                        "migration": "remove_obsolete_notes_input",
                        "option_ids": migrated_option_ids,
                        "migrated_at": _now_iso(),
                    }
                )
                state.task_context["pending_gate_migrations"] = history[-20:]
        if state.pending_gate.gate_id == "t4_prerun_gate" and workspace_dir is not None:
            return self._pause_for_t4_prerun_gate(state, workspace_dir)
        if node.task_id == "T2-PARAM-GATE":
            presentation["current_parameter_preview"] = build_literature_param_gate_preview(workspace_dir)
            options = enrich_literature_param_gate_options(options, workspace_dir)
        elif node.task_id in _CCF_TEMPLATE_GATE_TASKS:
            options = _ccf_template_gate_options(task_id=node.task_id)
        elif node.task_id == "T5-PROTOCOL-GATE" and workspace_dir is not None:
            presentation["protocol_readiness"] = self._t5_protocol_gate_summary(workspace_dir)
        elif node.task_id == "T4-GATE1" and workspace_dir is not None:
            resumed = self._resume_confirmed_native_t4_directive(state, workspace_dir)
            if resumed is not None:
                return resumed
            # A persisted operation confirmation is already a complete,
            # user-facing panel.  Refreshing it as if it were the main
            # candidate chooser used to mix in the candidate overview and
            # made a confirmed directive appear to have returned to Gate1.
            # Keep its immutable plan/options intact until the user confirms,
            # cancels, pauses, or sends another explicit dialogue turn.
            if isinstance(state.task_context.get("t4_pending_directive"), dict):
                return state
            redirected = self._redirect_incomplete_t4_gate_to_recovery(state, workspace_dir)
            if redirected is not None:
                return redirected
            presentation["candidate_overview"] = _t4_gate1_candidate_overview(workspace_dir)
            presentation["candidate_pool_fingerprints"] = _t4_gate1_candidate_pool_fingerprints(workspace_dir)
            presentation["t4_artifact_guide"] = _t4_gate1_file_navigation(workspace_dir)
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

    def _resume_confirmed_native_t4_directive(
        self,
        state: StateYaml,
        workspace_dir: Path,
    ) -> StateYaml | None:
        """Apply one durable T4 confirmation that was interrupted before execution.

        A confirmation is written before the state transition so Ctrl+D,
        process termination, or an old runtime error cannot lose the user's
        decision.  Historically, a selection blocked by the now-soft
        ``revise_before_selection`` label left that confirmation on disk but
        dropped ``t4_pending_directive``; every later ``resume`` then merely
        re-rendered Gate1.  Recover only a matching, accepted directive from
        the *current* population, and let the normal mutation boundary write
        the selection receipt.  Once written, that receipt prevents replay.
        """

        if state.current_task != "T4-GATE1":
            return None
        if isinstance(state.task_context.get("t4_pending_directive"), dict):
            return None
        if validate_t4_gate1_selection_file(workspace_dir)[0]:
            return None
        directives_root = workspace_dir / "ideation" / "human_directives"
        if not directives_root.is_dir():
            return None
        confirmation: dict[str, Any] | None = None
        for path in sorted(directives_root.glob("*_confirmation.json"), reverse=True):
            try:
                candidate = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(candidate, dict):
                continue
            if (
                candidate.get("semantics") == "t4_human_directive_confirmation"
                and candidate.get("accepted") is True
                and str(candidate.get("outcome") or "") == "confirmed_for_execution"
            ):
                confirmation = candidate
                break
        if confirmation is None:
            return None
        relative_directive_path = str(confirmation.get("directive_path") or "").strip()
        if not relative_directive_path:
            return None
        directive_file = (workspace_dir / relative_directive_path).resolve()
        try:
            directive_file.relative_to(workspace_dir.resolve())
            record = json.loads(directive_file.read_text(encoding="utf-8"))
            raw_directive = record.get("directive") if isinstance(record, dict) else None
            directive = IdeaDirective.model_validate(raw_directive)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if str(confirmation.get("directive_id") or "") != directive.directive_id:
            return None
        try:
            population, _dossiers = current_population_context(workspace_dir)
        except (OSError, ValueError):
            return None
        requested_population = str(record.get("population_id") or "").strip() if isinstance(record, dict) else ""
        if requested_population and requested_population != population.population_id:
            state.task_context["t4_stale_confirmed_directive"] = {
                "directive_id": directive.directive_id,
                "reason": "population_changed_before_confirmed_operation_could_resume",
                "confirmed_population_id": requested_population,
                "current_population_id": population.population_id,
            }
            return None
        state.task_context["t4_resumed_confirmed_directive"] = {
            "directive_id": directive.directive_id,
            "directive_path": relative_directive_path,
            "resumed_at": _now_iso(),
            "reason": "confirmed_before_interruption",
        }
        return self._apply_native_t4_directive(
            state,
            directive=directive,
            directive_path=relative_directive_path,
            workspace_dir=workspace_dir,
        )

    @staticmethod
    def _t4_gate1_ready_without_selection(workspace_dir: Path) -> bool:
        if validate_t4_gate1_selection_file(workspace_dir)[0]:
            return False
        try:
            from ..agents.ideation import validate_t4_gate1_ready

            ok, _err = validate_t4_gate1_ready(workspace_dir)
            cards_ok, _cards_error = validate_t4_portfolio_final_cards(workspace_dir)
            return bool(ok and cards_ok)
        except Exception:
            return False

    def _redirect_incomplete_t4_gate_to_recovery(
        self,
        state: StateYaml,
        workspace_dir: Path,
    ) -> StateYaml | None:
        """Keep every Gate1 entry behind the complete LLM Final Card boundary.

        Older persisted gates, direct CLI entry, and rollback all converge here.
        Candidate Population continuity is retained, but a researcher must not
        see or select a partial Card whose missing explanation could otherwise
        be replaced by a legacy field or renderer fallback.
        """

        if self._t4_gate1_ready_without_selection(workspace_dir):
            return None
        try:
            from ..agents.ideation import validate_t4_gate1_ready

            structural_ok, structural_error = validate_t4_gate1_ready(workspace_dir)
        except Exception as exc:
            structural_ok, structural_error = False, str(exc)
        cards_ok, cards_error = validate_t4_portfolio_final_cards(workspace_dir)
        if structural_ok and not cards_ok:
            reason = "当前 Population 已保存，但 Portfolio Idea Card 尚未由 LLM 完整编译。" + str(cards_error or "")
        else:
            reason = "T4 Gate1 产物尚未完整。" + str(structural_error or cards_error or "")
        state.current_task = "T4"
        state.pending_gate = None
        return self._pause_for_t4_recovery_gate(state, reason, workspace_dir)

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
            explicit_pause = self._result_has_explicit_runtime_pause(result)
            if not explicit_pause:
                t4_recovery = self._pause_for_t4_runtime_failure(
                    state,
                    result.error,
                    workspace_dir=workspace_dir,
                )
                if t4_recovery is not None:
                    return t4_recovery
            if explicit_pause:
                return self.mark_interrupted(state)
            if state.current_task == "T4":
                # A T4 interruption carrying an explicit integrity signature
                # is not a normal resumable pause.  Preserve the artifacts and
                # error, then use the configured failure transition so callers
                # receive the required nonzero outcome instead of a retry loop.
                history.status = "FAILED"
                node = self.nodes[state.current_task]
                next_task = node.next_on_failure
                if next_task and next_task in self.nodes:
                    state.current_task = next_task
                state.status = "FAILED"
                return state
            if state.current_task == "T3.6-ASSEMBLE" and self._is_t36_assemble_recoverable_error(result.error):
                return self._pause_for_t36_assemble_recovery_gate(state, result.error or "", workspace_dir)
            if state.current_task == "T3.6-COMPILE" and self._is_t36_compile_recoverable_error(result.error):
                return self._pause_for_t36_compile_recovery_gate(state, result.error or "", workspace_dir)
            runtime_recovery = self._pause_for_runtime_recovery_gate(
                state,
                error=result.error,
                workspace_dir=workspace_dir,
                result=result,
            )
            if runtime_recovery is not None:
                return runtime_recovery
            return self.mark_interrupted(state)

        node = self.nodes[state.current_task]
        if not result.ok:
            state.last_error = result.error
            t4_recovery = self._pause_for_t4_runtime_failure(
                state,
                result.error,
                workspace_dir=workspace_dir,
            )
            if t4_recovery is not None:
                return t4_recovery
            if state.current_task == "T3.6-ASSEMBLE" and self._is_t36_assemble_recoverable_error(result.error):
                return self._pause_for_t36_assemble_recovery_gate(state, result.error or "", workspace_dir)
            if state.current_task == "T3.6-COMPILE" and self._is_t36_compile_recoverable_error(result.error):
                return self._pause_for_t36_compile_recovery_gate(state, result.error or "", workspace_dir)
            runtime_recovery = self._pause_for_runtime_recovery_gate(
                state,
                error=result.error,
                workspace_dir=workspace_dir,
                result=result,
            )
            if runtime_recovery is not None:
                return runtime_recovery
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
            state.task_context.pop("t4_final_card_repair", None)
            return self._transition_to_next(state, "T4-GATE1", workspace_dir=workspace_dir)

        if (
            state.current_task == "T4"
            and (result.metadata or {}).get("completion_mode") == "t4_pre_novelty_ready"
            and "T4.5" in self.nodes
        ):
            return self._transition_to_next(state, "T4.5", workspace_dir=workspace_dir)

        if state.current_task == "T3.6-ASSEMBLE":
            # The recovery receipt documents a user-approved extra repair
            # window. It must reach that resumed Assemble run, but cannot leak
            # into a later ordinary assembly after this one has succeeded.
            state.task_context.pop("t36_assemble_recovery", None)

        if state.current_task == "T3.6-REVIEW":
            # A compile recovery directive applies to this one source-repair
            # review only.  Retaining it would make a later ordinary review
            # look like a still-pending compile failure.
            state.task_context.pop("t36_compile_recovery", None)

        runtime_recovery_directive = state.task_context.get("runtime_recovery")
        if (
            isinstance(runtime_recovery_directive, dict)
            and runtime_recovery_directive.get("target_task") == state.current_task
        ):
            # A generic repair window is single-use. Keeping it after a
            # successful task would make a later normal run look like it still
            # has an approved exception.
            state.task_context.pop("runtime_recovery", None)

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
            elif state.current_task in _CCF_TEMPLATE_GATE_TASKS:
                options = _ccf_template_gate_options(task_id=state.current_task)
            if state.current_task == "T4-GATE1" and workspace_dir is not None:
                presentation["candidate_overview"] = _t4_gate1_candidate_overview(workspace_dir)
                presentation["candidate_pool_fingerprints"] = _t4_gate1_candidate_pool_fingerprints(workspace_dir)
                presentation["t4_artifact_guide"] = _t4_gate1_file_navigation(workspace_dir)
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
        if state.pending_gate.gate_id == "t4_recovery_gate":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "pause")
            if option_id == "retry_t4":
                recovery_kind = str(state.pending_gate.presentation.get("_recovery_kind") or "targeted_recovery")
                # Carry a concise copy into ExecutionContext for observability,
                # but let the runtime revalidate the durable artifact before it
                # skips any scientific operation.  The original operation is
                # intentionally retained until a successful Gate1 transition;
                # its checkpoint identity proves it was already consumed.
                if workspace_dir is not None:
                    operation = state.task_context.get("t4_operation_request")
                    try:
                        checkpoint, _checkpoint_error = T4ArtifactStore(
                            workspace_dir
                        ).current_final_card_repair_checkpoint(
                            operation=operation if isinstance(operation, dict) else None,
                        )
                    except (OSError, ValueError):
                        checkpoint = None
                    if checkpoint is not None:
                        state.task_context["t4_final_card_repair"] = {
                            "checkpoint_path": "ideation/evolution/final_card_repair_state.json",
                            "population_id": str(checkpoint.get("population_id") or ""),
                            "population_generation": checkpoint.get("population_generation"),
                            "operation_action": str(checkpoint.get("operation_action") or ""),
                            "operation_directive_id": str(checkpoint.get("operation_directive_id") or ""),
                            "status": str(checkpoint.get("status") or ""),
                        }
                    else:
                        state.task_context.pop("t4_final_card_repair", None)
                state.pending_gate = None
                state.current_task = "T4"
                state.status = "RUNNING"
                state.paused_at = None
                state.last_error = None
                state.task_context["t4_recovery_request"] = {
                    "kind": recovery_kind,
                    "requested_at": _now_iso(),
                }
                return state
            if option_id == "open_gate1" and workspace_dir is not None and self._t4_gate1_ready_without_selection(workspace_dir):
                state.pending_gate = None
                state.current_task = "T4-GATE1"
                return self.pause_for_immediate_gate(state, workspace_dir=workspace_dir)
            state.pending_gate = None
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            state.last_error = "T4 recovery was paused by human decision; all saved Candidates and diagnostics remain available."
            return state
        if state.current_task == "T4-GATE1" and workspace_dir is not None:
            # A confirmed selection is a durable post-Gate transaction.  Check
            # its receipt before evaluating whether the *pre-selection* Gate1
            # deck is complete: selected compilation legitimately writes new
            # artifacts, and an old in-memory Gate presentation must never
            # send a Resume back through T4 after the user already chose T4.5.
            if validate_t4_gate1_selection_file(workspace_dir)[0]:
                return self._advance_verified_t4_gate1_selection(state, workspace_dir)
            redirected = self._redirect_incomplete_t4_gate_to_recovery(state, workspace_dir)
            if redirected is not None:
                return redirected
        if state.pending_gate.gate_id == "t36_assemble_recovery_gate":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "pause_review")
            recovery_presentation = dict(state.pending_gate.presentation or {})
            state.pending_gate = None
            if option_id == "retry_survey_repair":
                repair_guidance = recovery_presentation.get("repair_guidance")
                if not isinstance(repair_guidance, dict):
                    repair_guidance = {}
                directive: dict[str, Any] = {
                    "semantics": "t36_assemble_recovery_directive",
                    "action": "retry_survey_repair",
                    "requested_at": _now_iso(),
                    "audit_path": "drafts/survey/survey_audit.json",
                    "error_summary": str(recovery_presentation.get("error_summary") or "")[:1000],
                    "repair_guidance": repair_guidance,
                    "scope": (
                        "Read the saved audit guidance first. Repair only the implicated source sections, bibliography, plan, or state; "
                        "do not edit the derived survey.tex directly or add unsupported citations."
                    ),
                }
                if workspace_dir is not None:
                    directive_path = workspace_dir / "drafts" / "survey" / "survey_assemble_recovery_directive.json"
                    try:
                        directive_path.parent.mkdir(parents=True, exist_ok=True)
                        temporary_path = directive_path.with_suffix(directive_path.suffix + ".tmp")
                        temporary_path.write_text(
                            json.dumps(directive, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
                        temporary_path.replace(directive_path)
                        directive["path"] = "drafts/survey/survey_assemble_recovery_directive.json"
                    except OSError as exc:
                        # State remains the durable fallback if a transient
                        # filesystem issue prevents the convenience copy from
                        # being written. A human-approved recovery must not be
                        # turned into another terminal failure by this receipt.
                        directive["path"] = ""
                        directive["receipt_write_error"] = str(exc)[:500]
                state.current_task = "T3.6-ASSEMBLE"
                state.status = "RUNNING"
                state.paused_at = None
                state.last_error = None
                state.task_context["t36_assemble_recovery"] = directive
                return state
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            state.last_error = "Survey assembly recovery was paused by human decision; all section drafts and audit diagnostics remain available."
            return state
        if state.pending_gate.gate_id == "t36_compile_recovery_gate":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "pause_review")
            recovery_presentation = dict(state.pending_gate.presentation or {})
            state.pending_gate = None
            if option_id == "retry_compile":
                state.current_task = "T3.6-COMPILE"
                state.status = "RUNNING"
                state.paused_at = None
                state.last_error = None
                return state
            if option_id == "return_to_review":
                directive = {
                    "semantics": "t36_compile_recovery_directive",
                    "action": "return_to_review",
                    "requested_at": _now_iso(),
                    "compile_report_path": "drafts/survey/survey_compile_report.json",
                    "error_summary": str(recovery_presentation.get("error_summary") or "")[:1200],
                    "scope": (
                        "Read the compile report first. Patch only the implicated source section, bibliography, template input, or review action; "
                        "then reassemble, audit, and compile. Do not hand-edit the derived survey.tex."
                    ),
                }
                state.task_context["t36_compile_recovery"] = directive
                state.current_task = "T3.6-REVIEW"
                state.status = "RUNNING"
                state.paused_at = None
                state.last_error = None
                return state
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            state.last_error = (
                "Survey compilation recovery was paused by human decision; survey.tex, section drafts, audit, and compile report remain available."
            )
            return state
        if state.pending_gate.gate_id == "runtime_recovery_gate":
            return self._resolve_runtime_recovery_gate(
                state,
                gate_result,
                workspace_dir=workspace_dir,
            )
        node = self.nodes[state.current_task]
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
                presentation["t4_artifact_guide"] = _t4_gate1_file_navigation(workspace_dir)
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
        if node.task_id == "T5-EXECUTOR-GATE" and workspace_dir is not None:
            readiness = self._t5_execution_readiness(workspace_dir)
            if readiness["status"] != "ready":
                protocol_task = "T5-PROTOCOL-GATE" if "T5-PROTOCOL-GATE" in self.nodes else "T5-REBOOST-GATE"
                state.pending_gate = None
                state.current_task = protocol_task
                state.status = "RUNNING"
                state.paused_at = None
                state.last_error = (
                    "T5 executor selection was deferred because the handoff still requires protocol confirmation; "
                    "no executor selection artifact was written."
                )
                return self.pause_for_immediate_gate(state, workspace_dir=workspace_dir)

        next_task = self._resolve_branch(node, gate_result, state, workspace_dir=workspace_dir)
        # ``_resolve_branch`` runs before the material receipt is persisted,
        # so its ``__parse_from_output__`` route can only see the previous
        # decision file. Resolve the current affirmative material choice here
        # from the already-compiled handoff rather than briefly exposing an
        # executor choice that is not authorized by the protocol.
        if node.task_id == "T5-EXPR-MATERIAL-GATE" and workspace_dir is not None:
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "").strip().lower()
            if option_id in {"materials_ready", "ready", "continue", "done"}:
                readiness = self._t5_execution_readiness(workspace_dir)
                if readiness.get("status") != "ready":
                    next_task = "T5-PROTOCOL-GATE" if "T5-PROTOCOL-GATE" in self.nodes else "T5-REBOOST-GATE"
        self._persist_immediate_gate_result(node, gate_result, next_task, workspace_dir)
        if node.task_id == "T5-EXPR-MATERIAL-GATE" and next_task == "T5-EXPR-MATERIAL-GATE":
            state.pending_gate = None
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            state.last_error = (
                "WAITING_MATERIALS: place source datasets, repositories, baselines, benchmarks, weights, "
                "and material notes under resources/; place only deployed runnable assets under "
                "external_executor/expr/, then resume."
            )
            return state
        if node.task_id == "T5-PROTOCOL-GATE" and next_task == "T5-PROTOCOL-GATE":
            state.pending_gate = None
            state.status = "PAUSED"
            state.paused_at = _now_iso()
            state.last_error = (
                "WAITING_PROTOCOL: T5 handoff is preserved. Confirm the recorded simulation/benchmark, backbone, "
                "seed, scale, and resource decisions before formal execution."
            )
            return state
        if (
            node.task_id == "T5-EXTERNAL-WAIT"
            and workspace_dir is not None
            and next_task in {"T8-STYLE-GATE", "T8-RESOURCE"}
        ):
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
                state.last_error = str(readiness.get("message") or "external executor T8 handoff materials are not ready")
                return state
        state.pending_gate = None
        return self._transition_to_next(state, next_task, workspace_dir=workspace_dir)

    def _advance_verified_t4_gate1_selection(
        self,
        state: StateYaml,
        workspace_dir: Path,
    ) -> StateYaml:
        """Advance one validated Gate1 selection without re-entering T4.

        This is the only Resume/duplicate-confirmation path for a persisted
        selection receipt.  It makes the receipt authoritative across live
        Gate handling, generic runners, and process boundaries, while keeping
        legacy post-Gate targets readable when they are declared by the state
        machine.
        """

        next_task = "T4.5"
        try:
            selection_payload = json.loads(
                (workspace_dir / "ideation" / "_gate1_user_selection.json").read_text(encoding="utf-8")
            )
            declared = str(selection_payload.get("next_task") or "").strip() if isinstance(selection_payload, dict) else ""
            if declared in self.nodes:
                next_task = declared
        except (OSError, json.JSONDecodeError):
            # ``validate_t4_gate1_selection_file`` already established the
            # receipt's validity.  Keep the native safe target if the optional
            # second read races a transient filesystem error.
            pass
        state.pending_gate = None
        state.last_error = None
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
            # Compatibility repair for a short-lived Gate regression: older
            # LLM parsing could turn an explicit “查看 D1” into a pending
            # select_candidate.  Detect that durable raw wording before any
            # confirm action and invalidate the stale plan.  This preserves
            # the audit trail while making it impossible to execute a view as
            # a selection after upgrading ResearchOS.
            raw_pending = pending.get("directive") if isinstance(pending.get("directive"), dict) else {}
            try:
                pending_directive = IdeaDirective.model_validate(raw_pending)
            except Exception:
                pending_directive = None
            if pending_directive is not None and _explicit_read_only_action(
                pending_directive.raw_user_input,
                target_count=len(pending_directive.target_candidate_ids),
            ):
                state.task_context.pop("t4_pending_directive", None)
                persist_idea_directive_confirmation(
                    workspace_dir,
                    directive=pending_directive,
                    directive_path=str(pending.get("directive_path") or ""),
                    accepted=False,
                    outcome="invalidated_legacy_read_only_parse",
                )
                return self._reopen_native_t4_gate(
                    state,
                    workspace_dir,
                    result={
                        "title": "已修复旧版只读解析",
                        "summary": (
                            "此前保存的操作计划源自明确的查看请求，已自动作废；没有 Candidate、Population 或版本被改变。"
                            "现在可重新输入“查看 D1”读取详情，或明确输入“推进 D1”后再确认。"
                        ),
                        "kind": "legacy_read_only_parse_repaired",
                    },
                )
            if (
                pending_directive is not None
                and pending_directive.action != "select_candidate"
                and _explicit_selection_action(
                    pending_directive.raw_user_input,
                    target_count=len(pending_directive.target_candidate_ids),
                )
            ):
                state.task_context.pop("t4_pending_directive", None)
                persist_idea_directive_confirmation(
                    workspace_dir,
                    directive=pending_directive,
                    directive_path=str(pending.get("directive_path") or ""),
                    accepted=False,
                    outcome="invalidated_legacy_advance_parse",
                )
                return self._reopen_native_t4_gate(
                    state,
                    workspace_dir,
                    result={
                        "title": "已修复旧版推进解析",
                        "summary": (
                            "此前保存的“推进候选”计划被错误解释为重新演化，已自动作废；没有新版本被生成。"
                            "请重新输入“推进 D1”，系统会创建进入 T4.5 的确认计划。"
                        ),
                        "kind": "legacy_advance_parse_repaired",
                    },
                )
            if option_id == "t4_directive" and self._is_t4_readonly_gate_result(gate_result):
                _population, dossiers = current_population_context(workspace_dir)
                display_ids = _t4_gate1_display_id_map(workspace_dir)
                raw = self._native_t4_directive_text(option_id=option_id, captured=captured)
                raw = _resolve_t4_display_ids(raw, display_ids)
                parsed_directive = captured.get("parsed_directive") if isinstance(captured.get("parsed_directive"), dict) else None
                if parsed_directive is not None:
                    parsed_directive = _resolve_t4_display_ids_in_payload(parsed_directive, display_ids)
                try:
                    readonly_directive = parse_idea_directive(
                        raw,
                        candidate_ids=set(dossiers),
                        option_id=option_id,
                        llm_payload=parsed_directive,
                    )
                    readonly_result = self._native_t4_readonly_result(workspace_dir, readonly_directive)
                    readonly_result["pending_operation_preserved"] = True
                    readonly_result["summary"] = (
                        str(readonly_result.get("summary") or "").rstrip()
                        + " 原待确认操作仍未执行；查看完成后请继续选择确认或取消。"
                    ).strip()
                except ValueError as exc:
                    readonly_result = {
                        "title": "只读请求需要补充信息",
                        "summary": str(exc),
                        "kind": "pending_confirmation_readonly_needs_clarification",
                        "pending_operation_preserved": True,
                    }
                next_state = self._native_t4_confirmation_gate(state, workspace_dir, pending)
                if next_state.pending_gate is not None:
                    next_state.pending_gate.presentation["t4_directive_result"] = readonly_result
                return next_state
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
        if option_id == "more_actions" or any(token in inline_text for token in ("更多操作", "advanced actions", "more actions")):
            return self._reopen_native_t4_gate(
                state,
                workspace_dir,
                result={
                    "title": "更多操作",
                    "summary": (
                        "这些是低频高级操作。它们不会因为展开而执行；只有你明确输入“组合 D1 与 D3”、"
                        "“并行保留 D1 和 D3”、“重跑文献路线”或“回到上一代”等指令后，系统才会进入二次确认。"
                    ),
                    "kind": "advanced_actions",
                    "advanced_operations": [
                        {"label": "构建跨候选新方案", "description": "先检查两个候选是否兼容，兼容后再生成新的独立候选。"},
                        {"label": "组合指定组件", "description": "从不同候选中选择假设、贡献或方法模块，构建新方案。"},
                        {"label": "并行保留多个方向", "description": "将多个候选标记为独立研究方向，后续分别推进。"},
                        {"label": "查看完整候选池", "description": "查看未进入当前推荐列表的候选，不生成新内容。"},
                        {"label": "重新探索指定来源", "description": "重新运行某个 Idea 来源通道，并保留此前结果。"},
                        {"label": "调整投稿取向", "description": "按 UTD、CCF 或 Hybrid 取向重新评估适配度，不改变候选本身。"},
                        {"label": "回到上一代", "description": "将上一代候选池设为当前版本，不删除后续结果。"},
                    ],
                },
            )
        if pending_composition and (
            option_id == "confirm_composition"
            or any(token in inline_text for token in ("confirm composition", "确认组合", "确认生成", "生成组合"))
        ):
            return self._queue_confirmed_native_t4_composition(
                state,
                workspace_dir,
                pending_composition,
            )
        if option_id in {"confirm", "proceed", "yes"} or inline_text in {
            "confirm",
            "yes",
            "y",
            "确认",
            "继续",
            "确认执行",
        }:
            return self._reopen_native_t4_gate(
                state,
                workspace_dir,
                result={
                    "title": "没有待确认的 T4 操作",
                    "summary": (
                        "当前没有已保存的操作计划可执行。请先输入“推进 D1”“优化 D2”“查看 D1”"
                        "或“暂停”；系统会在真正需要执行前再次展示确认页。"
                    ),
                    "kind": "confirm_without_pending_plan",
                },
            )

        population, dossiers = current_population_context(workspace_dir)
        raw = self._native_t4_directive_text(option_id=option_id, captured=captured)
        bare_handles = _t4_public_handle_tokens(raw)
        if bare_handles and option_id in {"", "t4_directive"}:
            if len(bare_handles) == 1:
                handle = bare_handles[0]
                return self._reopen_native_t4_gate(
                    state,
                    workspace_dir,
                    result={
                        "title": "请再说明你想对这个候选做什么",
                        "summary": (
                            f"你输入了 {handle}。请直接输入：推进 {handle}、优化 {handle}、查看 {handle}，"
                            "或输入“暂停”。只输入编号不会改变 Candidate 或 Population。"
                        ),
                        "kind": "needs_clarification",
                        "candidate_ids": [handle],
                    },
                )
            return self._reopen_native_t4_gate(
                state,
                workspace_dir,
                result={
                    "title": "多个候选需要明确意图",
                    "summary": (
                        "你输入了 "
                        + "、".join(bare_handles)
                        + "。请说明是“分别推进”、 “并行保留”，还是“构建新方案”；只输入多个编号不会改变 Candidate 或 Population。"
                    ),
                    "kind": "needs_clarification",
                    "candidate_ids": bare_handles,
                },
            )
        # Gate1 speaks in stable D1/D2/D3 handles. Resolve only exact handles
        # at the boundary to the internal directive contract, keeping lineage
        # IDs out of the human-facing UI while preserving their safety checks.
        display_ids = _t4_gate1_display_id_map(workspace_dir)
        raw = _resolve_t4_display_ids(raw, display_ids)
        parsed_directive = captured.get("parsed_directive") if isinstance(captured.get("parsed_directive"), dict) else None
        if parsed_directive is not None:
            parsed_directive = _resolve_t4_display_ids_in_payload(parsed_directive, display_ids)
        try:
            directive = parse_idea_directive(
                raw,
                candidate_ids=set(dossiers),
                option_id=option_id,
                llm_payload=parsed_directive,
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
        candidate_refs: list[str] = []
        for key in ("candidate_id", "target_candidate_id"):
            value = captured.get(key)
            if str(value or "").strip():
                candidate_refs.append(str(value).strip())
        for key in ("candidate_ids", "target_candidate_ids"):
            values = captured.get(key)
            if isinstance(values, list):
                candidate_refs.extend(str(value).strip() for value in values if str(value).strip())
            elif str(values or "").strip():
                candidate_refs.extend(part.strip() for part in re.split(r"[,，、\s]+", str(values)) if part.strip())
        if candidate_refs:
            return " ".join([option_id or "select_candidate", *dict.fromkeys(candidate_refs)])
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
            "_title": "T4 操作二次确认",
            "_description": "系统已经理解你的研究操作，但尚未调用模型、生成新 Candidate 或改变 Population。请核对计划后确认执行，或取消返回当前候选集。",
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
                {"id": "confirm", "label": "确认执行", "description": "按上方计划执行；若是推进候选，将生成 Pre-Novelty brief 并进入 T4.5。"},
                {"id": "cancel", "label": "取消，返回候选页", "description": "保留当前 Population 和全部历史版本，不执行该操作。"},
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

        # Defense in depth at the only mutation boundary: a plain public
        # advance request must never re-enter T4 merely because an earlier
        # parser labeled it focus/refine.  It has the documented T4.5 meaning.
        if (
            directive.action != "select_candidate"
            and _explicit_selection_action(
                directive.raw_user_input,
                target_count=len(directive.target_candidate_ids),
            )
        ):
            return self._select_native_t4_candidate(state, workspace_dir, directive, directive_path)
        if directive.action in {
            "show_more",
            "show_archive",
            "inspect_score",
            "inspect_evidence",
            "inspect_lineage",
            "inspect_hypotheses",
            "inspect_contributions",
            "inspect_genome",
            "inspect_files",
            "compare_candidates",
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

    @staticmethod
    def _is_t4_readonly_gate_result(gate_result: dict[str, Any]) -> bool:
        captured = gate_result.get("captured") if isinstance(gate_result.get("captured"), dict) else {}
        directive = captured.get("parsed_directive") if isinstance(captured.get("parsed_directive"), dict) else {}
        action = str(directive.get("action") or "").strip()
        return action in {
            "show_more",
            "show_archive",
            "inspect_score",
            "inspect_evidence",
            "inspect_lineage",
            "inspect_hypotheses",
            "inspect_contributions",
            "inspect_genome",
            "inspect_files",
            "compare_candidates",
        }

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
        catalog_context = materialize_t4_cross_domain_catalog_context(workspace_dir)
        inspection = inspect_t4_inputs(workspace_dir)
        store.write_json(
            "ideation/evolution/pre_run_confirmation.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_pre_run_confirmation",
                "input_fingerprint": inspection.input_fingerprint,
                "input_fingerprints": build_t4_input_fingerprints(workspace_dir),
                "run_config_fingerprint": run_config_fingerprint(revised_config),
                "selected_option": "profile_revision",
                "captured": {"publication_orientation": directive.raw_user_input},
                "target_profile": model_dump(target_profile, mode="json"),
                "inspection_status": inspection.status,
                "cross_domain_catalog_context": catalog_context,
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
        selection_ready, selection_error = candidate_selection_readiness(
            workspace_dir,
            candidate_id=selected_candidate_id,
        )
        if not selection_ready:
            return self._reopen_native_t4_gate(
                state,
                workspace_dir,
                result={
                    "title": "Candidate needs enrichment before T4.5",
                    "summary": selection_error or "The selected Candidate is not ready for Pre-Novelty compilation.",
                    "kind": "selection_not_ready",
                    "candidate_ids": [selected_candidate_id],
                },
            )
        selection_warnings = candidate_selection_warnings_for_workspace(
            workspace_dir,
            candidate_id=selected_candidate_id,
        )
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
            "next_task": "T4.5",
            "selection_warnings": selection_warnings,
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
        state.current_task = "T4.5"
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
        # A Final Idea Card is bound to one Portfolio's immutable Candidate
        # package. The previous active card file cannot describe a rolled-back
        # Portfolio, so mark a fresh bounded LLM compilation as required before
        # any Human Gate is reopened.
        store.write_json(
            "ideation/final_cards/portfolio_cards.json",
            {
                "schema_version": "1.0.0",
                "semantics": "t4_final_idea_card_translations",
                "population_id": target.population_id,
                "target_profile": model_dump(run_config.target_profile, mode="json"),
                "cards": [],
                "status": "llm_repair_required",
                "reason": "population_rollback_requires_current_portfolio_cards",
            },
        )
        # Rollback itself is a consumed Population operation.  Bind the
        # required fresh LLM card compilation to the restored snapshot so a
        # Recovery Gate retry cannot accidentally reopen or evolve the former
        # generation.  The marker is intentionally written before projection:
        # a process stop during projection still resumes card/projection-only.
        rollback_operation = {
            "action": "rollback",
            "directive_path": directive_path,
            "directive": model_dump(directive, mode="json"),
            "requested_from_population": current.population_id,
        }
        store.write_final_card_repair_checkpoint(
            population=target,
            operation=rollback_operation,
            status="llm_repair_required",
            reason="population_rollback_requires_current_portfolio_cards",
        )
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

        redirected = self._redirect_incomplete_t4_gate_to_recovery(state, workspace_dir)
        if redirected is not None:
            return redirected

        node = self.nodes["T4-GATE1"]
        gate_spec = self._find_gate(self._gate_id_for_node(node))
        presentation = {
            "_title": str(gate_spec.get("title") or "T4 · 研究方向决策"),
            "_description": str(gate_spec.get("description") or "请选择如何继续当前 Candidate Population。"),
            "candidate_overview": _t4_gate1_candidate_overview(workspace_dir),
            "candidate_pool_fingerprints": _t4_gate1_candidate_pool_fingerprints(workspace_dir),
            "t4_artifact_guide": _t4_gate1_file_navigation(workspace_dir),
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
                    "label": "确认 Human-composed Candidate",
                    "description": "使用已审查的 Gene Donor Map 生成一个新 Candidate，并与来源 Candidate 独立比较评分；来源版本会被保留。",
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
            "ideation/final_cards/portfolio_cards.json",
        ):
            source = workspace_dir / rel
            if source.is_file():
                archive.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, archive / source.name)

    def _native_t4_readonly_result(self, workspace_dir: Path, directive: IdeaDirective) -> dict[str, Any]:
        """Build a read-only result from durable artifacts and the shared Gate card."""

        population, dossiers = current_population_context(workspace_dir)
        gate_overview = _t4_gate1_candidate_overview(workspace_dir)
        card_by_internal = {
            str(item.get("internal_id") or "").strip(): item
            for item in gate_overview.get("candidates", [])
            if isinstance(item, dict) and str(item.get("internal_id") or "").strip()
        }
        display_by_internal = {
            candidate_id: display_id
            for display_id, candidate_id in _t4_gate1_display_id_map(workspace_dir).items()
        }

        def candidate_summary(candidate: CandidateDossier) -> dict[str, Any]:
            summary = self._native_candidate_summary(candidate)
            summary["internal_id"] = candidate.candidate_id
            summary["candidate_id"] = display_by_internal.get(candidate.candidate_id, candidate.candidate_id)
            # The Gate deck is the only normal-UI scientific presentation.
            # Reusing it here prevents a “查看 D1” response from degrading to
            # a separate genome-only summary after a resume.
            card = card_by_internal.get(candidate.candidate_id)
            if card is not None:
                summary["candidate_card"] = card
            return summary

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
                "title": "其余 Active Population",
                "summary": "这些 Candidate 仍处于 Active Population，只是未进入当前首屏 Portfolio。查看不会调用模型，也不会改变任何版本。",
                "kind": "remaining_population",
                "candidates": [candidate_summary(dossiers[candidate_id]) for candidate_id in remaining],
                "artifact": f"ideation/populations/{population.population_id}.json",
            }
        if directive.action == "show_archive":
            return {
                "title": "已归档 Candidate",
                "summary": "归档 Candidate 会被保留用于审计，仍可通过回滚或定向演化重新查看和使用。",
                "kind": "archive",
                "candidate_ids": population.archived_candidate_ids,
                "artifact": f"ideation/populations/{population.population_id}.json",
            }
        if directive.action == "compare_candidates":
            compared = [dossiers[candidate_id] for candidate_id in directive.target_candidate_ids]
            compared_summaries = [candidate_summary(candidate) for candidate in compared]
            return {
                "title": "候选方向对比",
                "summary": "下列内容直接来自当前 Candidate 与评分产物，仅供比较；不会调用模型、修改 Candidate 或改变 Population。",
                "kind": "compare_candidates",
                "candidates": compared_summaries,
                "candidate_cards": [
                    item["candidate_card"]
                    for item in compared_summaries
                    if isinstance(item.get("candidate_card"), dict)
                ],
                "comparison": {
                    "candidate_ids": [display_by_internal.get(candidate.candidate_id, candidate.candidate_id) for candidate in compared],
                    "core_theses": [str(candidate.genome.core_thesis.value) for candidate in compared],
                    "mechanisms": [str(candidate.genome.mechanism.value) for candidate in compared],
                    "risks": [str(candidate.genome.risks.value) for candidate in compared],
                    "artifact_paths": {
                        display_by_internal.get(candidate.candidate_id, candidate.candidate_id): candidate.artifact_paths
                        for candidate in compared
                    },
                },
            }
        candidate_id = directive.target_candidate_ids[0]
        candidate = dossiers[candidate_id]
        raw_lower = " ".join(str(directive.raw_user_input or "").casefold().split())
        generic_candidate_view = directive.action == "inspect_score" and not any(
            token in raw_lower for token in ("评分", "分数", "score", "scoring")
        )

        def readable_gene_rows() -> list[dict[str, str]]:
            gene_fields = (
                ("研究问题", "problem"),
                ("机会来源", "opportunity"),
                ("核心命题", "core_thesis"),
                ("机制", "mechanism"),
                ("设计 / Artifact", "design_or_artifact"),
                ("贡献包", "contribution_package"),
                ("假设包", "hypothesis_bundle"),
                ("验证逻辑", "validation_logic"),
                ("边界条件", "boundary_conditions"),
                ("主要风险", "risks"),
            )
            rows: list[dict[str, str]] = []
            for label, field_name in gene_fields:
                gene = getattr(candidate.genome, field_name, None)
                value = " ".join(str(getattr(gene, "value", "") or "").split())
                if not value:
                    continue
                provenance = getattr(gene, "provenance", None)
                reading_levels = [
                    str(getattr(item, "value", item))
                    for item in (getattr(provenance, "reading_levels", []) if provenance is not None else [])
                    if str(getattr(item, "value", item)).strip()
                ]
                role = str(getattr(getattr(provenance, "evidence_role", ""), "value", getattr(provenance, "evidence_role", ""))).strip()
                refs = getattr(provenance, "source_refs", []) if provenance is not None else []
                ref_labels: list[str] = []
                for ref in refs[:3]:
                    for attr in ("citation_key", "paper_id", "source_path"):
                        item = str(getattr(ref, attr, "") or "").strip()
                        if item:
                            ref_labels.append(item)
                            break
                note_parts = []
                if reading_levels:
                    note_parts.append("阅读层级：" + "、".join(dict.fromkeys(reading_levels)))
                if role:
                    note_parts.append("证据角色：" + role)
                if ref_labels:
                    note_parts.append("来源：" + "；".join(dict.fromkeys(ref_labels)))
                if provenance is not None and getattr(provenance, "upgrade_required", False):
                    note_parts.append("需要补强阅读")
                rows.append(
                    {
                        "label": label,
                        "summary": value,
                        "source": "；".join(note_parts) if note_parts else "当前候选未绑定具体论文来源",
                    }
                )
            return rows

        score_detail = (
            {
                "label": "详情",
                "rows": readable_gene_rows(),
            }
            if generic_candidate_view
            else {"label": "评分", "path": candidate.score_report_path or f"ideation/scoring/U{population.generation}.json"}
        )
        detail_by_action = {
            "inspect_score": score_detail,
            "inspect_evidence": {
                "label": "证据",
                "path": "ideation/evidence/evidence_index.jsonl",
                "rows": readable_gene_rows(),
            },
            "inspect_lineage": {"label": "演化谱系", "path": f"ideation/candidates/{candidate.candidate_id}.v{candidate.version}.json"},
            "inspect_hypotheses": {
                "label": "候选假设",
                "rows": [
                    {
                        "label": getattr(item, "hypothesis_id", f"H{index}"),
                        "summary": str(getattr(item, "statement", "")),
                        "source": "观察信号：" + str(getattr(item, "observable_prediction", "")),
                    }
                    for index, item in enumerate(candidate.hypotheses, start=1)
                ],
            },
            "inspect_contributions": {
                "label": "贡献包",
                "rows": [
                    {
                        "label": getattr(item, "contribution_id", f"C{index}"),
                        "summary": str(getattr(item, "statement", "")),
                        "source": "若成立：" + str(getattr(item, "what_changes_if_true", "")),
                    }
                    for index, item in enumerate(candidate.contributions, start=1)
                ],
            },
            "inspect_genome": {"label": "Idea Genome", "path": f"ideation/candidates/{candidate.candidate_id}.v{candidate.version}.json"},
            "inspect_files": {
                "label": "关联产物",
                "paths": [
                    f"ideation/candidates/{candidate.candidate_id}.v{candidate.version}.json",
                    candidate.score_report_path or f"ideation/scoring/U{population.generation}.json",
                    *candidate.artifact_paths,
                ],
            },
        }
        detail = detail_by_action.get(directive.action, {})
        return {
            "title": f"{detail.get('label', 'Candidate')} · {display_by_internal.get(candidate_id, candidate_id)}",
            "summary": "这是基于当前 Candidate 产物的只读视图；不会调用模型、改变 Population 或进行合并。",
            "kind": directive.action,
            "candidate": candidate_summary(candidate),
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

        catalog_context = materialize_t4_cross_domain_catalog_context(workspace_dir)
        inspection = inspect_t4_inputs(workspace_dir)
        if catalog_context.get("status") == "degraded":
            inspection = inspection.model_copy(
                update={
                    "warnings": [
                        *inspection.warnings,
                        "Cross-domain catalog 未能自动物化："
                        + str(catalog_context.get("warning") or "请检查 catalog 诊断；T4 仍会保留已确认方向名称作为受限创意上下文。"),
                    ]
                }
            )
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
                "input_fingerprints": build_t4_input_fingerprints(workspace_dir),
                "run_config_fingerprint": run_config_fingerprint(config),
                "selected_option": option_id,
                "captured": captured,
                "target_profile": model_dump(target_profile, mode="json"),
                "inspection_status": inspection.status,
                "cross_domain_catalog_context": catalog_context,
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
        if node.task_id in _CCF_TEMPLATE_GATE_TASKS:
            template_id = normalize_ccf_template_id(str(option_id or "").removeprefix("ccf_"))
            if template_id in ccf_template_ids():
                return _CCF_TEMPLATE_GATE_TASKS[node.task_id]
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
        if node.task_id in {"T3.6-TEMPLATE-GATE", "T3.6-CCF-TEMPLATE-GATE"}:
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "basic_en")
            if option_id == "ccf":
                return
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
            captured = gate_result.get("captured") if isinstance(gate_result.get("captured"), dict) else {}
            scope = "complete" if option_id in {"complete", "full", "expand", "补检", "完整"} else "conservative"
            payload = {
                "scope": scope,
                "selected_option": option_id,
                "supplement_target_papers": captured.get("supplement_target_papers"),
                "supplement_focus": str(captured.get("supplement_focus") or "").strip(),
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
                    selection_ready, selection_error = candidate_selection_readiness(
                        workspace_dir,
                        candidate_id=selected_candidate_id,
                    )
                    if not selection_ready:
                        rejection_path = workspace_dir / "ideation" / "_gate1_selection_rejected.json"
                        rejection_path.write_text(
                            json.dumps(
                                {
                                    "schema_version": "1.0.0",
                                    "semantics": "t4_gate1_selection_rejected",
                                    "candidate_id": selected_candidate_id,
                                    "reason": selection_error,
                                    "recommended_action": "evolve_or_upgrade_before_selection",
                                    "decided_at": _now_iso(),
                                },
                                ensure_ascii=False,
                                indent=2,
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                        return
                    payload["selected_candidate_id"] = selected_candidate_id
                    payload["selection_warnings"] = candidate_selection_warnings_for_workspace(
                        workspace_dir,
                        candidate_id=selected_candidate_id,
                    )
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
        if node.task_id in {"T8-STYLE-GATE", "T8-CCF-TEMPLATE-GATE"}:
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "ccf_neurips")
            if option_id == "ccf":
                return
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
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "codex_cli")
            if next_task in {"T5-HANDOFF", "T5-REBOOST-GATE"} or option_id == "revise_handoff":
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
            path = workspace_dir / "external_executor" / "report" / "executor_selection.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            patch_external_executor_files_with_selection(workspace_dir, selection)
            return
        if node.task_id == "T5-PROTOCOL-GATE":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "pause_protocol")
            captured = gate_result.get("captured") or {}
            readiness = self._t5_execution_readiness(workspace_dir)
            payload = {
                "version": "1.0",
                "semantics": "external_executor_protocol_gate_decision",
                "task_id": node.task_id,
                "gate_id": self._gate_id_for_node(node),
                "selected_option": option_id,
                "captured": captured if isinstance(captured, dict) else {},
                "next_task": next_task,
                "execution_readiness": readiness,
                "decision_boundary": (
                    "This decision does not authorize implementation, formal experiments, results, or T8 handoff until a recompiled handoff reports execution readiness."
                ),
                "decided_at": _now_iso(),
            }
            path = workspace_dir / "external_executor" / "report" / "protocol_gate_decision.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return
        if node.task_id == "T5-EXPR-MATERIAL-GATE":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "pause_for_materials")
            captured = gate_result.get("captured") or {}
            resources_dir = workspace_dir / "resources"
            expr_dir = workspace_dir / "external_executor" / "expr"
            resources_dir.mkdir(parents=True, exist_ok=True)
            expr_dir.mkdir(parents=True, exist_ok=True)

            def snapshot_files(root: Path, *, with_sha256: bool) -> list[dict[str, object]]:
                files: list[dict[str, object]] = []
                for path in sorted(root.rglob("*")):
                    if not path.is_file():
                        continue
                    item: dict[str, object] = {
                        "path": path.relative_to(workspace_dir).as_posix(),
                        "bytes": path.stat().st_size,
                    }
                    if with_sha256:
                        item["sha256"] = _sha256_file(path)
                    files.append(item)
                return files

            # Datasets and checkpoints can be large. The material gate only
            # inventories their paths and sizes; Phase B owns source review,
            # immutable revisions, and costly integrity verification.
            resource_files = snapshot_files(resources_dir, with_sha256=False)
            expr_files = snapshot_files(expr_dir, with_sha256=True)
            payload = {
                "version": "1.1",
                "semantics": "external_executor_materials_gate_decision",
                "task_id": node.task_id,
                "gate_id": self._gate_id_for_node(node),
                "selected_option": option_id,
                "materials_ready": option_id == "materials_ready",
                "captured": captured if isinstance(captured, dict) else {},
                "next_task": next_task,
                "resource_material_root": "resources",
                "resource_snapshot": resource_files,
                "resource_snapshot_hash_policy": "deferred_to_phase_b",
                "deployment_asset_root": "external_executor/expr",
                "deployment_asset_snapshot": expr_files,
                # Preserve legacy readers that only know the old expr fields.
                "expr_dir": "external_executor/expr",
                "expr_snapshot": expr_files,
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

        # ``T7.5`` is no longer part of the current main state machine, but
        # persisted extension/legacy workspaces can still carry its explicit
        # ``__parse_from_output__`` transition.  Keep that old state readable
        # and translate it into the current T5 handoff or T8 entry instead of
        # treating a successful legacy evaluation as an unsupported state.
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
            "continue_to_t5",
            "continue_to_experiment",
            # Legacy aliases accepted only for older novelty_audit.md files.
            "go_t7",
            "continue_to_t7",
        }
        if verdict_token in pass_tokens:
            formal_ok, _formal_error = _validate_t45_post_novelty_formalization(workspace_dir, audit_path)
            if not formal_ok:
                return human_review
            if "T5-REBOOST-GATE" in self.nodes:
                return "T5-REBOOST-GATE"
            if "T5-HANDOFF" in self.nodes:
                return "T5-HANDOFF"
            return "failed"
        return human_review

    def _parse_t75_decision(self, workspace_dir: Path) -> str:
        """Translate a persisted legacy T7.5 recommendation into current flow.

        T7/T7.5 have been removed from the normal workflow: current external
        executors publish their verified report directly to the T8 handoff.
        A pre-existing workspace can nevertheless contain a completed T7.5
        node with ``next_on_success: __parse_from_output__``.  Its historical
        recommendation must remain resumable, but it must never revive the
        removed internal T7 executor.  The aliases below therefore map old
        experiment requests to the current external-experiment entry and old
        writing requests through the normal writing-style/resource boundary.
        """

        default_t8_entry = self._default_t8_entry(workspace_dir)
        decision_path = workspace_dir / "evaluation" / "evaluation_decision.md"
        if not decision_path.is_file():
            return default_t8_entry

        text = decision_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"next_task:\s*([A-Za-z0-9_.-]+)", text, re.DOTALL)
        if match is None:
            return default_t8_entry

        raw_target = match.group(1).strip()
        aliases = {
            # T7 is an historical internal executor name.  The current
            # equivalent is the external handoff/reboost entry selected by
            # the installed state-machine topology.
            "T5": self._default_experiment_entry(),
            "T6": self._default_experiment_entry(),
            "T7": self._default_experiment_entry(),
            # Writing must start from a validated style choice and the
            # resource index whenever the current topology supplies them.
            "T8": default_t8_entry,
            "T8-WRITE": default_t8_entry,
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

    def _t5_execution_readiness(self, workspace_dir: Path) -> dict[str, Any]:
        """Read the handoff's explicit authorization boundary without mutating it."""

        handoff = self._read_json_dict(workspace_dir / "external_executor" / "handoff_pack.json") or {}
        contract = handoff.get("execution_contract") if isinstance(handoff.get("execution_contract"), dict) else {}
        readiness = contract.get("execution_readiness") if isinstance(contract.get("execution_readiness"), dict) else {}
        if readiness:
            return dict(readiness)
        if str(handoff.get("generation_status") or "") == "completed":
            return {
                "status": "ready",
                "allowed_stages": [],
                "blocked_stages": [],
                "required_decisions": [],
                "formal_execution_allowed": True,
                "reason": "Legacy completed handoff without an explicit readiness object.",
            }
        return {
            "status": "blocked",
            "allowed_stages": [],
            "blocked_stages": [],
            "required_decisions": [],
            "formal_execution_allowed": False,
            "reason": "No completed T5 handoff is available.",
        }

    def _t5_protocol_gate_summary(self, workspace_dir: Path) -> dict[str, Any]:
        """Build the compact data model for the researcher-facing T5 Gate."""

        handoff = self._read_json_dict(workspace_dir / "external_executor" / "handoff_pack.json") or {}
        context = handoff.get("context_reboost") if isinstance(handoff.get("context_reboost"), dict) else {}
        scope = context.get("study_scope") if isinstance(context.get("study_scope"), dict) else {}
        baselines = handoff.get("baseline_matrix") if isinstance(handoff.get("baseline_matrix"), list) else []
        unresolved = handoff.get("unresolved_items") if isinstance(handoff.get("unresolved_items"), list) else []
        readiness = self._t5_execution_readiness(workspace_dir)
        unresolved_records = [
            {
                "affected_fields": item.get("affected_fields") or [],
                "required_action": item.get("required_action"),
            }
            for item in unresolved
            if isinstance(item, dict)
        ]
        return {
            "status": readiness.get("status"),
            "formal_execution_allowed": readiness.get("formal_execution_allowed"),
            "reason": readiness.get("reason"),
            "already_compiled": {
                "settings_or_datasets": scope.get("datasets") or [],
                "metrics": scope.get("metrics") or [],
                "required_baselines": [
                    str(item.get("name") or item.get("baseline_id") or "")
                    for item in baselines
                    if isinstance(item, dict) and str(item.get("name") or item.get("baseline_id") or "")
                ],
                "claim_count": len(handoff.get("claim_evidence_matrix") or []),
            },
            "required_decisions": readiness.get("required_decisions") or [],
            "missing_requirements": unresolved_records,
            "settings_file": "ideation/exp_plan.yaml",
            "proposal_file": "ideation/proposal/research_proposal.md",
        }

    def _parse_t5_expr_material_decision(self, workspace_dir: Path) -> str:
        """Route the T5 experiment-material gate from its explicit decision file."""

        path = workspace_dir / "external_executor" / "expr" / "materials_gate_decision.json"
        data = self._read_json_dict(path)
        if data is None:
            return "T5-EXPR-MATERIAL-GATE"
        selected = str(data.get("selected_option") or "").strip().lower()
        if data.get("materials_ready") is True or selected in {"materials_ready", "ready", "continue", "done"}:
            readiness = self._t5_execution_readiness(workspace_dir)
            if readiness.get("status") != "ready":
                return "T5-PROTOCOL-GATE" if "T5-PROTOCOL-GATE" in self.nodes else "T5-REBOOST-GATE"
            return "T5-EXECUTOR-GATE"
        if selected in {"back_to_t4", "t4", "rethink"}:
            return "T4"
        if selected in {"stop_project", "stop", "done"}:
            return "done" if "done" in self.nodes else "failed"
        return "T5-EXPR-MATERIAL-GATE"

    def _mock_dry_run_requires_real_executor(self, workspace_dir: Path) -> bool:
        """Return whether a protocol-only result must be kept outside T8."""

        selection = self._read_json_dict(workspace_dir / "external_executor" / "report" / "executor_selection.json")
        if not isinstance(selection, dict) or str(selection.get("selected_executor") or "") != "mock_dry_run":
            return False
        result_pack = self._read_json_dict(workspace_dir / "external_executor" / "result_pack.json")
        return isinstance(result_pack, dict) and (
            result_pack.get("mock_only") is True or result_pack.get("dry_run") is True
        )

    @staticmethod
    def _read_json_dict(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _default_experiment_entry(self) -> str:
        if "T5-REBOOST-GATE" in self.nodes:
            return "T5-REBOOST-GATE"
        if "T5-HANDOFF" in self.nodes:
            return "T5-HANDOFF"
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
            if workspace_dir is not None:
                # A path-only signature treats a user-expanded literature
                # query, a repaired artifact, and an unchanged retry as the
                # same attempt.  Bind deadlock detection to the actual declared
                # input contents instead.  This preserves protection against a
                # true no-change loop while allowing a documented recovery to
                # retry after its inputs changed.
                try:
                    params["input_fingerprints"] = build_input_fingerprints(
                        workspace_dir,
                        {str(name): str(path) for name, path in node.inputs.items()},
                    )
                except Exception as exc:
                    # An unreadable input remains part of the execution identity
                    # rather than making deadlock detection itself fail.
                    params["input_fingerprints"] = {"_error": type(exc).__name__}
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
        # Older workspaces store path-only hashes.  Versioning the identity
        # format lets them resume once under the content-aware guard instead of
        # being permanently blocked by a historical false positive.
        params["deadlock_identity_version"] = "content_fingerprint_v1"

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
            # Source changes to the native controller are a genuine execution
            # change.  Without this marker, a fixed validator or repair path
            # can be blocked by the history of the buggy implementation.
            params["t4_implementation_fingerprint"] = _t4_execution_implementation_fingerprint()
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


def _t36_supplement_recommendation(workspace_dir: Path) -> dict[str, Any]:
    """Recommend a supplement target from visible survey coverage."""

    plan_path = workspace_dir / "drafts" / "survey" / "survey_plan.json"
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        plan = {}
    taxonomy = ((plan.get("taxonomy") or {}).get("tree") if isinstance(plan, dict) and isinstance(plan.get("taxonomy"), dict) else []) or []
    outline = plan.get("outline") if isinstance(plan, dict) and isinstance(plan.get("outline"), list) else []
    weak = ((plan.get("coverage_selfcheck") or {}).get("classes_needing_more_lit") if isinstance(plan, dict) and isinstance(plan.get("coverage_selfcheck"), dict) else []) or []
    deep_notes = len(list((workspace_dir / "literature" / "deep_read_notes").glob("*.md")))
    base = 8 + min(4, len(outline)) + min(8, 2 * len(weak)) + min(6, len(taxonomy) // 2)
    if deep_notes >= 20:
        base -= 4
    suggested = max(8, min(30, base))
    return {
        "suggested_target_records": suggested,
        "basis": {"deep_note_count": deep_notes, "taxonomy_class_count": len(taxonomy), "outline_section_count": len(outline), "explicit_weak_class_count": len(weak)},
        "coverage_purpose": ["historical development", "frontier progress", "weak taxonomy classes", "confirmed Cross-domain bridges"],
        "boundary": "This is a retrieval target, not a citation quota. Records with abstracts become canonical shallow reading notes; full/partial notes remain required for substantive claims.",
    }
