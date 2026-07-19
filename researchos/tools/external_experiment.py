from __future__ import annotations

"""External experiment handoff and evidence tools.

ResearchOS owns protocol, provenance, integrity checks, and claim mapping.
External executors such as Codex CLI, Claude Code, or a manual runner own code
implementation and experiment execution in an isolated workspace. These tools
only read/write workspace artifacts and provide a mock dry-run path for tests.
"""

import csv
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from ..runtime.environment import workspace_host_hint
from ..runtime.bridge_catalog import (
    iter_bridge_catalog_paths,
    load_bridge_catalog_summaries,
    resolve_catalog_canonical_note_path,
)
from ..runtime.literature_contract import build_literature_manifest, iter_literature_note_cards
from ..literature_resources import refresh_resource_catalog
from ..ideation.proposal import (
    PROPOSAL_MANIFEST_REL_PATH,
    PROPOSAL_REL_PATH,
    PROPOSAL_SEMANTICS,
    PROPOSAL_STATUS,
    proposal_manifest_source_ref,
    proposal_source_ref,
    validate_t45_research_proposal,
)
from ..skills.project_specialization import specialize_project_skills
from ..skills.project_specialization.policies import (
    default_executor_capabilities,
    default_resource_acquisition_policy,
)
from .base import Tool, ToolResult
from .workspace_policy import ToolAccessDenied, WorkspaceAccessPolicy


EXECUTOR_SELECTION_PATH = "external_executor/report/executor_selection.json"
EXECUTOR_CAPABILITIES_PATH = "external_executor/report/executor_capabilities.json"
INPUT_FINGERPRINT_PATH = "external_executor/report/phase_A/input_fingerprint.json"
RUN_MANIFEST_PATH = "external_executor/report/run_manifest.json"
LEGACY_EXECUTOR_SELECTION_PATH = "external_executor/executor_selection.json"
LEGACY_EXECUTOR_CAPABILITIES_PATH = "external_executor/executor_capabilities.json"
LEGACY_RUN_MANIFEST_PATH = "external_executor/run_manifest.json"


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


def _first_existing_external_report_path(workspace: Path, canonical: str, legacy: str) -> Path:
    canonical_path = workspace / canonical
    if canonical_path.exists():
        return canonical_path
    return workspace / legacy


def _executor_selection_path(workspace: Path) -> Path:
    return _first_existing_external_report_path(workspace, EXECUTOR_SELECTION_PATH, LEGACY_EXECUTOR_SELECTION_PATH)


def _executor_capabilities_path(workspace: Path) -> Path:
    return _first_existing_external_report_path(workspace, EXECUTOR_CAPABILITIES_PATH, LEGACY_EXECUTOR_CAPABILITIES_PATH)


def _run_manifest_ref(workspace: Path, *payloads: dict[str, Any]) -> str:
    for payload in payloads:
        value = payload.get("run_manifest") if isinstance(payload, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return RUN_MANIFEST_PATH if (workspace / RUN_MANIFEST_PATH).exists() else LEGACY_RUN_MANIFEST_PATH


def _workspace_relative(workspace: Path, path: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return path.as_posix()


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
    elif path.exists() and path.is_dir():
        record.update({"kind": "directory", "file_count": sum(1 for item in path.rglob("*") if item.is_file())})
    return record


def _paper_card_evidence_index(workspace: Path) -> dict[str, Any]:
    """Locate literature cards without elevating them into experimental evidence."""

    manifest = build_literature_manifest(workspace, write=True)
    card_type_by_root = {
        "deep_read_notes": "full_or_partial",
        "bridge_notes": "bridge",
        "shallow_read_notes": "abstract_only",
    }
    cards: list[dict[str, Any]] = []
    for card in iter_literature_note_cards(workspace, include_shallow=True):
        cards.append(
            {
                "paper_id": card.paper_id,
                "path": card.rel_path,
                "card_type": card_type_by_root.get(card.root_type, card.root_type),
                "root_type": card.root_type,
                "evidence_level": card.evidence_level,
                "bytes": card.size,
                "sha256": card.sha256,
            }
        )
    bridge_catalogs: list[dict[str, Any]] = []
    for catalog_path in iter_bridge_catalog_paths(workspace):
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        records = catalog.get("records") if isinstance(catalog.get("records"), list) else []
        bridge_catalogs.append(
            {
                "bridge_id": str(catalog.get("bridge_id") or catalog_path.parent.name),
                "path": catalog_path.relative_to(workspace).as_posix(),
                "record_count": len(records),
                "abstract_leads": sum(bool(str(item.get("abstract") or "").strip()) for item in records if isinstance(item, dict)),
                "claim_usable_notes": sum(
                    resolve_catalog_canonical_note_path(workspace, item.get("canonical_note_path")) is not None
                    for item in records
                    if isinstance(item, dict)
                ),
                "usage_boundary": "supplementary_transfer_context_not_experiment_evidence",
            }
        )
    bridge_catalog_context = load_bridge_catalog_summaries(
        workspace,
        records_per_bridge=2,
        abstract_excerpt_chars=320,
    )
    resource_summary = _read_json(workspace / "literature" / "resource_catalog_summary.json")
    resource_catalog_path = workspace / "literature" / "resource_catalog.jsonl"
    resource_catalog = {
        "catalog_path": "literature/resource_catalog.jsonl",
        "summary_path": "literature/resource_catalog_summary.json",
        "available": resource_catalog_path.is_file(),
        "record_count": int(resource_summary.get("record_count") or 0),
        "paper_count": int(resource_summary.get("paper_count") or 0),
        "by_resource_type": resource_summary.get("by_resource_type")
        if isinstance(resource_summary.get("by_resource_type"), dict)
        else {},
        "usage_boundary": (
            "Discovery records guide feasibility, baseline/resource selection, and Phase B acquisition. "
            "They do not prove a resource is executable, licensed, official, protocol-equivalent, or empirical evidence."
        ),
    }
    return {
        "version": "1.0",
        "semantics": "paper_card_evidence_index",
        "literature_manifest_path": "literature/literature_manifest.json",
        "literature_manifest_sha256": _sha256(workspace / "literature" / "literature_manifest.json")
        if (workspace / "literature" / "literature_manifest.json").is_file()
        else "",
        "literature_manifest_counts": manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {},
        "purpose": "Locate literature evidence for rationale, baseline provenance, and claim boundaries.",
        "allowed_uses": [
            "mechanism and design-rationale context",
            "baseline identity and reproduction provenance",
            "known limitation, boundary condition, and related-work traceability",
        ],
        "prohibited_uses": [
            "empirical performance evidence for the proposed method",
            "replacement for result_pack, raw results, integrity audit, or result-to-claim mapping",
            "authority to claim novelty beyond ideation/novelty_audit.md",
        ],
        "card_count": len(cards),
        "cards": cards,
        "bridge_catalogs": bridge_catalogs,
        "bridge_catalog_context": {
            "semantics": "cross_domain_catalog_context_not_experiment_evidence",
            "tracks": bridge_catalog_context,
            "usage_boundary": (
                "Catalogs may guide baseline discovery, mechanism contrasts, external-validity risks, and follow-up reading. "
                "They do not establish a mechanism, baseline equivalence, implementation detail, or experimental result."
            ),
        },
        "resource_discovery_catalog": resource_catalog,
    }


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
        "figures": ("external_executor/figure", "figure"),
        "tables": ("external_executor/table", "table"),
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
        "result_diagnoses",
        "module_attributions",
        "framework_figure",
    }
    return {key: value for key, value in result_pack.items() if key not in known}


def _run_records_from_result_pack(result_pack: dict[str, Any], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    source_runs = result_pack.get("experiment_runs")
    if isinstance(source_runs, dict):
        source_runs = source_runs.get("items")
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


def _current_result_section(
    result_pack: dict[str, Any],
    canonical: str,
    legacy: str | None = None,
    *,
    id_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    value = result_pack.get(canonical)
    if (value is None or value == {}) and legacy:
        value = result_pack.get(legacy)
    if not isinstance(value, dict):
        return {}
    items = value.get("items")
    if not isinstance(items, list):
        return value
    current = value.get("current_by_iteration")
    if isinstance(current, dict) and current:
        current_id = list(current.values())[-1]
        for item in items:
            if isinstance(item, dict) and any(item.get(key) == current_id for key in id_keys):
                return item
    return next((item for item in reversed(items) if isinstance(item, dict)), {})


def _result_section_items(result_pack: dict[str, Any], *names: str) -> list[dict[str, Any]]:
    for name in names:
        value = result_pack.get(name)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict) and isinstance(value.get("items"), list):
            return [item for item in value["items"] if isinstance(item, dict)]
    return []


PRE_T5_SOURCE_FILES = [
    "project.yaml",
    "literature/synthesis.md",
    "literature/synthesis_workbench.json",
    "literature/domain_map.json",
    "literature/bridge_domain_plan.json",
    "literature/literature_manifest.json",
    "literature/cross_domain_catalogs/index.json",
    "literature/comparison_table.csv",
    "ideation/hypotheses.md",
    "ideation/research_dossier.json",
    "ideation/proposal/research_proposal.md",
    "ideation/proposal/proposal_manifest.json",
    "ideation/exp_plan.yaml",
    "ideation/selected/selected_candidate.json",
    "ideation/kill_criteria.yaml",
    "ideation/validation_map.yaml",
    "ideation/contribution_hypothesis_map.yaml",
    "ideation/idea_scorecard.yaml",
    "ideation/risks.md",
    "ideation/novelty_audit.md",
    "novelty/novelty_audit.md",
    "resources/baseline_candidates.jsonl",
    "literature/baseline_map.json",
    "literature/resource_catalog.jsonl",
    "literature/resource_catalog_summary.json",
    "literature/notes_manifest.json",
    "literature/deep_read_notes",
    "literature/bridge_notes",
    "literature/cross_domain_catalogs",
    "literature/shallow_read_notes",
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
    "result_diagnoses",
    "module_attributions",
    "realized_method_package",
    "framework_figure",
    "figure_table_inventory",
    "run_manifest",
]

EXTERNAL_RESULT_FIELD_ALIASES = {
    "result_diagnoses": ("result_diagnoses", "result_diagnosis"),
    "module_attributions": ("module_attributions", "module_attribution"),
    "framework_figure": ("framework_figure", "final_framework_figure"),
}


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


RESEARCH_REBOOST_REQUIRED_SOURCE_GROUPS = (
    ("project.yaml",),
    ("literature/synthesis.md",),
    ("literature/synthesis_workbench.json",),
    ("literature/domain_map.json",),
    ("literature/comparison_table.csv",),
    ("ideation/hypotheses.md",),
    ("ideation/exp_plan.yaml",),
    # T4.5 formalization now owns these two roles.  Legacy artifacts remain
    # supported only as fallbacks for existing workspaces.
    ("ideation/selected/selected_candidate.json", "ideation/idea_scorecard.yaml"),
    ("ideation/kill_criteria.yaml", "ideation/risks.md"),
    ("ideation/novelty_audit.md",),
)
RESEARCH_REBOOST_REQUIRED_SOURCE_PATHS = {
    path for group in RESEARCH_REBOOST_REQUIRED_SOURCE_GROUPS for path in group
}

RESEARCH_REBOOST_GATE_STAGES = [
    "context_alignment",
    "resource_mining",
    "baseline_reproduction",
    "claim_evidence_design",
    "method_refinement",
    "implementation",
    "code_protocol_review",
    "smoke_validation",
    "formal_run",
    "result_diagnosis",
    "module_attribution",
    "refinement_decision",
    "realized_method_packaging",
    "figure_table_packaging",
    "writer_handoff",
]

RESEARCH_REBOOST_WRITER_ARTIFACT_TYPES = [
    "realized_method_package",
    "result_to_claim",
    "evidence_pack",
    "result_diagnosis",
    "module_attribution",
    "final_framework_figure",
    "figure_table_inventory",
    "limitations",
    "reproducibility_manifest",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _research_reboost_skill_dir() -> Path:
    return _repo_root() / "skills" / "research-reboost"


def research_reboost_skill_prompt_excerpt(*, max_chars: int = 24000) -> str:
    """Return the current T5 reboost skill contract for injection into the LLM prompt."""

    skill_dir = _research_reboost_skill_dir()
    parts: list[str] = []
    for rel in ("SKILL.md", "references/reboost-protocol.md"):
        path = skill_dir / rel
        if path.is_file():
            parts.append(f"## {rel}\n\n{path.read_text(encoding='utf-8', errors='replace')}")
    text = "\n\n".join(parts)
    return text[:max_chars]


def _load_skill_python_module(module_name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _research_reboost_inventory(workspace: Path) -> dict[str, Any]:
    module = _load_skill_python_module(
        "research_reboost_inventory_sources",
        _research_reboost_skill_dir() / "scripts" / "inventory_sources.py",
    )
    inventory = module.build_inventory(workspace.resolve(), include_optional=True)
    used_for_by_category = {
        "project": ["project_scope", "research_goal"],
        "literature_synthesis": ["research_goal", "method_mechanism", "claim_boundary"],
        "synthesis_workbench": ["method_mechanism", "claim_boundary"],
        "domain_map": ["project_scope", "method_mechanism"],
        "comparison_table": ["baseline_selection", "experiment_protocol"],
        "hypotheses": ["hypothesis", "method_mechanism", "claim_boundary"],
        "experiment_plan": ["experiment_protocol", "baseline_selection", "writer_contract"],
        "idea_scorecard": ["risk_analysis", "method_mechanism"],
        "risks": ["risk_analysis", "claim_boundary"],
        "research_dossier": ["research_goal", "method_mechanism", "claim_boundary", "risk_analysis"],
        "research_proposal": ["research_goal", "method_mechanism", "experiment_protocol", "claim_boundary", "risk_analysis", "writer_contract"],
        "validation_map": ["experiment_protocol", "baseline_selection", "claim_boundary"],
        "contribution_hypothesis_map": ["method_mechanism", "claim_boundary", "writer_contract"],
        "novelty_audit": ["novelty_boundary", "baseline_selection", "claim_boundary"],
        "resource": ["resource_hint", "baseline_selection", "risk_analysis"],
    }
    for entry in inventory.get("sources", []) or []:
        if not isinstance(entry, dict):
            continue
        is_required = entry.get("requirement") == "required"
        is_available = entry.get("availability") == "available"
        is_current_t45_context = str(entry.get("category") or "") in {
            "research_dossier",
            "research_proposal",
            "validation_map",
            "contribution_hypothesis_map",
        }
        entry["used"] = bool(
            is_available
            and (
                is_required
                or is_current_t45_context
                or str(entry.get("category") or "") == "resource"
            )
        )
        if entry["used"]:
            entry["used_for"] = used_for_by_category.get(str(entry.get("category")), [])
        elif is_required:
            entry["used_for"] = []
        else:
            entry.setdefault("omission_reason", "Optional source discovered but not needed for the initial T5 reboost contract")
    return inventory


def _validate_research_reboost_pack(workspace: Path, pack_path: Path | None = None) -> tuple[bool, str | None, dict[str, Any]]:
    skill_dir = _research_reboost_skill_dir()
    pack_path = pack_path or workspace / "external_executor" / "handoff_pack.json"
    report: dict[str, Any] = {
        "validator": "skills/research-reboost/scripts/validate_handoff.py",
        "valid": False,
        "findings": [],
    }
    try:
        validator = _load_skill_python_module(
            "research_reboost_validate_handoff",
            skill_dir / "scripts" / "validate_handoff.py",
        )
        pack = json.loads(pack_path.read_text(encoding="utf-8"))
        schema = json.loads((skill_dir / "references" / "handoff_pack.schema.json").read_text(encoding="utf-8"))
        structural = validator.SchemaValidator(schema).validate(pack)
        semantic = [] if structural else validator.SemanticValidator(pack, workspace, True).validate()
        findings = structural + semantic
        report["findings"] = [
            {
                "severity": finding.severity,
                "code": finding.code,
                "path": finding.path,
                "message": finding.message,
            }
            for finding in findings
        ]
        errors = [item for item in report["findings"] if item.get("severity") == "error"]
        report["valid"] = not errors
        report["error_count"] = len(errors)
        report["warning_count"] = sum(1 for item in report["findings"] if item.get("severity") == "warning")
        if errors:
            first = errors[0]
            return False, f"{first.get('code')} at {first.get('path')}: {first.get('message')}", report
        return True, None, report
    except Exception as exc:
        report["findings"] = [{"severity": "error", "code": "validator.runtime", "path": "/", "message": str(exc)}]
        report["error_count"] = 1
        report["warning_count"] = 0
        return False, str(exc), report


def _is_research_reboost_pack(data: dict[str, Any]) -> bool:
    return (
        data.get("schema_version") == "external_executor_handoff.v1"
        and isinstance(data.get("source_manifest"), list)
        and isinstance(data.get("method_intent"), dict)
        and isinstance(data.get("execution_contract"), dict)
    )


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
    if _is_research_reboost_pack(data):
        if data.get("generation_status") != "completed":
            return False, "handoff_pack.generation_status 必须是 completed 才能进入外部执行器 gate"
        ok, err, _report = _validate_research_reboost_pack(workspace_dir, path)
        if not ok:
            return False, f"research-reboost validator failed: {err}"
        method_intent = data.get("method_intent")
        if method_intent.get("status") != "draft_intent_only" or method_intent.get("not_final_method_source") is not True:
            return False, "handoff_pack.method_intent 必须标记 draft_intent_only 且不是最终 Method 来源"
        return True, None
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
        "resource_acquisition_policy": default_resource_acquisition_policy(),
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
    # An empty plan is a protocol gap, not permission to manufacture a generic
    # experiment/claim row. The handoff records the gap separately and the
    # executor must wait for a source-backed plan.
    if not experiments:
        return []
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
            str(exp.get("dataset") or exp.get("benchmark") or "").strip()
            for exp in experiments
            if str(exp.get("dataset") or exp.get("benchmark") or "").strip()
        )
    )
    if not experiments or not datasets or not metrics:
        missing = []
        if not experiments:
            missing.append("experiments")
        if not datasets:
            missing.append("dataset_or_benchmark")
        if not metrics:
            missing.append("metrics")
        return [
            {
                "step": "protocol_input_required",
                "status": "blocked_missing_protocol",
                "missing_fields": missing,
                "required_output": "source-backed exp_plan.yaml or explicit human protocol decision",
                "claim_boundary": "Do not run, compare, or claim results until the missing protocol fields are supplied.",
            }
        ]
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
        {"step": "diagnosis_and_attribution", "required_output": "result_pack.result_diagnoses/module_attributions"},
        {"step": "writer_handoff", "required_output": "external_executor/executor_research_report.md"},
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
                "boundary": "Novelty audit contains explicit must-not-claim language; preserve it in the executor report and T8 claim boundaries.",
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
            "external_executor/executor_research_report.md",
            "external_executor/result_pack.json",
            "external_executor/executor_status.json",
            RUN_MANIFEST_PATH,
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
            "framework_figure": "Framework figure candidate that final handoff validation must preserve before T8 use.",
            "executor_research_report": "Writer Handoff's source-bound method, experiment, result, Claim, limitation, and artifact report for T8.",
        },
    }


def _executor_selection_payload(workspace: Path) -> tuple[dict[str, Any], str]:
    path = _executor_selection_path(workspace)
    selection = _read_json(path)
    return selection, _sha256(path) if path.exists() and path.is_file() else ""


def _selection_selected_executor(selection: dict[str, Any]) -> str:
    return str(selection.get("selected_executor") or "").strip()


def _is_mock_executor(executor: str) -> bool:
    return executor == "mock_dry_run"


VALID_EXTERNAL_EXECUTORS = {"mock_dry_run", "codex_cli", "claude_code_window", "manual"}
VALID_EXTERNAL_EXECUTION_SCOPES = {"full_execution", "resource_preparation"}


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
    """Normalize the T4.5 metric spellings into the T5 execution contract.

    T4.5 plans legitimately use a compact top-level ``metrics`` list and the
    researcher-facing ``measurements`` alias inside an experiment.  Dropping
    either one made an otherwise usable plan look as though it had no metrics.
    """

    metrics: list[str] = []

    def append_metrics(values: Any) -> None:
        if not isinstance(values, list):
            return
        for metric in values:
            if isinstance(metric, dict):
                value = metric.get("name") or metric.get("metric") or metric.get("metric_id")
            else:
                value = metric
            text = str(value or "").strip()
            if text and text.casefold() not in {"unknown", "tbd"}:
                metrics.append(text)

    append_metrics(exp_plan.get("metrics") if isinstance(exp_plan, dict) else [])
    for exp in exp_plan.get("experiments", []) or []:
        if not isinstance(exp, dict):
            continue
        append_metrics(exp.get("metrics"))
        append_metrics(exp.get("measurements"))
    return list(dict.fromkeys(metrics))


DEFAULT_RESEARCHOS_SEED_ENSEMBLE: tuple[int, ...] = (42, 123, 456, 789, 2025)


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
    return list(dict.fromkeys(seeds))


def _execution_seed_ensemble(project: dict[str, Any]) -> list[int]:
    """Use project-declared seeds when available, otherwise a stable default.

    A seed ensemble controls reproducibility but does not change a project's
    task, mechanism, benchmark, or claim boundary. Requiring a human to choose
    one made T5 stall for a mechanical setting that can be recorded honestly.
    The fallback is deliberately fixed and visible in the handoff so it can be
    overridden by a project-specific policy later.
    """

    return _extract_exp_plan_seeds(project) or list(DEFAULT_RESEARCHOS_SEED_ENSEMBLE)


def _handoff_metrics_for_execution(handoff: dict[str, Any]) -> list[str]:
    old_metrics = (handoff.get("experiment_contract") or {}).get("metrics")
    if isinstance(old_metrics, list) and old_metrics:
        return [str(item.get("name") if isinstance(item, dict) else item) for item in old_metrics if str(item).strip()]
    names: list[str] = []
    for claim in handoff.get("claim_evidence_matrix", []) or []:
        if not isinstance(claim, dict):
            continue
        for req in claim.get("evidence_requirements", []) or []:
            if isinstance(req, dict) and req.get("metric_or_observation"):
                names.append(str(req["metric_or_observation"]))
    return list(dict.fromkeys(name for name in names if name))


def _handoff_seeds_for_execution(handoff: dict[str, Any]) -> list[int]:
    old_seeds = (handoff.get("experiment_contract") or {}).get("seeds")
    if isinstance(old_seeds, list) and old_seeds:
        result: list[int] = []
        for seed in old_seeds:
            try:
                result.append(int(seed))
            except Exception:
                continue
        if result:
            return list(dict.fromkeys(result))
    return []


def _handoff_required_baselines_for_execution(handoff: dict[str, Any]) -> list[dict[str, Any]]:
    old_baselines = (handoff.get("experiment_contract") or {}).get("required_baselines") or handoff.get("required_baselines")
    if isinstance(old_baselines, list) and old_baselines:
        return [item for item in old_baselines if isinstance(item, dict)]
    result: list[dict[str, Any]] = []
    for item in handoff.get("baseline_matrix", []) or []:
        if isinstance(item, dict) and item.get("requirement") == "required":
            result.append(
                {
                    "baseline_id": item.get("baseline_id"),
                    "baseline_name": item.get("name"),
                    "reason_required": item.get("rationale"),
                    "source": item.get("implementation_source"),
                }
            )
    return result


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


def _safe_schema_id(prefix: str, value: Any, index: int) -> str:
    raw = str(value or "").strip()
    slug = re.sub(r"[^A-Za-z0-9_.:-]+", "_", raw)[:60].strip("_.:-")
    if not slug or not re.match(r"^[A-Za-z]", slug):
        slug = f"{prefix}{index}"
    if len(slug) < 2:
        slug = f"{prefix}{index}"
    return slug[:120]


def _source_ref(source_id: str, locator: str, note: str, support_type: str = "direct") -> dict[str, str]:
    return {
        "source_id": source_id,
        "locator": locator,
        "note": note,
        "support_type": support_type,
    }


def _dossier_statement(dossier: dict[str, Any], key: str) -> str:
    value = dossier.get(key)
    if isinstance(value, dict):
        return _compact_text(str(value.get("statement") or ""), limit=800)
    return _compact_text(str(value or ""), limit=800)


def _dossier_statements(dossier: dict[str, Any], group: str) -> list[str]:
    why_it_matters = dossier.get("why_it_matters") if isinstance(dossier.get("why_it_matters"), dict) else {}
    values = why_it_matters.get(group) if isinstance(why_it_matters, dict) else []
    if not isinstance(values, list):
        return []
    statements: list[str] = []
    for value in values:
        if isinstance(value, dict):
            text = _compact_text(str(value.get("statement") or ""), limit=500)
        else:
            text = _compact_text(str(value or ""), limit=500)
        if text:
            statements.append(text)
    return list(dict.fromkeys(statements))


def _dossier_contributions(dossier: dict[str, Any]) -> list[str]:
    values = dossier.get("contributions") if isinstance(dossier.get("contributions"), list) else []
    statements: list[str] = []
    for value in values:
        if isinstance(value, dict):
            text = _compact_text(str(value.get("statement") or value.get("what_changes_if_supported") or ""), limit=500)
        else:
            text = _compact_text(str(value or ""), limit=500)
        if text:
            statements.append(text)
    return list(dict.fromkeys(statements))


def _candidate_dossier_value(selected_candidate: dict[str, Any], *keys: str) -> str:
    candidate = selected_candidate.get("candidate") if isinstance(selected_candidate.get("candidate"), dict) else selected_candidate
    if not isinstance(candidate, dict):
        return ""
    for key in keys:
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return _compact_text(value, limit=800)
    return ""


def _load_t45_proposal_context(
    workspace: Path,
    *,
    dossier: dict[str, Any],
    hypotheses: str,
) -> dict[str, Any]:
    """Load the formal Proposal as planning context with a legacy fallback."""

    manifest = _read_json(workspace / PROPOSAL_MANIFEST_REL_PATH)
    proposal_path = workspace / PROPOSAL_REL_PATH
    audit_path = workspace / "ideation" / "novelty_audit.md"
    proposal_valid, _proposal_error = validate_t45_research_proposal(workspace, audit_path)
    if (
        proposal_valid
        and manifest.get("semantics") == PROPOSAL_SEMANTICS
        and manifest.get("status") == PROPOSAL_STATUS
        and manifest.get("proposal_path") == PROPOSAL_REL_PATH
        and proposal_path.is_file()
        and proposal_path.stat().st_size > 0
    ):
        executive = manifest.get("executive_summary")
        if isinstance(executive, dict):
            summary = _compact_text(str(executive.get("statement") or ""), limit=900)
        else:
            summary = ""
        if not summary:
            summary = _first_non_heading_line(_read_text(proposal_path, max_chars=4000))
        return {
            "path": PROPOSAL_REL_PATH,
            "manifest_path": PROPOSAL_MANIFEST_REL_PATH,
            "source_type": "formal_proposal",
            "planning_status": PROPOSAL_STATUS,
            "summary": summary or "Formal post-novelty proposal is available for execution planning.",
            "t5_role": "planning_context_not_results",
            "source_refs": [proposal_source_ref(), proposal_manifest_source_ref()],
        }

    fallback_path = "ideation/research_dossier.json" if dossier else "ideation/hypotheses.md"
    fallback_summary = _dossier_statement(dossier, "central_thesis") or _first_non_heading_line(hypotheses)
    return {
        "path": fallback_path,
        "manifest_path": "",
        "source_type": "legacy_formalization_fallback",
        "planning_status": "legacy_fallback",
        "summary": fallback_summary or "No formal post-novelty proposal was available; use the retained formalization files.",
        "t5_role": "planning_context_not_results",
        "source_refs": [
            _source_ref(
                "SRC_RESEARCH_DOSSIER" if dossier else "SRC_HYPOTHESES",
                fallback_path,
                "Legacy formalization fallback preserves planning context without becoming empirical evidence.",
                "reconciled",
            )
        ],
    }


def _research_context_source_refs(
    dossier: dict[str, Any],
    proposal_context: dict[str, Any],
) -> list[dict[str, str]]:
    refs = [
        _source_ref("SRC_IDEA_SCORECARD", "selected Candidate dossier", "T4 selected direction preserves the research problem and decision context"),
        _source_ref("SRC_HYPOTHESES", "formal research dossier", "Formal hypotheses define the testable mechanism and scope", "reconciled"),
        _source_ref("SRC_NOVELTY", "Final Gate Verdict", "Novelty audit bounds the interpretation and claim ceiling", "reconciled"),
    ]
    if dossier:
        refs.insert(
            1,
            _source_ref(
                "SRC_RESEARCH_DOSSIER",
                "research context and conditional implications",
                "Post-novelty dossier preserves T4 significance, stakeholders, contributions, and evidence status",
            ),
        )
    if proposal_context.get("source_type") == "formal_proposal":
        refs[1:1] = [proposal_source_ref(), proposal_manifest_source_ref()]
    return refs


def _build_t45_research_context(
    *,
    dossier: dict[str, Any],
    selected_candidate: dict[str, Any],
    hypotheses: str,
    contribution_map: dict[str, Any],
    proposal_context: dict[str, Any],
) -> dict[str, Any]:
    """Retain T4 decision context without promoting it to empirical evidence."""

    thesis = _dossier_statement(dossier, "central_thesis")
    problem = _dossier_statement(dossier, "research_problem") or _candidate_dossier_value(
        selected_candidate,
        "target_problem",
        "problem",
        "pitch",
    ) or thesis or _first_non_heading_line(hypotheses)
    scholarly = _dossier_statements(dossier, "scholarly")
    if not scholarly:
        scholarly = [
            _candidate_dossier_value(selected_candidate, "practical_implication", "summary", "pitch")
            or problem
        ]
    practical = _dossier_statements(dossier, "practical")
    commercial = _dossier_statements(dossier, "commercial")
    stakeholders = _dossier_statements(dossier, "stakeholders_or_processes")
    if not stakeholders:
        raw_stakeholders = _candidate_dossier_value(selected_candidate, "affected_stakeholders_or_processes")
        if raw_stakeholders:
            stakeholders = [raw_stakeholders]
    contributions = _dossier_contributions(dossier)
    if not contributions:
        mapped = contribution_map.get("contributions") if isinstance(contribution_map.get("contributions"), list) else []
        contributions = [
            _compact_text(str(item.get("statement") or item.get("what_changes_if_true") or ""), limit=500)
            for item in mapped
            if isinstance(item, dict) and str(item.get("statement") or item.get("what_changes_if_true") or "").strip()
        ]
    if not contributions:
        contributions = [thesis or problem]
    evidence_status = "unknown"
    central = dossier.get("central_thesis") if isinstance(dossier.get("central_thesis"), dict) else {}
    if isinstance(central, dict):
        candidate_status = str(central.get("evidence_status") or "").strip()
        if candidate_status in {"source_supported", "proposed_not_verified", "unknown"}:
            evidence_status = candidate_status
    if evidence_status == "unknown" and "proposed_not_verified" in hypotheses:
        evidence_status = "proposed_not_verified"
    return {
        "research_problem": problem or "Research problem remains to be recovered from the selected Candidate dossier.",
        "scholarly_stakes": scholarly,
        "practical_implications": practical,
        "commercial_implications": commercial,
        "stakeholders_or_processes": stakeholders,
        "contribution_intent": contributions,
        "evidence_status": evidence_status,
        "proposal_context": proposal_context,
        "source_refs": _research_context_source_refs(dossier, proposal_context),
    }


def _build_resource_discovery_context(
    workspace: Path,
    source_manifest: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Expose literature resource discoveries to T5 without treating them as assets.

    The catalog is intentionally optional for legacy workspaces. When present,
    it tells Phase B where a baseline/data lead originated and preserves the
    fact that access, licensing, revision pinning, and protocol equivalence are
    still unresolved.
    """

    catalog_path = "literature/resource_catalog.jsonl"
    summary_path = "literature/resource_catalog_summary.json"
    summary = _read_json(workspace / summary_path)
    if not (workspace / catalog_path).is_file() or not summary:
        return None
    entries_by_path = {
        str(entry.get("path") or ""): entry
        for entry in source_manifest
        if isinstance(entry, dict)
    }
    catalog_entry = entries_by_path.get(catalog_path)
    summary_entry = entries_by_path.get(summary_path)
    if not catalog_entry or not summary_entry:
        return None
    source_refs = [
        _source_ref(
            str(catalog_entry.get("source_id") or "SRC_RESOURCE_CATALOG"),
            catalog_path,
            "Paper-associated resource discovery records for feasibility and Phase B acquisition planning.",
        ),
        _source_ref(
            str(summary_entry.get("source_id") or "SRC_RESOURCE_CATALOG_SUMMARY"),
            summary_path,
            "Resource discovery counts and execution boundary.",
        ),
    ]
    return {
        "catalog_path": catalog_path,
        "summary_path": summary_path,
        "record_count": max(0, int(summary.get("record_count") or 0)),
        "paper_count": max(0, int(summary.get("paper_count") or 0)),
        "by_resource_type": {
            str(key): max(0, int(value or 0))
            for key, value in (summary.get("by_resource_type") or {}).items()
            if isinstance(key, str)
        },
        "t5_role": "discovery_context_for_phase_b_not_execution_evidence",
        "evidence_boundary": (
            "The catalog records paper-associated resource leads. T5 must verify identity, license, "
            "immutable version, security, and protocol compatibility before acquisition or use."
        ),
        "source_refs": source_refs,
    }


def _source_available(source_manifest: list[dict[str, Any]], path: str) -> bool:
    return any(
        item.get("path") == path and item.get("availability") == "available" and item.get("used") is True
        for item in source_manifest
        if isinstance(item, dict)
    )


def _source_group_available(source_manifest: list[dict[str, Any]], group: tuple[str, ...]) -> bool:
    return any(_source_available(source_manifest, path) for path in group)


def _source_group_label(group: tuple[str, ...]) -> str:
    return " 或 ".join(group)


def _missing_reboost_source_groups(source_manifest: list[dict[str, Any]]) -> list[str]:
    return [
        _source_group_label(group)
        for group in RESEARCH_REBOOST_REQUIRED_SOURCE_GROUPS
        if not _source_group_available(source_manifest, group)
    ]


def _required_source_coverage(source_manifest: list[dict[str, Any]]) -> float:
    if not RESEARCH_REBOOST_REQUIRED_SOURCE_GROUPS:
        return 0.0
    ready = sum(
        _source_group_available(source_manifest, group)
        for group in RESEARCH_REBOOST_REQUIRED_SOURCE_GROUPS
    )
    return ready / len(RESEARCH_REBOOST_REQUIRED_SOURCE_GROUPS)


def _used_source_count(source_manifest: list[dict[str, Any]]) -> int:
    return sum(1 for item in source_manifest if isinstance(item, dict) and item.get("used") is True)


def _extract_project_goal(project: dict[str, Any], exp_plan: dict[str, Any], hypotheses: str) -> str:
    for value in (
        exp_plan.get("goal") if isinstance(exp_plan, dict) else None,
        project.get("research_question"),
        project.get("topic"),
        project.get("research_direction"),
        _first_non_heading_line(hypotheses),
    ):
        text = _compact_text(str(value or ""), limit=700)
        if text:
            return text
    return "Compile the selected ResearchOS hypothesis into an auditable external execution contract."


def _extract_research_question(project: dict[str, Any], goal: str) -> str:
    return _compact_text(str(project.get("research_question") or goal), limit=500)


def _extract_assumptions(hypotheses: str) -> list[str]:
    section = _section_hint(hypotheses, ["核心假设", "assumption", "assumptions"])
    bullets = _extract_bullets(section or hypotheses, limit=4)
    if bullets:
        return bullets
    return [
        "The selected mechanism remains within the scope and novelty boundaries declared before T5.",
        "The external executor can obtain enough data or material evidence to test the planned comparisons.",
    ]


def _extract_datasets(exp_plan: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for exp in _experiments_from_plan(exp_plan):
        for dataset in exp.get("datasets", []) or []:
            if isinstance(dataset, dict):
                names.append(str(dataset.get("name") or dataset.get("dataset") or "").strip())
            elif isinstance(dataset, str):
                names.append(dataset.strip())
        for key in ("dataset", "benchmark"):
            if exp.get(key):
                names.append(str(exp.get(key)).strip())
        # T4.5 plans may intentionally specify an auditable simulated setting
        # before the concrete dataset or environment package is selected.  The
        # handoff schema accepts datasets *or settings*, so retain that explicit
        # setting rather than falsely treating the protocol as empty.
        for key in ("evaluation", "evaluation_setting", "setting"):
            value = exp.get(key)
            if isinstance(value, str) and value.strip() and value.strip().casefold() not in {"unknown", "tbd"}:
                names.append(value.strip())
        # Agent/human and controlled-simulation studies often have no named
        # benchmark dataset. Their declared population and design still form a
        # concrete execution setting. Preserve those explicit fields rather
        # than inventing a dataset or declaring the protocol empty.
        raw_design = exp.get("design")
        if isinstance(raw_design, str):
            design_text = raw_design.strip()
            if design_text and design_text.casefold() not in {"unknown", "tbd"}:
                names.append("declared experimental setting: " + design_text)
        design = raw_design if isinstance(raw_design, dict) else {}
        for key in ("dataset", "datasets", "benchmark", "evaluation", "evaluation_setting", "setting"):
            value = design.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        name = str(item.get("name") or item.get("dataset") or item.get("benchmark") or "").strip()
                    else:
                        name = str(item or "").strip()
                    if name and name.casefold() not in {"unknown", "tbd"}:
                        names.append(name)
            elif isinstance(value, dict):
                name = str(value.get("name") or value.get("dataset") or value.get("benchmark") or "").strip()
                if name and name.casefold() not in {"unknown", "tbd"}:
                    names.append(name)
            elif isinstance(value, str) and value.strip() and value.strip().casefold() not in {"unknown", "tbd"}:
                names.append(value.strip())

        population = design.get("population") if isinstance(design.get("population"), dict) else {}
        setting_parts: list[str] = []
        design_type = str(design.get("type") or exp.get("design_type") or "").strip()
        if design_type:
            setting_parts.append(design_type)
        agent_population = population.get("agent") if isinstance(population.get("agent"), dict) else {}
        models = agent_population.get("models") if isinstance(agent_population.get("models"), list) else []
        model_names = [str(model).strip() for model in models if str(model).strip()]
        if model_names:
            setting_parts.append("agent models=" + ", ".join(model_names))
        human_population = population.get("human") if isinstance(population.get("human"), dict) else {}
        platform = str(human_population.get("platform") or "").strip()
        if platform:
            setting_parts.append("human platform=" + platform)
        if setting_parts:
            names.append("declared experimental setting: " + "; ".join(setting_parts))
    return list(dict.fromkeys(item for item in names if item))


def _exp_plan_seed_policy(project: dict[str, Any]) -> str:
    declared = _extract_exp_plan_seeds(project)
    seeds = _execution_seed_ensemble(project)
    origin = "predeclared" if declared else "stable default"
    return f"Use the {origin} ResearchOS seed ensemble: " + ", ".join(str(seed) for seed in seeds)


def _protocol_decisions_from_plan(exp_plan: dict[str, Any], project: dict[str, Any]) -> list[str]:
    """Return explicit T4.5 protocol decisions without inventing a resolution."""

    decisions: list[str] = []
    raw_unknowns = exp_plan.get("unknown_fields") if isinstance(exp_plan, dict) else []
    if isinstance(raw_unknowns, list):
        decisions.extend(str(item).strip() for item in raw_unknowns if str(item).strip())
    elif isinstance(raw_unknowns, str) and raw_unknowns.strip():
        decisions.append(raw_unknowns.strip())
    return list(dict.fromkeys(decisions))


def _phase_b_operational_resolution(
    workspace: Path,
    decisions: list[str],
    *,
    has_declared_setting: bool,
) -> list[dict[str, Any]]:
    """Accept Phase B as an operational resolver without changing research scope.

    A reviewed Phase B report can supply the concrete public package, version,
    and provenance for an already declared study setting.  It must not choose a
    new research task or replace a T4.5-required comparison.  This function
    therefore resolves only categories whose exact operational value belongs in
    executor configs and source records, and only after ResearchOS accepted the
    bounded Phase B receipt.
    """

    acceptance = _read_json(workspace / "external_executor" / "report" / "resource_preparation_acceptance.json")
    report = _read_json(workspace / "external_executor" / "report" / "phase_B" / "resource_preparation_report.json")
    validation = _read_json(workspace / "external_executor" / "report" / "phase_B" / "validation_report.json")
    source_report = _read_json(workspace / "external_executor" / "report" / "phase_B" / "resource_source_report.json")
    readiness = report.get("resource_readiness") if isinstance(report.get("resource_readiness"), dict) else {}
    operational_settings = report.get("operational_settings") if isinstance(report.get("operational_settings"), dict) else {}
    setting_records = operational_settings.get("items") if isinstance(operational_settings.get("items"), list) else []
    if not (
        acceptance.get("semantics") == "t5_resource_preparation_acceptance"
        and acceptance.get("ok") is True
        and report.get("schema_version") == "resource_preparation_report.v1"
        and report.get("child_skill") == "resource-and-baseline-preparation"
        and readiness.get("status") in {"ready", "partial"}
        and validation.get("schema_version") == "resource_preparation_validation.v1"
        and validation.get("valid") is True
        and source_report.get("schema_version") == "resource_source_report.v1"
    ):
        return []

    categories: dict[str, str] = {
        "seed": "seed_policy",
        "environment": "runtime_environment",
        "backbone": "model_backbone",
        "scale": "execution_scale",
        "benchmark": "declared_benchmark_resource",
    }

    def classify(value: str) -> str | None:
        lowered = value.casefold()
        if "seed" in lowered or "随机种子" in value:
            return "seed"
        if any(token in lowered for token in ("framework", "simulat", "environment")) or "仿真" in value:
            return "environment"
        if any(token in lowered for token in ("backbone", "model", "agent")) or "骨干" in value:
            return "backbone"
        if any(token in lowered for token in ("scale", "sample", "episode", "rollout", "budget")) or any(token in value for token in ("规模", "预算", "样本")):
            return "scale"
        if ("benchmark" in lowered or "数据集" in value) and has_declared_setting:
            return "benchmark"
        return None

    source_paths = [
        "external_executor/report/phase_B/resource_preparation_report.json",
        "external_executor/report/phase_B/resource_source_report.json",
        "external_executor/report/phase_B/validation_report.json",
    ]
    records: list[dict[str, Any]] = []
    for decision in decisions:
        category = classify(decision)
        if category is None:
            continue
        setting_type = categories[category]
        setting_record = next(
            (
                item
                for item in setting_records
                if isinstance(item, dict)
                and str(item.get("setting") or "").strip() == decision
                and str(item.get("setting_type") or "").strip() == setting_type
                and item.get("status") == "resolved"
                and isinstance(item.get("selected_value"), str)
                and item.get("selected_value", "").strip()
                and isinstance(item.get("selection_basis"), str)
                and item.get("selection_basis", "").strip()
                and isinstance(item.get("source_refs"), list)
                and any(str(ref).strip() for ref in item.get("source_refs", []))
                and item.get("research_boundary_preserved") is True
            ),
            None,
        )
        if setting_record is None:
            continue
        selected_value = str(setting_record["selected_value"]).strip()
        selection_basis = str(setting_record["selection_basis"]).strip()
        source_refs = [str(ref).strip() for ref in setting_record.get("source_refs", []) if str(ref).strip()]
        records.append(
            {
                "setting": decision,
                "setting_type": setting_type,
                "selected_value": selected_value,
                "resolution": f"Use `{selected_value}`. {selection_basis}",
                "authority": "phase_b_source_backed_operational_resolution",
                "resource_readiness": readiness.get("status"),
                "source_paths": list(dict.fromkeys(source_paths + source_refs)),
                "research_boundary_preserved": True,
            }
        )
    return records


def _execution_readiness(
    *,
    missing_required_sources: list[str],
    protocol_missing: list[str],
    protocol_decisions: list[str],
) -> dict[str, Any]:
    """Separate handoff compilation from authorization to run an experiment."""

    all_stages = [
        "context_alignment",
        "resource_and_baseline_preparation",
        "experiment_design",
        "implementation",
        "experiment_run",
        "result_diagnosis",
        "writer_handoff",
    ]
    if missing_required_sources or protocol_missing:
        return {
            "status": "blocked",
            "allowed_stages": [],
            "blocked_stages": all_stages,
            "required_decisions": [],
            "formal_execution_allowed": False,
            "reason": "Required source material or the minimum experiment contract is absent.",
        }
    if protocol_decisions:
        return {
            "status": "protocol_decision_required",
            "allowed_stages": ["context_alignment", "resource_and_baseline_preparation"],
            "blocked_stages": [stage for stage in all_stages if stage not in {"context_alignment", "resource_and_baseline_preparation"}],
            "required_decisions": protocol_decisions,
            "formal_execution_allowed": False,
            "reason": "The research contract is compiled, but explicit T4.5 protocol decisions remain before implementation or formal runs.",
        }
    return {
        "status": "ready",
        "allowed_stages": all_stages,
        "blocked_stages": [],
        "required_decisions": [],
        "formal_execution_allowed": True,
        "reason": "The compiled experiment contract has no unresolved protocol decision recorded by T4.5.",
    }


def _exp_baseline_records(exp_plan: dict[str, Any], workspace: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    novelty_required = _extract_required_baselines(workspace)
    for item in novelty_required:
        name = _baseline_name(item)
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            records.append(
                {
                    "name": name,
                    "source": str(item.get("source") or "ideation/novelty_audit.md"),
                    "why": str(item.get("reason_required") or "Required by novelty audit"),
                    "requirement": "required",
                    "role": "nearest_work",
                }
            )
    def add_plan_baselines(baseline_items: list[Any]) -> None:
        for item in baseline_items:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("baseline_name") or "").strip()
                source = str(item.get("source") or "ideation/exp_plan.yaml")
                why = str(item.get("why") or item.get("rationale") or item.get("purpose") or "Listed in experiment plan")
            else:
                name = str(item or "").strip()
                source = "ideation/exp_plan.yaml"
                why = "Listed in experiment plan"
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            lowered = name.casefold()
            role = "canonical"
            if "htce" in lowered or "nearest" in lowered:
                role = "nearest_work"
            elif "target" in lowered or "lower" in lowered:
                role = "lower_bound"
            elif "naive" in lowered or "random" in lowered or "sanity" in lowered:
                role = "sanity_check"
            elif "m1-" in lowered:
                role = "component_source"
            records.append({"name": name, "source": source, "why": why, "requirement": "required", "role": role})

    root_baselines: list[Any] = []
    for key in ("required_baselines", "baselines", "baseline_methods"):
        value = exp_plan.get(key) if isinstance(exp_plan, dict) else None
        if isinstance(value, list):
            root_baselines.extend(value)
        elif value:
            root_baselines.append(value)
    add_plan_baselines(root_baselines)

    for exp in _experiments_from_plan(exp_plan):
        baseline_items: list[Any] = []
        for key in ("required_baselines", "baselines", "baseline_methods"):
            value = exp.get(key)
            if isinstance(value, list):
                baseline_items.extend(value)
            elif value:
                baseline_items.append(value)
        add_plan_baselines(baseline_items)
    return records


def _build_reboost_baseline_matrix(exp_plan: dict[str, Any], workspace: Path, claim_ids: list[str]) -> list[dict[str, Any]]:
    records = _exp_baseline_records(exp_plan, workspace)
    baselines: list[dict[str, Any]] = []
    for idx, record in enumerate(records, start=1):
        name = record["name"]
        source_id = "SRC_NOVELTY" if "novelty" in record.get("source", "") else "SRC_EXP_PLAN"
        baselines.append(
            {
                "baseline_id": f"B{idx}",
                "name": name,
                "role": record.get("role") or "canonical",
                "requirement": record.get("requirement") or "required",
                "category": "comparative_method",
                "rationale": _compact_text(record.get("why") or "Required for fair external evaluation", limit=300),
                "availability": "candidate",
                "implementation_source": record.get("source") or None,
                "reproduction_target": "Reproduce or document a faithful comparable implementation before making strong claims.",
                "fairness_contract": {
                    "same_data_split": True,
                    "same_metric_definition": True,
                    "same_tuning_budget": True,
                    "same_evaluation_protocol": True,
                    "additional_constraints": [
                        "Record configs, logs, seeds, and raw outputs for every baseline run.",
                        "Do not weaken a novelty-required baseline without human review.",
                    ],
                },
                "substitution_policy": {
                    "allowed": False,
                    "conditions": [],
                    "approval_required": "human",
                    "candidate_substitutes": [],
                },
                "linked_claim_ids": claim_ids,
                "source_refs": [
                    _source_ref(source_id, "baseline declarations", f"{name} is required or planned for comparison", "reconciled")
                ],
            }
        )
    return baselines


def _build_reboost_claims(exp_plan: dict[str, Any], metrics: list[str], baseline_ids: list[str], module_ids: list[str]) -> list[dict[str, Any]]:
    experiments = _experiments_from_plan(exp_plan)
    if not experiments:
        return []
    claims: list[dict[str, Any]] = []
    for idx, exp in enumerate(experiments[:4], start=1):
        claim_id = f"C{idx}"
        name = str(exp.get("title") or exp.get("name") or exp.get("id") or f"Experiment {idx}")
        exp_metrics = []
        for metric in exp.get("metrics", []) or metrics:
            if isinstance(metric, dict):
                name = str(metric.get("name") or metric.get("metric_id") or "").strip()
                if name:
                    exp_metrics.append(name)
            else:
                exp_metrics.append(str(metric))
        metric_text = ", ".join(list(dict.fromkeys(item for item in exp_metrics if item)) or metrics)
        claims.append(
            {
                "claim_id": claim_id,
                "statement": _compact_text(
                    str(exp.get("hypothesis_ref") or name)
                    + ": external execution must test whether the selected method supports this pre-experiment claim.",
                    limit=500,
                ),
                "claim_type": "performance" if idx == 1 else ("mechanism" if idx == 2 else "robustness"),
                "initial_strength_ceiling": "moderate",
                "pre_experiment_status": "untested",
                "related_module_ids": module_ids,
                "required_baseline_ids": baseline_ids,
                "evidence_requirements": [
                    {
                        "experiment_id": "E_FORMAL",
                        "evidence_type": "main_result" if idx == 1 else "ablation",
                        "dataset_or_setting": _compact_text(name, limit=160),
                        "metric_or_observation": metric_text or "unknown_metric_requires_protocol",
                        "comparison": "Compare against every required baseline under the same split, seed policy, and metric definition.",
                        "acceptance_criterion": "Support only if raw metrics, configs, logs, and baseline coverage are preserved in the final T8 handoff materials.",
                    }
                ],
                "support_criteria": [
                    "Required baselines are reproduced or explicitly audited as unavailable.",
                    "The formal run beats the relevant baseline under the predeclared metrics or yields a documented negative result.",
                ],
                "weaken_criteria": [
                    "Only one dataset, seed, or baseline is covered.",
                    "A planned ablation is missing or inconclusive.",
                ],
                "falsification_criteria": [
                    "The method does not outperform the declared lower-bound and nearest-work baselines.",
                    "A component ablation shows no measurable contribution for the claimed mechanism.",
                ],
                "prohibited_interpretations": [
                    "Do not interpret mock-only or dry-run outputs as empirical evidence.",
                    "Do not claim novelty beyond the T4.5 novelty boundary.",
                ],
                "source_refs": [
                    _source_ref("SRC_EXP_PLAN", f"experiments[{idx - 1}]", "Experiment plan defines the comparison to be tested"),
                    _source_ref("SRC_HYPOTHESES", "selected hypotheses", "Hypotheses define the pre-experiment claim boundary"),
                ],
            }
        )
    return claims


def _build_reboost_writer_contract() -> dict[str, Any]:
    return {
        "required_artifacts": [
            {
                "artifact_id": f"WA{idx}",
                "artifact_type": artifact_type,
                "description": f"Audited {artifact_type.replace('_', ' ')} from external execution.",
                "source_of_truth": "external_executor/executor_research_report.md with supporting external_executor artifacts",
                "requires_t7_audit": True,
                "requires_handoff_validation": True,
                "required_fields": ["path_or_json_pointer", "provenance", "audit_status"],
            }
            for idx, artifact_type in enumerate(RESEARCH_REBOOST_WRITER_ARTIFACT_TYPES, start=1)
        ],
        "must_include": [
            "raw result references, configs, logs, hashes, and run IDs",
            "baseline coverage status and substitutions, if any",
            "claim weakening and must-not-claim boundaries",
            "realized method package rather than draft method intent",
        ],
        "must_not_use_as_final_fact_source": [
            "method_intent",
            "research_context",
            "research_proposal",
            "initial_framework_figure_sketch",
            "unaudited_raw_results",
            "diagnostic_hint",
            "unsupported_claim",
        ],
    }


def _build_reboost_ordered_gates() -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for idx, stage in enumerate(RESEARCH_REBOOST_GATE_STAGES, start=1):
        gate_id = f"G{idx:02d}"
        gates.append(
            {
                "gate_id": gate_id,
                "order": idx,
                "stage": stage,
                "depends_on_gate_ids": [f"G{idx - 1:02d}"] if idx > 1 else [],
                "required_inputs": ["external_executor/handoff_pack.json"] if idx == 1 else [f"output_of_G{idx - 1:02d}"],
                "required_outputs": [f"external_executor/{stage}_artifact"],
                "pass_conditions": [f"{stage} output is present, auditable, and within allowed paths"],
                "on_failure": "diagnose_and_decide" if stage in {"result_diagnosis", "refinement_decision"} else "repair_and_retry",
            }
        )
    return gates


def _build_reboost_pack(workspace: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    inventory = _research_reboost_inventory(workspace)
    source_manifest = [item for item in inventory.get("sources", []) if isinstance(item, dict)]
    project = _read_yaml(workspace / "project.yaml")
    exp_plan = _read_yaml(workspace / "ideation" / "exp_plan.yaml")
    hypotheses = _read_text(workspace / "ideation" / "hypotheses.md", max_chars=20000)
    dossier = _read_json(workspace / "ideation" / "research_dossier.json")
    if dossier.get("semantics") != "t45_research_dossier" or dossier.get("status") != "formalized_after_novelty_pass":
        dossier = {}
    selected_candidate = _read_json(workspace / "ideation" / "selected" / "selected_candidate.json")
    contribution_map = _read_yaml(workspace / "ideation" / "contribution_hypothesis_map.yaml")
    proposal_context = _load_t45_proposal_context(
        workspace,
        dossier=dossier,
        hypotheses=hypotheses,
    )
    if proposal_context["source_type"] != "formal_proposal":
        for source in source_manifest:
            if source.get("path") not in {PROPOSAL_REL_PATH, PROPOSAL_MANIFEST_REL_PATH}:
                continue
            if source.get("availability") == "available":
                source["used"] = False
                source["used_for"] = []
                source["omission_reason"] = (
                    "Proposal is present but does not satisfy the current post-novelty proposal contract; "
                    "T5 uses the explicit legacy formalization fallback instead."
                )
    synthesis = _read_text(workspace / "literature" / "synthesis.md", max_chars=16000)
    novelty = _read_text(workspace / "ideation" / "novelty_audit.md", max_chars=16000)
    risks, risk_source = _first_existing_text(
        workspace,
        ["ideation/kill_criteria.yaml", "ideation/risks.md", "ideation/validation_map.yaml"],
        max_chars=8000,
    )
    goal = _extract_project_goal(project, exp_plan, hypotheses)
    research_question = _extract_research_question(project, goal)
    research_context = _build_t45_research_context(
        dossier=dossier,
        selected_candidate=selected_candidate,
        hypotheses=hypotheses,
        contribution_map=contribution_map,
        proposal_context=proposal_context,
    )
    resource_discovery_context = _build_resource_discovery_context(workspace, source_manifest)
    metrics = _extract_exp_plan_metrics(exp_plan)
    datasets = _extract_datasets(exp_plan)
    experiments = _experiments_from_plan(exp_plan)
    module_id = "M1"
    claim_ids = [f"C{idx}" for idx, _exp in enumerate(experiments[:4], start=1)]
    baselines = _build_reboost_baseline_matrix(exp_plan, workspace, claim_ids)
    baseline_ids = [item["baseline_id"] for item in baselines]
    claims = _build_reboost_claims(exp_plan, metrics, baseline_ids, [module_id])
    claim_ids = [item["claim_id"] for item in claims]
    for baseline in baselines:
        baseline["linked_claim_ids"] = claim_ids
    assumptions = _extract_assumptions(hypotheses)
    mechanism = _compact_text(
        _section_hint(hypotheses, ["Mechanism", "技术机制", "机制"]) or _section_hint(synthesis, ["mechanism", "method"]),
        limit=900,
    ) or goal
    novelty_constraints = []
    if "Final Gate Verdict" in novelty or "must not" in novelty.lower() or "不能" in novelty:
        novelty_constraints.append("Preserve T4.5 novelty warnings and must-not-claim boundaries during external execution.")
    if "collision" in novelty.lower() or "碰撞" in novelty:
        novelty_constraints.append("Treat component-level collision risks as claim ceilings, not as executable method shortcuts.")
    if not novelty_constraints:
        novelty_constraints.append("Do not exceed the novelty boundary declared in ideation/novelty_audit.md.")
    mismatch_records: list[dict[str, Any]] = []
    if "drop_due_to_collision" in novelty or "true_collision" in novelty:
        mismatch_records.append(
            {
                "mismatch_id": "MM1",
                "severity": "warning",
                "topic": "Component novelty constraints",
                "conflicting_statements": [
                    "Some component hypotheses are marked as collision risks in novelty audit.",
                    "The experiment plan still uses those components as part of the executable architecture.",
                ],
                "resolution_status": "resolved_by_precedence",
                "selected_resolution": "Keep the components as implementation constraints but prohibit claiming them as standalone inventions.",
                "rationale": "The novelty audit controls claim ceilings while the experiment plan controls executable protocol details.",
                "affected_fields": ["claim_boundaries", "method_intent", "baseline_matrix"],
                "requires_human_review": False,
                "source_refs": [
                    _source_ref("SRC_NOVELTY", "Final Gate Verdict / collision notes", "Novelty audit constrains contribution claims", "direct"),
                    _source_ref("SRC_EXP_PLAN", "experiments", "Experiment plan keeps component tests as protocol steps", "reconciled"),
                ],
            }
        )

    status = "completed"
    unresolved_items: list[dict[str, Any]] = []
    missing_required = _missing_reboost_source_groups(source_manifest)
    if missing_required:
        status = "blocked"
        for idx, missing in enumerate(missing_required, start=1):
            unresolved_items.append(
                {
                    "item_id": f"U{idx}",
                    "severity": "blocking",
                    "question": f"Required Pre-T5 source is missing: {missing}",
                    "why_unresolved": "Research reboost cannot mark a completed execution contract without this required source.",
                    "affected_fields": ["source_manifest", "context_reboost", "validation_summary"],
                    "blocking": True,
                    "owner": "human",
                    "required_action": f"Restore or regenerate one current source for {missing}, then rerun T5-REBOOST.",
                    "source_refs": [_source_ref("SRC_PROJECT", "project root", "Missing source was detected by deterministic inventory")],
                }
            )

    protocol_missing: list[str] = []
    if not experiments:
        protocol_missing.append("experiments")
    if not datasets:
        protocol_missing.append("dataset_or_benchmark")
    if not metrics:
        protocol_missing.append("metrics")
    if not baselines:
        protocol_missing.append("required_baselines")
    if not claims:
        protocol_missing.append("claim_evidence_mapping")
    if protocol_missing:
        status = "blocked"
        unresolved_items.append(
            {
                "item_id": f"U{len(unresolved_items) + 1}",
                "severity": "blocking",
                "question": "The minimum experiment contract is incomplete in the fields listed below.",
                "why_unresolved": "ResearchOS may preserve the handoff draft, but it cannot invent the missing experiment protocol fields or authorize formal execution without them.",
                "affected_fields": protocol_missing,
                "blocking": True,
                "owner": "human",
                "required_action": "Provide or approve the listed source-backed experiment fields in ideation/exp_plan.yaml, then rerun T5-REBOOST.",
                "source_refs": [_source_ref("SRC_EXP_PLAN", "experiments", "Deterministic protocol completeness check")],
            }
        )

        # Claim rows reference formal experiment IDs.  With no formal protocol
        # there is no valid experiment to reference, so retain the source
        # deficiency in ``unresolved_items`` rather than emitting dangling
        # claim mappings that look executable.
        claims = []
        claim_ids = []
        for baseline in baselines:
            baseline["linked_claim_ids"] = []

    raw_protocol_decisions = _protocol_decisions_from_plan(exp_plan, project)
    auto_resolved_operational_settings = _phase_b_operational_resolution(
        workspace,
        raw_protocol_decisions,
        has_declared_setting=bool(datasets),
    )
    auto_resolved_decisions = {
        str(item.get("setting") or "")
        for item in auto_resolved_operational_settings
        if isinstance(item, dict)
    }
    protocol_decisions = [item for item in raw_protocol_decisions if item not in auto_resolved_decisions]
    execution_readiness = _execution_readiness(
        missing_required_sources=missing_required,
        protocol_missing=protocol_missing,
        protocol_decisions=protocol_decisions,
    )
    if execution_readiness["status"] == "protocol_decision_required":
        unresolved_items.append(
            {
                "item_id": f"U{len(unresolved_items) + 1}",
                "severity": "material",
                "question": "The handoff is compiled, but formal execution still requires the recorded protocol decisions.",
                "why_unresolved": "T4.5 explicitly retained these decisions as unknown and the current Phase B record cannot resolve them within the declared research boundary.",
                "affected_fields": protocol_decisions,
                "blocking": False,
                "owner": "human",
                "required_action": "Use T5 automatic resource preparation when the setting can be resolved from an existing study scope. Request a human decision only when resolution would change the research task, core mechanism, required baseline set, benchmark scope, or claim boundary.",
                "source_refs": [_source_ref("SRC_EXP_PLAN", "unknown_fields", "T4.5 explicitly retained protocol decisions for later confirmation")],
            }
        )

    # A blocked handoff is a durable record of genuinely missing required
    # fields. A compiled setting with bounded unknowns is different: T5 may
    # prepare its context and resources, while the protocol Gate prevents
    # implementation or formal runs until those choices are approved.
    required_experiments: list[dict[str, Any]] = []
    if not protocol_missing:
        required_experiments = [
            {
                "experiment_id": "E_REPRO",
                "run_type": "reproduction",
                "purpose": "Reproduce or audit required baselines before formal claims.",
                "datasets_or_settings": datasets,
                "metrics_or_observations": metrics,
                "baseline_ids": baseline_ids,
                "module_ids": [module_id],
                "claim_ids": claim_ids,
                "seed_policy": _exp_plan_seed_policy(project),
                "pass_conditions": ["Every required baseline has raw artifacts or an audited unavailable status."],
                "failure_interpretation": "Strong comparative claims must be narrowed or blocked.",
                "required": True,
            },
            {
                "experiment_id": "E_FORMAL",
                "run_type": "formal",
                "purpose": "Run the proposed method and required comparisons under the fixed protocol.",
                "datasets_or_settings": datasets,
                "metrics_or_observations": metrics,
                "baseline_ids": baseline_ids,
                "module_ids": [module_id],
                "claim_ids": claim_ids,
                "seed_policy": _exp_plan_seed_policy(project),
                "pass_conditions": ["Formal metrics, configs, logs, and hashes are complete enough for final T8 handoff validation."],
                "failure_interpretation": "Report a negative or narrowed result; do not invent support.",
                "required": True,
            },
        ]

    pack = {
        "schema_version": "external_executor_handoff.v1",
        "pack_id": f"HP_{_safe_schema_id('P', project.get('project_id') or 'project', 1)}",
        "generated_at": _now_iso(),
        "generation_status": status,
        "project": {
            "project_id": _safe_schema_id("P", project.get("project_id") or project.get("name") or "project", 1),
            "title": _compact_text(str(project.get("title") or project.get("project_id") or "ResearchOS project"), limit=180),
            "research_area": _compact_text(str(project.get("research_direction") or project.get("domain") or "research"), limit=500),
            "task_type": _compact_text(str((project.get("metadata") or {}).get("manuscript_type") or "external_experiment_handoff"), limit=120),
            "workspace_root": ".",
        },
        "source_manifest": source_manifest,
        "context_reboost": {
            "project_goal": {
                "statement": goal,
                "success_criteria": [
                    "External executor returns audited result_pack/status/manifest artifacts.",
                    "Every strong claim remains tied to required baselines, metrics, and the final T8 handoff evidence.",
                ],
                "out_of_scope": [
                    "Writing paper prose during T5",
                    "Treating method_intent as final Method source",
                    "Using mock-only outputs as empirical evidence",
                ],
                "source_refs": [_source_ref("SRC_PROJECT", "research_question/research_direction", "Project file defines the research scope")],
            },
            "research_question": {
                "statement": _compact_text(research_context["research_problem"] or research_question, limit=500),
                "source_refs": research_context["source_refs"],
            },
            "central_hypothesis": {
                "hypothesis_id": "H1",
                "statement": _compact_text(str(exp_plan.get("goal") or _first_non_heading_line(hypotheses) or goal), limit=900),
                "causal_rationale": _compact_text(mechanism, limit=700),
                "assumptions": assumptions,
                "falsification_criteria": [
                    "Required baselines outperform or match the proposed method under the predeclared metrics.",
                    "Core component ablations fail to show the claimed mechanism contribution.",
                    "Final handoff validation or T8 claim audit finds provenance, fairness, or scope violations that block the claim.",
                ],
                "source_refs": [
                    _source_ref("SRC_HYPOTHESES", "selected hypothesis / mechanism sections", "Hypotheses define the central claim"),
                    _source_ref("SRC_EXP_PLAN", "goal and experiments", "Experiment plan operationalizes the hypothesis", "reconciled"),
                    *([_source_ref("SRC_RESEARCH_DOSSIER", "central_thesis", "Dossier preserves the formalized thesis and its evidence status", "reconciled")] if dossier else []),
                ],
            },
            "research_context": research_context,
            **(
                {"resource_discovery_context": resource_discovery_context}
                if resource_discovery_context is not None
                else {}
            ),
            "study_scope": {
                "target_setting": _compact_text(research_question, limit=500),
                "tasks": [
                    str(exp.get("task") or exp.get("name") or exp.get("id") or "").strip()
                    for exp in experiments
                    if str(exp.get("task") or exp.get("name") or exp.get("id") or "").strip()
                ],
                "datasets": datasets,
                "metrics": metrics,
                "constraints": [
                    _exp_plan_seed_policy(project),
                    "Keep evaluation protocol and metric definitions fixed across baselines.",
                ],
                "exclusions": [
                    "No paper claims before external_executor/executor_research_report.md exists and T8 validates the evidence boundary.",
                    "No unapproved scope change to task, benchmark, or contribution type.",
                ],
                "source_refs": [_source_ref("SRC_EXP_PLAN", "datasets/metrics/experiments", "Experiment plan defines scope")],
            },
            "method_mechanism": {
                "core_mechanism": mechanism,
                "contribution_intent": _compact_text(
                    "; ".join(research_context["contribution_intent"])
                    or "Test the selected architecture as a bounded, auditable research contribution under novelty-audit claim ceilings.",
                    limit=700,
                ),
                "mechanism_invariants": [
                    {
                        "invariant_id": "I1",
                        "statement": "The external executor must preserve the central mechanism while implementation details may be refined and audited.",
                        "rationale": "Silent contribution drift would invalidate the pre-T5 hypothesis and T4.5 novelty boundary.",
                        "source_refs": [
                            _source_ref("SRC_HYPOTHESES", "technical mechanism", "Hypotheses define the mechanism to preserve"),
                            _source_ref("SRC_NOVELTY", "claim boundaries", "Novelty audit constrains contribution drift", "reconciled"),
                        ],
                    }
                ],
                "must_preserve_module_ids": [module_id],
                "candidate_module_ids": [],
                "source_refs": [
                    _source_ref("SRC_HYPOTHESES", "technical mechanism", "Hypotheses define the method mechanism"),
                    *([_source_ref("SRC_CONTRIBUTION_MAP", "contribution-to-hypothesis links", "Contribution map constrains the intended contribution", "reconciled")] if contribution_map else []),
                    *([_source_ref("SRC_RESEARCH_DOSSIER", "contributions", "Dossier retains the selected contribution intent", "reconciled")] if dossier else []),
                ],
            },
            "novelty_audit_resolution": {
                "status": "mismatch" if mismatch_records else "aligned",
                "nearest_work": [baseline["name"] for baseline in baselines[:5]],
                "distinguishing_mechanism": "The executable contribution is limited to the combined system and its audited evidence, not unverified standalone component novelty.",
                "required_baseline_ids": baseline_ids,
                "claim_constraints": novelty_constraints,
                "source_refs": [_source_ref("SRC_NOVELTY", "Final Gate Verdict / Required actions", "Novelty audit sets claim boundaries")],
            },
            "execution_priorities": [
                {
                    "priority": 1,
                    "objective": "Preserve the formalized research problem, conditional implications, and contribution intent while testing rather than asserting them.",
                    "rationale": "T4 decision context guides implementation scope and interpretation but remains pre-experiment context, not empirical evidence.",
                    "source_refs": research_context["source_refs"],
                },
                {
                    "priority": 2,
                    "objective": "Align executor scope with the Pre-T5 hypothesis, novelty constraints, and experiment plan.",
                    "rationale": "Context alignment prevents implementation drift before resource or code work begins.",
                    "source_refs": [_source_ref("SRC_EXP_PLAN", "experiments", "Experiment plan defines the execution order")],
                },
                {
                    "priority": 3,
                    "objective": "Reproduce or audit required baselines before strong comparative claims.",
                    "rationale": "Missing baselines directly lower claim strength and novelty confidence.",
                    "source_refs": [_source_ref("SRC_NOVELTY", "baseline and claim boundary discussion", "Novelty audit controls baseline requirements")],
                },
            ],
            "risk_register": [
                {
                    "risk_id": "R1",
                    "category": "evaluation",
                    "description": _compact_text(risks or "Baseline, dataset, and ablation coverage may be insufficient for strong claims.", limit=500),
                    "likelihood": "medium",
                    "impact": "high",
                    "mitigation": "Require baseline reproduction, ablation diagnostics, and conservative claim narrowing in result_pack.",
                    "source_refs": [_source_ref("SRC_RISKS", "Top risks", f"{risk_source or 'Risk artifact'} defines execution and scientific risks")],
                }
            ],
            "known_context_mismatches": mismatch_records,
        },
        "method_intent": {
            "status": "draft_intent_only",
            "not_final_method_source": True,
            "central_mechanism_hypothesis": _compact_text(mechanism, limit=700),
            "candidate_modules": [
                {
                    "module_id": module_id,
                    "name": _compact_text(str((_experiments_from_plan(exp_plan)[-1].get("our_method") or {}).get("name") if _experiments_from_plan(exp_plan) and isinstance(_experiments_from_plan(exp_plan)[-1].get("our_method"), dict) else "Selected method"), limit=120),
                    "classification": "core",
                    "intended_role": "Implement the selected Pre-T5 method closely enough to test the central hypothesis.",
                    "mechanism": _compact_text(mechanism, limit=600),
                    "expected_inputs": ["datasets, treatments, features, baselines, and seed policy from ideation/exp_plan.yaml"],
                    "expected_outputs": ["audited metrics, ablations, raw artifacts, realized method package, and writer handoff"],
                    "depends_on_module_ids": [],
                    "why_it_may_help": "It encodes the mechanism selected by T4 and bounded by T4.5 before external execution.",
                    "implementation_constraints": [
                        "Do not replace the core mechanism without human review.",
                        "Do not drop required baselines or change benchmark scope silently.",
                    ],
                    "related_claim_ids": claim_ids,
                    "planned_ablation_ids": ["A1"],
                    "source_refs": [_source_ref("SRC_HYPOTHESES", "method mechanism", "Hypotheses define the core module")],
                }
            ],
            "expected_algorithm_flow": [
                {
                    "step_id": "S1",
                    "order": 1,
                    "description": "Prepare data, resources, baselines, and exact metric protocol.",
                    "module_ids": [module_id],
                    "inputs": ["project artifacts", "experiment materials", "baseline specifications"],
                    "outputs": ["resource inventory", "protocol snapshot", "baseline readiness"],
                },
                {
                    "step_id": "S2",
                    "order": 2,
                    "description": "Implement and evaluate the selected method under the fixed protocol.",
                    "module_ids": [module_id],
                    "inputs": ["prepared resources", "method constraints", "seed policy"],
                    "outputs": ["raw results", "configs", "logs", "realized method package"],
                },
            ],
            "allowed_refinements": [
                {
                    "refinement_id": "REF1",
                    "description": "Tune hyperparameters or repair implementation details without changing task, contribution, or required baselines.",
                    "boundary": "Every refinement must be recorded in result_pack and preserve the central mechanism.",
                    "review_requirement": "none",
                }
            ],
            "forbidden_silent_changes": [
                {
                    "change_type": "replace_core_mechanism",
                    "description": "Replacing the selected mechanism invalidates the T4/T4.5 contract.",
                    "required_action": "human_review",
                },
                {
                    "change_type": "drop_required_baseline",
                    "description": "Dropping a required baseline changes the claim ceiling.",
                    "required_action": "human_review",
                },
                {
                    "change_type": "change_task_or_benchmark",
                    "description": "Changing task, dataset, or benchmark scope breaks the experiment contract.",
                    "required_action": "human_review",
                },
                {
                    "change_type": "change_contribution_type",
                    "description": "Changing contribution type requires returning to novelty review.",
                    "required_action": "rerun_novelty",
                },
                {
                    "change_type": "promote_engineering_trick",
                    "description": "Engineering convenience must not become the paper contribution.",
                    "required_action": "human_review",
                },
            ],
            "mechanism_to_ablation_plan": [
                {
                    "ablation_id": "A1",
                    "mechanism": "Core mechanism contribution",
                    "module_ids": [module_id],
                    "planned_test": "Remove or replace the core mechanism while preserving the same data, metrics, and baseline protocol.",
                    "control": "A fair baseline or component-removal variant from the experiment plan.",
                    "expected_if_supported": "The full method beats the ablated/control variant under audited metrics.",
                    "expected_if_not_supported": "The ablated/control variant matches or beats the full method, forcing claim narrowing.",
                    "related_claim_ids": claim_ids,
                }
            ],
            "initial_framework_figure_sketch": {
                "status": "draft_intent_only",
                "purpose": "Guide external implementation and later audited figure construction.",
                "main_message": goal,
                "candidate_panels": ["problem setting", "method modules", "evaluation and evidence"],
                "candidate_nodes": ["inputs", "core module", "baselines", "audited outputs"],
                "candidate_edges": ["data flow", "module dependency", "claim evidence linkage"],
                "must_not_be_used_directly_by_t8": True,
            },
            "source_refs": [
                _source_ref("SRC_HYPOTHESES", "method mechanism", "Method intent is derived from the reconciled hypothesis"),
                _source_ref("SRC_NOVELTY", "claim boundaries", "Draft intent is not a final Method source", "reconciled"),
            ],
        },
        "baseline_matrix": baselines,
        "claim_evidence_matrix": claims,
        "minimum_experiment_loop": {
            "required_experiments": required_experiments,
            "ordered_gates": _build_reboost_ordered_gates(),
        },
        "iteration_budget": {
            "max_rounds": 3,
            "max_wall_time_hours": None,
            "compute_budget": str((project.get("constraints") or {}).get("max_budget_usd") or "project budget"),
            "plateau_definition": "Stop refinement when two consecutive audited rounds fail to improve the primary metric or claim support.",
            "stop_conditions": [
                {"condition": "budget_exhausted", "trigger": "Project compute or cost budget is reached.", "required_action": "stop"},
                {"condition": "improvement_plateau", "trigger": "No audited improvement after bounded refinement.", "required_action": "stop"},
                {
                    "condition": "required_baseline_unavailable",
                    "trigger": "A required baseline cannot be reproduced or substituted under policy.",
                    "required_action": "human_review",
                },
                {
                    "condition": "audited_target_reached",
                    "trigger": "Formal run satisfies the predeclared claim support threshold.",
                    "required_action": "stop",
                },
                {
                    "condition": "implementation_blocked",
                    "trigger": "The external executor cannot implement the method within allowed paths and materials.",
                    "required_action": "preserve_failure_and_stop",
                },
                {
                    "condition": "claim_must_be_narrowed",
                    "trigger": "Evidence supports only a weaker claim than planned.",
                    "required_action": "narrow_claim",
                },
            ],
        },
        "claim_boundaries": {
            "novelty_boundary": {
                "statement": "Novelty and contribution claims cannot exceed the T4.5 audit result and must credit nearest prior mechanisms.",
                "source_refs": [_source_ref("SRC_NOVELTY", "Final Gate Verdict / claim boundaries", "Novelty audit sets the maximum claim scope")],
            },
            "method_vs_engineering_boundary": {
                "statement": "Implementation repairs are engineering details unless the audited realized method supports them as research contributions.",
                "source_refs": [_source_ref("SRC_RISKS", "execution risks", "Risk and stop-condition artifact warns against contribution drift", "reconciled")],
            },
            "conditional_claims": [
                {
                    "claim_id": claim_id,
                    "maximum_strength": "moderate",
                    "conditions": ["final T8 handoff validation passes", "required baselines and ablations are covered"],
                }
                for claim_id in claim_ids
            ],
            "must_not_claim": [
                {
                    "boundary_id": "BND1",
                    "statement": "Do not use method_intent or mock-only outputs as final paper evidence.",
                    "reason": "T5 reboost is a draft execution contract; final facts require external execution and T8 handoff validation.",
                    "source_refs": [_source_ref("SRC_NOVELTY", "claim boundaries", "Novelty audit and T5 protocol constrain final claims", "reconciled")],
                }
            ],
            "narrowing_triggers": [
                "required baseline missing or weaker than expected",
                "ablation fails to support the claimed mechanism",
                "Final handoff validation reports provenance or fairness failure",
            ],
        },
        "writer_handoff_contract": _build_reboost_writer_contract(),
            "execution_contract": {
                "allowed_paths": [
                    "external_executor/",
                    "external_executor/raw_results/",
                "external_executor/configs/",
                "external_executor/logs/",
                "external_executor/patches/",
                "external_executor/figure/",
                    "external_executor/table/",
                    "external_executor/expr/",
                    "resources/",
                "literature/",
                "ideation/",
            ],
            "write_paths": [
                "external_executor/raw_results/",
                "external_executor/configs/",
                "external_executor/logs/",
                "external_executor/patches/",
                "external_executor/figure/",
                "external_executor/table/",
                "external_executor/expr/",
                "resources/",
                "external_executor/result_pack.json",
                "external_executor/executor_status.json",
                RUN_MANIFEST_PATH,
            ],
            "prohibited_paths": [
                "researchos/",
                "config/",
                "drafts/",
                "submission/",
                "_runtime/",
            ],
            "authority_rules": [
                {"action": "deploy method and baseline code under external_executor/expr", "authority": "allowed"},
                {"action": "place by-hand local resources under resources", "authority": "allowed"},
                {"action": "place remote acquisitions and baseline reimplementations under resources", "authority": "allowed"},
                {"action": "change task, benchmark, or contribution type", "authority": "human_approval"},
                {"action": "write paper claims", "authority": "forbidden"},
            ],
            "scope_change_policy": {
                "silent_changes_forbidden": True,
                "request_artifact": "external_executor/report/phase_D/scope_change_request.json",
                "major_change_action": "human_review",
                "minor_change_action": "record_and_continue",
            },
            "resource_policy": {
                "public_resources_allowed": True,
                "authenticated_resources_allowed": False,
                "license_checks_required": True,
                "checksum_required": True,
                "citation_required": True,
            },
            "source_conflict_order": [
                "project.yaml",
                "ideation/novelty_audit.md",
                "ideation/exp_plan.yaml",
                "ideation/proposal/research_proposal.md",
                "ideation/research_dossier.json",
                "ideation/selected/selected_candidate.json",
                "ideation/contribution_hypothesis_map.yaml",
                "ideation/validation_map.yaml",
                "ideation/hypotheses.md",
                "literature/synthesis.md",
            ],
            "root_skill_path": "external_executor/skills/research-execution/SKILL.md",
            "expected_outputs_schema_path": "external_executor/expected_outputs_schema.json",
            "execution_readiness": execution_readiness,
        },
        "resource_acquisition_policy": default_resource_acquisition_policy(),
        "unresolved_items": unresolved_items,
        "extensions": {
            "phase_b_operational_resolution": {
                "status": "resolved" if auto_resolved_operational_settings else "not_applied",
                "settings": auto_resolved_operational_settings,
                "boundary": "Operational settings are resolved only from an accepted Phase B report; research-task, mechanism, required-baseline, benchmark-scope, and claim-boundary changes remain outside this automation.",
            }
        },
        "validation_summary": {
            "status": "pass" if status == "completed" else "blocked",
            "required_source_coverage": _required_source_coverage(source_manifest),
            "used_source_count": _used_source_count(source_manifest),
            "inferred_statement_count": 0,
            "checks": [
                {"check_id": "CHK_SCHEMA", "status": "pass" if status == "completed" else "not_run", "message": "research-reboost schema contract assembled"},
                {"check_id": "CHK_SOURCES", "status": "pass" if not missing_required else "fail", "message": "required source coverage checked"},
                {"check_id": "CHK_METHOD_INTENT", "status": "pass", "message": "method_intent is draft_intent_only and not a final Method source"},
                {
                    "check_id": "CHK_EXECUTION_READINESS",
                    "status": "pass" if execution_readiness["status"] == "ready" else ("warning" if execution_readiness["status"] == "protocol_decision_required" else "fail"),
                    "message": execution_readiness["reason"],
                },
            ],
        },
    }
    report = {
        "version": "1.0",
        "semantics": "external_executor_context_reboost_report",
        "skill": "skills/research-reboost/SKILL.md",
        "skill_sha256": _sha256(_research_reboost_skill_dir() / "SKILL.md"),
        "handoff_pack": "external_executor/handoff_pack.json",
        "generation_status": status,
        "execution_readiness": execution_readiness,
        "protocol_missing_fields": protocol_missing,
        "protocol_decisions": protocol_decisions,
        "auto_resolved_operational_settings": auto_resolved_operational_settings,
        "source_files_used": [item.get("path") for item in source_manifest if item.get("used")],
        "missing_required_sources": missing_required,
        "missing_optional_sources": [
            item.get("path")
            for item in source_manifest
            if item.get("requirement") == "optional_backtrack" and item.get("availability") != "available"
        ],
        "known_context_mismatches": mismatch_records,
        "validation_report": REBOOST_VALIDATION_REPORT_PATH,
    }
    return pack, report


def _allowed_path_rules_for_external_executor() -> list[str]:
    return [
        "rw  resources/",
        "rw  external_executor/raw_results/",
        "rw  external_executor/configs/",
        "rw  external_executor/logs/",
        "rw  external_executor/patches/",
        "rw  external_executor/figure/",
        "rw  external_executor/table/",
        "rw  external_executor/expr/",
        "rw  external_executor/env/",
        "rw  external_executor/runs/",
        "rw  external_executor/reviews/",
        "rw  external_executor/method_specs/",
        "rw  external_executor/evidence_package/",
        "rw  external_executor/report/phase_A/",
        "rw  external_executor/report/phase_B/",
        "rw  external_executor/report/phase_C/",
        "rw  external_executor/report/phase_D/",
        "rw  external_executor/report/phase_E/",
        "rw  external_executor/report/phase_F/",
        "rw  external_executor/resource_requirement_matrix.json",
        "rw  external_executor/experiment_plan.json",
        "rw  external_executor/method_implementation_spec.json",
        "rw  external_executor/module_attribution_report.json",
        "rw  external_executor/result_diagnosis_report.json",
        "rw  external_executor/result_diagnosis/",
        "rw  external_executor/executor_research_report.md",
        "rw  external_executor/result_pack.json",
        "rw  external_executor/executor_status.json",
        f"rw  {RUN_MANIFEST_PATH}",
        "rw  external_executor/job_state.json",
        "ro  external_executor/handoff_pack.json",
        "ro  external_executor/expected_outputs_schema.json",
        f"ro  {EXECUTOR_SELECTION_PATH}",
        f"ro  {EXECUTOR_CAPABILITIES_PATH}",
        "ro  external_executor/project_skill_context.yaml",
        "ro  external_executor/report/skill_specialization_report.json",
        "ro  novelty/",
        "ro  literature/",
        "ro  ideation/",
        "ro  user_seeds/",
        "no  researchos/",
        "no  config/",
        "no  drafts/",
        "no  submission/",
        "no  _runtime/",
    ]


def _guide_view_from_reboost_pack(pack: dict[str, Any], project: dict[str, Any], metrics: list[str]) -> dict[str, Any]:
    baselines = [
        {
            "baseline_id": item.get("baseline_id"),
            "baseline_name": item.get("name"),
            "reason_required": item.get("rationale"),
            "source": item.get("implementation_source"),
        }
        for item in pack.get("baseline_matrix", [])
        if isinstance(item, dict)
    ]
    execution_contract = pack.get("execution_contract") if isinstance(pack.get("execution_contract"), dict) else {}
    readiness = execution_contract.get("execution_readiness") if isinstance(execution_contract.get("execution_readiness"), dict) else {}
    extensions = pack.get("extensions") if isinstance(pack.get("extensions"), dict) else {}
    phase_b_resolution = extensions.get("phase_b_operational_resolution") if isinstance(extensions.get("phase_b_operational_resolution"), dict) else {}
    effective_metrics = metrics or [
        str(item).strip()
        for item in ((pack.get("context_reboost") or {}).get("study_scope") or {}).get("metrics", [])
        if str(item).strip()
    ]
    return {
        "project_id": (pack.get("project") or {}).get("project_id") or project.get("project_id") or "unknown",
        "project": pack.get("project") or {},
        "experiment_intent_oneliner": ((pack.get("context_reboost") or {}).get("project_goal") or {}).get("statement"),
        "context_reboost": pack.get("context_reboost"),
        "resource_acquisition_policy": pack.get("resource_acquisition_policy") or default_resource_acquisition_policy(),
        "method_intent": pack.get("method_intent"),
        "baseline_matrix": pack.get("baseline_matrix"),
        "claim_evidence_matrix": pack.get("claim_evidence_matrix"),
        "required_baselines": baselines,
        "metrics": [{"metric_id": f"metric_{idx}", "name": name, "direction": "unknown", "primary": idx == 1} for idx, name in enumerate(effective_metrics, start=1)],
        "seeds": _execution_seed_ensemble(project),
        "execution_readiness": readiness,
        "phase_b_operational_resolution": phase_b_resolution,
        "allowed_paths": _allowed_path_rules_for_external_executor(),
        "executor_outputs_contract": {
            "must_write": [
                "external_executor/executor_research_report.md",
                "external_executor/result_pack.json",
                "external_executor/executor_status.json",
                RUN_MANIFEST_PATH,
                "external_executor/raw_results/",
                "external_executor/configs/",
                "external_executor/logs/",
                "external_executor/figure/",
                "external_executor/table/",
            ],
        },
    }


def build_executor_selection_payload(
    *,
    selected_executor: str,
    selected_by: str = "human",
    notes: str = "",
    execution_scope: str = "full_execution",
) -> dict[str, Any]:
    if execution_scope not in VALID_EXTERNAL_EXECUTION_SCOPES:
        raise ValueError(f"Unsupported external execution scope: {execution_scope}")
    if execution_scope == "resource_preparation" and selected_executor == "mock_dry_run":
        raise ValueError("mock_dry_run cannot perform resource preparation")
    real_allowed = selected_executor != "mock_dry_run" and execution_scope == "full_execution"
    requires_copy = selected_executor in {"codex_cli", "claude_code_window", "manual"}
    next_state = (
        "T5-DRY-RUN"
        if selected_executor == "mock_dry_run"
        else ("T5-RESOURCE-PREP-WAIT" if execution_scope == "resource_preparation" else "T5-EXTERNAL-WAIT")
    )
    payload: dict[str, Any] = {
        "version": "1.0",
        "semantics": "external_executor_selection",
        "selected_executor": selected_executor,
        "execution_scope": execution_scope,
        "real_experiment_allowed": real_allowed,
        "resource_preparation_allowed": selected_executor != "mock_dry_run",
        "requires_user_copy_paste": requires_copy,
        "selected_by": selected_by,
        "selected_at": _now_iso(),
        "next_state": next_state,
        "fallback_order": [item for item in ["mock_dry_run", "claude_code_window", "manual"] if item != selected_executor],
        "notes": notes or _default_executor_selection_note(selected_executor),
    }
    if selected_executor == "codex_cli":
        payload["executor_root"] = "."
        payload["workspace_relative_workdir"] = "."
        payload["workspace_relative_executor_root"] = "."
        payload["workspace_relative_deployment_dir"] = "external_executor/expr"
        payload["codex_user_input"] = "请读取 external_executor/AGENTS.md，并执行 external_executor/skills/research-execution/SKILL.md。"
        payload["launch_instruction"] = (
            "On the host, enter the <workspace> root, start Codex CLI there, and paste codex_user_input."
        )
        if execution_scope == "resource_preparation":
            payload["resume_instruction"] = (
                "After Codex completes the bounded Phase B resource report and stops, run: "
                "python -m researchos.cli resume --workspace <workspace>"
            )
        else:
            payload["resume_instruction"] = (
                "After Codex writes external_executor/executor_research_report.md, result_pack.json, "
                f"executor_status.json, and {RUN_MANIFEST_PATH}, run: python -m researchos.cli resume --workspace <workspace>"
            )
    return payload


def _default_executor_selection_note(selected_executor: str) -> str:
    if selected_executor == "mock_dry_run":
        return "Protocol-only dry run selected; no real experiment evidence will be produced."
    if selected_executor == "codex_cli":
        return "Codex CLI selected; launch it from the workspace root and deploy runnable method/baseline code under external_executor/expr."
    if selected_executor == "claude_code_window":
        return "Claude Code window selected; user must provide AGENTS.md/CLAUDE.md and resume after executor_research_report.md exists."
    return "Manual external execution selected; ResearchOS waits for executor_research_report.md."


def _active_execution_scope_notice(selection: dict[str, Any]) -> str:
    """Render the current selection as an overridable section in executor guides."""

    selected = str(selection.get("selected_executor") or "UNSET").strip()
    scope = str(selection.get("execution_scope") or "full_execution").strip()
    if selected == "UNSET":
        return "<!-- RESEARCHOS_ACTIVE_SCOPE: UNSET -->\n<!-- RESEARCHOS_ACTIVE_SCOPE: END -->"
    if scope == "resource_preparation":
        return (
            "<!-- RESEARCHOS_ACTIVE_SCOPE: resource_preparation -->\n"
            "## Active Scope: Resource Preparation Only\n\n"
            "This active selection overrides any generic full-execution wording below. Run Phase A context alignment and Phase B resource/baseline preparation only. "
            "You may inspect local materials, search authorized public sources, acquire fixed revisions, perform static review, and write provenance/readiness records. "
            "Do not implement a method, reproduce a baseline, run experiments, diagnose results, package evidence, write Writer Handoff, or enter T8.\n\n"
            "Before stopping, write `external_executor/report/phase_B/resource_preparation_report.json`, "
            "`external_executor/report/phase_B/resource_source_report.json`, and a valid overall "
            "`external_executor/report/phase_B/validation_report.json`; then return control to ResearchOS.\n"
            "<!-- RESEARCHOS_ACTIVE_SCOPE: END -->"
        )
    if selected == "mock_dry_run":
        return (
            "<!-- RESEARCHOS_ACTIVE_SCOPE: mock_dry_run -->\n"
            "## Active Scope: Protocol Dry Run Only\n\n"
            "Produce only schema-valid mock artifacts. Do not treat mock output as experiment evidence or enter T8.\n"
            "<!-- RESEARCHOS_ACTIVE_SCOPE: END -->"
        )
    return (
        "<!-- RESEARCHOS_ACTIVE_SCOPE: full_execution -->\n"
        "## Active Scope: Full External Execution\n\n"
        "The protocol is ready for the declared external workflow. Keep all work within the handoff, project-specific Skills, and allowed paths; do not change the research boundary by inference.\n"
        "<!-- RESEARCHOS_ACTIVE_SCOPE: END -->"
    )


def patch_external_executor_files_with_selection(workspace: Path, selection: dict[str, Any]) -> None:
    dry_run = "true" if selection.get("selected_executor") == "mock_dry_run" else "false"
    mock_only = "true" if selection.get("selected_executor") == "mock_dry_run" else "false"
    real_allowed = "true" if selection.get("real_experiment_allowed") else "false"
    selected = str(selection.get("selected_executor") or "mock_dry_run")
    scope_notice = _active_execution_scope_notice(selection)
    execution_mode = "dry_run" if selected == "mock_dry_run" else "external"
    handoff_path = workspace / "external_executor" / "handoff_pack.json"
    handoff = _read_json(handoff_path)
    if handoff:
        if _is_research_reboost_pack(handoff):
            contract = handoff.get("execution_contract")
            if isinstance(contract, dict):
                rules = contract.setdefault("authority_rules", [])
                if isinstance(rules, list):
                    selection_rule = {"action": f"executor selected: {selected}", "authority": "allowed"}
                    if selection_rule not in rules:
                        rules.append(selection_rule)
                _write_json(handoff_path, handoff)
        else:
            handoff["executor"] = selected
            handoff["execution_mode"] = execution_mode
            _write_json(handoff_path, handoff)
    for rel in (
        "external_executor/AGENTS.md",
        "external_executor/CLAUDE.md",
        "external_executor/README.md",
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
        scope_marker = re.compile(
            r"<!-- RESEARCHOS_ACTIVE_SCOPE: [^>]* -->.*?<!-- RESEARCHOS_ACTIVE_SCOPE: END -->",
            flags=re.DOTALL,
        )
        if scope_marker.search(text):
            text = scope_marker.sub(scope_notice, text, count=1)
        else:
            text = text.rstrip() + "\n\n" + scope_notice + "\n"
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
    _write_json(
        workspace / EXECUTOR_CAPABILITIES_PATH,
        default_executor_capabilities(selected),
    )


def validate_external_executor_ready(
    workspace: Path,
    result_pack_rel: str,
    status_rel: str,
    executor_report_rel: str = "external_executor/executor_research_report.md",
    *,
    allow_partial_results: bool = False,
) -> dict[str, Any]:
    missing = [rel for rel in (executor_report_rel, result_pack_rel, status_rel) if not (workspace / rel).exists()]
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
    executor_report_path = workspace / executor_report_rel
    selection, selection_hash = _executor_selection_payload(workspace)
    selection_rel = _workspace_relative(workspace, _executor_selection_path(workspace)) if selection else ""
    selected_executor = _selection_selected_executor(selection)
    manifest_rel = _run_manifest_ref(workspace, result_pack, status)
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
    missing_required_fields = [
        field for field in EXTERNAL_RESULT_REQUIRED_FIELDS
        if not any(alias in result_pack for alias in EXTERNAL_RESULT_FIELD_ALIASES.get(field, (field,)))
    ]
    if missing_required_fields:
        issues.append("result_pack missing required fields: " + ", ".join(missing_required_fields))
    if selection.get("semantics") != "external_executor_selection" or not selected_executor:
        issues.append(f"{EXECUTOR_SELECTION_PATH} missing or semantics invalid")
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
    if not executor_report_path.is_file() or executor_report_path.stat().st_size <= 0:
        issues.append("executor_research_report.md missing or empty")
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
    if not isinstance(metrics, list):
        issues.append("result_pack.metrics must be a list")
    elif not metrics and not result_pack.get("mock_only"):
        issues.append("real result_pack.metrics missing")
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
            "message": "WAITING_EXTERNAL: external handoff materials exist but are not valid: " + "; ".join(issues),
            "issues": issues,
            "selected_executor": selected_executor,
            "executor_selection": selection_rel,
            "selection_sha256": selection_hash,
        }
        _write_wait_rejection_report(workspace, report)
        return report
    report = {
        "version": "1.0",
        "semantics": "external_executor_wait_acceptance_report",
        "ok": True,
        "message": "External executor T8 handoff materials are present and schema-compatible.",
        "executor_research_report": executor_report_rel,
        "result_pack": result_pack_rel,
        "executor_status": status_rel,
        "run_manifest": manifest_rel,
        "executor_selection": selection_rel,
        "selected_executor": selected_executor,
        "selection_sha256": selection_hash,
        "executor_research_report_sha256": _sha256(workspace / executor_report_rel),
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
    include_legacy_control_files: bool = True,
) -> None:
    ext_dir = policy.workspace_dir / "external_executor"
    ext_dir.mkdir(parents=True, exist_ok=True)
    project_id = str(handoff.get("project_id") or handoff.get("project", {}).get("project_id") or "unknown")
    metrics_block = "\n".join(
        f"- {metric.get('name')}"
        for metric in handoff.get("metrics", [])
        if isinstance(metric, dict) and str(metric.get("name") or "").strip()
    ) or "- unknown; a source-backed metric definition is required before execution"
    baselines = handoff.get("required_baselines") or []
    baselines_block = _format_required_baselines_block(baselines)
    resource_policy = handoff.get("resource_acquisition_policy") or default_resource_acquisition_policy()
    resource_policy_block = json.dumps(resource_policy, ensure_ascii=False, indent=2)
    required_outputs = "\n".join(f"- `{path}`" for path in (handoff.get("executor_outputs_contract") or {}).get("must_write", []))
    seeds = ", ".join(str(seed) for seed in handoff.get("seeds", []) or []) or "unknown; require a declared seed policy"
    readiness = handoff.get("execution_readiness") if isinstance(handoff.get("execution_readiness"), dict) else {}
    readiness_status = str(readiness.get("status") or "ready")
    pending_decisions = readiness.get("required_decisions") if isinstance(readiness.get("required_decisions"), list) else []
    phase_b_resolution = handoff.get("phase_b_operational_resolution") if isinstance(handoff.get("phase_b_operational_resolution"), dict) else {}
    auto_settings = phase_b_resolution.get("settings") if isinstance(phase_b_resolution.get("settings"), list) else []
    auto_settings_block = ""
    if auto_settings:
        lines = []
        for item in auto_settings:
            if not isinstance(item, dict):
                continue
            setting = str(item.get("setting") or "operational setting").strip()
            resolution = str(item.get("resolution") or "Record the reviewed Phase B selection in the run configuration.").strip()
            lines.append(f"- {setting}: {resolution}")
        if lines:
            auto_settings_block = (
                "## Phase B Resolved Operational Settings\n"
                "These settings were accepted from Phase B within the existing research boundary. Use only reviewed resources and record the exact version/configuration in every run; do not broaden task, benchmark scope, baseline set, mechanism, or claim boundary.\n"
                + "\n".join(lines)
                + "\n\n"
            )
    execution_scope = str(selection.get("execution_scope") or "full_execution")
    if execution_scope == "resource_preparation":
        readiness_block = (
            "## Resource Preparation Scope\n"
            "This launch is deliberately limited to Phase A context alignment and Phase B resource/baseline preparation. "
            "Use the literature resource catalog and authorized public sources to discover, acquire, statically review, and record candidate datasets, benchmarks, code, checkpoints, and baseline implementations. "
            "Do not dispatch experiment-design, implementation, baseline reproduction, experiment-run, diagnosis, evidence packaging, or Writer Handoff.\n\n"
            "Before stopping, validate and apply the Phase B resource report at "
            "`external_executor/report/phase_B/resource_preparation_report.json`. "
            "It may be `ready`, `partial`, or `blocked`; record every unavailable, restricted, or incompatible resource honestly. "
            "Write the overall Phase B receipt `external_executor/report/phase_B/validation_report.json` with "
            "`schema_version=resource_preparation_validation.v1` and `valid=true`. "
            "After it is written, stop and let ResearchOS resume T5 compilation.\n\n"
        )
    elif readiness_status == "protocol_decision_required":
        readiness_block = (
            "## Protocol Readiness\n"
            "The research handoff is compiled, but formal execution is not authorized yet. You may inspect context and prepare or verify resource/baseline candidates only. "
            "Do not implement the method, run experiments, emit result_pack, or write a T8 handoff until the ResearchOS T5 protocol Gate has recorded and recompiled the required decisions.\n\n"
            "Required decisions:\n"
            + "\n".join(f"- {item}" for item in pending_decisions)
            + "\n\n"
        )
    elif readiness_status == "blocked":
        readiness_block = (
            "## Protocol Readiness\n"
            "The handoff is missing required source or protocol fields. Do not perform resource acquisition, implementation, or execution; return to ResearchOS for the reported repair.\n\n"
        )
    else:
        readiness_block = "## Protocol Readiness\nThe handoff is ready for the declared preparation and execution stages, subject to executor selection and all remaining evidence boundaries.\n\n"
    scope_header = (
        "RESOURCE PREPARATION ONLY" if execution_scope == "resource_preparation" else "FULL EXECUTION"
    )
    t8_launch_block = (
        "## T5-to-T8 transition\n"
        "After the project-specific `writer-handoff` Skill reports `status=ready` or `status=partial`, rerun the root routing helper. "
        "When it returns `launch-t8`, execute its command exactly once from the workspace root, normally:\n\n"
        "```bash\npython -m researchos.cli run-task T8 --workspace <workspace>\n```\n\n"
        "This is the only normal T5-to-T8 handoff. ResearchOS independently validates the frozen Writer Handoff and creates "
        "`drafts/t5_t8_handoff.json`, `drafts/experiment_evidence_pack.json`, and `drafts/result_to_claim.json` before it opens T8. "
        "Do not tell the researcher to use `resume` as a substitute, and do not write manuscript files under `drafts/` yourself. "
        "A nonzero command exit means T8 did not start; preserve the external package and repair the named Writer Handoff issue.\n\n"
        if execution_scope != "resource_preparation"
        else "## T5-to-T8 transition\n"
        "This launch is resource preparation only. Do not run `run-task T8`; stop after the accepted Phase B report and return control to ResearchOS T5.\n\n"
    )
    common_header = (
        "> EXECUTION MODE NOT YET SELECTED - see external_executor/report/executor_selection.json after T5-EXECUTOR-GATE\n\n"
        f"- execution_scope: {scope_header}\n- dry_run: UNSET\n- mock_only: UNSET\n- real_experiment_allowed: UNSET\n\n"
        "<!-- RESEARCHOS_ACTIVE_SCOPE: UNSET -->\n<!-- RESEARCHOS_ACTIVE_SCOPE: END -->\n\n"
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
        + readiness_block
        + auto_settings_block
        + "## Read first\n"
        "1. external_executor/handoff_pack.json\n"
        "2. external_executor/expected_outputs_schema.json\n"
        "3. external_executor/allowed_paths.txt\n"
        "4. external_executor/report/executor_selection.json\n"
        "5. external_executor/report/executor_capabilities.json, if present\n"
        "6. external_executor/report/skill_specialization_report.json\n"
        "7. external_executor/project_skill_context.yaml\n"
        "8. novelty/required_baselines.json, if present\n"
        "9. ideation/novelty_audit.md\n\n"
        "## Read if present\n"
        "- resources/baseline_candidates.jsonl\n"
        "- literature/baseline_map.json\n"
        "- literature/resource_catalog.jsonl and literature/resource_catalog_summary.json\n"
        "\n"
        "The literature resource catalog contains discovery leads only; verify identity, license, revision, security, and protocol fit in Phase B. "
        "Missing optional resource or baseline map files are not blockers. Use the handoff, project context, and Phase B acquisition workflow.\n\n"
        "## Resource materials\n"
        "Phase B resource preparation uses `resources/` as the only resource material root. "
        "Place by-hand local resources, public remote acquisitions, and baseline reimplementations under `resources/`.\n\n"
        "## Resource acquisition policy\n"
        "Dataset downloads, GitHub access, and baseline reimplementation are allowed within `allowed_paths.txt` and license/security review constraints.\n\n"
        "```json\n"
        f"{resource_policy_block}\n"
        "```\n\n"
        "## Metrics you must report\n"
        f"{metrics_block}\n\n"
        "## Required baselines\n"
        f"{baselines_block}\n\n"
        "## Seeds\n"
        f"Run required configurations over seeds: {seeds}\n\n"
        "## Hard boundaries\n"
        "Read and obey `external_executor/allowed_paths.txt`; it is the authoritative path policy. "
        "Do not rely on any copied whitelist in this file, and do not write outside paths allowed there.\n\n"
        "Do not fabricate datasets, baselines, metrics, or results. Every metric must trace to a raw file, config, run id, log, and sha256.\n\n"
        "## Required outputs\n"
        f"{required_outputs}\n\n"
        "Write external_executor/executor_research_report.md as the final T8 handoff report. Do not write paper text or final claims.\n\n"
        + t8_launch_block
    )
    claude = (
        f"# Claude Code External Execution Guide - project {project_id}\n\n"
        + common_header
        + readiness_block
        + auto_settings_block
        + "You are used as an external coding executor for ResearchOS via a Claude Code window.\n\n"
        "## Steps\n"
        "1. Read handoff_pack.json, expected_outputs_schema.json, allowed_paths.txt.\n"
        "2. Read optional baseline/resource maps and literature/resource_catalog.jsonl only if they exist; the catalog is a discovery lead, not an approved resource.\n"
        "3. If mock_only=true, emit schema-valid mock artifacts with mock_only=true and dry_run=true.\n"
        "4. If real, keep Phase B resource materials under resources/; later build/run skills deploy runnable assets according to their own instructions.\n"
        f"5. Run required configs over seeds {seeds}.\n"
        "6. Write all required outputs, including the final Writer Handoff report and validation.\n\n"
        + t8_launch_block
        + "## Metrics\n"
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
        "Key files: handoff_pack.json, expected_outputs_schema.json, allowed_paths.txt, AGENTS.md, CLAUDE.md, report/executor_selection.json, report/run_manifest.json, result_pack.json.\n"
    )
    dir_guide = (
        "# Workspace Directory Guide\n\n"
        "| 项目 | 说明 |\n"
        "|---|---|\n"
        "| 目录用途 | ResearchOS 与 Codex/Claude/manual 外部实验执行器的边界目录。 |\n"
        "| 生成阶段/来源 | T5-REBOOST/T5-HANDOFF, T5-EXECUTOR-GATE, external executor, T5-DRY-RUN. |\n"
        "| 下游使用方 | T5-EXTERNAL-WAIT, T8-RESOURCE and later T8 writing/review tasks. |\n"
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
        "| `skills/` | Project-specific external executor Skill Suite published by `T5-SPECIALIZE-EXECUTOR-SKILLS`. Use `researchos specialize-executor-skills --deterministic` only for offline preview, repair, or validation. |\n"
        "| `expr/` | Human-provided experimental materials gate directory. |\n"
        "| `executor_research_report.md` | T5 直接交给 T8 的核心外部执行研究报告。 |\n"
        "| `result_pack.json` | 外部执行器写回的支持性结果包，供 T8 需要时回查。 |\n"
        "| `executor_status.json` | 外部执行器状态、accepted/mock/dry-run 标记。 |\n"
        "| `report/phase_A/input_fingerprint.json` | research-execution 初始化/恢复时计算的控制输入指纹。 |\n"
        "| `report/run_manifest.json` | 运行记录、raw/config/log 路径和 provenance。 |\n\n"
        "Generated by ResearchOS workspace initialization.\n"
    )
    _write_text(policy.resolve_write("external_executor/AGENTS.md"), agents)
    _write_text(policy.resolve_write("external_executor/CLAUDE.md"), claude)
    if not include_legacy_control_files:
        return
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
        if isinstance(inventory.get("items"), list):
            inventory_items.extend(item for item in inventory["items"] if isinstance(item, dict))
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
    elif realized.get("status") == "unavailable":
        issues.append({"level": "FAIL", "code": "realized_method_unavailable", "detail": "actual final method could not be reconstructed"})
    elif realized.get("status") != "complete":
        issues.append({"level": "WARN", "code": "realized_method_partial", "detail": ", ".join(_coerce_str_list(realized.get("unresolved_fields"))) or "method package is partial"})
    if realized and realized.get("source_validation", {}).get("status") != "pass":
        issues.append({"level": "WARN", "code": "realized_method_source_validation_incomplete", "detail": ", ".join(_coerce_str_list(realized.get("source_validation", {}).get("errors")))})
    if realized and not realized.get("final_version"):
        issues.append({"level": "WARN", "code": "realized_method_final_version_missing", "detail": "final implementation/spec/review identity is absent"})
    for field in ("training_flow", "inference_flow", "actual_losses"):
        if realized and not realized.get(field):
            issues.append({"level": "WARN", "code": f"realized_method_{field}_missing", "detail": f"{field} is required for formal Method writing"})
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
    method_status = (
        "fail" if any(item.get("level") == "FAIL" for item in issues)
        else "mock_only" if summary.get("mock_only")
        else "warn" if any(item.get("level") == "WARN" for item in issues)
        else "pass"
    )
    method_audit = {
        "version": "1.0",
        "semantics": "external_method_intent_vs_realized_audit",
        "status": method_status,
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
    if not figure_path and figure:
        rendered = figure.get("rendered_files") if isinstance(figure.get("rendered_files"), list) else []
        if rendered:
            first = rendered[0]
            figure_path = str(first.get("path") if isinstance(first, dict) else first or "")
    if not figure:
        figure_issues.append({"level": "FAIL", "code": "missing_final_framework_figure", "detail": "result_pack.framework_figure/final_framework_figure missing"})
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
    if not inventory_figures and isinstance(inventory.get("items"), list):
        inventory_figures = [
            item for item in inventory["items"]
            if isinstance(item, dict) and item.get("kind") in {"figure", "framework_figure"}
        ]
    if figure_path and inventory_figures:
        inv_paths = {
            str(
                item.get("path")
                or ((item.get("rendered_files") or [{}])[0].get("path") if isinstance((item.get("rendered_files") or [{}])[0], dict) else (item.get("rendered_files") or [""])[0])
                or ""
            )
            for item in inventory_figures if isinstance(item, dict)
        }
        if figure_path not in inv_paths:
            figure_issues.append({"level": "WARN", "code": "framework_figure_not_in_inventory", "detail": figure_path})
    framework_matches_code = bool(figure) and not any(item.get("level") == "FAIL" for item in figure_issues)
    method_consistency_audit["framework_figure_matches_code"] = framework_matches_code
    framework_audit = {
        "version": "1.0",
        "semantics": "external_framework_figure_audit",
        "status": "fail" if any(item.get("level") == "FAIL" for item in figure_issues) else ("mock_only" if summary.get("mock_only") else "pass"),
        "figure_ref": "external_executor/result_pack.json#framework_figure",
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
    for key in ("module_attributions", "mechanism_attributions", "interaction_effects"):
        section = module_attribution.get(key, {})
        items = section.get("items", []) if isinstance(section, dict) else section if isinstance(section, list) else []
        for item in items:
            if isinstance(item, dict):
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


REBOOST_REPORT_PATH = "external_executor/report/reboost_report.json"
REBOOST_VALIDATION_REPORT_PATH = "external_executor/report/reboost_validation_report.json"
LLM_REBOOST_CANDIDATE_PATH = "external_executor/report/reboost_llm_candidate_handoff_pack.json"
LLM_REBOOST_CANDIDATE_VALIDATION_REPORT_PATH = (
    "external_executor/report/reboost_llm_candidate_validation_report.json"
)


class CompileResearchReboostHandoffParams(BaseModel):
    handoff_pack: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Full handoff_pack object compiled by the LLM while executing "
            "skills/research-reboost. When omitted, the tool uses the legacy "
            "deterministic recovery compiler."
        ),
    )
    output_path: str = Field(default="external_executor/handoff_pack.json")
    report_path: str = Field(default=REBOOST_REPORT_PATH)
    validation_report_path: str = Field(default=REBOOST_VALIDATION_REPORT_PATH)
    expected_schema_path: str = Field(default="external_executor/expected_outputs_schema.json")
    allowed_paths_path: str = Field(default="external_executor/allowed_paths.txt")


class SpecializeExecutorSkillsParams(BaseModel):
    """Parameters for the deterministic T5 project-Skill publication step."""

    report_path: str = Field(default="external_executor/report/skill_specialization_report.json")


def _validation_findings_summary(report: dict[str, Any], *, limit: int = 8) -> list[dict[str, Any]]:
    findings = [item for item in report.get("findings", []) if isinstance(item, dict)]
    errors = [item for item in findings if item.get("severity") == "error"]
    selected = errors or findings
    return [
        {
            "severity": item.get("severity"),
            "code": item.get("code"),
            "path": item.get("path"),
            "message": item.get("message"),
        }
        for item in selected[:limit]
    ]


class BuildExperimentHandoffPackParams(BaseModel):
    executor: Literal["UNSET", "mock_dry_run", "codex_cli", "claude_code_window", "manual"] = Field(
        default="UNSET",
        description="Initial executor mode. T5-EXECUTOR-GATE patches the real selection later.",
    )
    output_path: str = Field(default="external_executor/handoff_pack.json")
    prompt_output_path: str = Field(default="external_executor/executor_prompt.md", description="Deprecated; executor prompt files are no longer generated.")
    expected_schema_path: str = Field(default="external_executor/expected_outputs_schema.json")
    allowed_paths_path: str = Field(default="external_executor/allowed_paths.txt")
    executor_selection_path: str = Field(default=EXECUTOR_SELECTION_PATH)
    input_manifest_path: str = Field(default="external_executor/input_manifest.json")
    codex_prompt_path: str = Field(default="external_executor/codex_prompt.md", description="Deprecated; executor prompt files are no longer generated.")
    claude_prompt_path: str = Field(default="external_executor/claude_code_prompt.md", description="Deprecated; executor prompt files are no longer generated.")
    manual_instructions_path: str = Field(default="external_executor/manual_instructions.md", description="Deprecated; executor prompt files are no longer generated.")
    specialize_skills: bool = Field(
        default=False,
        description=(
            "Deprecated compatibility flag. Project-specific executor Skills are now "
            "generated by T5-SPECIALIZE-EXECUTOR-SKILLS."
        ),
    )


class SelectExternalExecutorParams(BaseModel):
    selected_executor: Literal["mock_dry_run", "codex_cli", "claude_code_window", "manual"] = Field(
        default="mock_dry_run",
        description="Executor selected by the T5-EXECUTOR-GATE human decision.",
    )
    executor_selection_path: str = Field(default=EXECUTOR_SELECTION_PATH)
    selected_by: str = Field(default="human")
    notes: str = Field(default="")


class WaitForExternalExecutorResultParams(BaseModel):
    executor_report_path: str = Field(default="external_executor/executor_research_report.md")
    result_pack_path: str = Field(default="external_executor/result_pack.json")
    status_path: str = Field(default="external_executor/executor_status.json")
    output_path: str = Field(default="external_executor/wait_acceptance_report.json")
    allow_partial_results: bool = Field(
        default=False,
        description="默认不允许 PARTIAL_RESULTS_READY 进入 T8 handoff；只有显式打开时才接受部分结果。",
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


class CompileResearchReboostHandoffTool(Tool):
    name = "compile_research_reboost_handoff"
    description = (
        "Compile, publish, and validate the authoritative T4.5-to-T5 handoff. "
        "When a legacy LLM candidate is supplied it is audited and repaired against "
        "the workspace sources; without one, the deterministic compiler is the primary "
        "path. The tool writes the pretty-printed "
        "external_executor/handoff_pack.json and the minimal T5 handoff control files. "
        "Project-specific executor Skills are published separately by "
        "T5-SPECIALIZE-EXECUTOR-SKILLS."
    )
    parameters_schema = CompileResearchReboostHandoffParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = CompileResearchReboostHandoffParams(**kwargs)
        try:
            ws = self.policy.workspace_dir
            existing_selection, _selection_hash = _executor_selection_payload(ws)
            selected_executor = str(existing_selection.get("selected_executor") or "")
            preserve_existing_selection = selected_executor in {
                "codex_cli",
                "claude_code_window",
                "manual",
                "mock_dry_run",
            }
            # Refresh discovery records before hashing the Pre-T5 source
            # manifest. Refreshing later would mutate the catalog after its
            # SHA-256 was recorded and make an otherwise valid handoff fail
            # its own integrity validation.
            refresh_resource_catalog(ws)
            project = _read_yaml(ws / "project.yaml")
            exp_plan = _read_yaml(ws / "ideation" / "exp_plan.yaml")
            metrics = _extract_exp_plan_metrics(exp_plan)
            if params.handoff_pack is not None:
                llm_candidate = dict(params.handoff_pack)
                _write_json(self.policy.resolve_write(LLM_REBOOST_CANDIDATE_PATH), llm_candidate)
                candidate_ok, candidate_err, candidate_validation_report = _validate_research_reboost_pack(
                    ws,
                    self.policy.resolve_read(LLM_REBOOST_CANDIDATE_PATH),
                )
                _write_json(
                    self.policy.resolve_write(LLM_REBOOST_CANDIDATE_VALIDATION_REPORT_PATH),
                    candidate_validation_report,
                )
                if candidate_ok:
                    pack = llm_candidate
                    source_manifest = [
                        item
                        for item in pack.get("source_manifest", [])
                        if isinstance(item, dict)
                    ]
                    report = {
                        "version": "1.0",
                        "semantics": "external_executor_context_reboost_report",
                        "skill": "skills/research-reboost/SKILL.md",
                        "skill_sha256": _sha256(_research_reboost_skill_dir() / "SKILL.md"),
                        "handoff_pack": params.output_path,
                        "generation_status": str(pack.get("generation_status") or "unknown"),
                        "generation_source": "llm_api_skill_execution",
                        "source_files_used": [item.get("path") for item in source_manifest if item.get("used")],
                        "missing_required_sources": _missing_reboost_source_groups(source_manifest),
                        "missing_optional_sources": [
                            item.get("path")
                            for item in source_manifest
                            if item.get("requirement") == "optional_backtrack"
                            and item.get("availability") != "available"
                        ],
                        "known_context_mismatches": pack.get("known_context_mismatches")
                        or (pack.get("context_reboost") or {}).get("known_context_mismatches")
                        or [],
                        "validation_report": params.validation_report_path,
                        "llm_candidate_handoff_pack": LLM_REBOOST_CANDIDATE_PATH,
                        "llm_candidate_validation_report": LLM_REBOOST_CANDIDATE_VALIDATION_REPORT_PATH,
                        "llm_candidate_validation_ok": True,
                    }
                else:
                    pack, report = _build_reboost_pack(ws)
                    report["generation_source"] = "llm_api_skill_execution_with_deterministic_schema_repair"
                    report["llm_candidate_handoff_pack"] = LLM_REBOOST_CANDIDATE_PATH
                    report["llm_candidate_validation_report"] = LLM_REBOOST_CANDIDATE_VALIDATION_REPORT_PATH
                    report["llm_candidate_validation_ok"] = False
                    report["llm_candidate_validation_error"] = candidate_err
                    report["llm_candidate_validation_findings"] = _validation_findings_summary(
                        candidate_validation_report
                    )
                    report["deterministic_schema_repair"] = {
                        "used": True,
                        "reason": "llm_candidate_failed_research_reboost_validation",
                        "final_handoff_pack": params.output_path,
                    }
            else:
                pack, report = _build_reboost_pack(ws)
                report["generation_source"] = "deterministic_recovery_compiler"
            # This compact index is part of the research handoff, not an
            # executor-selection prompt. Keep it available before the later
            # specialization task so executor Skills can trace literature
            # rationale without treating paper notes as empirical results.
            paper_card_index_path = "external_executor/paper_card_evidence_index.json"
            paper_card_index = _paper_card_evidence_index(ws)
            _write_json(self.policy.resolve_write(paper_card_index_path), paper_card_index)
            pack["paper_card_evidence_index"] = paper_card_index_path
            pack["paper_card_evidence_policy"] = {
                "allowed_uses": paper_card_index["allowed_uses"],
                "prohibited_uses": paper_card_index["prohibited_uses"],
            }
            _write_json(self.policy.resolve_write(params.output_path), pack)
            ok, err, validation_report = _validate_research_reboost_pack(
                ws,
                self.policy.resolve_read(params.output_path),
            )
            _write_json(self.policy.resolve_write(params.validation_report_path), validation_report)
            report["validation_ok"] = ok
            report["validation_error"] = err
            report["validator"] = "skills/research-reboost/scripts/validate_handoff.py"
            _write_json(self.policy.resolve_write(params.report_path), report)
            if not ok:
                return ToolResult(
                    ok=False,
                    content=f"research-reboost validation failed: {err}",
                    error="research_reboost_validation_failed",
                    data={"report": params.validation_report_path, "handoff_pack": params.output_path},
                )

            guide_handoff = _guide_view_from_reboost_pack(pack, project, metrics)
            _write_json(self.policy.resolve_write(params.expected_schema_path), _build_expected_outputs_schema())
            _write_text(self.policy.resolve_write(params.allowed_paths_path), "\n".join(_allowed_path_rules_for_external_executor()) + "\n")
            _write_external_executor_guides(
                self.policy,
                guide_handoff,
                # Rebuild neutral guide text first.  A persisted selection may
                # be a bounded Phase-B launch from the previous T5 pass; using
                # it to choose the static prose here would leave stale
                # resource-only instructions after T5 recompiles to ready.
                # The deterministic patch below reapplies the current active
                # scope through its replaceable scope marker.
                selection={"selected_executor": "UNSET"},
                include_legacy_control_files=False,
            )
            if preserve_existing_selection:
                patch_external_executor_files_with_selection(ws, existing_selection)
            report["existing_executor_selection_preserved"] = preserve_existing_selection
            _write_json(self.policy.resolve_write(params.report_path), report)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"research reboost failed: {exc}", error="research_reboost_failed")
        return ToolResult(
            ok=True,
            content=f"Wrote research-reboost handoff pack to {params.output_path}.",
            data={
                "handoff_pack": params.output_path,
                "reboost_report": params.report_path,
                "validation_report": params.validation_report_path,
                "skill": "skills/research-reboost/SKILL.md",
                "next_task": "T5-SPECIALIZE-EXECUTOR-SKILLS",
            },
        )


class SpecializeExecutorSkillsTool(Tool):
    """Publish the project-specific executor Skill Suite in its own T5 step.

    Publication is deterministic: an executor suite must not depend on an LLM
    choosing the correct tool call. The compiler stages, validates, and
    atomically publishes all Skills, while preserving a usable suite if a
    rebuild fails.
    """

    name = "specialize_executor_skills"
    description = (
        "Generate and validate the project-specific external executor Skill Suite "
        "from the current handoff pack. This publishes the suite atomically and "
        "does not run an experiment."
    )
    parameters_schema = SpecializeExecutorSkillsParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = SpecializeExecutorSkillsParams(**kwargs)
        try:
            result = specialize_project_skills(workspace=self.policy.workspace_dir)
        except Exception as exc:
            return ToolResult(
                ok=False,
                content=f"Project-specific executor Skills could not be generated: {exc}",
                error="project_skill_specialization_failed",
            )

        report = result.report or {}
        try:
            relative_report = result.report_path.relative_to(self.policy.workspace_dir).as_posix()
        except ValueError:
            relative_report = params.report_path
        data = {
            "report": relative_report,
            "context": "external_executor/project_skill_context.yaml",
            "skills_dir": "external_executor/skills",
            "status": result.status,
            "skills_specialized": int(report.get("skills_specialized") or 0),
            "skills_total": int(report.get("skills_total") or 0),
            "required_uncertain_fields": list(report.get("required_uncertain_fields") or []),
        }
        if result.status == "failed":
            first_error = (result.errors or [{}])[0]
            message = str(first_error.get("message") or "See the specialization failure report.")
            return ToolResult(
                ok=False,
                content=f"Project-specific executor Skills were not published. {message}",
                error="project_skill_specialization_failed",
                data=data,
            )
        return ToolResult(
            ok=True,
            content=(
                "Published and validated the project-specific executor Skill Suite "
                f"({data['skills_specialized']}/{data['skills_total']} Skills)."
            ),
            data=data,
        )


class BuildExperimentHandoffPackTool(Tool):
    name = "build_experiment_handoff_pack"
    description = "Compile a protocol pack and AGENTS/CLAUDE control instructions for external experiment execution."
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
            seeds = _execution_seed_ensemble(project)
            required_baselines = _extract_required_baselines(ws)
            _write_json(
                self.policy.resolve_write("novelty/required_baselines.json"),
                {
                    "version": "1.0",
                    "semantics": "required_baselines_from_novelty_audit",
                    "source": "ideation/novelty_audit.md",
                    "status": "present" if required_baselines else "no_required_baselines_extracted",
                    "required_baselines": required_baselines,
                },
            )
            source_artifacts = _source_artifacts(ws)
            source_artifacts.append(_artifact_record(ws, "novelty/required_baselines.json", role="required_baselines"))
            paper_card_index_path = "external_executor/paper_card_evidence_index.json"
            paper_card_index = _paper_card_evidence_index(ws)
            _write_json(self.policy.resolve_write(paper_card_index_path), paper_card_index)
            source_artifacts.append(_artifact_record(ws, paper_card_index_path, role="paper_card_evidence_index"))
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
            context_reboost["resource_acquisition_policy"] = default_resource_acquisition_policy()
            method_intent = _build_method_intent(
                hypotheses=hypotheses,
                exp_plan=exp_plan,
                context_reboost=context_reboost,
            )
            host_workspace = workspace_host_hint(ws)
            host_workdir = host_workspace
            host_deployment_dir = str(Path(host_workspace) / "external_executor" / "expr") if host_workspace else ""
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
                "workspace_relative_workdir": ".",
                "workspace_relative_executor_root": ".",
                "workspace_relative_deployment_dir": "external_executor/expr",
                "host_workspace_hint": host_workspace,
                "host_workdir_hint": host_workdir,
                "host_deployment_dir_hint": host_deployment_dir,
                "resource_acquisition_policy": default_resource_acquisition_policy(),
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
                "paper_card_evidence_index": paper_card_index_path,
                "paper_card_evidence_policy": {
                    "allowed_uses": paper_card_index["allowed_uses"],
                    "prohibited_uses": paper_card_index["prohibited_uses"],
                },
                "executor_outputs": {
                    "executor_research_report": "external_executor/executor_research_report.md",
                    "result_pack": "external_executor/result_pack.json",
                    "status": "external_executor/executor_status.json",
                    "run_manifest": RUN_MANIFEST_PATH,
                    "raw_results": "external_executor/raw_results/",
                    "configs": "external_executor/configs/",
                    "logs_dir": "external_executor/logs",
                },
                "allowed_paths": _allowed_path_rules_for_external_executor(),
                "executor_outputs_contract": {
                    "must_write": [
                        "external_executor/executor_research_report.md",
                        "external_executor/result_pack.json",
                        "external_executor/executor_status.json",
                        RUN_MANIFEST_PATH,
                        "external_executor/raw_results/",
                        "external_executor/configs/",
                        "external_executor/logs/",
                        "external_executor/figure/",
                        "external_executor/table/",
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
                "paper_card_evidence_index": paper_card_index_path,
                "paper_card_evidence_policy": handoff["paper_card_evidence_policy"],
                "required_executor_outputs": handoff["executor_outputs"],
            }
            _write_json(self.policy.resolve_write(params.input_manifest_path), input_manifest)
            placeholder_next_state = "T5-SPECIALIZE-EXECUTOR-SKILLS"
            placeholder_notes = (
                "Execution mode is intentionally UNSET until T5-EXECUTOR-GATE; "
                "next step is T5-SPECIALIZE-EXECUTOR-SKILLS project Skill specialization."
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
            specialization_status = "deferred_to_T5-SPECIALIZE-EXECUTOR-SKILLS"
            for directory in (
                "expr", "report", "report/phase_A", "report/phase_B", "report/phase_C",
                "report/phase_D", "report/phase_E", "report/phase_F", "figure", "table",
            ):
                (self.policy.workspace_dir / "external_executor" / directory).mkdir(parents=True, exist_ok=True)
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
    description = "Validate that external executor handoff materials exist before T8."
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
                params.executor_report_path,
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
    description = "Build a conservative optional post-experiment novelty/collision check artifact for T8."
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
                "recommended_next_task": "T8-RESOURCE",
                "notes": (
                    "This tool performs deterministic evidence-status checks only. "
                    "LLM novelty interpretation should read this artifact before T8."
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
            selection = _read_json(_executor_selection_path(ws))
            executor_type = str(selection.get("selected_executor") or handoff.get("executor") or "mock_dry_run")
            if executor_type == "UNSET":
                executor_type = "mock_dry_run"
            raw_result_rel = "external_executor/raw_results/mock_results.json"
            config_rel = "external_executor/configs/mock_config.json"
            log_rel = "external_executor/logs/mock_dry_run.log"
            manifest_rel = RUN_MANIFEST_PATH
            heartbeat_rel = "external_executor/heartbeat.json"
            raw_result = {
                "version": "1.0",
                "semantics": "mock_raw_result_file_not_scientific_evidence",
                "run_id": "mock_dry_run",
                "metrics": [],
            }
            # A protocol dry run verifies handoff shape only.  It must not
            # manufacture metric values, a dataset name, split, or seed that
            # could later be mistaken for a scientific result.
            metrics: list[dict[str, Any]] = []
            handoff_seeds = _handoff_seeds_for_execution(handoff)
            _write_json(self.policy.resolve_write(raw_result_rel), raw_result)
            config = {
                "version": "1.0",
                "semantics": "mock_external_executor_config",
                "run_id": "mock_dry_run",
                "executor": executor_type,
                "seeds": handoff_seeds,
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
            required_baselines = _handoff_required_baselines_for_execution(handoff)
            baseline_coverage = _baseline_coverage_from_metrics(required_baselines, metrics, mock_only=True)
            experiment_runs = [
                {
                    "run_id": "mock_dry_run",
                    "run_type": "smoke",
                    "status": "completed",
                    "dry_run": True,
                    "mock_only": True,
                    "dataset": None,
                    "seed": handoff_seeds[0] if handoff_seeds else None,
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
                "result_diagnosis_ref": "result_pack.result_diagnoses",
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
                "result_diagnoses": {"status": "mock_only", "items": [result_diagnosis]},
                "module_attributions": {"status": "mock_only", "items": [module_attribution]},
                "realized_method_package": realized_method_package,
                "framework_figure": final_framework_figure,
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
            if not isinstance(metrics, list):
                return ToolResult(ok=False, content="result_pack metrics must be a list", error="invalid_metrics")
            if not metrics and not result_pack.get("mock_only"):
                return ToolResult(ok=False, content="real result_pack metrics missing", error="missing_metrics")
            manifest_rel = _run_manifest_ref(self.policy.workspace_dir, result_pack)
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
            if not experiments and result_pack.get("mock_only"):
                experiments = [
                    {
                        "experiment_id": str(result_pack.get("run_id") or "mock_dry_run"),
                        "metrics": {},
                        "seed": None,
                        "source_artifact": None,
                        "mock_only": True,
                        "protocol_only": True,
                    }
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
                "experiment_runs": _result_section_items(result_pack, "experiment_runs", "runs"),
                "run_manifest": manifest_rel,
                "baseline_coverage": result_pack.get("baseline_coverage") or {},
                "context_alignment": result_pack.get("context_alignment") or {},
                "result_diagnosis": _current_result_section(result_pack, "result_diagnoses", "result_diagnosis", id_keys=("diagnosis_id",)),
                "module_attribution": _current_result_section(result_pack, "module_attributions", "module_attribution", id_keys=("attribution_id",)),
                "realized_method_package": result_pack.get("realized_method_package") or {},
                "final_framework_figure": result_pack.get("framework_figure") or result_pack.get("final_framework_figure") or {},
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
                "experiment_runs": _result_section_items(result_pack, "experiment_runs", "runs"),
                "baseline_reproduction": result_pack.get("baseline_reproduction") or [],
                "resources": result_pack.get("resources") or {},
                "result_diagnosis": _current_result_section(result_pack, "result_diagnoses", "result_diagnosis", id_keys=("diagnosis_id",)),
                "module_attribution": _current_result_section(result_pack, "module_attributions", "module_attribution", id_keys=("attribution_id",)),
                "realized_method_package": result_pack.get("realized_method_package") or {},
                "final_framework_figure": result_pack.get("framework_figure") or result_pack.get("final_framework_figure") or {},
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
                "framework_figure_present": bool(result_pack.get("framework_figure") or result_pack.get("final_framework_figure")),
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
                issues.append(
                    {
                        "level": "WARN" if summary.get("mock_only") else "FAIL",
                        "code": "no_scientific_metrics_in_mock" if summary.get("mock_only") else "missing_metrics",
                        "detail": "Protocol-only dry run contains no scientific metrics."
                        if summary.get("mock_only")
                        else "No metrics in results summary.",
                    }
                )
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
            method_blocked = method_audit.get("status") in {"fail", "warn", "mock_only"} or contribution_drift == "major"
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
