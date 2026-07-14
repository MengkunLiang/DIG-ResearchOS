#!/usr/bin/env python3
"""Run lightweight preflight for ResearchOS project Skill specialization."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _common import emit_json, resolve_repo_root, resolve_workspace, run_preflight


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check project, template, marker, and compiler prerequisites."
    )
    parser.add_argument("--workspace", required=True, help="ResearchOS workspace path")
    parser.add_argument("--repo-root", help="ResearchOS repository root")
    parser.add_argument(
        "--mode",
        choices=("build", "dry-run", "validate-only"),
        default="build",
        help="Mode whose safety prerequisites should be checked",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (default output is also JSON for stable agent use)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        workspace = resolve_workspace(args.workspace)
        repo_root = resolve_repo_root(args.repo_root, workspace)
        payload = run_preflight(workspace, repo_root, mode=args.mode)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        payload = {
            "schema_version": "project_skill_specialization_preflight.v1",
            "status": "fail",
            "errors": [
                {
                    "code": "preflight_invocation_error",
                    "message": str(exc),
                }
            ],
            "warnings": [],
        }
    emit_json(payload)
    return 1 if payload.get("status") == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
