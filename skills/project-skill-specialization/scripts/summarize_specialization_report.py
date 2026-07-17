#!/usr/bin/env python3
"""Summarize a durable Project Skill specialization report."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import emit_json, load_report, resolve_workspace

ALLOWED_STATUSES = {"ready", "incomplete", "failed"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read and summarize skill_specialization_report.json."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--workspace", help="ResearchOS workspace path")
    source.add_argument("--report", help="Path to specialization report")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    return parser


def _paths(items: Any, *, limit: int = 8) -> tuple[list[str], int]:
    output: list[str] = []
    if not isinstance(items, list):
        return output
    for item in items:
        if isinstance(item, str):
            output.append(item)
        elif isinstance(item, dict):
            value = item.get("path") or item.get("field_path")
            if isinstance(value, str):
                output.append(value)
    return output[:limit], len(output)


def _error_codes(report: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for key in (
        "schema_errors",
        "mapping_errors",
        "render_errors",
        "template_integrity_errors",
        "errors",
    ):
        values = report.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict) and isinstance(value.get("code"), str):
                codes.append(value["code"])
            elif isinstance(value, str):
                codes.append(value)
    return list(dict.fromkeys(codes))


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.workspace:
            workspace = resolve_workspace(args.workspace)
            report_path = (
                workspace / "external_executor/report/skill_specialization_report.json"
            )
        else:
            report_path = Path(args.report).expanduser().resolve()
            if report_path.parent.name == "report" and report_path.parent.parent.name == "external_executor":
                workspace = report_path.parent.parent.parent
            else:
                workspace = report_path.parent.parent

        report = load_report(report_path)
        status = report.get("status")
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"Unsupported report status: {status!r}")

        skills_total = report.get("skills_total")
        skills_specialized = report.get("skills_specialized")
        required_uncertain, required_uncertain_count = _paths(report.get("required_uncertain_fields"))
        optional_uncertain, optional_uncertain_count = _paths(report.get("optional_uncertain_fields"))
        errors = _error_codes(report)

        next_action = {
            "ready": "continue_to_gate",
            "incomplete": "resolve_uncertain_context",
            "failed": "repair_specializer",
        }[status]

        summary = {
            "schema_version": "project_skill_specialization_summary.v1",
            "skill": "project-skill-specialization",
            "status": status,
            "workspace": str(workspace),
            "context": report.get("context_file"),
            "skills": f"{skills_specialized}/{skills_total}",
            "report": str(report_path),
            "required_uncertain_count": required_uncertain_count,
            "required_uncertain_fields": required_uncertain,
            "required_uncertain_truncated": required_uncertain_count > len(required_uncertain),
            "optional_uncertain_count": optional_uncertain_count,
            "optional_uncertain_fields": optional_uncertain,
            "optional_uncertain_truncated": optional_uncertain_count > len(optional_uncertain),
            "errors": errors,
            "next_action": next_action,
        }
        if args.json:
            emit_json(summary)
        else:
            print(f"Project Skill Specialization: {status}")
            print(f"Context: {summary['context'] or 'not available'}")
            print(f"Skills: {summary['skills']}")
            print(
                "Required uncertain fields: "
                f"{len(required_uncertain)}"
            )
            print(f"Report: {report_path}")
            print(f"Next action: {next_action}")
        return 0 if status in {"ready", "incomplete"} else 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        payload = {
            "schema_version": "project_skill_specialization_summary.v1",
            "skill": "project-skill-specialization",
            "status": "failed",
            "errors": [
                {
                    "code": "specialization_report_summary_error",
                    "message": str(exc),
                }
            ],
            "next_action": "repair_specializer",
        }
        emit_json(payload)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
