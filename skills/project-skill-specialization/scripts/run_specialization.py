#!/usr/bin/env python3
"""Invoke the public ResearchOS Project Skill Specializer service."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import (
    EXPECTED_SKILL_COUNT,
    emit_json,
    load_compiler,
    load_report,
    normalize_result,
    resolve_repo_root,
    resolve_workspace,
    run_preflight,
)

ALLOWED_MODES = ("build", "dry-run", "validate-only")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build, dry-run, or validate a project-specific executor Skill Suite."
    )
    parser.add_argument("--workspace", required=True, help="ResearchOS workspace path")
    parser.add_argument("--repo-root", help="ResearchOS repository root")
    parser.add_argument(
        "--mode",
        choices=ALLOWED_MODES,
        default="build",
        help="Specialization mode",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (default output is also JSON for stable agent use)",
    )
    return parser


def _status_from(result: dict[str, Any], report: dict[str, Any] | None) -> str:
    for payload in (report, result):
        if isinstance(payload, dict):
            status = payload.get("status")
            if status in {"ready", "incomplete", "failed"}:
                return status
    return "failed"


def _field_paths(items: Any, *, limit: int = 8) -> tuple[list[str], int]:
    """Return a bounded, stable field-path preview for terminal consumers."""

    paths: list[str] = []
    if isinstance(items, list):
        for item in items:
            value = item if isinstance(item, str) else item.get("path") if isinstance(item, Mapping) else None
            if isinstance(value, str) and value and value not in paths:
                paths.append(value)
    return paths[:limit], len(paths)


def _compact_errors(items: Any, *, limit: int = 8) -> tuple[list[dict[str, str]], int]:
    errors: list[dict[str, str]] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, Mapping):
                entry = {
                    key: str(item[key])
                    for key in ("code", "path", "message")
                    if item.get(key) not in (None, "")
                }
            else:
                entry = {"message": str(item)}
            if entry:
                errors.append(entry)
    return errors[:limit], len(errors)


def _next_action(status: str, errors: Any) -> str:
    if isinstance(errors, list):
        codes = {
            str(item.get("code"))
            for item in errors
            if isinstance(item, Mapping) and item.get("code")
        }
        if "active_executor_suite_cannot_be_replaced" in codes:
            return "wait_for_executor_completion"
        if "required_input_missing" in codes or "handoff_missing" in codes:
            return "provide_required_input"
    return {
        "ready": "continue_to_gate",
        "incomplete": "resolve_uncertain_context",
        "failed": "repair_specializer",
    }.get(status, "repair_specializer")


def _compact_payload(
    *,
    mode: str,
    status: str,
    workspace: Path,
    repo_root: Path,
    preflight: Mapping[str, Any],
    report: Mapping[str, Any] | None,
    report_path: Path,
    errors: Any,
) -> dict[str, Any]:
    """Keep wrapper output readable; the durable report remains on disk."""

    report_data = report if isinstance(report, Mapping) else {}
    required_preview, required_count = _field_paths(report_data.get("required_uncertain_fields"))
    optional_preview, optional_count = _field_paths(report_data.get("optional_uncertain_fields"))
    error_preview, error_count = _compact_errors(errors)
    warning_preview, warning_count = _compact_errors(preflight.get("warnings"))
    report_exists = report_path.is_file()
    suite_present = (
        (workspace / "external_executor/project_skill_context.yaml").is_file()
        and (workspace / "external_executor/skills").is_dir()
    )
    try:
        report_reference = report_path.relative_to(workspace).as_posix() if report_exists else "not_published"
    except ValueError:
        report_reference = str(report_path) if report_exists else "not_published"
    publication = (
        "published"
        if mode == "build" and status in {"ready", "incomplete"}
        else "existing_suite_preserved"
        if suite_present
        else "not_published"
    )
    return {
        "schema_version": "project_skill_specialization_invocation.v1",
        "mode": mode,
        "status": status,
        "workspace": str(workspace),
        "repo_root": str(repo_root),
        "preflight_status": preflight.get("status"),
        "publication": publication,
        "context": "external_executor/project_skill_context.yaml" if suite_present else "not_published",
        "skills": f"{report_data.get('skills_specialized', 0)}/{report_data.get('skills_total', EXPECTED_SKILL_COUNT)}",
        "report": report_reference,
        "required_uncertain_count": required_count,
        "required_uncertain_fields": required_preview,
        "required_uncertain_truncated": required_count > len(required_preview),
        "optional_uncertain_count": optional_count,
        "optional_uncertain_fields": optional_preview,
        "optional_uncertain_truncated": optional_count > len(optional_preview),
        "warnings": warning_preview,
        "warning_count": warning_count,
        "errors": error_preview,
        "error_count": error_count,
        "next_action": _next_action(status, errors),
    }


def main() -> int:
    args = build_parser().parse_args()
    try:
        workspace = resolve_workspace(args.workspace)
        repo_root = resolve_repo_root(args.repo_root, workspace)
        preflight = run_preflight(workspace, repo_root, mode=args.mode)
        if preflight.get("status") == "fail":
            existing_report_path = workspace / "external_executor/skill_specialization_report.json"
            existing_report: dict[str, Any] | None = None
            if existing_report_path.is_file():
                try:
                    existing_report = load_report(existing_report_path)
                except Exception:
                    existing_report = None
            payload = _compact_payload(
                mode=args.mode,
                status="failed",
                workspace=workspace,
                repo_root=repo_root,
                preflight=preflight,
                report=existing_report,
                report_path=existing_report_path,
                errors=preflight.get("errors", []),
            )
            emit_json(payload)
            return 1

        compiler = load_compiler(repo_root)
        result_object = compiler(
            workspace=workspace,
            repo_root=repo_root,
            dry_run=args.mode == "dry-run",
            validate_only=args.mode == "validate-only",
        )
        result = normalize_result(result_object)

        report_path = workspace / "external_executor/skill_specialization_report.json"
        candidate = result.get("report_path")
        if candidate:
            candidate_path = Path(str(candidate))
            if not candidate_path.is_absolute():
                candidate_path = workspace / candidate_path
            report_path = candidate_path

        report: dict[str, Any] | None = None
        report_error: str | None = None
        if report_path.is_file():
            try:
                report = load_report(report_path)
            except Exception as exc:  # noqa: BLE001 - preserve report error
                report_error = str(exc)

        # Dry runs deliberately leave no durable report.  The compiler still
        # returns an in-memory report, which is enough for a concise summary.
        if report is None and isinstance(result.get("report"), dict):
            report = result["report"]

        status = _status_from(result, report)
        errors = list(result.get("errors") or [])
        if report_error:
            errors.append(
                {
                    "code": "specialization_report_unreadable",
                    "path": str(report_path),
                    "message": report_error,
                }
            )
            status = "failed"

        payload = _compact_payload(
            mode=args.mode,
            status=status,
            workspace=workspace,
            repo_root=repo_root,
            preflight=preflight,
            report=report,
            report_path=report_path,
            errors=errors,
        )
        emit_json(payload)
        return 0 if status in {"ready", "incomplete"} else 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        payload = {
            "schema_version": "project_skill_specialization_invocation.v1",
            "mode": getattr(args, "mode", "build"),
            "status": "failed",
            "errors": [
                {
                    "code": "specializer_invocation_error",
                    "message": str(exc),
                }
            ],
        }
        emit_json(payload)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
