from __future__ import annotations

"""Deterministic acceptance and ingestion for the modern T5-to-T8 handoff.

The external executor owns the production and Writer Handoff validation of its
artifacts.  This module performs a separate ResearchOS-side acceptance pass,
normalizes structured evidence for the existing T8 claim-audit tools, and
prepares an existing workspace for safe T8 re-entry.
"""

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import yaml

from ..schemas.state import StateYaml


CORE_PATHS = {
    "executor_research_report": "external_executor/executor_research_report.md",
    "result_pack": "external_executor/result_pack.json",
    "executor_status": "external_executor/executor_status.json",
    "run_manifest": "external_executor/report/run_manifest.json",
    "writer_handoff_facts": "external_executor/report/phase_F/writer_handoff_facts.json",
    "writer_handoff_validation": "external_executor/report/phase_F/writer_handoff_validation.json",
}
REQUIRED_REPORT_HEADINGS = (
    "## 1. Project Summary",
    "## 2. Implementation Summary",
    "## 3. Experiment Inventory",
    "## 4. Comprehensive Results",
    "## 5. Claim Support Table",
    "## 6. Verified Literature Additions",
    "## 7. Limitations and Open Issues",
    "## 8. Artifact Index",
)
TERMINAL_STATUSES = {"completed", "partial", "blocked", "failed"}
_T8_RECEIPT = "drafts/t5_t8_handoff.json"
_EVIDENCE_PACK = "drafts/experiment_evidence_pack.json"
_RESULT_TO_CLAIM = "drafts/result_to_claim.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _normalize_status(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "ready": "completed",
        "complete": "completed",
        "done": "completed",
        "success": "completed",
        "partially_completed": "partial",
        "partial_results_ready": "partial",
        "failure": "failed",
    }
    return aliases.get(raw, raw)


def _manifest_items(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("artifacts", "items", "files"):
        value = manifest.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _workspace_file(workspace: Path, relative: str) -> Path:
    candidate = (workspace / relative).resolve()
    try:
        candidate.relative_to(workspace.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {relative}") from exc
    return candidate


def _schema_matches(document: dict[str, Any], expected_prefix: str) -> bool:
    return str(document.get("schema_version") or "").startswith(expected_prefix)


def validate_modern_t5_handoff(workspace: Path, *, allow_partial: bool = True) -> dict[str, Any]:
    """Independently validate the modern Writer Handoff package."""

    workspace = workspace.resolve()
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    documents: dict[str, dict[str, Any]] = {}

    def issue(bucket: list[dict[str, str]], code: str, path: str, message: str) -> None:
        bucket.append({"code": code, "path": path, "message": message})

    for name, relative in CORE_PATHS.items():
        path = _workspace_file(workspace, relative)
        if not path.is_file() or path.stat().st_size <= 0:
            issue(errors, "missing_or_empty_handoff_artifact", relative, name)
            continue
        if path.suffix == ".json":
            try:
                documents[name] = _load_object(path)
            except Exception as exc:  # noqa: BLE001 - surfaced as a typed acceptance error
                issue(errors, "invalid_handoff_json", relative, str(exc))

    report_path = _workspace_file(workspace, CORE_PATHS["executor_research_report"])
    report_text = report_path.read_text(encoding="utf-8", errors="replace") if report_path.is_file() else ""
    for heading in REQUIRED_REPORT_HEADINGS:
        if heading not in report_text:
            issue(errors, "missing_research_report_section", CORE_PATHS["executor_research_report"], heading)

    result = documents.get("result_pack", {})
    status = documents.get("executor_status", {})
    manifest = documents.get("run_manifest", {})
    facts = documents.get("writer_handoff_facts", {})
    validation = documents.get("writer_handoff_validation", {})
    for name, document, prefix in (
        ("result_pack", result, "external_executor_result.v1"),
        ("executor_status", status, "external_executor_status.v1"),
        ("run_manifest", manifest, "external_executor_manifest.v1"),
        ("writer_handoff_facts", facts, "writer_handoff_facts.v1"),
        ("writer_handoff_validation", validation, "writer_handoff_validation.v2"),
    ):
        if document and not _schema_matches(document, prefix):
            issue(errors, "unsupported_modern_handoff_schema", CORE_PATHS[name], repr(document.get("schema_version")))

    validation_status = _normalize_status(validation.get("status"))
    accepted_validation = {"completed", "partial"} if allow_partial else {"completed"}
    if validation_status not in accepted_validation:
        issue(
            errors,
            "writer_handoff_not_accepted",
            CORE_PATHS["writer_handoff_validation"],
            f"status={validation.get('status')!r}; expected {'ready|partial' if allow_partial else 'ready'}",
        )
    if validation.get("errors"):
        issue(errors, "writer_handoff_has_errors", CORE_PATHS["writer_handoff_validation"], "validation errors are non-empty")

    result_status = _normalize_status(result.get("executor_status") or result.get("status"))
    executor_status = _normalize_status(status.get("executor_status") or status.get("status") or status.get("current_state"))
    if result_status not in TERMINAL_STATUSES:
        issue(errors, "result_pack_not_terminal", CORE_PATHS["result_pack"], repr(result_status))
    if executor_status not in TERMINAL_STATUSES:
        issue(errors, "executor_status_not_terminal", CORE_PATHS["executor_status"], repr(executor_status))
    if result_status and executor_status and result_status != executor_status:
        issue(errors, "terminal_status_mismatch", CORE_PATHS["executor_status"], f"{result_status} != {executor_status}")
    if status.get("accepted") is True:
        issue(errors, "external_executor_cannot_self_accept", CORE_PATHS["executor_status"], "accepted=true")

    expected_hashes = validation.get("hashes") if isinstance(validation.get("hashes"), dict) else {}
    hash_paths = {
        "executor_research_report": CORE_PATHS["executor_research_report"],
        "result_pack": CORE_PATHS["result_pack"],
        "executor_status": CORE_PATHS["executor_status"],
        "run_manifest": CORE_PATHS["run_manifest"],
    }
    actual_hashes: dict[str, str] = {}
    for name, relative in hash_paths.items():
        path = _workspace_file(workspace, relative)
        if not path.is_file():
            continue
        actual_hashes[name] = _sha256(path)
        expected = expected_hashes.get(name)
        if not expected:
            issue(errors, "writer_validation_missing_core_hash", CORE_PATHS["writer_handoff_validation"], name)
        elif expected != actual_hashes[name]:
            issue(errors, "core_hash_changed_after_writer_validation", relative, name)

    if facts and validation:
        if facts.get("input_fingerprint") != validation.get("input_fingerprint"):
            issue(errors, "facts_validation_fingerprint_mismatch", CORE_PATHS["writer_handoff_facts"], "input fingerprints differ")
        if facts.get("handoff_id") != validation.get("handoff_id"):
            issue(errors, "facts_validation_handoff_id_mismatch", CORE_PATHS["writer_handoff_facts"], "handoff ids differ")

    indexed: dict[str, dict[str, Any]] = {}
    for item in _manifest_items(manifest):
        relative = item.get("path") or item.get("artifact_path")
        if not isinstance(relative, str) or not relative:
            issue(errors, "manifest_entry_missing_path", CORE_PATHS["run_manifest"], repr(item))
            continue
        indexed[relative] = item
    assets: list[dict[str, Any]] = []
    for kind, suffixes in (("figure", {".svg", ".png"}), ("table", {".csv", ".tsv"})):
        root = workspace / "external_executor" / kind
        if not root.is_dir():
            issue(warnings, "final_asset_directory_missing", f"external_executor/{kind}/", "no final assets were produced")
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(workspace).as_posix()
            if path.suffix.lower() not in suffixes:
                issue(errors, "unsupported_final_asset_format", relative, path.suffix.lower())
                continue
            if path.stat().st_size <= 0:
                issue(errors, "empty_final_asset", relative, kind)
                continue
            actual = _sha256(path)
            entry = indexed.get(relative)
            if not entry:
                issue(errors, "final_asset_not_registered", relative, CORE_PATHS["run_manifest"])
            elif entry.get("sha256") and entry.get("sha256") != actual:
                issue(errors, "final_asset_manifest_hash_mismatch", relative, kind)
            assets.append({"kind": kind, "path": relative, "sha256": actual, "size_bytes": path.stat().st_size})

    return {
        "schema_version": "researchos_t5_handoff_acceptance.v1",
        "ok": not errors,
        "status": "accepted_with_constraints" if not errors and (validation_status == "partial" or executor_status != "completed" or warnings) else "accepted" if not errors else "rejected",
        "executor_terminal_status": executor_status,
        "writer_handoff_status": validation.get("status"),
        "core_paths": CORE_PATHS,
        "core_hashes": actual_hashes,
        "handoff_id": validation.get("handoff_id"),
        "input_fingerprint": validation.get("input_fingerprint"),
        "assets": assets,
        "errors": errors,
        "warnings": warnings,
        "validated_at": _now_iso(),
    }


def _records(section: Any) -> list[dict[str, Any]]:
    if isinstance(section, list):
        return [item for item in section if isinstance(item, dict)]
    if isinstance(section, dict):
        value = section.get("items")
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _finite(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _unique_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        values = [values]
    return list(dict.fromkeys(str(value) for value in values if value not in (None, "")))


def _result_metrics(facts: dict[str, Any], result_pack: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    def add(record: dict[str, Any]) -> None:
        value = _finite(record.get("value"))
        if value is None:
            return
        record["value"] = value
        key = (
            str(record.get("experiment_id") or ""),
            str(record.get("dataset") or ""),
            str(record.get("metric") or record.get("name") or ""),
            str(record.get("method_id") or ""),
            str(value),
        )
        if key in seen:
            return
        seen.add(key)
        metrics.append({key: value for key, value in record.items() if value not in (None, "", [])})

    for item in facts.get("comprehensive_results", []) or []:
        if not isinstance(item, dict):
            continue
        experiment_ids = _unique_strings(item.get("experiment_ids")) or ["external_executor"]
        source_refs = _unique_strings(
            list(item.get("raw_result_files", []) or [])
            + list(item.get("table_files", []) or [])
            + list(item.get("figure_files", []) or [])
        )
        common = {
            "experiment_id": experiment_ids[0],
            "experiment_ids": experiment_ids,
            "dataset": item.get("dataset"),
            "split": item.get("split"),
            "metric": item.get("metric"),
            "name": item.get("metric"),
            "metric_direction": item.get("metric_direction"),
            "protocol_fingerprint": item.get("protocol_fingerprint"),
            "result_id": item.get("result_id"),
            "result_kind": item.get("result_kind"),
            "statistical_test": item.get("statistical_test"),
            "source_artifact": source_refs[0] if source_refs else None,
            "evidence_refs": source_refs,
        }
        add({
            **common,
            "metric_id": f"{item.get('result_id')}:method",
            "method_id": item.get("method"),
            "method_role": "ours" if item.get("result_kind") == "main" else "method_or_variant",
            "value": item.get("method_mean"),
            "std": item.get("method_std"),
            "n": item.get("method_n"),
            "comparison_outcome": item.get("comparison_outcome"),
        })
        add({
            **common,
            "metric_id": f"{item.get('result_id')}:comparator",
            "method_id": item.get("comparator"),
            "method_role": "baseline" if item.get("result_kind") == "main" else "comparator_or_variant",
            "baseline_id": item.get("comparator") if item.get("result_kind") == "main" else None,
            "value": item.get("comparator_mean"),
            "std": item.get("comparator_std"),
            "n": item.get("comparator_n"),
        })

    for run in _records(result_pack.get("experiment_runs")):
        run_metrics = run.get("metrics") if isinstance(run.get("metrics"), dict) else {}
        for name, value in run_metrics.items():
            source = run.get("raw_result_path") or run.get("result_path") or run.get("metrics_path")
            add({
                "metric_id": f"{run.get('run_id') or 'run'}:{name}",
                "experiment_id": run.get("experiment_id") or "external_executor",
                "run_id": run.get("run_id"),
                "dataset": run.get("dataset") or run.get("dataset_id"),
                "split": run.get("split"),
                "metric": name,
                "name": name,
                "value": value,
                "seed": run.get("seed"),
                "method_id": run.get("method_id") or run.get("implementation_id"),
                "method_role": run.get("method_role"),
                "baseline_id": run.get("baseline_id"),
                "protocol_fingerprint": run.get("protocol_fingerprint"),
                "source_artifact": source,
                "evidence_refs": _unique_strings([source, run.get("raw_log_path"), run.get("config_path")]),
            })
    return metrics


def _must_not_claim(facts: dict[str, Any], result_pack: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    boundary = result_pack.get("claim_boundary")
    if not isinstance(boundary, dict):
        realized = result_pack.get("realized_method_package")
        boundary = realized.get("claim_boundary") if isinstance(realized, dict) else {}
    if isinstance(boundary, dict):
        values.extend(boundary.get("must_not_claim", []) or [])
    for item in facts.get("limitations_and_open_issues", []) or []:
        if isinstance(item, dict) and item.get("category") == "prohibited over-claim":
            values.append(item.get("description"))
    return _unique_strings(values)


def build_t8_ingest_artifacts(workspace: Path, acceptance: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build T8-compatible evidence artifacts from a validated modern handoff."""

    if not acceptance.get("ok"):
        raise ValueError("cannot ingest a rejected T5 handoff")
    workspace = workspace.resolve()
    result_pack = _load_object(workspace / CORE_PATHS["result_pack"])
    facts = _load_object(workspace / CORE_PATHS["writer_handoff_facts"])
    metrics = _result_metrics(facts, result_pack)
    must_not_claim = _must_not_claim(facts, result_pack)
    artifacts = [item for item in facts.get("artifact_index", []) or [] if isinstance(item, dict)]

    claim_mappings: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    strength_map = {
        "supported candidate": ("supported_candidate", "moderate"),
        "partially supported candidate": ("partially_supported_candidate", "weak"),
        "unsupported": ("unsupported", "unsupported"),
    }
    for item in facts.get("claim_support", []) or []:
        if not isinstance(item, dict):
            continue
        claim_id = str(item.get("claim_id") or f"claim_{len(claim_mappings) + 1}")
        support_status, claim_strength = strength_map.get(str(item.get("strength") or "").strip().lower(), ("unresolved", "weak"))
        evidence_refs = _unique_strings(item.get("supporting_files"))
        limitations = _unique_strings(item.get("limitation"))
        mapping = {
            "claim_id": claim_id,
            "proposed_claim": item.get("proposed_claim"),
            "support_status": support_status,
            "claim_strength": claim_strength,
            "experiment_refs": _unique_strings(item.get("supporting_experiments")),
            "metric_refs": [metric.get("metric_id") for metric in metrics if metric.get("experiment_id") in _unique_strings(item.get("supporting_experiments"))],
            "evidence_refs": evidence_refs,
            "limitations": limitations,
            "authority": item.get("authority"),
            "allowed_wording": item.get("proposed_claim") if support_status != "unsupported" else None,
            "forbidden_wording": must_not_claim,
        }
        claim_mappings.append(mapping)
        claims.append({
            "claim_id": claim_id,
            "claim_text_conservative": item.get("proposed_claim"),
            "claim_strength": claim_strength,
            "supported_by": _unique_strings(mapping["metric_refs"] + evidence_refs),
            "must_not_say": must_not_claim,
            "paper_sections": ["experiments", "analysis"],
        })

    evidence_pack = {
        "version": "1.0",
        "semantics": "normalized_experiment_evidence_pack",
        "source": "modern_external_executor_writer_handoff",
        "dry_run": bool(result_pack.get("dry_run")),
        "mock_only": bool(result_pack.get("mock_only")),
        "evidence_grade": "writer_handoff_validated" if acceptance.get("status") == "accepted" else "writer_handoff_validated_with_constraints",
        "source_packs": [
            {"path": CORE_PATHS["executor_research_report"], "role": "primary_t8_research_fact_report"},
            {"path": CORE_PATHS["result_pack"], "role": "structured_executor_state"},
            {"path": CORE_PATHS["run_manifest"], "role": "artifact_provenance"},
            {"path": CORE_PATHS["writer_handoff_facts"], "role": "validated_structured_report_facts"},
        ],
        "artifacts": artifacts,
        "metrics": metrics,
        "claims": claim_mappings,
        "experiments": facts.get("experiments", []),
        "comprehensive_results": facts.get("comprehensive_results", []),
        "method_writing_resources": {
            "realized_method_package": result_pack.get("realized_method_package") or {},
            "implementations": result_pack.get("implementations") or {},
            "module_attributions": result_pack.get("module_attributions") or {},
            "framework_figure": result_pack.get("framework_figure") or {},
        },
        "realized_method_package": result_pack.get("realized_method_package") or {},
        "final_framework_figure": result_pack.get("framework_figure") or {},
        "figure_table_inventory": result_pack.get("figure_table_inventory") or {},
        "writer_handoff": {
            "report": CORE_PATHS["executor_research_report"],
            "validation": CORE_PATHS["writer_handoff_validation"],
            "handoff_id": acceptance.get("handoff_id"),
        },
        "must_not_claim": must_not_claim,
        "integrity": {
            "status": acceptance.get("status"),
            "issues": acceptance.get("errors", []) + acceptance.get("warnings", []),
            "source": _T8_RECEIPT,
        },
        "limitations": facts.get("limitations_and_open_issues", []),
    }
    result_to_claim = {
        "version": "1.0",
        "semantics": "mechanical_result_to_claim_map_not_final_scientific_judgment",
        "schema_semantics": "result_to_claim_mapping_not_paper_text",
        "source": "modern_external_executor_writer_handoff",
        "dry_run": bool(result_pack.get("dry_run")),
        "mock_only": bool(result_pack.get("mock_only")),
        "evidence_grade": evidence_pack["evidence_grade"],
        "integrity_audit": _T8_RECEIPT,
        "claim_mappings": claim_mappings,
        "claims": claims,
        "global_must_not_claim": must_not_claim,
        "final_claim_authority": "ResearchOS T8",
    }
    receipt = {
        **acceptance,
        "schema_version": "researchos_t5_t8_handoff.v1",
        "primary_input": CORE_PATHS["executor_research_report"],
        "supporting_inputs": [
            CORE_PATHS["result_pack"],
            CORE_PATHS["executor_status"],
            CORE_PATHS["run_manifest"],
            CORE_PATHS["writer_handoff_facts"],
            CORE_PATHS["writer_handoff_validation"],
            "external_executor/raw_results/",
            "external_executor/figure/",
            "external_executor/table/",
            "external_executor/expr/",
            "external_executor/evidence_package/",
        ],
        "normalized_outputs": [_EVIDENCE_PACK, _RESULT_TO_CLAIM],
        "metric_count": len(metrics),
        "claim_mapping_count": len(claim_mappings),
        "ingest_fingerprint": _canonical_hash({
            "core_hashes": acceptance.get("core_hashes"),
            "assets": acceptance.get("assets"),
            "metrics": metrics,
            "claims": claim_mappings,
        }),
        "ingested_at": _now_iso(),
    }
    return {_T8_RECEIPT: receipt, _EVIDENCE_PACK: evidence_pack, _RESULT_TO_CLAIM: result_to_claim}


def accept_and_ingest_t5_handoff(workspace: Path, *, allow_partial: bool = True) -> dict[str, Any]:
    """Validate the handoff and atomically publish deterministic T8 inputs."""

    acceptance = validate_modern_t5_handoff(workspace, allow_partial=allow_partial)
    if not acceptance.get("ok"):
        return acceptance
    outputs = build_t8_ingest_artifacts(workspace, acceptance)
    for relative, payload in outputs.items():
        _write_json_atomic(workspace / relative, payload)
    return outputs[_T8_RECEIPT]


def _project_id(workspace: Path) -> str:
    project_path = workspace / "project.yaml"
    if project_path.is_file():
        try:
            payload = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
            if isinstance(payload, dict):
                value = payload.get("project_id") or payload.get("id")
                if value:
                    return str(value)
        except Exception:  # noqa: BLE001 - fallback is deterministic
            pass
    return workspace.name or "t5-t8-project"


def prepare_t8_state(workspace: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    """Move a T5 workspace to T8 once, while preserving history and resumes."""

    if not receipt.get("ok"):
        raise ValueError("cannot prepare T8 state from a rejected handoff")
    workspace = workspace.resolve()
    state_path = workspace / "state.yaml"
    state_existed = state_path.is_file()
    if state_existed:
        state = StateYaml.load_yaml(state_path)
    else:
        state = StateYaml(project_id=_project_id(workspace), current_task="T8-STYLE-GATE", status="PAUSED")

    t8_history = any(str(item.task).startswith("T8") for item in state.history)
    already_downstream = str(state.current_task).startswith(("T8", "T9")) or t8_history
    if state.status == "COMPLETED" and already_downstream:
        return {"action": "already_completed", "should_run": False, "current_task": state.current_task, "status": state.status}
    if str(state.current_task).startswith("T9"):
        return {"action": "already_downstream", "should_run": False, "current_task": state.current_task, "status": state.status}

    prior_task = state.current_task
    if not state_existed:
        action = "entered_t8"
    elif not str(state.current_task).startswith("T8"):
        state.current_task = "T8-STYLE-GATE"
        state.status = "PAUSED"
        state.pending_gate = None
        state.paused_at = _now_iso()
        state.last_error = None
        action = "entered_t8"
    else:
        action = "resume_t8"

    records = state.task_context.get("t5_t8_bridge")
    history = list(records) if isinstance(records, list) else []
    fingerprint = str(receipt.get("ingest_fingerprint") or "")
    if not any(item.get("ingest_fingerprint") == fingerprint for item in history if isinstance(item, dict)):
        history.append({
            "from_task": prior_task,
            "to_task": state.current_task,
            "action": action,
            "handoff_id": receipt.get("handoff_id"),
            "ingest_fingerprint": fingerprint,
            "receipt": _T8_RECEIPT,
            "prepared_at": _now_iso(),
        })
    state.task_context["t5_t8_bridge"] = history[-20:]
    state.task_context["t5_t8_handoff_receipt"] = _T8_RECEIPT
    state.dump_yaml(state_path)
    return {"action": action, "should_run": True, "current_task": state.current_task, "status": state.status}
