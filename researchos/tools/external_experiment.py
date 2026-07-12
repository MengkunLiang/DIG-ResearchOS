from __future__ import annotations

"""External experiment handoff and evidence tools.

ResearchOS owns protocol, provenance, integrity checks, and claim mapping.
External executors such as Codex CLI, Claude Code, or a manual runner own code
implementation and experiment execution in an isolated workspace. These tools
only read/write workspace artifacts and provide a mock dry-run path for tests.
"""

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from ..runtime.environment import workspace_host_hint
from ..skills.project_specialization import specialize_project_skills
from .base import Tool, ToolResult
from .workspace_policy import ToolAccessDenied, WorkspaceAccessPolicy


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_json(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(payload)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_text(path: Path, *, max_chars: int | None = None) -> str:
    if not path.exists() or path.stat().st_size <= 0:
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:max_chars] if max_chars is not None else text


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _artifact_record(workspace: Path, rel_path: str, *, role: str, kind: str = "file") -> dict[str, Any]:
    path = workspace / rel_path
    record: dict[str, Any] = {"path": rel_path, "kind": kind, "role": role, "exists": path.exists()}
    if path.exists() and path.is_file():
        record.update({"sha256": _sha256(path), "bytes": path.stat().st_size})
    return record


def _rel_artifact_record(workspace: Path, path: Path, *, role: str, kind: str = "file") -> dict[str, Any]:
    try:
        rel_path = path.relative_to(workspace).as_posix()
    except ValueError:
        rel_path = path.as_posix()
    return _artifact_record(workspace, rel_path, role=role, kind=kind)


def _merge_artifact_records(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            existing = merged.get(path, {})
            merged[path] = {**existing, **item}
    return list(merged.values())


def _scan_external_artifacts(workspace: Path) -> dict[str, list[dict[str, Any]]]:
    specs = {
        "raw_results": ("external_executor/raw_results", "raw_result"),
        "configs": ("external_executor/configs", "config"),
        "logs": ("external_executor/logs", "log"),
        "patches": ("external_executor/patches", "patch"),
        "figures": ("external_executor/figures", "figure"),
        "tables": ("external_executor/tables", "table"),
    }
    scanned: dict[str, list[dict[str, Any]]] = {}
    for key, (rel_dir, role) in specs.items():
        base = workspace / rel_dir
        records: list[dict[str, Any]] = []
        if base.exists():
            for path in sorted(item for item in base.rglob("*") if item.is_file()):
                records.append(_rel_artifact_record(workspace, path, role=role, kind=key.rstrip("s")))
        scanned[key] = records
    return scanned


def _artifact_paths(records: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("path")) for item in records if isinstance(item, dict) and item.get("path")]


def _result_pack_extra_fields(result_pack: dict[str, Any]) -> dict[str, Any]:
    known = set(EXTERNAL_RESULT_REQUIRED_FIELDS) | {
        "runs",
        "raw_result_files",
        "config_files",
        "log_files",
        "logs",
        "scope_change_requests",
        "failed_trials",
        "replacement_baselines",
        "additional_resources",
        "manual_notes",
        "evidence_grade",
        "limitations",
    }
    return {key: value for key, value in result_pack.items() if key not in known}


def _run_records_from_result_pack(result_pack: dict[str, Any], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    source_runs = result_pack.get("experiment_runs")
    if not isinstance(source_runs, list) or not source_runs:
        source_runs = result_pack.get("runs")
    if not isinstance(source_runs, list) or not source_runs:
        source_runs = manifest.get("runs")
    if not isinstance(source_runs, list) or not source_runs:
        source_runs = [{"run_id": result_pack.get("run_id") or "external_run", "status": "unknown"}]
    records: list[dict[str, Any]] = []
    metrics = [item for item in result_pack.get("metrics", []) or [] if isinstance(item, dict)]
    for idx, run in enumerate(source_runs, start=1):
        run_payload = dict(run) if isinstance(run, dict) else {"run_id": str(run)}
        run_id = str(run_payload.get("run_id") or run_payload.get("id") or f"run_{idx}")
        run_metrics = [
            metric
            for metric in metrics
            if str(metric.get("run_id") or metric.get("experiment_id") or result_pack.get("run_id") or "") in {run_id, ""}
        ]
        records.append(
            {
                "semantics": "external_executor_run_record",
                "run_id": run_id,
                "source": "external_executor/result_pack.json",
                "run": run_payload,
                "metrics": run_metrics,
                "raw_result_refs": _coerce_str_list(run_payload.get("raw_results") or run_payload.get("raw_result_refs")),
                "config_refs": _coerce_str_list(run_payload.get("configs") or run_payload.get("config_refs") or run_payload.get("config")),
                "log_refs": _coerce_str_list(run_payload.get("logs") or run_payload.get("log_refs") or run_payload.get("log")),
            }
        )
    records.append({"semantics": "external_executor_result_pack", "result_pack": result_pack})
    return records


def _coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value:
        return [value]
    return []


PRE_T5_SOURCE_FILES = [
    "project.yaml",
    "literature/synthesis.md",
    "literature/synthesis_workbench.json",
    "literature/domain_map.json",
    "literature/comparison_table.csv",
    "ideation/hypotheses.md",
    "ideation/exp_plan.yaml",
    "ideation/idea_scorecard.yaml",
    "ideation/risks.md",
    "ideation/novelty_audit.md",
    "novelty/novelty_audit.md",
    "resources/baseline_candidates.jsonl",
    "literature/baseline_map.json",
    "user_seeds/seed_external_resources.jsonl",
    "user_seeds/bridge_domains.yaml",
]


EXTERNAL_RESULT_REQUIRED_FIELDS = [
    "schema_version",
    "semantics",
    "run_id",
    "executor",
    "dry_run",
    "mock_only",
    "executor_status",
    "context_alignment",
    "resources",
    "baseline_reproduction",
    "experiment_runs",
    "metrics",
    "artifacts",
    "baseline_coverage",
    "result_diagnosis",
    "module_attribution",
    "realized_method_package",
    "final_framework_figure",
    "figure_table_inventory",
    "writer_handoff",
    "run_manifest",
]


SKILL_SUITE = [
    "research-execution",
    "context-alignment",
    "resource-and-baseline-preparation",
    "experiment-design",
    "baseline-reproduction",
    "method-refinement",
    "implementation",
    "code-and-protocol-review",
    "experiment-run",
    "result-diagnosis",
    "module-attribution",
    "evidence-packaging",
    "writer-handoff",
]

def validate_context_reboost_handoff(workspace_dir: Path) -> tuple[bool, str | None]:
    """Validate the LLM-generated context re-boost handoff skeleton."""

    path = workspace_dir / "external_executor" / "handoff_pack.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"external_executor/handoff_pack.json missing or invalid JSON: {exc}"
    if not isinstance(data, dict):
        return False, "external_executor/handoff_pack.json must be a JSON object"
    if data.get("schema_version") != "external_executor_handoff.v1":
        return False, "handoff_pack.schema_version must be external_executor_handoff.v1"
    context = data.get("context_reboost")
    if not isinstance(context, dict):
        return False, "handoff_pack.context_reboost missing"
    required = [
        "project_goal",
        "central_hypothesis",
        "method_mechanism",
        "required_baselines",
        "baseline_matrix",
        "claim_evidence_matrix",
        "minimum_experiment_loop",
        "iteration_budget",
        "claim_boundaries",
        "writer_handoff_contract",
        "source_files_used",
        "known_context_mismatches",
    ]
    missing = [key for key in required if key not in context]
    if missing:
        return False, "handoff_pack.context_reboost missing fields: " + ", ".join(missing)
    mechanism = context.get("method_mechanism")
    if not isinstance(mechanism, dict) or not mechanism.get("core_mechanism"):
        return False, "handoff_pack.context_reboost.method_mechanism.core_mechanism missing"
    if not isinstance(context.get("baseline_matrix"), list):
        return False, "handoff_pack.context_reboost.baseline_matrix must be a list"
    if not isinstance(context.get("claim_evidence_matrix"), list) or not context.get("claim_evidence_matrix"):
        return False, "handoff_pack.context_reboost.claim_evidence_matrix must be a non-empty list"
    if not isinstance(data.get("baseline_matrix"), list):
        return False, "handoff_pack.baseline_matrix must be a list"
    if not isinstance(data.get("claim_evidence_matrix"), list) or not data.get("claim_evidence_matrix"):
        return False, "handoff_pack.claim_evidence_matrix must be a non-empty list"
    return True, None


def _source_artifacts(workspace: Path) -> list[dict[str, Any]]:
    return [
        _artifact_record(workspace, rel_path, role=_source_role(rel_path))
        for rel_path in PRE_T5_SOURCE_FILES
    ]


def _source_role(rel_path: str) -> str:
    if rel_path == "project.yaml":
        return "project"
    if rel_path.startswith("literature/"):
        return "literature_context"
    if rel_path.startswith("ideation/"):
        return "ideation_context"
    if rel_path.startswith("novelty/"):
        return "novelty_context"
    if rel_path.startswith("resources/"):
        return "resource_hint"
    if rel_path.startswith("user_seeds/"):
        return "user_seed_hint"
    return "source_context"


def _first_existing_text(workspace: Path, rel_paths: list[str], *, max_chars: int | None = None) -> tuple[str, str]:
    for rel_path in rel_paths:
        text = _read_text(workspace / rel_path, max_chars=max_chars)
        if text.strip():
            return text, rel_path
    return "", ""


def _source_files_used(workspace: Path) -> list[str]:
    return [rel for rel in PRE_T5_SOURCE_FILES if (workspace / rel).exists()]


def _experiments_from_plan(exp_plan: dict[str, Any]) -> list[dict[str, Any]]:
    experiments = exp_plan.get("experiments") if isinstance(exp_plan, dict) else []
    return [item for item in experiments or [] if isinstance(item, dict)]


def _baseline_names_from_exp_plan(exp_plan: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for exp in _experiments_from_plan(exp_plan):
        for key in ("baseline_methods", "baselines", "required_baselines"):
            value = exp.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        names.append(item)
                    elif isinstance(item, dict):
                        names.append(str(item.get("name") or item.get("baseline_name") or item.get("id") or ""))
            elif isinstance(value, str):
                names.append(value)
    return list(dict.fromkeys(name.strip() for name in names if name and name.strip()))


def _baseline_name(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("baseline_name") or item.get("name") or item.get("baseline_id") or "").strip()
    return str(item or "").strip()


def _build_context_reboost(
    *,
    workspace: Path,
    project: dict[str, Any],
    exp_plan: dict[str, Any],
    hypotheses: str,
    synthesis: str,
    novelty_audit: str,
    novelty_source: str,
    risks: str,
    required_baselines: list[dict[str, Any]],
    metrics: list[str],
) -> dict[str, Any]:
    project_goal = _infer_experiment_intent(project, hypotheses)
    central_hypothesis = _first_non_heading_line(hypotheses) or project_goal
    exp_baselines = _baseline_names_from_exp_plan(exp_plan)
    required_names = [_baseline_name(item) for item in required_baselines]
    required_names = [name for name in required_names if name]
    missing_from_exp_plan = [name for name in required_names if name not in exp_baselines]
    known_context_mismatches = []
    if missing_from_exp_plan:
        known_context_mismatches.append(
            {
                "type": "required_baseline_missing_from_exp_plan",
                "source_of_truth": novelty_source or "novelty_audit",
                "baselines": missing_from_exp_plan,
                "resolution": "Treat novelty audit baselines as required in external execution.",
            }
        )
    return {
        "project_goal": project_goal,
        "central_hypothesis": central_hypothesis,
        "method_mechanism": {
            "core_mechanism": _compact_text(_section_hint(hypotheses, ["mechanism", "方法", "机制"]) or central_hypothesis),
            "must_preserve_components": _extract_bullets(hypotheses, limit=8),
            "candidate_components": _extract_bullets(synthesis, limit=6),
            "allowed_refinements": [
                "implementation details may change when documented in result_pack.realized_method_package",
                "claims may be narrowed when baseline, metric, or dataset evidence is incomplete",
            ],
            "forbidden_scope_changes": [
                "replace_core_mechanism_without_review",
                "drop_required_baseline_without_claim_risk",
                "change_task_or_dataset_without_scope_change_record",
                "treat_engineering_trick_as_paper_contribution",
            ],
        },
        "required_baselines": required_baselines,
        "baseline_matrix": _build_baseline_matrix(required_baselines, exp_baselines),
        "claim_evidence_matrix": _build_claim_evidence_matrix(exp_plan, metrics, required_baselines),
        "minimum_experiment_loop": _build_minimum_experiment_loop(exp_plan, metrics),
        "iteration_budget": {
            "max_rounds": 3,
            "stop_conditions": [
                "budget_exhausted",
                "improvement_plateau",
                "required_baseline_unavailable",
                "audited_target_reached",
                "implementation_blocked",
                "claim_must_be_narrowed",
            ],
        },
        "claim_boundaries": _claim_boundaries_from_context(novelty_audit, risks, required_baselines),
        "writer_handoff_contract": [
            "realized_method_package",
            "final_framework_figure",
            "figure_table_inventory",
            "result_diagnosis",
            "module_attribution",
            "claim_boundaries",
            "must_not_claim",
        ],
        "source_files_used": _source_files_used(workspace),
        "known_context_mismatches": known_context_mismatches,
    }


def _existing_context_reboost_for_handoff(workspace: Path) -> dict[str, Any] | None:
    handoff = _read_json(workspace / "external_executor" / "handoff_pack.json")
    context = handoff.get("context_reboost") if isinstance(handoff, dict) else None
    if not isinstance(context, dict):
        return None
    required = [
        "project_goal",
        "central_hypothesis",
        "method_mechanism",
        "required_baselines",
        "baseline_matrix",
        "claim_evidence_matrix",
        "minimum_experiment_loop",
        "iteration_budget",
        "claim_boundaries",
        "writer_handoff_contract",
        "source_files_used",
        "known_context_mismatches",
    ]
    if any(key not in context for key in required):
        return None
    mechanism = context.get("method_mechanism")
    if not isinstance(mechanism, dict) or not mechanism.get("core_mechanism"):
        return None
    if not isinstance(context.get("baseline_matrix"), list):
        return None
    if not isinstance(context.get("claim_evidence_matrix"), list) or not context.get("claim_evidence_matrix"):
        return None
    copied = json.loads(json.dumps(context, ensure_ascii=False))
    copied.setdefault("reboost_source", "external_executor/handoff_pack.json#context_reboost")
    return copied


def _build_method_intent(
    *,
    hypotheses: str,
    exp_plan: dict[str, Any],
    context_reboost: dict[str, Any],
) -> dict[str, Any]:
    experiments = _experiments_from_plan(exp_plan)
    candidate_modules = []
    for idx, exp in enumerate(experiments or [{}], start=1):
        method = exp.get("our_method") if isinstance(exp, dict) else {}
        if isinstance(method, str):
            name = method
            description = method
        elif isinstance(method, dict):
            name = str(method.get("name") or method.get("method_name") or f"candidate_module_{idx}")
            description = str(method.get("description") or method.get("intended_role") or "")
        else:
            name = f"candidate_module_{idx}"
            description = str(exp.get("description") or "") if isinstance(exp, dict) else ""
        candidate_modules.append(
            {
                "module_id": f"M{idx}",
                "name": name,
                "intended_role": description or str(exp.get("description") or "external executor must refine this role"),
                "expected_input": str(exp.get("dataset") or exp.get("input") or "dataset defined by exp_plan"),
                "expected_output": "auditable metrics, raw results, logs, configs, and module attribution",
                "why_it_may_help": _compact_text(str(exp.get("hypothesis_ref") or exp.get("rationale") or context_reboost.get("central_hypothesis") or "")),
                "related_claim": str(exp.get("hypothesis_ref") or exp.get("name") or f"claim_{idx}"),
                "planned_ablation": str(exp.get("ablation") or exp.get("planned_ablation") or "executor must define module-removal or replacement ablation"),
            }
        )
    return {
        "status": "draft_intent_only",
        "not_final_method_source": True,
        "central_mechanism_hypothesis": context_reboost.get("central_hypothesis") or _first_non_heading_line(hypotheses),
        "candidate_modules": candidate_modules,
        "expected_algorithm_flow": [
            {
                "step": idx,
                "description": f"Implement and evaluate {module.get('name')}",
                "related_module": module.get("module_id"),
            }
            for idx, module in enumerate(candidate_modules, start=1)
        ],
        "allowed_refinements": (context_reboost.get("method_mechanism") or {}).get("allowed_refinements", []),
        "forbidden_silent_changes": [
            "replace_core_mechanism",
            "drop_required_baseline",
            "change_task_or_benchmark",
            "change_contribution_type_without_review",
        ],
        "mechanism_to_ablation_plan": [
            {
                "mechanism": module.get("name"),
                "planned_test": module.get("planned_ablation"),
                "expected_observation_if_supported": "module removal or replacement degrades the relevant audited metric",
                "expected_observation_if_not_supported": "module removal has no meaningful effect or improves the metric",
            }
            for module in candidate_modules
        ],
        "initial_framework_figure_sketch": {
            "status": "draft_intent_only",
            "purpose": "guide implementation, not final paper figure",
            "main_message": context_reboost.get("project_goal"),
            "candidate_panels": ["problem/setup", "method modules", "experiment evidence"],
            "candidate_nodes": [module.get("name") for module in candidate_modules],
            "candidate_edges": ["data_flow", "module_dependency", "evidence_support"],
            "must_not_be_used_directly_by_T8": True,
        },
    }


def _first_non_heading_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip(" -*\t")
        if stripped and not stripped.startswith("#"):
            return stripped[:300]
    return ""


def _compact_text(text: str, *, limit: int = 300) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def _section_hint(text: str, keys: list[str]) -> str:
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if any(key.lower() in lowered for key in keys):
            return "\n".join(lines[idx : idx + 6])
    return ""


def _extract_bullets(text: str, *, limit: int) -> list[str]:
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*")):
            item = stripped.lstrip("-* ").strip()
            if item:
                items.append(_compact_text(item, limit=180))
        if len(items) >= limit:
            break
    return items


def _build_baseline_matrix(required_baselines: list[dict[str, Any]], exp_baselines: list[str]) -> list[dict[str, Any]]:
    matrix: list[dict[str, Any]] = []
    for idx, item in enumerate(required_baselines, start=1):
        name = _baseline_name(item) or f"required_baseline_{idx}"
        matrix.append(
            {
                "baseline_id": str(item.get("baseline_id") or f"required_baseline_{idx}") if isinstance(item, dict) else f"required_baseline_{idx}",
                "baseline_name": name,
                "priority": str(item.get("priority") or "must_run") if isinstance(item, dict) else "must_run",
                "source": str(item.get("source") or "novelty_audit") if isinstance(item, dict) else "novelty_audit",
                "reason_required": str(item.get("reason_required") or "required by novelty audit") if isinstance(item, dict) else "required by novelty audit",
                "acceptable_substitute": item.get("acceptable_substitute") if isinstance(item, dict) else None,
                "appears_in_exp_plan": name in exp_baselines,
                "claim_risk_if_missing": (item.get("cannot_claim_without_it") if isinstance(item, dict) else None)
                or ["outperforms prior work", "state-of-the-art", "strong empirical advantage"],
            }
        )
    known_required = {_baseline_name(item) for item in required_baselines}
    for idx, name in enumerate(exp_baselines, start=1):
        if name in known_required:
            continue
        matrix.append(
            {
                "baseline_id": f"planned_baseline_{idx}",
                "baseline_name": name,
                "priority": "planned",
                "source": "exp_plan",
                "reason_required": "listed in exp_plan",
                "acceptable_substitute": None,
                "appears_in_exp_plan": True,
                "claim_risk_if_missing": ["weaken comparative claims"],
            }
        )
    return matrix


def _build_claim_evidence_matrix(
    exp_plan: dict[str, Any],
    metrics: list[str],
    required_baselines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    experiments = _experiments_from_plan(exp_plan)
    if not experiments:
        experiments = [{"name": "minimum_loop", "metrics": metrics}]
    required_names = [_baseline_name(item) for item in required_baselines if _baseline_name(item)]
    rows = []
    for idx, exp in enumerate(experiments, start=1):
        exp_metrics = []
        for metric in exp.get("metrics", []) or metrics:
            if isinstance(metric, dict):
                exp_metrics.append(str(metric.get("name") or metric.get("metric_id") or "metric"))
            else:
                exp_metrics.append(str(metric))
        claim_id = str(exp.get("hypothesis_ref") or exp.get("claim_id") or exp.get("name") or f"claim_{idx}")
        rows.append(
            {
                "claim_id": claim_id,
                "claim_candidate": str(exp.get("description") or exp.get("name") or claim_id),
                "reviewer_question": "Does the claimed mechanism improve the target metric under fair baseline and dataset conditions?",
                "required_evidence": [
                    "raw_result_file",
                    "config_file",
                    "log_file",
                    "metric_provenance",
                    "baseline_reproduction",
                    "ablation_or_diagnostic_evidence",
                ],
                "metrics": exp_metrics,
                "required_baselines": required_names,
                "strong_claim_requires": [
                    "all required baselines covered",
                    "raw logs and configs indexed",
                    "method audit pass",
                    "non-mock execution",
                ],
                "weak_claim_when": [
                    "baseline unavailable",
                    "single dataset or seed only",
                    "method drift is minor but documented",
                    "diagnostic evidence only",
                ],
                "must_not_claim_when": [
                    "mock_only",
                    "missing metric source artifact",
                    "major contribution drift",
                    "required baseline silently dropped",
                ],
            }
        )
    return rows


def _build_minimum_experiment_loop(exp_plan: dict[str, Any], metrics: list[str]) -> list[dict[str, Any]]:
    experiments = _experiments_from_plan(exp_plan)
    datasets = list(
        dict.fromkeys(
            str(exp.get("dataset") or exp.get("benchmark") or "dataset_from_exp_plan")
            for exp in experiments
        )
    ) or ["dataset_from_exp_plan"]
    return [
        {"step": "context_alignment", "required_output": "result_pack.context_alignment"},
        {"step": "resource_and_baseline_mining", "required_output": "result_pack.resources"},
        {"step": "baseline_reproduction", "required_output": "result_pack.baseline_reproduction"},
        {"step": "method_implementation", "required_output": "result_pack.realized_method_package"},
        {
            "step": "smoke_small_formal_runs",
            "datasets": datasets,
            "metrics": metrics,
            "required_output": "result_pack.experiment_runs",
        },
        {"step": "diagnosis_and_attribution", "required_output": "result_pack.result_diagnosis/module_attribution"},
        {"step": "writer_handoff", "required_output": "result_pack.writer_handoff"},
    ]


def _claim_boundaries_from_context(
    novelty_audit: str,
    risks: str,
    required_baselines: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    boundaries = [
        {
            "boundary": "No strong comparative claim without required baseline coverage.",
            "source": "required_baselines",
            "affected_claims": ["state-of-the-art", "outperforms prior work", "strong empirical advantage"],
        },
        {
            "boundary": "T5 method_intent is not final Method; T8 must use audited realized_method_package.",
            "source": "external_executor_design",
            "affected_claims": ["method definition", "framework figure"],
        },
    ]
    if "must not" in novelty_audit.lower() or "不能" in novelty_audit:
        boundaries.append(
            {
                "boundary": "Novelty audit contains explicit must-not-claim language; preserve it in result pack and T7 claims.",
                "source": "novelty_audit",
                "affected_claims": ["novelty", "contribution"],
            }
        )
    if risks.strip():
        boundaries.append(
            {
                "boundary": _compact_text(risks, limit=240),
                "source": "ideation/risks.md",
                "affected_claims": ["limitations", "scope"],
            }
        )
    if not required_baselines:
        boundaries.append(
            {
                "boundary": "No mandatory baseline was extracted; executor must document baseline limitations before strong claims.",
                "source": "handoff_compiler",
                "affected_claims": ["comparative performance"],
            }
        )
    return boundaries


def _build_expected_outputs_schema() -> dict[str, Any]:
    return {
        "version": "1.0",
        "schema_version": "external_executor_result_pack.v1",
        "semantics": "expected_external_executor_outputs_schema",
        "required": EXTERNAL_RESULT_REQUIRED_FIELDS,
        "required_files": [
            "external_executor/result_pack.json",
            "external_executor/executor_status.json",
            "external_executor/run_manifest.json",
            "external_executor/raw_results/",
            "external_executor/configs/",
            "external_executor/logs/",
        ],
        "metric_required": ["metric_id", "name", "value", "source_artifact", "dataset", "seed"],
        "artifact_required": ["path", "kind", "role", "sha256"],
        "status_required": ["semantics", "run_id", "status", "accepted", "dry_run", "mock_only"],
        "run_manifest_required": ["semantics", "run_id", "executor", "raw_results", "configs", "logs", "artifacts"],
        "field_semantics": {
            "method_intent": "T5 draft intent only; never final method source.",
            "realized_method_package": "External executor's implemented method package after runs and diagnosis.",
            "final_framework_figure": "Framework figure candidate that T7 must audit before T8 use.",
            "writer_handoff": "Structured method/experiment/figure handoff for T7 and T8.",
        },
    }


def _executor_selection_payload(workspace: Path) -> tuple[dict[str, Any], str]:
    path = workspace / "external_executor" / "executor_selection.json"
    selection = _read_json(path)
    return selection, _sha256(path) if path.exists() and path.is_file() else ""


def _selection_selected_executor(selection: dict[str, Any]) -> str:
    return str(selection.get("selected_executor") or "").strip()


def _is_mock_executor(executor: str) -> bool:
    return executor == "mock_dry_run"


VALID_EXTERNAL_EXECUTORS = {"mock_dry_run", "codex_cli", "claude_code_window", "manual"}


def _validate_executor_identity_binding(
    *,
    selected_executor: str,
    result_pack: dict[str, Any],
    status: dict[str, Any],
    manifest: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    if selected_executor not in VALID_EXTERNAL_EXECUTORS:
        issues.append(
            "executor_selection.selected_executor invalid or not finalized: "
            f"{selected_executor or '<missing>'}"
        )
        return issues
    for label, payload, key_options in (
        ("result_pack", result_pack, ("executor",)),
        ("executor_status", status, ("executor", "executor_type")),
        ("run_manifest", manifest, ("executor",)),
    ):
        value = ""
        for key in key_options:
            value = str(payload.get(key) or "").strip()
            if value:
                break
        if not value:
            issues.append(f"{label} executor missing; must match executor_selection")
        elif value != selected_executor:
            issues.append(f"{label} executor does not match executor_selection: {value} != {selected_executor}")
    return issues


def _external_binding_fingerprint_issues(workspace: Path, payload: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    required = {
        "executor_selection_ref": "selection_sha256",
        "result_pack_ref": "result_pack_sha256",
        "executor_status_ref": "executor_status_sha256",
    }
    for rel_key, hash_key in required.items():
        rel = str(payload.get(rel_key) or "").strip()
        expected_hash = str(payload.get(hash_key) or "").strip()
        if not rel or not expected_hash:
            issues.append(f"external binding missing {rel_key}/{hash_key}")
            continue
        path = workspace / rel
        if not path.exists() or not path.is_file():
            issues.append(f"external binding source missing: {rel}")
            continue
        if _sha256(path) != expected_hash:
            issues.append(f"external binding hash mismatch: {rel}")
    return issues


def _extract_exp_plan_metrics(exp_plan: dict[str, Any]) -> list[str]:
    metrics: list[str] = []
    for exp in exp_plan.get("experiments", []) or []:
        if not isinstance(exp, dict):
            continue
        for metric in exp.get("metrics", []) or []:
            if isinstance(metric, dict) and metric.get("name"):
                metrics.append(str(metric["name"]))
            elif isinstance(metric, str):
                metrics.append(metric)
    return list(dict.fromkeys(metrics)) or ["task_score"]


def _extract_exp_plan_seeds(project: dict[str, Any]) -> list[int]:
    seed_ensemble = project.get("seed_ensemble") if isinstance(project, dict) else {}
    seeds: list[int] = []
    if isinstance(seed_ensemble, dict):
        for key in ("tier1_seeds", "tier2_seeds", "tier3_seeds"):
            for value in seed_ensemble.get(key, []) or []:
                try:
                    seeds.append(int(value))
                except Exception:
                    continue
    return list(dict.fromkeys(seeds)) or [42]


def _infer_experiment_intent(project: dict[str, Any], hypotheses: str) -> str:
    topic = str(project.get("topic") or project.get("research_direction") or project.get("project_id") or "").strip()
    for line in hypotheses.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:240]
    if topic:
        return f"Evaluate the ResearchOS hypothesis for: {topic}"
    return "Evaluate the selected ResearchOS hypothesis under an auditable external experiment protocol."


def _extract_required_baselines(workspace: Path) -> list[dict[str, Any]]:
    """Extract user/LLM-written required baselines without inventing knowledge."""

    existing = _read_json(workspace / "novelty" / "required_baselines.json")
    if isinstance(existing.get("required_baselines"), list):
        return [item for item in existing["required_baselines"] if isinstance(item, dict)]

    text = ""
    for rel in ("ideation/novelty_audit.md", "novelty/must_add_baselines.md"):
        path = workspace / rel
        if path.exists():
            text += "\n" + path.read_text(encoding="utf-8", errors="replace")
    if "Required Baselines" not in text and "Baseline:" not in text:
        return []

    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    baseline_index = 1
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        baseline_match = re.match(r"^-?\s*(?:Baseline|基线)\s*[:：]\s*(.+)$", line, re.IGNORECASE)
        if baseline_match:
            if current and current.get("baseline_name"):
                items.append(current)
            name = baseline_match.group(1).strip()
            current = {
                "baseline_id": f"required_baseline_{baseline_index}",
                "baseline_name": name,
                "source": "novelty_audit",
                "reason_required": "",
                "priority": "must_run",
                "acceptable_substitute": None,
                "cannot_claim_without_it": ["outperforms prior work", "state-of-the-art", "strong empirical advantage"],
            }
            baseline_index += 1
            continue
        if current is None:
            continue
        field_match = re.match(r"^-?\s*([^:：]+)\s*[:：]\s*(.+)$", line)
        if not field_match:
            continue
        key = field_match.group(1).strip().lower().replace(" ", "_")
        value = field_match.group(2).strip()
        if key in {"reason", "reason_required", "must_run_because", "why"}:
            current["reason_required"] = value
        elif key in {"acceptable_substitute", "substitute", "可替代"}:
            current["acceptable_substitute"] = value
        elif key in {"claims_blocked_if_missing", "cannot_claim_without_it", "blocked_claims"}:
            current["cannot_claim_without_it"] = [part.strip() for part in re.split(r"[,;；，]", value) if part.strip()]
    if current and current.get("baseline_name"):
        items.append(current)
    return items


def build_executor_selection_payload(
    *,
    selected_executor: str,
    selected_by: str = "human",
    notes: str = "",
) -> dict[str, Any]:
    real_allowed = selected_executor != "mock_dry_run"
    requires_copy = selected_executor in {"codex_cli", "claude_code_window", "manual"}
    next_state = "T5-DRY-RUN" if selected_executor == "mock_dry_run" else "T5-EXTERNAL-WAIT"
    payload: dict[str, Any] = {
        "version": "1.0",
        "semantics": "external_executor_selection",
        "selected_executor": selected_executor,
        "real_experiment_allowed": real_allowed,
        "requires_user_copy_paste": requires_copy,
        "selected_by": selected_by,
        "selected_at": _now_iso(),
        "next_state": next_state,
        "fallback_order": [item for item in ["mock_dry_run", "claude_code_window", "manual"] if item != selected_executor],
        "notes": notes or _default_executor_selection_note(selected_executor),
    }
    if selected_executor == "claude_code_window":
        payload["prompt_to_copy"] = "external_executor/claude_code_prompt.md"
    if selected_executor == "codex_cli":
        payload["prompt_file"] = "external_executor/codex_prompt.md"
        payload["allowed_workdir"] = "external_executor/workdir"
        payload["workspace_relative_workdir"] = "external_executor/workdir"
        payload["workspace_relative_prompt"] = "external_executor/codex_prompt.md"
        payload["codex_user_input"] = (
            "请读取 external_executor/AGENTS.md，并执行 "
            "external_executor/skills/research-execution/SKILL.md。"
        )
        payload["launch_instruction"] = (
            "On the host, enter the <workspace> root, start Codex CLI, and paste codex_user_input."
        )
        payload["resume_instruction"] = (
            "After Codex writes external_executor/result_pack.json, executor_status.json, "
            "and run_manifest.json, run: python -m researchos.cli resume --workspace <workspace>"
        )
    if selected_executor == "manual":
        payload["prompt_to_copy"] = "external_executor/manual_instructions.md"
    return payload


def _default_executor_selection_note(selected_executor: str) -> str:
    if selected_executor == "mock_dry_run":
        return "Protocol-only dry run selected; no real experiment evidence will be produced."
    if selected_executor == "codex_cli":
        return "Codex CLI selected for external real execution inside allowed workdir."
    if selected_executor == "claude_code_window":
        return "Claude Code window selected; user must copy prompt and resume after result_pack.json exists."
    return "Manual external execution selected; ResearchOS waits for result_pack.json."


def patch_external_executor_files_with_selection(workspace: Path, selection: dict[str, Any]) -> None:
    dry_run = "true" if selection.get("selected_executor") == "mock_dry_run" else "false"
    mock_only = "true" if selection.get("selected_executor") == "mock_dry_run" else "false"
    real_allowed = "true" if selection.get("real_experiment_allowed") else "false"
    selected = str(selection.get("selected_executor") or "mock_dry_run")
    execution_mode = "dry_run" if selected == "mock_dry_run" else "external"
    handoff_path = workspace / "external_executor" / "handoff_pack.json"
    handoff = _read_json(handoff_path)
    if handoff:
        handoff["executor"] = selected
        handoff["execution_mode"] = execution_mode
        _write_json(handoff_path, handoff)
    for rel in (
        "external_executor/AGENTS.md",
        "external_executor/CLAUDE.md",
        "external_executor/README.md",
        "external_executor/executor_prompt.md",
        "external_executor/codex_prompt.md",
        "external_executor/claude_code_prompt.md",
        "external_executor/manual_instructions.md",
    ):
        path = workspace / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        text = text.replace("dry_run: UNSET", f"dry_run: {dry_run}")
        text = text.replace("mock_only: UNSET", f"mock_only: {mock_only}")
        text = text.replace("real_experiment_allowed: UNSET", f"real_experiment_allowed: {real_allowed}")
        text = text.replace('"executor": "UNSET"', f'"executor": "{selected}"')
        text = text.replace('"execution_mode": "unselected"', f'"execution_mode": "{execution_mode}"')
        text = text.replace('"selected_executor": "UNSET"', f'"selected_executor": "{selected}"')
        text = text.replace(
            "EXECUTION MODE NOT YET SELECTED",
            f"EXECUTION MODE SELECTED: {selection.get('selected_executor')}",
        )
        path.write_text(text, encoding="utf-8")
    job_state = _read_json(workspace / "external_executor" / "job_state.json")
    if job_state:
        job_state.update(
            {
                "executor_type": selection.get("selected_executor"),
                "dry_run": selection.get("selected_executor") == "mock_dry_run",
                "mock_only": selection.get("selected_executor") == "mock_dry_run",
                "current_state": "CREATED",
                "selection": selection,
            }
        )
        _write_json(workspace / "external_executor" / "job_state.json", job_state)


def validate_external_executor_ready(
    workspace: Path,
    result_pack_rel: str,
    status_rel: str,
    *,
    allow_partial_results: bool = False,
) -> dict[str, Any]:
    missing = [rel for rel in (result_pack_rel, status_rel) if not (workspace / rel).exists()]
    if missing:
        report = {
            "version": "1.0",
            "semantics": "external_executor_wait_acceptance_report",
            "ok": False,
            "message": (
                "WAITING_EXTERNAL: external executor has not produced required files: "
                + ", ".join(missing)
                + ". Run the selected executor, then resume ResearchOS."
            ),
            "missing": missing,
        }
        _write_wait_rejection_report(workspace, report)
        return report
    result_pack = _read_json(workspace / result_pack_rel)
    status = _read_json(workspace / status_rel)
    selection, selection_hash = _executor_selection_payload(workspace)
    selected_executor = _selection_selected_executor(selection)
    manifest_rel = str(result_pack.get("run_manifest") or status.get("run_manifest") or "external_executor/run_manifest.json")
    manifest = _read_json(workspace / manifest_rel)
    allowed_paths_path = workspace / "external_executor" / "allowed_paths.txt"
    allowed_rules = _parse_allowed_paths(allowed_paths_path)
    issues: list[str] = []
    if not allowed_paths_path.exists() or not allowed_rules:
        issues.append("external_executor/allowed_paths.txt missing or empty")
    if result_pack.get("semantics") != "external_executor_result_pack":
        issues.append("result_pack semantics invalid")
    if status.get("semantics") != "external_executor_status":
        issues.append("executor_status semantics invalid")
    missing_required_fields = [field for field in EXTERNAL_RESULT_REQUIRED_FIELDS if field not in result_pack]
    if missing_required_fields:
        issues.append("result_pack missing required fields: " + ", ".join(missing_required_fields))
    if selection.get("semantics") != "external_executor_selection" or not selected_executor:
        issues.append("executor_selection.json missing or semantics invalid")
    else:
        issues.extend(
            _validate_executor_identity_binding(
                selected_executor=selected_executor,
                result_pack=result_pack,
                status=status,
                manifest=manifest,
            )
        )
        if _is_mock_executor(selected_executor):
            if result_pack.get("mock_only") is not True or result_pack.get("dry_run") is not True:
                issues.append("mock_dry_run selection requires result_pack.mock_only=true and dry_run=true")
        else:
            if result_pack.get("mock_only") is True or result_pack.get("dry_run") is True:
                issues.append("real external executor selection cannot ingest mock_only/dry_run result_pack")
            if status.get("mock_only") is True or status.get("dry_run") is True:
                issues.append("real external executor selection cannot ingest mock_only/dry_run executor_status")
    if status.get("accepted") is True:
        issues.append("executor_status.accepted cannot be true; external executor done is not ResearchOS accepted")
    current_state = status.get("current_state") or status.get("status")
    allowed_terminal_states = {"done", "COMPLETED", "completed"}
    if allow_partial_results:
        allowed_terminal_states.add("PARTIAL_RESULTS_READY")
    if current_state not in allowed_terminal_states:
        if current_state == "PARTIAL_RESULTS_READY" and not allow_partial_results:
            issues.append(
                "executor_status is PARTIAL_RESULTS_READY, but partial external results are disabled; "
                "finish the external run or enable allow_partial_results explicitly."
            )
        else:
            issues.append(
                "executor_status current_state/status is not "
                + "/".join(sorted(allowed_terminal_states))
            )
    job_state = _read_json(workspace / "external_executor" / "job_state.json")
    allowed_states = job_state.get("allowed_states") if isinstance(job_state, dict) else None
    if allowed_states:
        state_value = status.get("current_state") or status.get("status")
        if state_value not in allowed_states and state_value not in {"done"}:
            issues.append(f"executor_status state not declared in job_state.allowed_states: {state_value}")
        job_current = job_state.get("current_state")
        if job_current and state_value in allowed_states and job_current in allowed_states:
            if allowed_states.index(state_value) < allowed_states.index(job_current):
                issues.append(f"executor_status state regressed from job_state current_state: {job_current} -> {state_value}")
    metrics = result_pack.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        issues.append("result_pack.metrics missing")
    if manifest.get("semantics") != "external_executor_run_manifest":
        issues.append("run_manifest semantics invalid or missing")
    for rel_path in _referenced_executor_paths(result_pack, manifest):
        if not _path_allowed(rel_path, allowed_rules):
            issues.append(f"path not allowed by allowed_paths.txt: {rel_path}")
            continue
        path = workspace / rel_path
        if not path.exists():
            issues.append(f"referenced artifact missing: {rel_path}")
    for artifact in result_pack.get("artifacts", []) or []:
        if not isinstance(artifact, dict):
            continue
        rel_path = str(artifact.get("path") or "")
        expected_hash = str(artifact.get("sha256") or "")
        if not rel_path or not expected_hash:
            issues.append(f"artifact missing path/hash: {artifact}")
            continue
        path = workspace / rel_path
        if path.exists() and path.is_file() and _sha256(path) != expected_hash:
            issues.append(f"artifact hash mismatch: {rel_path}")
    if not result_pack.get("mock_only"):
        runs = result_pack.get("experiment_runs") or result_pack.get("runs")
        manifest_runs = manifest.get("runs")
        if not isinstance(runs, list) or not runs:
            issues.append("real result_pack must include non-empty experiment_runs/runs")
        if not isinstance(manifest_runs, list) or not manifest_runs:
            issues.append("real run_manifest.runs must be non-empty")
    if issues:
        report = {
            "version": "1.0",
            "semantics": "external_executor_wait_acceptance_report",
            "ok": False,
            "message": "WAITING_EXTERNAL: external result pack exists but is not valid: " + "; ".join(issues),
            "issues": issues,
            "selected_executor": selected_executor,
            "executor_selection": "external_executor/executor_selection.json" if selection else "",
            "selection_sha256": selection_hash,
        }
        _write_wait_rejection_report(workspace, report)
        return report
    report = {
        "version": "1.0",
        "semantics": "external_executor_wait_acceptance_report",
        "ok": True,
        "message": "External executor result pack is present and schema-compatible.",
        "result_pack": result_pack_rel,
        "executor_status": status_rel,
        "run_manifest": manifest_rel,
        "executor_selection": "external_executor/executor_selection.json" if selection else "",
        "selected_executor": selected_executor,
        "selection_sha256": selection_hash,
        "result_pack_sha256": _sha256(workspace / result_pack_rel),
        "executor_status_sha256": _sha256(workspace / status_rel),
        "dry_run": bool(result_pack.get("dry_run")),
        "mock_only": bool(result_pack.get("mock_only")),
        "partial_results_allowed": allow_partial_results,
    }
    return report


def _parse_allowed_paths(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    rules: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        mode, prefix = parts[0].strip(), parts[1].strip()
        if mode in {"rw", "ro", "no"}:
            rules.append((mode, prefix))
    return rules


def _path_allowed(rel_path: str, rules: list[tuple[str, str]]) -> bool:
    normalized = rel_path.strip().lstrip("./")
    matched: str | None = None
    for mode, prefix in rules:
        prefix_norm = prefix.strip().lstrip("./")
        if normalized == prefix_norm or normalized.startswith(prefix_norm):
            matched = mode
    return matched == "rw"


def _referenced_executor_paths(result_pack: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("raw_result_files", "config_files", "log_files"):
        value = result_pack.get(key)
        if isinstance(value, list):
            paths.extend(str(item) for item in value if isinstance(item, str))
    for key in ("raw_results", "configs", "logs"):
        value = manifest.get(key)
        if isinstance(value, list):
            paths.extend(str(item) for item in value if isinstance(item, str))
    for artifact in list(result_pack.get("artifacts", []) or []) + list(manifest.get("artifacts", []) or []):
        if isinstance(artifact, dict) and artifact.get("path"):
            paths.append(str(artifact["path"]))
    for metric in result_pack.get("metrics", []) or []:
        if isinstance(metric, dict) and metric.get("source_artifact"):
            paths.append(str(metric["source_artifact"]))
    return list(dict.fromkeys(path for path in paths if path))


def _write_wait_rejection_report(workspace: Path, report: dict[str, Any]) -> None:
    path = workspace / "external_executor" / "wait_rejection_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    issues = report.get("issues") or report.get("missing") or []
    lines = [
        "# External Executor Wait Rejection Report",
        "",
        str(report.get("message") or "External executor result is not ready."),
        "",
        "## Issues",
    ]
    lines.extend(f"- {issue}" for issue in issues)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_external_executor_guides(
    policy: WorkspaceAccessPolicy,
    handoff: dict[str, Any],
    *,
    selection: dict[str, Any],
) -> None:
    ext_dir = policy.workspace_dir / "external_executor"
    ext_dir.mkdir(parents=True, exist_ok=True)
    project_id = str(handoff.get("project_id") or handoff.get("project", {}).get("project_id") or "unknown")
    metrics_block = "\n".join(f"- {metric.get('name')}" for metric in handoff.get("metrics", []) if isinstance(metric, dict)) or "- task_score"
    baselines = handoff.get("required_baselines") or []
    baselines_block = _format_required_baselines_block(baselines)
    allowed_paths = "\n".join(f"- {path}" for path in handoff.get("allowed_paths", []))
    required_outputs = "\n".join(f"- `{path}`" for path in (handoff.get("executor_outputs_contract") or {}).get("must_write", []))
    seeds = ", ".join(str(seed) for seed in handoff.get("seeds", []) or [42])
    common_header = (
        "> EXECUTION MODE NOT YET SELECTED - see executor_selection.json after T5-EXECUTOR-GATE\n\n"
        f"- dry_run: UNSET\n- mock_only: UNSET\n- real_experiment_allowed: UNSET\n\n"
    )
    agents = (
        f"# External Executor Instructions for ResearchOS - project {project_id}\n\n"
        + common_header
        + "## Role\n"
        "You are an external experiment executor for ResearchOS. You are not the paper writer and not the ResearchOS runtime.\n\n"
        "## Start command\n"
        "Read this file, then execute `external_executor/skills/research-execution/SKILL.md`.\n\n"
        "## This experiment in one line\n"
        f"{handoff.get('experiment_intent_oneliner')}\n\n"
        "## Read first\n"
        "1. external_executor/handoff_pack.json\n"
        "2. external_executor/expected_outputs_schema.json\n"
        "3. external_executor/allowed_paths.txt\n"
        "4. external_executor/executor_selection.json\n"
        "5. external_executor/skill_specialization_report.json\n"
        "6. external_executor/project_skill_context.yaml\n"
        "7. novelty/required_baselines.json\n"
        "8. resources/baseline_candidates.jsonl\n"
        "9. literature/baseline_map.json\n"
        "10. ideation/novelty_audit.md\n\n"
        "## Human-provided experiment materials\n"
        "Inspect `external_executor/expr/` before real execution. This directory is the gate where the user places datasets, baseline models, repositories, weights, and material notes.\n\n"
        "## Metrics you must report\n"
        f"{metrics_block}\n\n"
        "## Required baselines\n"
        f"{baselines_block}\n\n"
        "## Seeds\n"
        f"Run required configurations over seeds: {seeds}\n\n"
        "## Hard boundaries\n"
        f"{allowed_paths}\n\n"
        "Do not fabricate datasets, baselines, metrics, or results. Every metric must trace to a raw file, config, run id, log, and sha256.\n\n"
        "## Required outputs\n"
        f"{required_outputs}\n\n"
        "Write external_executor/result_pack.json last. Do not write paper text or final claims.\n"
    )
    claude = (
        f"# Claude Code External Execution Guide - project {project_id}\n\n"
        + common_header
        + "You are used as an external coding executor for ResearchOS via a Claude Code window.\n\n"
        "## Steps\n"
        "1. Read handoff_pack.json, expected_outputs_schema.json, allowed_paths.txt.\n"
        "2. Read novelty/required_baselines.json and resources/baseline_candidates.jsonl.\n"
        "3. If mock_only=true, emit schema-valid mock artifacts with mock_only=true and dry_run=true.\n"
        "4. If real, clone/inspect baseline repos only inside external_executor/workdir.\n"
        f"5. Run required configs over seeds {seeds}.\n"
        "6. Write all required outputs and stop after result_pack.json.\n\n"
        "## Metrics\n"
        f"{metrics_block}\n\n"
        "## Required baselines\n"
        f"{baselines_block}\n\n"
        "## Required outputs\n"
        f"{required_outputs}\n"
    )
    readme = (
        f"# External Executor Workspace - {project_id}\n\n"
        + common_header
        + "ResearchOS writes experiment contracts here. External executors write auditable result artifacts here.\n\n"
        "Key files: handoff_pack.json, expected_outputs_schema.json, allowed_paths.txt, AGENTS.md, CLAUDE.md, result_pack.json.\n"
    )
    dir_guide = (
        "# Workspace Directory Guide\n\n"
        "| 项目 | 说明 |\n"
        "|---|---|\n"
        "| 目录用途 | ResearchOS 与 Codex/Claude/manual 外部实验执行器的边界目录。 |\n"
        "| 生成阶段/来源 | T5-REBOOST/T5-HANDOFF, T5-EXECUTOR-GATE, external executor, T5-DRY-RUN. |\n"
        "| 下游使用方 | T5-EXTERNAL-WAIT, T7-INGEST, T7-AUDIT, T7-POST-NOVELTY, T7-CLAIMS, T8-RESOURCE. |\n"
        "| 人工可编辑范围 | Manual executor outputs only. |\n"
        "| Agent 可写范围 | External executor may write only paths allowed by allowed_paths.txt. |\n"
        "| 不应放入 | Paper text, unsupported claims, API keys, or unrelated notebooks. |\n"
        "| 校验/恢复规则 | Every metric must have source artifact, config/log linkage, run id, and hash provenance. |\n\n"
        "## Key Files\n\n"
        "| 文件/子目录 | 内容与用途 |\n"
        "|---|---|\n"
        "| `AGENTS.md` | Codex/agent 外部执行约束。 |\n"
        "| `CLAUDE.md` | Claude Code 外部执行约束。 |\n"
        "| `handoff_pack.json` | T5 编译的实验任务、协议、证据契约和 allowed paths。 |\n"
        "| `expected_outputs_schema.json` | 外部执行器必须写回的 result pack/status/manifest schema。 |\n"
        "| `allowed_paths.txt` | 外部执行器可读写路径边界。 |\n"
        "| `skills/` | Project-specific external executor skill suite generated by `researchos specialize-executor-skills` from root templates and LLM project guidance. |\n"
        "| `expr/` | Human-provided experimental materials gate directory. |\n"
        "| `result_pack.json` | 外部执行器写回的核心结果包，T7 只从这里摄取实验结果。 |\n"
        "| `executor_status.json` | 外部执行器状态、accepted/mock/dry-run 标记。 |\n"
        "| `run_manifest.json` | 运行记录、raw/config/log 路径和 provenance。 |\n\n"
        "Generated by ResearchOS workspace initialization.\n"
    )
    _write_text(policy.resolve_write("external_executor/AGENTS.md"), agents)
    _write_text(policy.resolve_write("external_executor/CLAUDE.md"), claude)
    _write_text(policy.resolve_write("external_executor/README.md"), readme)
    _write_text(policy.resolve_write("external_executor/_DIR_GUIDE.md"), dir_guide)
    _write_json(
        policy.resolve_write("external_executor/job_state.json"),
        {
            "version": "1.0",
            "semantics": "external_executor_job_state",
            "job_id": f"external_{project_id}",
            "executor_type": selection.get("selected_executor", "UNSET"),
            "current_state": "CREATED",
            "allowed_states": [
                "CREATED",
                "CLAIMED_BY_EXECUTOR",
                "ENV_PREPARED",
                "BASELINE_REPO_CLONED",
                "DATASET_PREPARED",
                "BASELINE_SMOKE_PASSED",
                "METHOD_IMPLEMENTED",
                "EXPERIMENT_RUNNING",
                "PARTIAL_RESULTS_READY",
                "FAILED_RECOVERABLE",
                "FAILED_FINAL",
                "COMPLETED",
                "INGESTED",
                "AUDITED",
            ],
            "last_heartbeat_at": None,
            "dry_run": None,
            "mock_only": None,
            "result_pack_path": "external_executor/result_pack.json",
        },
    )
    events = policy.resolve_write("external_executor/executor_events.jsonl")
    if not events.exists():
        events.write_text(
            json.dumps({"time": _now_iso(), "state": "CREATED", "message": "Handoff files generated."}, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )


def _write_expr_materials_scaffold(policy: WorkspaceAccessPolicy, handoff: dict[str, Any]) -> None:
    expr_dir = policy.workspace_dir / "external_executor" / "expr"
    expr_dir.mkdir(parents=True, exist_ok=True)
    checklist = {
        "version": "1.0",
        "semantics": "external_executor_expr_materials_checklist",
        "created_at": _now_iso(),
        "purpose": "Place human-provided experimental materials here before selecting a real external executor.",
        "expected_materials": [
            "datasets or dataset access instructions",
            "baseline model repositories or paths",
            "pretrained weights or download notes",
            "environment constraints and credentials notes without secrets",
            "README describing material provenance",
        ],
        "required_baselines": handoff.get("required_baselines", []),
        "minimum_experiment_loop": (handoff.get("context_reboost") or {}).get("minimum_experiment_loop", []),
        "next_step": "After materials are ready, resume ResearchOS and select an external executor.",
    }
    checklist_path = expr_dir / "MATERIALS_CHECKLIST.json"
    if not checklist_path.exists():
        _write_json(checklist_path, checklist)
    readme_path = expr_dir / "README.md"
    if not readme_path.exists():
        _write_text(
            readme_path,
            "# External Experiment Materials\n\n"
            "Place baseline models, datasets, repositories, pretrained weights, and material notes here.\n"
            "Do not commit secrets. After materials are ready, resume ResearchOS and choose Codex CLI, Claude Code, manual, or mock dry-run.\n",
        )


def _format_required_baselines_block(baselines: list[Any]) -> str:
    if not baselines:
        return "No mandatory baselines were extracted. Executor should still report any baseline limitations."
    lines = []
    for item in baselines:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- {item.get('baseline_name') or item.get('name')}: "
            f"{item.get('reason_required') or 'required by novelty audit'}"
        )
    return "\n".join(lines) or "No mandatory baselines were extracted."


def _baseline_coverage_from_metrics(
    required_baselines: list[dict[str, Any]],
    metrics: list[Any],
    *,
    mock_only: bool,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if existing and existing.get("status") in {"complete", "incomplete", "missing", "no_required_baselines", "mock_only"}:
        return existing
    required_names = [
        str(item.get("baseline_name") or item.get("name") or item.get("baseline_id") or "").strip()
        for item in required_baselines
        if isinstance(item, dict)
    ]
    required_names = [name for name in required_names if name]
    if not required_names:
        return {
            "status": "no_required_baselines",
            "required": [],
            "completed": [],
            "missing_baselines": [],
            "substituted_baselines": [],
            "claim_blocks": [],
        }
    metric_text = "\n".join(json.dumps(metric, ensure_ascii=False).lower() for metric in metrics if isinstance(metric, dict))
    completed = [name for name in required_names if name.lower() in metric_text]
    missing = [name for name in required_names if name not in completed]
    status = "mock_only" if mock_only else ("complete" if not missing else "incomplete")
    return {
        "status": status,
        "required": required_names,
        "completed": completed,
        "missing_baselines": missing,
        "substituted_baselines": [],
        "claim_blocks": ["outperforms prior work", "state-of-the-art", "strong empirical advantage"] if missing else [],
    }


def _metrics_object(metrics: list[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for idx, metric in enumerate(metrics, start=1):
        if not isinstance(metric, dict):
            continue
        metric_id = str(metric.get("metric_id") or f"metric_{idx}")
        out[metric_id] = {key: value for key, value in metric.items() if key != "metric_id"}
    return out


def _summary_metric_records(summary: dict[str, Any]) -> list[dict[str, Any]]:
    records = summary.get("metric_records")
    if isinstance(records, list):
        return [item for item in records if isinstance(item, dict)]
    metrics = summary.get("metrics")
    if isinstance(metrics, list):
        return [item for item in metrics if isinstance(item, dict)]
    if isinstance(metrics, dict):
        out = []
        for metric_id, payload in metrics.items():
            if isinstance(payload, dict):
                item = dict(payload)
                item.setdefault("metric_id", str(metric_id))
                out.append(item)
        return out
    return []


def _format_fairness_review(audit: dict[str, Any]) -> str:
    coverage = audit.get("required_baseline_coverage") or {}
    result_audit = audit.get("result_audit") or {}
    return (
        "# Experiment Fairness Review\n\n"
        "This is a deterministic scaffold. LLM/human reviewers should inspect fairness before strong claims.\n\n"
        f"- integrity_status: {audit.get('status')}\n"
        f"- evidence_grade: {audit.get('evidence_grade')}\n"
        f"- baseline_coverage_status: {coverage.get('status')}\n"
        f"- missing_baselines: {', '.join(coverage.get('missing_baselines', []) or []) or 'none'}\n"
        f"- metric_provenance_status: {(result_audit.get('metric_provenance') or {}).get('status')}\n"
        f"- mock_dry_run_status: {(result_audit.get('mock_dry_run') or {}).get('status')}\n"
    )


def _indexed_paths(evidence: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for item in evidence.get("artifacts", []) or []:
        if isinstance(item, dict) and item.get("path"):
            paths.add(str(item["path"]))
    for key in ("raw_result_files", "config_files", "log_files", "patch_files", "figure_files", "table_files"):
        for item in evidence.get(key, []) or []:
            if isinstance(item, str):
                paths.add(item)
    return paths


def _metric_ref(metric: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = metric.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list) and value:
            return str(value[0])
    return ""


def _build_result_audit(
    *,
    workspace: Path,
    summary: dict[str, Any],
    evidence: dict[str, Any],
    baseline_coverage: dict[str, Any],
) -> dict[str, Any]:
    metric_records = _summary_metric_records(summary)
    indexed_paths = _indexed_paths(evidence)
    metric_issues: list[dict[str, Any]] = []
    audited_metric_ids: list[str] = []
    raw_result_files = set(evidence.get("raw_result_files") or [])
    config_files = set(evidence.get("config_files") or [])
    log_files = set(evidence.get("log_files") or [])
    for metric in metric_records:
        if not isinstance(metric, dict):
            continue
        metric_id = str(metric.get("metric_id") or metric.get("name") or "<unknown>")
        source_artifact = _metric_ref(metric, "source_artifact", "raw_result", "raw_result_ref")
        config_ref = _metric_ref(metric, "config", "config_path", "config_ref")
        log_ref = _metric_ref(metric, "log", "log_path", "log_ref")
        seed = metric.get("seed")
        split = metric.get("dataset_split") or metric.get("split")
        metric_direction = metric.get("metric_direction") or metric.get("direction")
        metric_ok = True
        checks = [
            ("missing_source_artifact", source_artifact, raw_result_files),
            ("missing_config_ref", config_ref, config_files),
            ("missing_log_ref", log_ref, log_files),
        ]
        for code, rel_path, expected_group in checks:
            if not rel_path:
                metric_ok = False
                metric_issues.append({"level": "FAIL", "code": code, "metric_id": metric_id})
                continue
            if rel_path not in indexed_paths and rel_path not in expected_group:
                metric_ok = False
                metric_issues.append({"level": "FAIL", "code": code + "_not_indexed", "metric_id": metric_id, "path": rel_path})
            elif not (workspace / rel_path).exists():
                metric_ok = False
                metric_issues.append({"level": "FAIL", "code": code + "_missing_on_disk", "metric_id": metric_id, "path": rel_path})
        if seed in {None, ""}:
            metric_ok = False
            metric_issues.append({"level": "WARN", "code": "missing_seed", "metric_id": metric_id})
        if not split:
            metric_ok = False
            metric_issues.append({"level": "WARN", "code": "missing_split", "metric_id": metric_id})
        if not metric_direction:
            metric_ok = False
            metric_issues.append({"level": "WARN", "code": "missing_metric_direction", "metric_id": metric_id})
        if metric_ok:
            audited_metric_ids.append(metric_id)

    figure_issues: list[dict[str, Any]] = []
    inventory = evidence.get("figure_table_inventory")
    inventory_items: list[dict[str, Any]] = []
    if isinstance(inventory, dict):
        for key in ("figures", "tables"):
            inventory_items.extend(item for item in inventory.get(key, []) or [] if isinstance(item, dict))
    elif isinstance(inventory, list):
        inventory_items = [item for item in inventory if isinstance(item, dict)]
    for item in inventory_items:
        item_id = str(item.get("figure_id") or item.get("table_id") or item.get("id") or "<unknown>")
        evidence_refs = _coerce_str_list(item.get("evidence_refs"))
        source_result = str(item.get("source_result") or item.get("source_artifact") or "")
        if source_result:
            evidence_refs.append(source_result)
        if not evidence_refs:
            figure_issues.append({"level": "WARN", "code": "figure_table_missing_source", "id": item_id})
        elif not any(ref in indexed_paths for ref in evidence_refs):
            figure_issues.append({"level": "WARN", "code": "figure_table_source_not_indexed", "id": item_id})

    mock_status = "mock_only" if summary.get("mock_only") or summary.get("dry_run") else "pass"
    cherry_pick_status = "warn" if any(run.get("status") in {"failed", "partial"} for run in summary.get("experiment_runs", []) or [] if isinstance(run, dict)) else "pass"
    baseline_status = baseline_coverage.get("status") or "unknown"
    status = "fail" if any(issue.get("level") == "FAIL" for issue in metric_issues) else ("mock_only" if mock_status == "mock_only" else "pass")
    return {
        "version": "1.0",
        "semantics": "external_experiment_result_audit",
        "status": status,
        "baseline_fairness": {
            "status": baseline_status,
            "missing_baselines": baseline_coverage.get("missing_baselines", []) or [],
            "claim_blocks": baseline_coverage.get("claim_blocks", []) or [],
        },
        "metric_provenance": {
            "status": "fail" if any(issue.get("level") == "FAIL" for issue in metric_issues) else ("warn" if metric_issues else "pass"),
            "audited_metric_ids": audited_metric_ids,
            "issues": metric_issues,
        },
        "raw_log_config": {
            "raw_result_count": len(raw_result_files),
            "config_count": len(config_files),
            "log_count": len(log_files),
        },
        "seed_split_consistency": {
            "status": "warn" if any(issue.get("code") in {"missing_seed", "missing_split"} for issue in metric_issues) else "pass"
        },
        "mock_dry_run": {"status": mock_status, "dry_run": bool(summary.get("dry_run")), "mock_only": bool(summary.get("mock_only"))},
        "cherry_pick": {"status": cherry_pick_status},
        "result_figure_provenance": {
            "status": "warn" if figure_issues else "pass",
            "issues": figure_issues,
        },
    }


def _build_method_and_figure_audits(
    *,
    workspace: Path,
    summary: dict[str, Any],
    evidence: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    handoff = _read_json(workspace / "external_executor" / "handoff_pack.json")
    method_intent = handoff.get("method_intent") if isinstance(handoff.get("method_intent"), dict) else {}
    realized = evidence.get("realized_method_package") if isinstance(evidence.get("realized_method_package"), dict) else {}
    figure = evidence.get("final_framework_figure") if isinstance(evidence.get("final_framework_figure"), dict) else {}
    inventory = evidence.get("figure_table_inventory") if isinstance(evidence.get("figure_table_inventory"), dict) else {}
    issues: list[dict[str, Any]] = []
    if not method_intent:
        issues.append({"level": "FAIL", "code": "missing_method_intent", "detail": "handoff_pack.method_intent missing"})
    elif method_intent.get("status") != "draft_intent_only" or method_intent.get("not_final_method_source") is not True:
        issues.append({"level": "FAIL", "code": "method_intent_not_marked_draft", "detail": "T5 method intent must be draft-only"})
    if not realized:
        issues.append({"level": "FAIL", "code": "missing_realized_method_package", "detail": "result_pack.realized_method_package missing"})
    elif realized.get("status") in {"mock_only", "missing"} or summary.get("mock_only"):
        issues.append({"level": "WARN", "code": "realized_method_mock_only", "detail": "mock/dry-run method package is not a final Method source"})
    intent_modules = {
        str(item.get("module_id") or item.get("name") or "")
        for item in method_intent.get("candidate_modules", []) or []
        if isinstance(item, dict)
    }
    realized_module_items = realized.get("implemented_modules")
    if not isinstance(realized_module_items, list):
        realized_module_items = realized.get("modules", []) or []
    realized_module_items = [item for item in realized_module_items if isinstance(item, dict)]
    realized_modules = {
        str(item.get("module_id") or item.get("name") or "")
        for item in realized_module_items
        if isinstance(item, dict)
    }
    missing_modules = sorted(item for item in intent_modules if item and item not in realized_modules)
    if missing_modules and not summary.get("mock_only"):
        issues.append({"level": "WARN", "code": "intent_modules_not_realized", "detail": ", ".join(missing_modules)})
    code_path_issues = []
    for module in realized_module_items:
        module_id = str(module.get("module_id") or module.get("name") or "<unknown>")
        code_paths = _coerce_str_list(module.get("code_paths") or module.get("code_refs") or module.get("code_path"))
        if not code_paths:
            code_path_issues.append(module_id)
            continue
        for rel_path in code_paths:
            if rel_path.startswith("external_executor/") and not (workspace / rel_path).exists():
                code_path_issues.append(module_id + ":" + rel_path)
    if code_path_issues and not summary.get("mock_only"):
        issues.append({"level": "WARN", "code": "realized_modules_missing_code_paths", "detail": ", ".join(code_path_issues)})
    module_attribution = evidence.get("module_attribution") if isinstance(evidence.get("module_attribution"), dict) else {}
    ablation_matches_modules = bool(module_attribution) and not code_path_issues
    scope_changes = summary.get("scope_change_requests") or evidence.get("scope_change_requests") or []
    contribution_drift = "none"
    if any(item.get("level") == "FAIL" for item in issues) or scope_changes:
        contribution_drift = "major"
    elif any(item.get("level") == "WARN" for item in issues) or missing_modules:
        contribution_drift = "minor"
    required_action = "none"
    if contribution_drift == "major":
        required_action = "human_review"
    elif contribution_drift == "minor":
        required_action = "narrow_claim"
    method_consistency_audit = {
        "method_intent_matches_realized_method": bool(method_intent) and bool(realized) and not missing_modules,
        "realized_method_matches_code": bool(realized) and not code_path_issues,
        "framework_figure_matches_code": "pending_framework_audit",
        "ablation_matches_modules": ablation_matches_modules,
        "contribution_drift": contribution_drift,
        "requires_post_novelty_check": contribution_drift in {"minor", "major"} or bool(scope_changes),
        "required_action": required_action,
    }
    method_audit = {
        "version": "1.0",
        "semantics": "external_method_intent_vs_realized_audit",
        "status": "fail" if any(item.get("level") == "FAIL" for item in issues) else ("mock_only" if summary.get("mock_only") else "pass"),
        "contribution_drift": contribution_drift,
        "method_consistency_audit": method_consistency_audit,
        "method_intent_status": method_intent.get("status"),
        "realized_method_status": realized.get("status"),
        "missing_intent_modules": missing_modules,
        "missing_or_invalid_code_paths": code_path_issues,
        "scope_change_requests": scope_changes,
        "issues": issues,
        "method_intent_ref": "external_executor/handoff_pack.json#method_intent",
        "realized_method_ref": "external_executor/result_pack.json#realized_method_package",
    }
    figure_issues: list[dict[str, Any]] = []
    figure_path = str(figure.get("path") or "") if figure else ""
    if not figure:
        figure_issues.append({"level": "FAIL", "code": "missing_final_framework_figure", "detail": "result_pack.final_framework_figure missing"})
    elif figure.get("status") in {"mock_only", "missing"} or summary.get("mock_only"):
        figure_issues.append({"level": "WARN", "code": "framework_figure_mock_only", "detail": "mock/dry-run figure cannot be used by T8"})
    elif figure_path and not (workspace / figure_path).exists():
        figure_issues.append({"level": "FAIL", "code": "framework_figure_missing_on_disk", "detail": figure_path})
    figure_nodes = figure.get("nodes") if isinstance(figure.get("nodes"), list) else []
    missing_figure_modules: list[str] = []
    missing_figure_code_refs: list[str] = []
    for node in figure_nodes:
        if not isinstance(node, dict):
            continue
        module_id = str(node.get("module_id") or "").strip()
        if module_id and realized_modules and module_id not in realized_modules:
            missing_figure_modules.append(module_id)
        for rel_path in _coerce_str_list(node.get("code_refs") or node.get("code_path")):
            if rel_path.startswith("external_executor/") and not (workspace / rel_path).exists():
                missing_figure_code_refs.append(rel_path)
    if missing_figure_modules:
        figure_issues.append({"level": "FAIL", "code": "figure_node_not_implemented", "detail": ", ".join(sorted(set(missing_figure_modules)))})
    if missing_figure_code_refs:
        figure_issues.append({"level": "WARN", "code": "figure_code_ref_missing_on_disk", "detail": ", ".join(sorted(set(missing_figure_code_refs)))})
    evidence_mapping = figure.get("evidence_mapping") if isinstance(figure.get("evidence_mapping"), list) else []
    if figure_nodes and not evidence_mapping and not summary.get("mock_only"):
        figure_issues.append({"level": "WARN", "code": "framework_figure_missing_evidence_mapping", "detail": "figure nodes lack evidence_mapping"})
    inventory_figures = inventory.get("figures") if isinstance(inventory.get("figures"), list) else []
    if figure_path and inventory_figures:
        inv_paths = {str(item.get("path") or "") for item in inventory_figures if isinstance(item, dict)}
        if figure_path not in inv_paths:
            figure_issues.append({"level": "WARN", "code": "framework_figure_not_in_inventory", "detail": figure_path})
    framework_matches_code = bool(figure) and not any(item.get("level") == "FAIL" for item in figure_issues)
    method_consistency_audit["framework_figure_matches_code"] = framework_matches_code
    framework_audit = {
        "version": "1.0",
        "semantics": "external_framework_figure_audit",
        "status": "fail" if any(item.get("level") == "FAIL" for item in figure_issues) else ("mock_only" if summary.get("mock_only") else "pass"),
        "figure_ref": "external_executor/result_pack.json#final_framework_figure",
        "figure_path": figure_path or None,
        "consistent_with_realized_method": False if summary.get("mock_only") else framework_matches_code,
        "implemented_module_ids": sorted(item for item in realized_modules if item),
        "figure_module_ids": sorted(
            str(node.get("module_id"))
            for node in figure_nodes
            if isinstance(node, dict) and node.get("module_id")
        ),
        "missing_figure_modules": sorted(set(missing_figure_modules)),
        "missing_figure_code_refs": sorted(set(missing_figure_code_refs)),
        "issues": figure_issues,
    }
    return method_audit, framework_audit


def _format_method_writing_resources(
    *,
    summary: dict[str, Any],
    evidence: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    method_audit = audit.get("method_audit") or {}
    framework_audit = audit.get("framework_figure_audit") or {}
    realized = evidence.get("realized_method_package") or {}
    figure = evidence.get("final_framework_figure") or {}
    method_consistency = method_audit.get("method_consistency_audit") or {}
    implemented_modules = realized.get("implemented_modules") if isinstance(realized.get("implemented_modules"), list) else realized.get("modules", [])
    implemented_modules = implemented_modules if isinstance(implemented_modules, list) else []
    algorithm_flow = realized.get("actual_algorithm_flow") if isinstance(realized.get("actual_algorithm_flow"), list) else []
    ablation_mapping = []
    module_attribution = evidence.get("module_attribution") if isinstance(evidence.get("module_attribution"), dict) else {}
    for key in ("ours_effective_modules", "ours_weak_modules", "mechanism_supported", "mechanism_not_supported"):
        for item in module_attribution.get(key, []) or []:
            ablation_mapping.append({"source": key, "item": item})
    wrapper = {
        "method_overview": realized.get("one_sentence_method") or realized.get("final_method_name") or "",
        "realized_method_package": realized,
        "module_graph": implemented_modules,
        "algorithm_flow": algorithm_flow,
        "final_framework_figure": figure,
        "caption_draft": figure.get("caption_draft") if isinstance(figure, dict) else "",
        "symbol_table": realized.get("symbol_table") if isinstance(realized.get("symbol_table"), list) else [],
        "ablation_mapping": ablation_mapping,
        "implementation_notes": realized.get("implementation_notes") if isinstance(realized.get("implementation_notes"), list) else [],
        "method_consistency_audit": method_consistency,
        "do_not_claim": [
            "Do not use T5 method_intent as final Method.",
            "Do not use external executor prose without audited evidence.",
        ]
        + (["Do not use mock/dry-run method or figure as paper evidence."] if summary.get("mock_only") else [])
        + (["Do not use final framework figure until framework audit passes."] if framework_audit.get("status") != "pass" else []),
    }
    return {
        "version": "1.0",
        "semantics": "audited_method_writing_resources",
        "source": "external_executor",
        "dry_run": bool(summary.get("dry_run")),
        "mock_only": bool(summary.get("mock_only")),
        "use_realized_method_package": method_audit.get("status") == "pass",
        "use_framework_figure": framework_audit.get("status") == "pass",
        "contribution_drift": method_audit.get("contribution_drift", "unknown"),
        "method_writing_resources": wrapper,
        "realized_method_package": realized,
        "final_framework_figure": figure,
        "figure_table_inventory": evidence.get("figure_table_inventory") or {},
        "writer_handoff": evidence.get("writer_handoff") or {},
        "method_audit_ref": "experiments/method_audit.json",
        "framework_figure_audit_ref": "experiments/framework_figure_audit.json",
        "method_consistency_audit": method_consistency,
        "must_not_use": [
            "T5 method_intent as final Method",
            "external executor natural language without raw evidence",
        ]
        + (["mock/dry-run method or figure as paper evidence"] if summary.get("mock_only") else []),
    }


def _forbidden_wording(summary: dict[str, Any], baseline_missing: bool) -> list[str]:
    forbidden = ["proves"]
    if summary.get("mock_only"):
        forbidden.extend(["validated", "empirically validates", "state-of-the-art", "outperforms"])
    if baseline_missing:
        forbidden.extend(["outperforms prior work", "state-of-the-art", "strong empirical advantage"])
    return list(dict.fromkeys(forbidden))


def _claim_limitations(summary: dict[str, Any], baseline_missing: bool, coverage: dict[str, Any]) -> list[str]:
    limitations = []
    if summary.get("mock_only"):
        limitations.append("mock_only")
    if baseline_missing:
        limitations.append("required_baselines_missing:" + ",".join(coverage.get("missing_baselines", []) or []))
    return limitations


def _global_must_not_claim(summary: dict[str, Any], baseline_missing: bool, coverage: dict[str, Any]) -> list[str]:
    claims = []
    if summary.get("mock_only"):
        claims.append("Do not present dry-run/mock metrics as empirical evidence.")
    if baseline_missing:
        claims.append("Do not claim superiority over prior work until required baselines are covered.")
        for block in coverage.get("claim_blocks", []) or []:
            claims.append(f"Do not claim: {block}")
    return claims


def _format_must_not_claim(result: dict[str, Any]) -> str:
    lines = ["# Must Not Claim", ""]
    items = result.get("global_must_not_claim") or []
    if not items:
        lines.append("- No global claim block generated by deterministic audit.")
    else:
        lines.extend(f"- {item}" for item in items)
    return "\n".join(lines) + "\n"


def _format_claim_support_matrix(result: dict[str, Any]) -> str:
    rows = [["claim_id", "support_status", "claim_strength", "metric_refs", "evidence_refs", "limitations"]]
    for item in result.get("claim_mappings", []) or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            [
                str(item.get("claim_id") or ""),
                str(item.get("support_status") or ""),
                str(item.get("claim_strength") or ""),
                ";".join(str(x) for x in item.get("metric_refs", []) or []),
                ";".join(str(x) for x in item.get("evidence_refs", []) or []),
                ";".join(str(x) for x in item.get("limitations", []) or []),
            ]
        )
    return "\n".join(",".join(_csv_cell(cell) for cell in row) for row in rows) + "\n"


def _csv_cell(value: str) -> str:
    if any(ch in value for ch in [",", "\n", '"']):
        return '"' + value.replace('"', '""') + '"'
    return value


def _format_limitations_from_experiments(result: dict[str, Any]) -> str:
    lines = ["# Limitations From Experiments", ""]
    seen: set[str] = set()
    for item in result.get("claim_mappings", []) or []:
        if isinstance(item, dict):
            for limitation in item.get("limitations", []) or []:
                seen.add(str(limitation))
    if not seen:
        lines.append("- No deterministic limitation generated; still require reviewer inspection.")
    else:
        lines.extend(f"- {item}" for item in sorted(seen))
    return "\n".join(lines) + "\n"


class BuildExperimentHandoffPackParams(BaseModel):
    executor: Literal["UNSET", "mock_dry_run", "codex_cli", "claude_code_window", "manual"] = Field(
        default="UNSET",
        description="Initial executor mode. T5-EXECUTOR-GATE patches the real selection later.",
    )
    output_path: str = Field(default="external_executor/handoff_pack.json")
    prompt_output_path: str = Field(default="external_executor/executor_prompt.md")
    expected_schema_path: str = Field(default="external_executor/expected_outputs_schema.json")
    allowed_paths_path: str = Field(default="external_executor/allowed_paths.txt")
    executor_selection_path: str = Field(default="external_executor/executor_selection.json")
    input_manifest_path: str = Field(default="external_executor/input_manifest.json")
    codex_prompt_path: str = Field(default="external_executor/codex_prompt.md")
    claude_prompt_path: str = Field(default="external_executor/claude_code_prompt.md")
    manual_instructions_path: str = Field(default="external_executor/manual_instructions.md")
    specialize_skills: bool = Field(
        default=True,
        description=(
            "Whether to generate external_executor/skills in the same handoff call. "
            "T5-REBOOST uses false so specialize-executor-skills remains an explicit step."
        ),
    )


class SelectExternalExecutorParams(BaseModel):
    selected_executor: Literal["mock_dry_run", "codex_cli", "claude_code_window", "manual"] = Field(
        default="mock_dry_run",
        description="Executor selected by the T5-EXECUTOR-GATE human decision.",
    )
    executor_selection_path: str = Field(default="external_executor/executor_selection.json")
    selected_by: str = Field(default="human")
    notes: str = Field(default="")


class WaitForExternalExecutorResultParams(BaseModel):
    result_pack_path: str = Field(default="external_executor/result_pack.json")
    status_path: str = Field(default="external_executor/executor_status.json")
    output_path: str = Field(default="external_executor/wait_acceptance_report.json")
    allow_partial_results: bool = Field(
        default=False,
        description="默认不允许 PARTIAL_RESULTS_READY 进入 T7；只有显式打开时才接受部分结果。",
    )


class BuildPostExperimentNoveltyCheckParams(BaseModel):
    results_summary_path: str = Field(default="experiments/results_summary.json")
    integrity_audit_path: str = Field(default="experiments/integrity_audit.json")
    novelty_audit_path: str = Field(default="ideation/novelty_audit.md")
    required_baselines_path: str = Field(default="novelty/required_baselines.json")
    output_path: str = Field(default="novelty/post_experiment_novelty_check.json")
    collision_output_path: str = Field(default="novelty/post_experiment_collision_cases.md")


class MockExternalDryRunParams(BaseModel):
    handoff_pack_path: str = Field(default="external_executor/handoff_pack.json")
    output_path: str = Field(default="external_executor/result_pack.json")
    status_path: str = Field(default="external_executor/executor_status.json")


class IngestExternalResultsParams(BaseModel):
    result_pack_path: str = Field(default="external_executor/result_pack.json")
    status_path: str = Field(default="external_executor/executor_status.json")
    results_summary_path: str = Field(default="experiments/results_summary.json")
    run_records_path: str = Field(default="experiments/run_records.jsonl")
    evidence_index_path: str = Field(default="experiments/evidence_index.json")
    ingest_report_path: str = Field(default="experiments/ingest_report.json")


class AuditExperimentIntegrityParams(BaseModel):
    results_summary_path: str = Field(default="experiments/results_summary.json")
    evidence_index_path: str = Field(default="experiments/evidence_index.json")
    output_path: str = Field(default="experiments/integrity_audit.json")


class MapResultsToClaimsParams(BaseModel):
    results_summary_path: str = Field(default="experiments/results_summary.json")
    integrity_audit_path: str = Field(default="experiments/integrity_audit.json")
    output_path: str = Field(default="experiments/experimental_claims.json")
    draft_output_path: str = Field(default="drafts/result_to_claim.json")
    must_not_claim_path: str = Field(default="drafts/must_not_claim.md")
    claim_support_matrix_path: str = Field(default="drafts/claim_support_matrix.csv")
    limitations_path: str = Field(default="drafts/limitations_from_experiments.md")
    figure_table_evidence_map_path: str = Field(default="drafts/figure_table_evidence_map.json")


class BuildExperimentEvidencePackParams(BaseModel):
    results_summary_path: str = Field(default="experiments/results_summary.json")
    integrity_audit_path: str = Field(default="experiments/integrity_audit.json")
    experimental_claims_path: str = Field(default="experiments/experimental_claims.json")
    evidence_index_path: str = Field(default="experiments/evidence_index.json")
    output_path: str = Field(default="drafts/experiment_evidence_pack.json")


class AuditPaperClaimsParams(BaseModel):
    paper_path: str = Field(default="drafts/paper.tex")
    evidence_pack_path: str = Field(default="drafts/experiment_evidence_pack.json")
    result_to_claim_path: str = Field(default="drafts/result_to_claim.json")
    output_path: str = Field(default="drafts/paper_claim_audit.md")


class BuildExperimentHandoffPackTool(Tool):
    name = "build_experiment_handoff_pack"
    description = "Compile a protocol pack and executor prompt for external experiment execution."
    parameters_schema = BuildExperimentHandoffPackParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BuildExperimentHandoffPackParams(**kwargs)
        try:
            ws = self.policy.workspace_dir
            project = _read_yaml(ws / "project.yaml")
            exp_plan = _read_yaml(ws / "ideation" / "exp_plan.yaml")
            hypotheses = (ws / "ideation" / "hypotheses.md").read_text(encoding="utf-8", errors="replace") if (ws / "ideation" / "hypotheses.md").exists() else ""
            synthesis = _read_text(ws / "literature" / "synthesis.md", max_chars=12000)
            novelty_audit, novelty_source = _first_existing_text(
                ws,
                ["novelty/novelty_audit.md", "ideation/novelty_audit.md"],
                max_chars=12000,
            )
            risks = _read_text(ws / "ideation" / "risks.md", max_chars=6000)
            metrics = _extract_exp_plan_metrics(exp_plan)
            seeds = _extract_exp_plan_seeds(project)
            required_baselines = _extract_required_baselines(ws)
            if required_baselines:
                _write_json(
                    self.policy.resolve_write("novelty/required_baselines.json"),
                    {
                        "version": "1.0",
                        "semantics": "required_baselines_from_novelty_audit",
                        "source": "ideation/novelty_audit.md",
                        "required_baselines": required_baselines,
                    },
                )
            source_artifacts = _source_artifacts(ws)
            source_artifacts.append(_artifact_record(ws, "novelty/required_baselines.json", role="required_baselines"))
            context_reboost = _existing_context_reboost_for_handoff(ws) or _build_context_reboost(
                workspace=ws,
                project=project,
                exp_plan=exp_plan,
                hypotheses=hypotheses,
                synthesis=synthesis,
                novelty_audit=novelty_audit,
                novelty_source=novelty_source,
                risks=risks,
                required_baselines=required_baselines,
                metrics=metrics,
            )
            method_intent = _build_method_intent(
                hypotheses=hypotheses,
                exp_plan=exp_plan,
                context_reboost=context_reboost,
            )
            host_workspace = workspace_host_hint(ws)
            host_workdir = str(Path(host_workspace) / "external_executor" / "workdir") if host_workspace else ""
            handoff = {
                "version": "1.0",
                "schema_version": "external_executor_handoff.v1",
                "semantics": "external_experiment_handoff_contract",
                "legacy_semantics": "external_experiment_handoff_pack_not_execution_result",
                "created_at": _now_iso(),
                "executor": params.executor,
                "execution_mode": "unselected" if params.executor == "UNSET" else ("dry_run" if params.executor == "mock_dry_run" else "external"),
                "accepted": False,
                "status": "handoff_compiled",
                "project": {
                    "project_id": project.get("project_id") or project.get("name") or "unknown",
                    "target_venue": project.get("target_venue", ""),
                },
                "project_id": project.get("project_id") or project.get("name") or "unknown",
                "experiment_intent_oneliner": _infer_experiment_intent(project, hypotheses),
                "executor_special_notes": "Use structure and provenance from this contract; do not write paper claims.",
                "workspace_relative_workdir": "external_executor/workdir",
                "workspace_relative_prompt": "external_executor/codex_prompt.md",
                "host_workspace_hint": host_workspace,
                "host_workdir_hint": host_workdir,
                "context_reboost": context_reboost,
                "method_intent": method_intent,
                "baseline_matrix": context_reboost["baseline_matrix"],
                "claim_evidence_matrix": context_reboost["claim_evidence_matrix"],
                "experiment_contract": {
                    "metrics": metrics,
                    "seeds": seeds,
                    "experiments": exp_plan.get("experiments", []) if isinstance(exp_plan, dict) else [],
                    "required_baselines": required_baselines,
                    "acceptance": {
                        "must_write_result_pack": True,
                        "dry_run_results_must_be_marked_mock_only": True,
                    },
                },
                "metrics": [
                    {
                        "metric_id": f"metric_{idx}",
                        "name": name,
                        "direction": "unknown_requires_executor_or_llm_annotation",
                        "primary": idx == 1,
                    }
                    for idx, name in enumerate(metrics, start=1)
                ],
                "required_baselines": required_baselines,
                "seeds": seeds,
                "source_artifacts": source_artifacts,
                "executor_outputs": {
                    "result_pack": "external_executor/result_pack.json",
                    "status": "external_executor/executor_status.json",
                    "run_manifest": "external_executor/run_manifest.json",
                    "raw_results": "external_executor/raw_results/",
                    "configs": "external_executor/configs/",
                    "logs_dir": "external_executor/logs",
                },
                "allowed_paths": [
                    "rw  external_executor/workdir/",
                    "rw  external_executor/raw_results/",
                    "rw  external_executor/configs/",
                    "rw  external_executor/logs/",
                    "rw  external_executor/patches/",
                    "rw  external_executor/figures/",
                    "rw  external_executor/tables/",
                    "rw  external_executor/expr/",
                    "rw  external_executor/result_pack.json",
                    "rw  external_executor/executor_status.json",
                    "rw  external_executor/run_manifest.json",
                    "rw  external_executor/job_state.json",
                    "ro  external_executor/handoff_pack.json",
                    "ro  external_executor/expected_outputs_schema.json",
                    "ro  novelty/required_baselines.json",
                    "ro  resources/",
                    "ro  literature/",
                    "ro  ideation/",
                    "no  researchos/",
                    "no  config/",
                    "no  drafts/",
                    "no  submission/",
                    "no  _runtime/",
                ],
                "executor_outputs_contract": {
                    "must_write": [
                        "external_executor/result_pack.json",
                        "external_executor/executor_status.json",
                        "external_executor/run_manifest.json",
                        "external_executor/raw_results/",
                        "external_executor/configs/",
                        "external_executor/logs/",
                        "external_executor/figures/",
                        "external_executor/tables/",
                    ],
                    "result_pack_semantics": "external_executor_result_pack",
                    "required_fields": EXTERNAL_RESULT_REQUIRED_FIELDS,
                },
                "hypotheses_preview": hypotheses[:1200],
            }
            output_path = self.policy.resolve_write(params.output_path)
            _write_json(output_path, handoff)
            input_manifest = {
                "version": "1.0",
                "semantics": "external_executor_input_manifest",
                "handoff_pack": params.output_path,
                "source_artifacts": source_artifacts,
                "required_executor_outputs": handoff["executor_outputs"],
            }
            _write_json(self.policy.resolve_write(params.input_manifest_path), input_manifest)
            placeholder_next_state = "T5-SKILL-CUSTOMIZATION-GATE" if params.specialize_skills else "T5-EXECUTOR-GATE"
            placeholder_notes = (
                "Execution mode is intentionally UNSET until T5-EXECUTOR-GATE; "
                "next step is T5-SKILL-CUSTOMIZATION-GATE specialization report review."
                if params.specialize_skills
                else (
                    "Execution mode is intentionally UNSET until T5-EXECUTOR-GATE; "
                    "run researchos specialize-executor-skills before selecting Codex or another executor."
                )
            )
            executor_selection = {
                "version": "1.0",
                "semantics": "external_executor_selection",
                "selected_executor": params.executor,
                "real_experiment_allowed": False,
                "requires_user_copy_paste": False,
                "selected_by": "system_placeholder",
                "selected_at": None,
                "next_state": placeholder_next_state,
                "fallback_order": ["mock_dry_run", "claude_code_window", "manual"],
                "notes": placeholder_notes,
            }
            _write_json(self.policy.resolve_write(params.executor_selection_path), executor_selection)
            _write_json(self.policy.resolve_write(params.expected_schema_path), _build_expected_outputs_schema())
            _write_text(
                self.policy.resolve_write(params.allowed_paths_path),
                "\n".join(handoff["allowed_paths"]) + "\n",
            )
            _write_external_executor_guides(self.policy, handoff, selection=executor_selection)
            specialization_status = "skipped"
            if params.specialize_skills:
                specialization = specialize_project_skills(workspace=ws)
                specialization_status = specialization.status
                if specialization.status == "failed":
                    raise RuntimeError(
                        "project skill specialization failed; see external_executor/skill_specialization_report.json"
                    )
            _write_expr_materials_scaffold(self.policy, handoff)
            prompt = _render_executor_prompt(handoff, executor=params.executor)
            _write_text(self.policy.resolve_write(params.prompt_output_path), prompt)
            _write_text(self.policy.resolve_write(params.codex_prompt_path), _render_executor_prompt(handoff, executor="codex_cli"))
            _write_text(self.policy.resolve_write(params.claude_prompt_path), _render_executor_prompt(handoff, executor="claude_code_window"))
            _write_text(self.policy.resolve_write(params.manual_instructions_path), _render_manual_instructions(handoff))
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"handoff pack failed: {exc}", error="handoff_failed")
        return ToolResult(
            ok=True,
            content=f"Wrote external experiment handoff pack to {params.output_path}.",
            data={
                "path": params.output_path,
                "executor": params.executor,
                "specialize_skills": params.specialize_skills,
                "skill_specialization_status": specialization_status,
            },
        )


class SelectExternalExecutorTool(Tool):
    name = "select_external_executor"
    description = "Persist the T5 executor gate selection and patch external executor instructions."
    parameters_schema = SelectExternalExecutorParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = SelectExternalExecutorParams(**kwargs)
        try:
            selection = build_executor_selection_payload(
                selected_executor=params.selected_executor,
                selected_by=params.selected_by,
                notes=params.notes,
            )
            _write_json(self.policy.resolve_write(params.executor_selection_path), selection)
            patch_external_executor_files_with_selection(self.policy.workspace_dir, selection)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"executor selection failed: {exc}", error="executor_selection_failed")
        return ToolResult(ok=True, content=f"Selected executor {params.selected_executor}.", data=selection)


class WaitForExternalExecutorResultTool(Tool):
    name = "wait_for_external_executor_result"
    description = "Validate that an external executor result pack exists before T7 ingest."
    parameters_schema = WaitForExternalExecutorResultParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = WaitForExternalExecutorResultParams(**kwargs)
        try:
            report = validate_external_executor_ready(
                self.policy.workspace_dir,
                params.result_pack_path,
                params.status_path,
                allow_partial_results=params.allow_partial_results,
            )
            if not report["ok"]:
                return ToolResult(ok=False, content=report["message"], error="external_executor_not_ready", data=report)
            _write_json(self.policy.resolve_write(params.output_path), report)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"external wait failed: {exc}", error="external_wait_failed")
        return ToolResult(ok=True, content=report["message"], data=report)


class BuildPostExperimentNoveltyCheckTool(Tool):
    name = "build_post_experiment_novelty_check"
    description = "Build a conservative post-experiment novelty/collision check artifact for T7-POST-NOVELTY."
    parameters_schema = BuildPostExperimentNoveltyCheckParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BuildPostExperimentNoveltyCheckParams(**kwargs)
        try:
            summary = _read_json(self.policy.resolve_read(params.results_summary_path))
            audit = _read_json(self.policy.resolve_read(params.integrity_audit_path))
            required = _read_json(self.policy.workspace_dir / params.required_baselines_path)
            baseline_status = ((audit.get("required_baseline_coverage") or {}).get("status") or "unknown")
            claim_downgrades: list[str] = []
            if summary.get("mock_only"):
                claim_downgrades.append("mock_only_results_cannot_support_empirical_novelty")
            if audit.get("status") == "fail":
                claim_downgrades.append("integrity_audit_failed")
            if baseline_status in {"missing", "incomplete"}:
                claim_downgrades.append("required_baselines_missing_or_incomplete")
            contribution_drift = str(audit.get("contribution_drift") or (audit.get("method_audit") or {}).get("contribution_drift") or "unknown")
            if contribution_drift == "major":
                claim_downgrades.append("major_contribution_drift_requires_human_review")
            elif contribution_drift == "minor":
                claim_downgrades.append("minor_contribution_drift_requires_method_update")
            if contribution_drift == "major":
                required_action = "human_review"
            elif baseline_status in {"missing", "incomplete"}:
                required_action = "narrow_claim"
            elif audit.get("status") == "fail":
                required_action = "rerun_experiment"
            elif contribution_drift == "minor":
                required_action = "update_method"
            else:
                required_action = "none"
            novelty_after = "weak" if claim_downgrades else ("moderate" if audit.get("status") == "pass" else "collision_risk")
            check = {
                "version": "1.0",
                "semantics": "post_experiment_novelty_check",
                "implementation_matches_original_idea": "partial" if summary.get("mock_only") else "unknown_requires_llm_review",
                "novelty_after_implementation": novelty_after,
                "contribution_drift": contribution_drift,
                "required_action": required_action,
                "method_audit_ref": "experiments/method_audit.json",
                "framework_figure_audit_ref": "experiments/framework_figure_audit.json",
                "collision_risks": [],
                "claim_downgrades_required": claim_downgrades,
                "additional_baselines_required": (audit.get("required_baseline_coverage") or {}).get("missing_baselines", []),
                "required_baselines": required.get("required_baselines", []),
                "recommended_next_task": "T7-CLAIMS",
                "notes": (
                    "This tool performs deterministic evidence-status checks only. "
                    "LLM novelty interpretation should read this artifact before T7.5/T8."
                ),
            }
            _write_json(self.policy.resolve_write(params.output_path), check)
            collision_lines = [
                "# Post-Experiment Collision Cases",
                "",
                "No deterministic collision case was proven by this tool.",
                "Review implementation diffs, baseline reuse, and novelty_audit.md before strong claims.",
                "",
                "## Claim Downgrades",
            ]
            collision_lines.extend(f"- {item}" for item in claim_downgrades)
            _write_text(self.policy.resolve_write(params.collision_output_path), "\n".join(collision_lines) + "\n")
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"post novelty check failed: {exc}", error="post_novelty_failed")
        return ToolResult(ok=True, content=f"Wrote post-experiment novelty check to {params.output_path}.", data=check)


class MockExternalDryRunTool(Tool):
    name = "mock_external_dry_run"
    description = "Generate a schema-compatible mock external result pack without running real experiments."
    parameters_schema = MockExternalDryRunParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = MockExternalDryRunParams(**kwargs)
        try:
            ws = self.policy.workspace_dir
            handoff_path = self.policy.resolve_read(params.handoff_pack_path)
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            selection = _read_json(ws / "external_executor" / "executor_selection.json")
            executor_type = str(selection.get("selected_executor") or handoff.get("executor") or "mock_dry_run")
            if executor_type == "UNSET":
                executor_type = "mock_dry_run"
            raw_result_rel = "external_executor/raw_results/mock_results.json"
            config_rel = "external_executor/configs/mock_config.json"
            log_rel = "external_executor/logs/mock_dry_run.log"
            manifest_rel = "external_executor/run_manifest.json"
            heartbeat_rel = "external_executor/heartbeat.json"
            raw_result = {
                "version": "1.0",
                "semantics": "mock_raw_result_file_not_scientific_evidence",
                "run_id": "mock_dry_run",
                "metrics": [],
            }
            metrics = []
            for idx, name in enumerate((handoff.get("experiment_contract") or {}).get("metrics", []) or ["task_score"], start=1):
                metric = (
                    {
                        "metric_id": f"mock_metric_{idx}",
                        "experiment_id": "mock_dry_run",
                        "name": str(name),
                        "value": round(0.7 + idx * 0.01, 4),
                        "unit": "score",
                        "dataset": "mock_dataset",
                        "dataset_split": "mock_split",
                        "seed": ((handoff.get("experiment_contract") or {}).get("seeds") or [42])[0],
                        "metric_direction": "higher_is_better",
                        "source_artifact": raw_result_rel,
                        "config": config_rel,
                        "log": log_rel,
                        "mock_only": True,
                    }
                )
                metrics.append(metric)
                raw_result["metrics"].append(metric)
            _write_json(self.policy.resolve_write(raw_result_rel), raw_result)
            config = {
                "version": "1.0",
                "semantics": "mock_external_executor_config",
                "run_id": "mock_dry_run",
                "executor": executor_type,
                "seeds": (handoff.get("experiment_contract") or {}).get("seeds") or [42],
                "mock_only": True,
            }
            _write_json(self.policy.resolve_write(config_rel), config)
            log_path = self.policy.resolve_write(log_rel)
            _write_text(log_path, "mock dry-run completed; no real experiment executed\n")
            heartbeat = {
                "version": "1.0",
                "semantics": "external_executor_heartbeat",
                "run_id": "mock_dry_run",
                "status": "done",
                "dry_run": True,
                "mock_only": True,
            }
            _write_json(self.policy.resolve_write(heartbeat_rel), heartbeat)
            artifacts = [
                _artifact_record(ws, raw_result_rel, role="mock_raw_results", kind="raw_results"),
                _artifact_record(ws, config_rel, role="mock_config", kind="config"),
                _artifact_record(ws, log_rel, role="mock_log", kind="log"),
            ]
            required_baselines = (handoff.get("experiment_contract") or {}).get("required_baselines", []) or handoff.get("required_baselines", []) or []
            baseline_coverage = _baseline_coverage_from_metrics(required_baselines, metrics, mock_only=True)
            experiment_runs = [
                {
                    "run_id": "mock_dry_run",
                    "run_type": "smoke",
                    "status": "completed",
                    "dry_run": True,
                    "mock_only": True,
                    "dataset": "mock_dataset",
                    "seed": ((handoff.get("experiment_contract") or {}).get("seeds") or [42])[0],
                    "metrics": [metric.get("metric_id") for metric in metrics],
                    "raw_result_refs": [raw_result_rel],
                    "config_refs": [config_rel],
                    "log_refs": [log_rel],
                }
            ]
            context_alignment = {
                "status": "pass",
                "source_files_checked": (handoff.get("context_reboost") or {}).get("source_files_used", []),
                "mismatches": (handoff.get("context_reboost") or {}).get("known_context_mismatches", []),
                "resolution": ["mock_dry_run checks schema only; no scientific evidence produced"],
            }
            resources = {
                "status": "mock_only",
                "expr_dir": "external_executor/expr",
                "resources_checked": [],
                "baseline_candidates": [],
                "notes": "Mock dry-run does not mine real resources.",
            }
            baseline_reproduction = [
                {
                    "baseline_name": _baseline_name(item) or "no_required_baseline",
                    "status": "mock_only",
                    "command": None,
                    "config": config_rel,
                    "raw_log_path": log_rel,
                    "result": None,
                    "failure_reason": "mock dry-run only",
                    "claim_risk": "not publishable evidence",
                }
                for item in (required_baselines or [{"baseline_name": "no_required_baseline"}])
            ]
            result_diagnosis = {
                "status": "mock_only",
                "summary": "Protocol dry-run completed; no empirical diagnosis.",
                "failure_modes": [],
                "baseline_strengths": [],
                "claim_risks": ["mock_only"],
            }
            module_attribution = {
                "status": "mock_only",
                "modules": [
                    {
                        "module_id": module.get("module_id"),
                        "name": module.get("name"),
                        "evidence_level": "unsupported",
                        "attribution_summary": "Mock dry-run cannot attribute module effects.",
                    }
                    for module in ((handoff.get("method_intent") or {}).get("candidate_modules", []) or [])
                ],
            }
            realized_method_package = {
                "status": "mock_only",
                "source": "mock_external_dry_run",
                "method_summary": "No realized method; this is a schema-only dry-run.",
                "modules": [],
                "algorithm_flow": [],
                "implementation_refs": [],
                "not_final_method_source": True,
            }
            final_framework_figure = {
                "status": "mock_only",
                "figure_id": "fig:mock_framework",
                "path": None,
                "caption_draft": "Mock dry-run placeholder; not usable by T8.",
                "evidence_level": "unsupported",
                "consistent_with_realized_method": False,
            }
            figure_table_inventory = {
                "status": "mock_only",
                "figures": [],
                "tables": [],
                "notes": "No publishable figures or tables produced by mock dry-run.",
            }
            writer_handoff = {
                "status": "mock_only",
                "method_package_ref": "result_pack.realized_method_package",
                "result_diagnosis_ref": "result_pack.result_diagnosis",
                "figure_table_inventory_ref": "result_pack.figure_table_inventory",
                "must_not_claim": ["Do not use mock dry-run outputs as empirical evidence."],
            }
            run_manifest = {
                "version": "1.0",
                "semantics": "external_executor_run_manifest",
                "run_id": "mock_dry_run",
                "executor": executor_type,
                "dry_run": True,
                "mock_only": True,
                "raw_results": [raw_result_rel],
                "configs": [config_rel],
                "logs": [log_rel],
                "artifacts": artifacts,
                "runs": experiment_runs,
            }
            _write_json(self.policy.resolve_write(manifest_rel), run_manifest)
            result_pack = {
                "version": "1.0",
                "schema_version": "external_executor_result_pack.v1",
                "semantics": "external_executor_result_pack",
                "run_id": "mock_dry_run",
                "executor": executor_type,
                "dry_run": True,
                "mock_only": True,
                "executor_status": "completed",
                "context_alignment": context_alignment,
                "resources": resources,
                "baseline_reproduction": baseline_reproduction,
                "experiment_runs": experiment_runs,
                "runs": experiment_runs,
                "evidence_grade": "mock_only",
                "metrics": metrics,
                "artifacts": artifacts,
                "baseline_coverage": baseline_coverage,
                "result_diagnosis": result_diagnosis,
                "module_attribution": module_attribution,
                "realized_method_package": realized_method_package,
                "final_framework_figure": final_framework_figure,
                "figure_table_inventory": figure_table_inventory,
                "writer_handoff": writer_handoff,
                "run_manifest": manifest_rel,
                "raw_result_files": [raw_result_rel],
                "config_files": [config_rel],
                "log_files": [log_rel],
                "logs": [{"path": log_rel, "level": "info"}],
                "limitations": ["mock_dry_run: not evidence for paper claims"],
            }
            _write_json(self.policy.resolve_write(params.output_path), result_pack)
            status = {
                "version": "1.0",
                "semantics": "external_executor_status",
                "run_id": "mock_dry_run",
                "executor": executor_type,
                "status": "done",
                "accepted": False,
                "dry_run": True,
                "mock_only": True,
                "heartbeat": heartbeat_rel,
                "run_manifest": manifest_rel,
            }
            _write_json(self.policy.resolve_write(params.status_path), status)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"mock dry-run failed: {exc}", error="mock_dry_run_failed")
        return ToolResult(ok=True, content=f"Wrote mock result pack to {params.output_path}.", data={"path": params.output_path})


class IngestExternalResultsTool(Tool):
    name = "ingest_external_results"
    description = "Normalize an external executor result pack into ResearchOS result artifacts."
    parameters_schema = IngestExternalResultsParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = IngestExternalResultsParams(**kwargs)
        try:
            readiness = validate_external_executor_ready(
                self.policy.workspace_dir,
                params.result_pack_path,
                params.status_path,
            )
            if not readiness.get("ok"):
                return ToolResult(
                    ok=False,
                    content=str(readiness.get("message") or "external executor result is not ready"),
                    error="external_result_not_ready",
                    data=readiness,
                )
            result_pack_path = self.policy.resolve_read(params.result_pack_path)
            result_pack = json.loads(result_pack_path.read_text(encoding="utf-8"))
            if result_pack.get("semantics") != "external_executor_result_pack":
                return ToolResult(ok=False, content="result_pack semantics invalid", error="invalid_result_pack")
            metrics = result_pack.get("metrics")
            if not isinstance(metrics, list) or not metrics:
                return ToolResult(ok=False, content="result_pack metrics missing", error="missing_metrics")
            manifest_rel = str(result_pack.get("run_manifest") or "external_executor/run_manifest.json")
            manifest = _read_json(self.policy.workspace_dir / manifest_rel)
            scanned_artifacts = _scan_external_artifacts(self.policy.workspace_dir)
            declared_artifacts = [item for item in result_pack.get("artifacts", []) or [] if isinstance(item, dict)]
            manifest_artifacts = [item for item in manifest.get("artifacts", []) or [] if isinstance(item, dict)]
            scanned_flat = [item for group in scanned_artifacts.values() for item in group]
            all_artifacts = _merge_artifact_records(declared_artifacts, manifest_artifacts, scanned_flat)
            experiments = [
                {
                    "experiment_id": str(metric.get("experiment_id") or result_pack.get("run_id") or "external_run"),
                    "metrics": {str(metric.get("name")): metric.get("value")},
                    "seed": metric.get("seed"),
                    "source_artifact": metric.get("source_artifact"),
                    "mock_only": bool(metric.get("mock_only") or result_pack.get("mock_only")),
                    "baseline_id": metric.get("baseline_id"),
                    "method_role": metric.get("method_role"),
                }
                for metric in metrics
                if isinstance(metric, dict) and metric.get("name") and metric.get("value") is not None
            ]
            summary = {
                "version": "1.0",
                "semantics": "external_executor_results_summary",
                "source": "external_executor",
                "run_id": result_pack.get("run_id"),
                "selected_executor": readiness.get("selected_executor") or result_pack.get("executor"),
                "executor_selection_ref": readiness.get("executor_selection"),
                "result_pack_ref": params.result_pack_path,
                "executor_status_ref": params.status_path,
                "selection_sha256": readiness.get("selection_sha256"),
                "result_pack_sha256": readiness.get("result_pack_sha256"),
                "executor_status_sha256": readiness.get("executor_status_sha256"),
                "dry_run": bool(result_pack.get("dry_run")),
                "mock_only": bool(result_pack.get("mock_only")),
                "evidence_grade": str(result_pack.get("evidence_grade") or ("mock_only" if result_pack.get("mock_only") else "external_unverified")),
                "ingest_report_ref": params.ingest_report_path,
                "experiments": experiments,
                "metrics": _metrics_object(metrics),
                "metric_records": metrics,
                "experiment_runs": result_pack.get("experiment_runs") or result_pack.get("runs") or [],
                "run_manifest": manifest_rel,
                "baseline_coverage": result_pack.get("baseline_coverage") or {},
                "context_alignment": result_pack.get("context_alignment") or {},
                "result_diagnosis": result_pack.get("result_diagnosis") or {},
                "module_attribution": result_pack.get("module_attribution") or {},
                "realized_method_package": result_pack.get("realized_method_package") or {},
                "final_framework_figure": result_pack.get("final_framework_figure") or {},
                "figure_table_inventory": result_pack.get("figure_table_inventory") or {},
                "writer_handoff": result_pack.get("writer_handoff") or {},
                "quality_status": "mock_only" if result_pack.get("mock_only") else "ingested_unverified",
            }
            _write_json(self.policy.resolve_write(params.results_summary_path), summary)
            run_records = self.policy.resolve_write(params.run_records_path)
            run_records.parent.mkdir(parents=True, exist_ok=True)
            run_records.write_text(
                "\n".join(
                    json.dumps(record, ensure_ascii=False)
                    for record in _run_records_from_result_pack(result_pack, manifest)
                )
                + "\n",
                encoding="utf-8",
            )
            evidence_index = {
                "version": "1.0",
                "semantics": "external_experiment_evidence_index",
                "result_pack": params.result_pack_path,
                "run_manifest": manifest_rel,
                "executor_selection_ref": readiness.get("executor_selection"),
                "result_pack_ref": params.result_pack_path,
                "executor_status_ref": params.status_path,
                "selection_sha256": readiness.get("selection_sha256"),
                "result_pack_sha256": readiness.get("result_pack_sha256"),
                "executor_status_sha256": readiness.get("executor_status_sha256"),
                "metrics": metrics,
                "baseline_coverage": result_pack.get("baseline_coverage") or {},
                "artifacts": all_artifacts,
                "logs": result_pack.get("logs", []),
                "raw_result_files": list(
                    dict.fromkeys(
                        _coerce_str_list(result_pack.get("raw_result_files"))
                        + _coerce_str_list(manifest.get("raw_results"))
                        + _artifact_paths(scanned_artifacts["raw_results"])
                    )
                ),
                "config_files": list(
                    dict.fromkeys(
                        _coerce_str_list(result_pack.get("config_files"))
                        + _coerce_str_list(manifest.get("configs"))
                        + _artifact_paths(scanned_artifacts["configs"])
                    )
                ),
                "log_files": list(
                    dict.fromkeys(
                        _coerce_str_list(result_pack.get("log_files"))
                        + _coerce_str_list(manifest.get("logs"))
                        + _artifact_paths(scanned_artifacts["logs"])
                    )
                ),
                "patch_files": _artifact_paths(scanned_artifacts["patches"]),
                "figure_files": _artifact_paths(scanned_artifacts["figures"]),
                "table_files": _artifact_paths(scanned_artifacts["tables"]),
                "scanned_artifacts": scanned_artifacts,
                "experiment_runs": result_pack.get("experiment_runs") or result_pack.get("runs") or [],
                "baseline_reproduction": result_pack.get("baseline_reproduction") or [],
                "resources": result_pack.get("resources") or {},
                "result_diagnosis": result_pack.get("result_diagnosis") or {},
                "module_attribution": result_pack.get("module_attribution") or {},
                "realized_method_package": result_pack.get("realized_method_package") or {},
                "final_framework_figure": result_pack.get("final_framework_figure") or {},
                "figure_table_inventory": result_pack.get("figure_table_inventory") or {},
                "writer_handoff": result_pack.get("writer_handoff") or {},
                "extra_fields": _result_pack_extra_fields(result_pack),
            }
            _write_json(self.policy.resolve_write(params.evidence_index_path), evidence_index)
            report = {
                "version": "1.0",
                "semantics": "external_result_ingest_report",
                "ok": True,
                "dry_run": bool(result_pack.get("dry_run")),
                "mock_only": bool(result_pack.get("mock_only")),
                "selected_executor": summary["selected_executor"],
                "executor_selection_ref": readiness.get("executor_selection"),
                "result_pack_ref": params.result_pack_path,
                "executor_status_ref": params.status_path,
                "selection_sha256": readiness.get("selection_sha256"),
                "result_pack_sha256": readiness.get("result_pack_sha256"),
                "executor_status_sha256": readiness.get("executor_status_sha256"),
                "evidence_grade": summary["evidence_grade"],
                "metric_count": len(metrics),
                "artifact_count": len(all_artifacts),
                "run_record_count": max(0, len(_run_records_from_result_pack(result_pack, manifest)) - 1),
                "method_package_present": bool(result_pack.get("realized_method_package")),
                "framework_figure_present": bool(result_pack.get("final_framework_figure")),
                "results_summary": params.results_summary_path,
            }
            _write_json(self.policy.resolve_write(params.ingest_report_path), report)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"ingest failed: {exc}", error="ingest_failed")
        return ToolResult(ok=True, content=f"Ingested result pack to {params.results_summary_path}.", data=report)


class AuditExperimentIntegrityTool(Tool):
    name = "audit_experiment_integrity"
    description = "Audit ingested external results for provenance, dry-run status, seed and metric issues."
    parameters_schema = AuditExperimentIntegrityParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = AuditExperimentIntegrityParams(**kwargs)
        try:
            summary = _read_json(self.policy.resolve_read(params.results_summary_path))
            evidence = _read_json(self.policy.resolve_read(params.evidence_index_path))
            issues: list[dict[str, Any]] = []
            binding_payload = dict(summary)
            for key in (
                "executor_selection_ref",
                "result_pack_ref",
                "executor_status_ref",
                "selection_sha256",
                "result_pack_sha256",
                "executor_status_sha256",
            ):
                if not binding_payload.get(key):
                    binding_payload[key] = evidence.get(key)
            for issue in _external_binding_fingerprint_issues(self.policy.workspace_dir, binding_payload):
                issues.append({"level": "FAIL", "code": "external_binding_fingerprint", "detail": issue})
            if summary.get("mock_only"):
                issues.append({"level": "WARN", "code": "mock_only", "detail": "Dry-run result is not publishable evidence."})
            metric_records = _summary_metric_records(summary)
            if not metric_records:
                issues.append({"level": "FAIL", "code": "missing_metrics", "detail": "No metrics in results summary."})
            evidence_artifacts = [
                item for item in (evidence.get("artifacts", []) or []) if isinstance(item, dict)
            ]
            artifact_by_path = {str(item.get("path")): item for item in evidence_artifacts if item.get("path")}
            seen_metric_ids: set[str] = set()
            for metric in metric_records:
                if not isinstance(metric, dict):
                    continue
                metric_id = str(metric.get("metric_id") or "")
                if metric_id in seen_metric_ids:
                    issues.append({"level": "WARN", "code": "duplicate_metric_id", "detail": metric_id})
                seen_metric_ids.add(metric_id)
                value = metric.get("value")
                if not isinstance(value, (int, float)):
                    issues.append({"level": "FAIL", "code": "non_numeric_metric", "detail": metric_id})
                source_artifact = str(metric.get("source_artifact") or "")
                if not source_artifact:
                    issues.append({"level": "FAIL", "code": "missing_source_artifact", "detail": metric_id})
                elif source_artifact not in artifact_by_path:
                    issues.append({"level": "FAIL", "code": "metric_source_not_indexed", "detail": f"{metric_id}: {source_artifact}"})
            for artifact in evidence_artifacts:
                rel_path = str(artifact.get("path") or "")
                if not rel_path:
                    issues.append({"level": "FAIL", "code": "artifact_missing_path", "detail": str(artifact)})
                    continue
                path = self.policy.workspace_dir / rel_path
                if not path.exists():
                    issues.append({"level": "FAIL", "code": "artifact_missing_on_disk", "detail": rel_path})
                    continue
                expected_hash = artifact.get("sha256")
                if expected_hash and path.is_file() and expected_hash != _sha256(path):
                    issues.append({"level": "FAIL", "code": "artifact_hash_mismatch", "detail": rel_path})
            manifest_rel = evidence.get("run_manifest")
            if manifest_rel:
                manifest_path = self.policy.workspace_dir / str(manifest_rel)
                if not manifest_path.exists():
                    issues.append({"level": "FAIL", "code": "missing_run_manifest", "detail": str(manifest_rel)})
                else:
                    manifest = _read_json(manifest_path)
                    if manifest.get("semantics") != "external_executor_run_manifest":
                        issues.append({"level": "FAIL", "code": "run_manifest_semantics", "detail": str(manifest_rel)})
            required_baselines = _extract_required_baselines(self.policy.workspace_dir)
            baseline_coverage = _baseline_coverage_from_metrics(
                required_baselines,
                metric_records,
                mock_only=bool(summary.get("mock_only")),
                existing=summary.get("baseline_coverage") if isinstance(summary.get("baseline_coverage"), dict) else None,
            )
            if baseline_coverage.get("status") in {"missing", "incomplete"}:
                issues.append(
                    {
                        "level": "FAIL" if not summary.get("mock_only") else "WARN",
                        "code": "required_baseline_coverage",
                        "detail": ", ".join(baseline_coverage.get("missing_baselines", []) or []),
                    }
                )
            result_audit = _build_result_audit(
                workspace=self.policy.workspace_dir,
                summary=summary,
                evidence=evidence,
                baseline_coverage=baseline_coverage,
            )
            for item in (result_audit.get("metric_provenance") or {}).get("issues", []) or []:
                if isinstance(item, dict):
                    issues.append({"level": item.get("level", "WARN"), "code": "result_audit:" + str(item.get("code") or "issue"), "detail": item.get("metric_id") or item.get("path") or item.get("id")})
            for item in (result_audit.get("result_figure_provenance") or {}).get("issues", []) or []:
                if isinstance(item, dict):
                    issues.append({"level": item.get("level", "WARN"), "code": "result_audit:" + str(item.get("code") or "issue"), "detail": item.get("id") or item.get("path")})
            method_audit, framework_figure_audit = _build_method_and_figure_audits(
                workspace=self.policy.workspace_dir,
                summary=summary,
                evidence=evidence,
            )
            for item in method_audit.get("issues", []) or []:
                if isinstance(item, dict):
                    issues.append({"level": item.get("level", "WARN"), "code": "method_audit:" + str(item.get("code") or "issue"), "detail": item.get("detail")})
            for item in framework_figure_audit.get("issues", []) or []:
                if isinstance(item, dict):
                    issues.append({"level": item.get("level", "WARN"), "code": "framework_figure_audit:" + str(item.get("code") or "issue"), "detail": item.get("detail")})
            audit = {
                "version": "1.0",
                "semantics": "external_experiment_integrity_audit",
                "status": "fail" if any(item["level"] == "FAIL" for item in issues) else ("mock_only" if summary.get("mock_only") else "pass"),
                "dry_run": bool(summary.get("dry_run")),
                "mock_only": bool(summary.get("mock_only")),
                "evidence_grade": str(summary.get("evidence_grade") or ("mock_only" if summary.get("mock_only") else "audited_external")),
                "issues": issues,
                "evidence_index": params.evidence_index_path,
                "selected_executor": summary.get("selected_executor"),
                "executor_selection_ref": binding_payload.get("executor_selection_ref"),
                "result_pack_ref": binding_payload.get("result_pack_ref"),
                "executor_status_ref": binding_payload.get("executor_status_ref"),
                "selection_sha256": binding_payload.get("selection_sha256"),
                "result_pack_sha256": binding_payload.get("result_pack_sha256"),
                "executor_status_sha256": binding_payload.get("executor_status_sha256"),
                "artifact_count": len(evidence.get("artifacts", []) or []),
                "checked_artifacts": len(evidence_artifacts),
                "required_baseline_coverage": baseline_coverage,
                "result_audit": result_audit,
                "method_audit": method_audit,
                "framework_figure_audit": framework_figure_audit,
                "contribution_drift": method_audit.get("contribution_drift", "unknown"),
            }
            _write_json(self.policy.resolve_write(params.output_path), audit)
            _write_json(self.policy.resolve_write("experiments/result_audit.json"), result_audit)
            _write_json(self.policy.resolve_write("experiments/method_audit.json"), method_audit)
            _write_json(self.policy.resolve_write("experiments/framework_figure_audit.json"), framework_figure_audit)
            _write_json(
                self.policy.resolve_write("drafts/method_writing_resources.json"),
                _format_method_writing_resources(summary=summary, evidence=evidence, audit=audit),
            )
            _write_text(
                self.policy.resolve_write("experiments/experiment_fairness_review.md"),
                _format_fairness_review(audit),
            )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"integrity audit failed: {exc}", error="integrity_audit_failed")
        return ToolResult(ok=True, content=f"Wrote experiment integrity audit to {params.output_path}.", data=audit)


class MapResultsToClaimsTool(Tool):
    name = "map_results_to_claims"
    description = "Map audited result metrics to conservative experimental claims for T8 writing."
    parameters_schema = MapResultsToClaimsParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = MapResultsToClaimsParams(**kwargs)
        try:
            summary = _read_json(self.policy.resolve_read(params.results_summary_path))
            audit = _read_json(self.policy.resolve_read(params.integrity_audit_path))
            mappings = []
            claims = []
            baseline_coverage = audit.get("required_baseline_coverage") if isinstance(audit.get("required_baseline_coverage"), dict) else {}
            baseline_missing = baseline_coverage.get("status") in {"missing", "incomplete"}
            method_audit = audit.get("method_audit") if isinstance(audit.get("method_audit"), dict) else {}
            framework_figure_audit = audit.get("framework_figure_audit") if isinstance(audit.get("framework_figure_audit"), dict) else {}
            contribution_drift = str(audit.get("contribution_drift") or method_audit.get("contribution_drift") or "unknown")
            method_blocked = method_audit.get("status") in {"fail", "mock_only"} or contribution_drift == "major"
            result_audit = audit.get("result_audit") if isinstance(audit.get("result_audit"), dict) else {}
            metric_provenance = result_audit.get("metric_provenance") if isinstance(result_audit.get("metric_provenance"), dict) else {}
            audited_metric_ids = {
                str(item)
                for item in metric_provenance.get("audited_metric_ids", []) or []
                if item
            }
            result_audit_pass = result_audit.get("status") == "pass"
            excluded_metric_ids: list[str] = []
            for metric in _summary_metric_records(summary):
                if not isinstance(metric, dict):
                    continue
                metric_id = str(metric.get("metric_id") or metric.get("name") or "")
                metric_audited = result_audit_pass and metric_id in audited_metric_ids
                if not metric_audited and not summary.get("mock_only"):
                    excluded_metric_ids.append(metric_id)
                    continue
                status = "unsupported_mock_only" if summary.get("mock_only") else ("supported" if audit.get("status") == "pass" and not baseline_missing and not method_blocked and metric_audited else "weak")
                claim_strength = "unsupported" if summary.get("mock_only") else ("strong" if status == "supported" else "weak")
                if baseline_missing and claim_strength == "strong":
                    claim_strength = "weak"
                if method_blocked and claim_strength in {"strong", "moderate"}:
                    claim_strength = "weak"
                claim_id = f"claim_{metric_id or len(mappings)+1}"
                mappings.append(
                    {
                        "claim_id": claim_id,
                        "support_status": status,
                        "claim_strength": claim_strength,
                        "metric_refs": [metric_id],
                        "evidence_refs": [metric.get("source_artifact")],
                        "allowed_wording": (
                            "Dry-run only; do not use as a paper result."
                            if summary.get("mock_only")
                            else f"Reports {metric.get('name')}={metric.get('value')} under audited external execution."
                        ),
                        "forbidden_wording": _forbidden_wording(summary, baseline_missing),
                        "limitations": _claim_limitations(summary, baseline_missing, baseline_coverage)
                        + ([] if metric_audited else ["metric_not_passed_result_audit"])
                        + (["method_audit_not_pass"] if method_blocked else [])
                        + ([f"contribution_drift:{contribution_drift}"] if contribution_drift in {"minor", "major"} else []),
                    }
                )
                claims.append(
                    {
                        "claim_id": claim_id,
                        "claim_text_conservative": (
                            "Protocol-only dry-run result; not empirical evidence."
                            if summary.get("mock_only")
                            else f"Audited external result reports {metric.get('name')}={metric.get('value')}."
                        ),
                        "claim_strength": claim_strength,
                        "supported_by": [metric_id, metric.get("source_artifact")],
                        "blocked_by": baseline_coverage.get("claim_blocks", []) if baseline_missing else [],
                        "must_not_say": _forbidden_wording(summary, baseline_missing),
                        "paper_sections": ["experiments", "analysis"],
                    }
                )
            result = {
                "version": "1.0",
                "semantics": "mechanical_result_to_claim_map_not_final_scientific_judgment",
                "schema_semantics": "result_to_claim_mapping_not_paper_text",
                "source": "external_executor",
                "dry_run": bool(summary.get("dry_run")),
                "mock_only": bool(summary.get("mock_only")),
                "evidence_grade": str(summary.get("evidence_grade") or audit.get("evidence_grade") or ""),
                "integrity_audit": params.integrity_audit_path,
                "required_baseline_coverage": baseline_coverage,
                "method_audit": method_audit,
                "framework_figure_audit": framework_figure_audit,
                "result_audit": result_audit,
                "contribution_drift": contribution_drift,
                "excluded_metric_ids": excluded_metric_ids,
                "claim_mappings": mappings,
                "claims": claims,
                "global_must_not_claim": _global_must_not_claim(summary, baseline_missing, baseline_coverage)
                + ([f"Do not claim results for metrics that failed result audit: {', '.join(excluded_metric_ids)}"] if excluded_metric_ids else [])
                + (["Do not present the realized method as final Method until method audit passes."] if method_blocked else [])
                + (["Do not use the final framework figure until framework figure audit passes."] if framework_figure_audit.get("status") != "pass" else []),
            }
            _write_json(self.policy.resolve_write(params.output_path), result)
            _write_json(self.policy.resolve_write(params.draft_output_path), result)
            _write_text(self.policy.resolve_write(params.must_not_claim_path), _format_must_not_claim(result))
            _write_text(self.policy.resolve_write(params.claim_support_matrix_path), _format_claim_support_matrix(result))
            _write_text(self.policy.resolve_write(params.limitations_path), _format_limitations_from_experiments(result))
            _write_json(
                self.policy.resolve_write(params.figure_table_evidence_map_path),
                {
                    "version": "1.0",
                    "semantics": "figure_table_evidence_map_from_result_to_claim",
                    "tables": [
                        {
                            "table_id": "tab:main_results",
                            "claim_ids": [claim.get("claim_id") for claim in claims],
                            "metric_refs": [metric.get("metric_id") for metric in _summary_metric_records(summary) if isinstance(metric, dict)],
                        }
                    ],
                    "figures": [
                        {
                            "figure_id": (summary.get("final_framework_figure") or {}).get("figure_id", "fig:framework"),
                            "audit_status": framework_figure_audit.get("status"),
                            "usable_by_t8": framework_figure_audit.get("status") == "pass",
                        }
                    ],
                },
            )
            iteration_log = self.policy.workspace_dir / "experiments" / "iteration_log.md"
            iteration_log.parent.mkdir(parents=True, exist_ok=True)
            iteration_log.write_text(_format_iteration_log(summary, audit, result), encoding="utf-8")
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"result-to-claim failed: {exc}", error="result_to_claim_failed")
        return ToolResult(ok=True, content=f"Wrote result-to-claim map to {params.output_path}.", data={"claim_count": len(mappings)})


class BuildExperimentEvidencePackTool(Tool):
    name = "build_experiment_evidence_pack"
    description = "Build a normalized experiment evidence pack for manuscript writing."
    parameters_schema = BuildExperimentEvidencePackParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BuildExperimentEvidencePackParams(**kwargs)
        try:
            summary = _read_json(self.policy.resolve_read(params.results_summary_path))
            audit = _read_json(self.policy.resolve_read(params.integrity_audit_path))
            claims = _read_json(self.policy.resolve_read(params.experimental_claims_path))
            evidence = _read_json(self.policy.resolve_read(params.evidence_index_path))
            method_resources = _read_json(self.policy.workspace_dir / "drafts" / "method_writing_resources.json")
            pack = {
                "version": "1.0",
                "semantics": "normalized_experiment_evidence_pack",
                "source": "external_executor",
                "dry_run": bool(summary.get("dry_run")),
                "mock_only": bool(summary.get("mock_only")),
                "evidence_grade": str(summary.get("evidence_grade") or audit.get("evidence_grade") or ""),
                "source_packs": [{"path": params.results_summary_path}, {"path": params.integrity_audit_path}],
                "artifacts": evidence.get("artifacts", []),
                "metrics": _summary_metric_records(summary),
                "claims": claims.get("claim_mappings", []),
                "method_writing_resources": method_resources,
                "method_writing_resources_ref": "drafts/method_writing_resources.json",
                "realized_method_package": evidence.get("realized_method_package") or {},
                "final_framework_figure": evidence.get("final_framework_figure") or {},
                "figure_table_inventory": evidence.get("figure_table_inventory") or {},
                "writer_handoff": evidence.get("writer_handoff") or {},
                "required_baseline_coverage": audit.get("required_baseline_coverage") or {},
                "must_not_claim": claims.get("global_must_not_claim", []),
                "integrity": {
                    "status": audit.get("status"),
                    "issues": audit.get("issues", []),
                    "method_audit": audit.get("method_audit") or {},
                    "framework_figure_audit": audit.get("framework_figure_audit") or {},
                    "contribution_drift": audit.get("contribution_drift"),
                },
                "limitations": ["mock_only"] if summary.get("mock_only") else [],
            }
            _write_json(self.policy.resolve_write(params.output_path), pack)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"evidence pack failed: {exc}", error="evidence_pack_failed")
        return ToolResult(ok=True, content=f"Wrote experiment evidence pack to {params.output_path}.", data={"metric_count": len(pack.get("metrics", []))})


class AuditPaperClaimsTool(Tool):
    name = "audit_paper_claims"
    description = "Audit manuscript numeric claims against normalized experiment evidence pack."
    parameters_schema = AuditPaperClaimsParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = AuditPaperClaimsParams(**kwargs)
        try:
            paper = self.policy.resolve_read(params.paper_path).read_text(encoding="utf-8", errors="replace")
            pack = _read_json(self.policy.resolve_read(params.evidence_pack_path))
            result_to_claim = _read_json(self.policy.resolve_read(params.result_to_claim_path))
            known_values = {
                str(metric.get("value"))
                for metric in pack.get("metrics", []) or []
                if isinstance(metric, dict) and metric.get("value") is not None
            }
            known_numeric_values = _known_numeric_metric_values(pack)
            issues = []
            for number in _extract_substantive_numbers(paper):
                if not _number_supported_by_evidence(number, known_values, known_numeric_values):
                    issues.append({"level": "FAIL", "number": number["raw"], "issue": "number_not_in_evidence_pack"})
            if pack.get("mock_only"):
                issues.append({"level": "FAIL", "issue": "mock_only_evidence_pack", "detail": "Dry-run evidence cannot support paper claims."})
            forbidden_violations = _detect_forbidden_wording_violations(paper, result_to_claim)
            for violation in forbidden_violations:
                issues.append({"level": "FAIL", "issue": "forbidden_wording_violation", **violation})
            unsupported_strong_claims = _detect_unsupported_strong_claims(result_to_claim)
            for item in unsupported_strong_claims:
                issues.append({"level": "FAIL", "issue": "unsupported_strong_claim", **item})
            audit = {
                "version": "1.0",
                "semantics": "paper_claim_audit_against_experiment_evidence_pack",
                "input_fingerprints": {
                    "paper_path": params.paper_path,
                    "paper_sha256": _sha256_text(paper),
                    "evidence_pack_path": params.evidence_pack_path,
                    "evidence_pack_sha256": _sha256_json(pack),
                    "result_to_claim_path": params.result_to_claim_path,
                    "result_to_claim_sha256": _sha256_json(result_to_claim),
                },
                "summary": {
                    "fail_count": sum(1 for item in issues if item["level"] == "FAIL"),
                    "warn_count": sum(1 for item in issues if item["level"] == "WARN"),
                },
                "mock_only": bool(pack.get("mock_only")),
                "known_metric_values": sorted(known_values),
                "claim_mappings_count": len(result_to_claim.get("claim_mappings", []) or []),
                "unsupported_strong_claims": unsupported_strong_claims,
                "forbidden_wording_violations": forbidden_violations,
                "global_must_not_claim": result_to_claim.get("global_must_not_claim", []) or [],
                "issues": issues,
            }
            output_path = self.policy.resolve_write(params.output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(_format_paper_claim_audit_markdown(audit), encoding="utf-8")
            output_path.with_suffix(".json").write_text(
                json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"paper claim audit failed: {exc}", error="paper_claim_audit_failed")
        return ToolResult(ok=True, content=f"Wrote paper claim audit to {params.output_path}.", data=audit["summary"])


def _render_executor_prompt(handoff: dict[str, Any], *, executor: str) -> str:
    executor_label = {
        "codex_cli": "Codex CLI",
        "claude_code_window": "Claude Code",
        "manual": "Manual External Executor",
        "mock_dry_run": "Mock Dry-Run Executor",
        "UNSET": "Unselected External Executor",
    }.get(executor, executor)
    return (
        f"# External Experiment Executor Prompt ({executor_label})\n\n"
        "> EXECUTION MODE NOT YET SELECTED - see executor_selection.json after T5-EXECUTOR-GATE\n\n"
        "- dry_run: UNSET\n"
        "- mock_only: UNSET\n"
        "- real_experiment_allowed: UNSET\n\n"
        "You are an external executor for ResearchOS. Do not modify the ResearchOS main repo outside allowed paths.\n\n"
        "## Required Output\n"
        "- Write `external_executor/result_pack.json` matching `external_executor/expected_outputs_schema.json`.\n"
        "- Write `external_executor/run_manifest.json` with raw result/config/log artifact paths and hashes.\n"
        "- Write `external_executor/executor_status.json` with `status: done` and `accepted: false`.\n"
        "- Write raw logs under `external_executor/logs/`.\n"
        "- Mark dry-run outputs with `mock_only: true`.\n\n"
        "## Integrity Rules\n"
        "- Do not summarize results without raw files.\n"
        "- Do not write outside `external_executor/` or other allowed paths listed in the handoff pack.\n"
        "- Executor completion is only `done`; ResearchOS ingest/audit decides whether evidence is accepted.\n\n"
        "## Handoff Pack\n\n"
        "```json\n"
        + json.dumps(handoff, ensure_ascii=False, indent=2)
        + "\n```\n"
    )


def _render_manual_instructions(handoff: dict[str, Any]) -> str:
    return (
        "# Manual External Executor Instructions\n\n"
        "Use this file when a human or non-integrated external agent runs the experiment outside ResearchOS.\n\n"
        "1. Read `external_executor/handoff_pack.json` and `external_executor/expected_outputs_schema.json`.\n"
        "2. Implement/run only inside the allowed paths.\n"
        "3. Place raw metrics in `external_executor/raw_results/` and configs in `external_executor/configs/`.\n"
        "4. Write `external_executor/run_manifest.json`, `external_executor/result_pack.json`, and "
        "`external_executor/executor_status.json`.\n"
        "5. Keep `executor_status.accepted=false`; ResearchOS will set acceptance through ingest/audit.\n\n"
        "Allowed paths:\n\n"
        + "\n".join(f"- `{path}`" for path in handoff.get("allowed_paths", []))
        + "\n"
    )


def _format_iteration_log(summary: dict[str, Any], audit: dict[str, Any], claims: dict[str, Any]) -> str:
    lines = [
        "# External Experiment Iteration Log",
        "",
        f"- source: {summary.get('source')}",
        f"- run_id: {summary.get('run_id')}",
        f"- dry_run: {summary.get('dry_run')}",
        f"- mock_only: {summary.get('mock_only')}",
        f"- integrity_status: {audit.get('status')}",
        f"- claim_mappings: {len(claims.get('claim_mappings', []) or [])}",
        "",
        "Dry-run results are protocol tests only and must not be used as paper evidence.",
    ]
    return "\n".join(lines) + "\n"


def _known_numeric_metric_values(pack: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for metric in pack.get("metrics", []) or []:
        if not isinstance(metric, dict):
            continue
        value = metric.get("value")
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return values


def _number_supported_by_evidence(
    number: dict[str, Any],
    known_values: set[str],
    known_numeric_values: list[float],
) -> bool:
    raw = str(number.get("raw") or "")
    if raw in known_values:
        return True
    value = number.get("value")
    if not isinstance(value, (int, float)):
        return False
    candidates = [float(value)]
    if number.get("percent"):
        candidates.append(float(value) / 100.0)
    for candidate in candidates:
        for known in known_numeric_values:
            if abs(candidate - known) <= max(1e-6, abs(known) * 0.005):
                return True
    return False


def _extract_substantive_numbers(text: str) -> list[dict[str, Any]]:
    import re

    result: list[dict[str, Any]] = []
    pattern = re.compile(r"(?<![A-Za-z0-9])[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?%?", re.IGNORECASE)
    for match in pattern.finditer(text or ""):
        raw = match.group(0)
        num = raw.rstrip("%")
        try:
            value = float(num)
        except Exception:
            continue
        is_percent = raw.endswith("%")
        if value in {0.0, 1.0} or 1900 <= value <= 2100:
            continue
        if not is_percent and abs(value).is_integer() and 1 <= abs(value) <= 20:
            continue
        result.append({"raw": raw, "value": value, "percent": is_percent})
    return result


def _format_paper_claim_audit_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# Paper Claim Audit",
        "",
        f"- semantics: `{audit.get('semantics')}`",
        f"- fail_count: {audit.get('summary', {}).get('fail_count')}",
        f"- warn_count: {audit.get('summary', {}).get('warn_count')}",
        f"- mock_only: {audit.get('mock_only')}",
        "",
        "## Issues",
    ]
    for issue in audit.get("issues", []) or []:
        lines.append(f"- **{issue.get('level')}** {issue.get('issue')}: {issue.get('number', '')} {issue.get('detail', '')}".strip())
    return "\n".join(lines) + "\n"


def _detect_forbidden_wording_violations(paper: str, result_to_claim: dict[str, Any]) -> list[dict[str, str]]:
    paper_lc = paper.lower()
    forbidden: list[str] = []
    for item in result_to_claim.get("global_must_not_claim", []) or []:
        text = str(item or "").strip()
        match = re.search(r"Do not claim:\s*(.+)$", text, re.IGNORECASE)
        forbidden.append(match.group(1).strip() if match else text)
    for mapping in result_to_claim.get("claim_mappings", []) or []:
        if isinstance(mapping, dict):
            forbidden.extend(str(item) for item in mapping.get("forbidden_wording", []) or [])
    violations: list[dict[str, str]] = []
    for phrase in sorted({item.strip() for item in forbidden if item and len(item.strip()) >= 4}):
        phrase_lc = phrase.lower()
        if _forbidden_phrase_present(phrase_lc, paper_lc):
            violations.append({"phrase": phrase, "detail": f"paper contains forbidden wording: {phrase}"})
    return violations


def _forbidden_phrase_present(phrase_lc: str, paper_lc: str) -> bool:
    if phrase_lc.startswith("do not present"):
        for token in ("empirical evidence", "validated", "outperforms", "state-of-the-art"):
            if token in paper_lc:
                return True
        return False
    if phrase_lc.startswith("do not "):
        return False
    return phrase_lc in paper_lc


def _detect_unsupported_strong_claims(result_to_claim: dict[str, Any]) -> list[dict[str, str]]:
    unsupported: list[dict[str, str]] = []
    coverage = result_to_claim.get("required_baseline_coverage")
    baseline_incomplete = isinstance(coverage, dict) and coverage.get("status") in {"missing", "incomplete", "mock_only"}
    for mapping in result_to_claim.get("claim_mappings", []) or []:
        if not isinstance(mapping, dict):
            continue
        strength = str(mapping.get("claim_strength") or "").lower()
        support_status = str(mapping.get("support_status") or "").lower()
        if strength == "strong" and (support_status.startswith("unsupported") or baseline_incomplete):
            unsupported.append(
                {
                    "claim_id": str(mapping.get("claim_id") or ""),
                    "detail": f"claim_strength=strong with support_status={support_status or 'unknown'}",
                }
            )
    for claim in result_to_claim.get("claims", []) or []:
        if not isinstance(claim, dict):
            continue
        strength = str(claim.get("claim_strength") or "").lower()
        blocked = claim.get("blocked_by") or claim.get("must_not_say") or []
        if strength == "strong" and blocked:
            unsupported.append(
                {
                    "claim_id": str(claim.get("claim_id") or ""),
                    "detail": "claim_strength=strong but claim has blocked_by/must_not_say entries",
                }
            )
    return unsupported
