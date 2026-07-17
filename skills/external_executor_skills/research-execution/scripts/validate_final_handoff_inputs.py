#!/usr/bin/env python3
"""Validate final external-executor inputs needed by T8."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import atomic_write_json, resolve_in_workspace, sha256_file, utc_now, workspace_root


REQUIRED_INPUTS = {
    "executor_research_report": "external_executor/executor_research_report.md",
}

OPTIONAL_CONTEXT_INPUTS = {
    "result_pack": "external_executor/result_pack.json",
    "executor_status": "external_executor/executor_status.json",
    "handoff_pack": "external_executor/handoff_pack.json",
    "run_manifest": "external_executor/run_manifest.json",
}

TERMINAL_STATUS_ALIASES = {
    "done": "completed",
    "complete": "completed",
    "completed": "completed",
    "partial": "partial",
    "partial_results_ready": "partial",
    "blocked": "blocked",
    "failed": "failed",
}


def _add(report: dict[str, Any], level: str, code: str, path: str, message: str) -> None:
    report[level].append({"code": code, "path": path, "message": message})


def _normalize_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    return TERMINAL_STATUS_ALIASES.get(text, text)


def _read_required_file(root: Path, rel_path: str, report: dict[str, Any]) -> str:
    path = resolve_in_workspace(root, rel_path)
    entry = {
        "path": rel_path,
        "exists": path.is_file(),
        "sha256": None,
        "bytes": 0,
        "nonempty": False,
    }
    report["required_inputs"].append(entry)
    if not path.is_file():
        _add(report, "errors", "missing_required_input", rel_path, "required T8 handoff input is missing")
        return ""
    entry["sha256"] = sha256_file(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    entry["bytes"] = len(text.encode("utf-8"))
    entry["nonempty"] = bool(text.strip())
    if not text.strip():
        _add(report, "errors", "empty_required_input", rel_path, "executor research report is empty")
    return text


def _read_json_object(root: Path, rel_path: str, report: dict[str, Any], *, required: bool = False) -> dict[str, Any]:
    path = resolve_in_workspace(root, rel_path)
    entry = {"path": rel_path, "exists": path.is_file(), "sha256": None, "json_object": False}
    bucket = "required_inputs" if required else "context_inputs"
    report[bucket].append(entry)
    if not path.is_file():
        if required:
            _add(report, "errors", "missing_required_input", rel_path, "required downstream input is missing")
        return {}
    entry["sha256"] = sha256_file(path)
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _add(report, "errors" if required else "warnings", "invalid_json", rel_path, str(exc))
        return {}
    if not isinstance(payload, dict):
        _add(report, "errors" if required else "warnings", "invalid_json_type", rel_path, "expected a JSON object")
        return {}
    entry["json_object"] = True
    return payload


def _validate_result_pack(result: dict[str, Any], report: dict[str, Any]) -> None:
    rel = OPTIONAL_CONTEXT_INPUTS["result_pack"]
    if not result:
        return
    semantics = result.get("semantics")
    schema = result.get("schema_version")
    if semantics not in {"external_executor_result_pack", None}:
        _add(report, "warnings", "unexpected_result_pack_semantics", rel, repr(semantics))
    if semantics is None and schema != "external_executor_result.v1":
        _add(report, "warnings", "missing_result_pack_semantics", rel, "result_pack semantics is absent")
    status = _normalize_status(result.get("executor_status") or result.get("status"))
    if status not in {"completed", "partial", "blocked", "failed"}:
        _add(report, "errors", "non_terminal_result_pack_status", rel, repr(result.get("executor_status") or result.get("status")))
    report["checks"]["result_pack_status"] = status or None


def _validate_executor_status(status_payload: dict[str, Any], report: dict[str, Any]) -> None:
    rel = OPTIONAL_CONTEXT_INPUTS["executor_status"]
    if not status_payload:
        return
    semantics = status_payload.get("semantics")
    schema = status_payload.get("schema_version")
    if semantics not in {"external_executor_status", None}:
        _add(report, "warnings", "unexpected_executor_status_semantics", rel, repr(semantics))
    if semantics is None and schema != "external_executor_status.v1":
        _add(report, "warnings", "missing_executor_status_semantics", rel, "executor_status semantics is absent")
    if status_payload.get("accepted") is True:
        _add(report, "errors", "executor_status_accepted_true", rel, "external executor must leave accepted=false")
    status = _normalize_status(
        status_payload.get("status")
        or status_payload.get("current_state")
        or status_payload.get("executor_status")
    )
    if status not in {"completed", "partial", "blocked", "failed"}:
        _add(report, "errors", "non_terminal_executor_status", rel, repr(status_payload.get("status") or status_payload.get("current_state") or status_payload.get("executor_status")))
    report["checks"]["executor_status"] = status or None


def _validate_handoff_pack(handoff: dict[str, Any], report: dict[str, Any]) -> None:
    rel = OPTIONAL_CONTEXT_INPUTS["handoff_pack"]
    if not handoff:
        return
    if handoff.get("schema_version") != "external_executor_handoff.v1":
        _add(report, "warnings", "unexpected_handoff_schema_version", rel, repr(handoff.get("schema_version")))
    if handoff.get("semantics") not in {"external_experiment_handoff_contract", None}:
        _add(report, "warnings", "unexpected_handoff_semantics", rel, repr(handoff.get("semantics")))


def _validate_manifest_reference(root: Path, result: dict[str, Any], status: dict[str, Any], report: dict[str, Any]) -> None:
    manifest_rel = result.get("run_manifest") or status.get("run_manifest")
    if not isinstance(manifest_rel, str) or not manifest_rel.strip():
        report["checks"]["run_manifest_ref"] = None
        return
    manifest_rel = manifest_rel.strip()
    report["checks"]["run_manifest_ref"] = manifest_rel
    try:
        path = resolve_in_workspace(root, manifest_rel)
    except ValueError as exc:
        _add(report, "errors", "manifest_path_escape", manifest_rel, str(exc))
        return
    if not path.is_file():
        _add(report, "errors", "missing_referenced_manifest", manifest_rel, "referenced run manifest is missing")


def _validate_executor_report(text: str, report: dict[str, Any]) -> None:
    rel = REQUIRED_INPUTS["executor_research_report"]
    if not text:
        return
    lowered = text.lower()
    report["checks"]["executor_research_report_chars"] = len(text)
    for label, tokens in {
        "method_or_implementation": ("method", "implementation", "实现", "方法"),
        "experiment_or_result": ("experiment", "result", "metric", "实验", "结果", "指标"),
        "limitation_or_boundary": ("limitation", "boundary", "risk", "限制", "边界", "风险"),
    }.items():
        present = any(token in lowered for token in tokens)
        report["checks"][f"report_mentions_{label}"] = present
        if not present:
            _add(
                report,
                "warnings",
                f"report_missing_{label}",
                rel,
                "executor research report may be too thin for T8 writing handoff",
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate final T5-to-T8 handoff inputs.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--output", default="external_executor/final_handoff_input_validation.json")
    args = parser.parse_args()

    root = workspace_root(args.workspace)
    external_executor_dir = resolve_in_workspace(root, "external_executor")
    report: dict[str, Any] = {
        "schema_version": "research_execution_final_handoff_input_validation.v1",
        "semantics": "final_external_executor_handoff_inputs_validation",
        "validated_at": utc_now(),
        "valid": True,
        "required_inputs": [],
        "context_inputs": [],
        "errors": [],
        "warnings": [],
        "checks": {},
    }
    report["checks"]["external_executor_dir_exists"] = external_executor_dir.is_dir()
    if not external_executor_dir.is_dir():
        _add(report, "errors", "missing_external_executor_dir", "external_executor", "external executor directory is missing")

    executor_report = _read_required_file(root, REQUIRED_INPUTS["executor_research_report"], report)
    _validate_executor_report(executor_report, report)

    result = _read_json_object(root, OPTIONAL_CONTEXT_INPUTS["result_pack"], report)
    status = _read_json_object(root, OPTIONAL_CONTEXT_INPUTS["executor_status"], report)
    handoff = _read_json_object(root, OPTIONAL_CONTEXT_INPUTS["handoff_pack"], report)
    _read_json_object(root, OPTIONAL_CONTEXT_INPUTS["run_manifest"], report)
    _validate_result_pack(result, report)
    _validate_executor_status(status, report)
    _validate_handoff_pack(handoff, report)
    _validate_manifest_reference(root, result, status, report)

    result_status = report["checks"].get("result_pack_status")
    executor_status = report["checks"].get("executor_status")
    if result_status and executor_status and result_status != executor_status:
        _add(
            report,
            "warnings",
            "status_alias_mismatch",
            REQUIRED_INPUTS["executor_status"],
            f"result_pack={result_status}, executor_status={executor_status}",
        )

    report["valid"] = not report["errors"]
    output_path = resolve_in_workspace(root, args.output)
    atomic_write_json(output_path, report)
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
