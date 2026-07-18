#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from _common import canonical_hash, load_json, output_path, resolve_workspace, schema_major, utc_now, dump_json_atomic


REQUIRED_JSON = {
    "result_pack": "result_pack.json",
    "executor_status": "executor_status.json",
    "run_manifest": "report/run_manifest.json",
}
CONTROL_FILES = ("AGENTS.md", "allowed_paths.txt", "handoff_pack.json", "expected_outputs_schema.json")
OUTPUTS = (
    "external_executor/report/phase_F/writer_handoff_preflight.json",
    "external_executor/report/phase_F/writer_handoff_snapshot.json",
    "external_executor/report/phase_F/writer_handoff_facts.json",
    "external_executor/executor_research_report.md",
    "external_executor/report/phase_F/writer_handoff_validation.json",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check final Writer Handoff inputs and write boundaries.")
    parser.add_argument("--workspace")
    parser.add_argument("--output")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    documents: dict[str, object] = {}

    for name in CONTROL_FILES:
        path = ext / name
        if not path.is_file():
            errors.append({"code": "missing_control", "path": f"external_executor/{name}"})
    for label, rel in REQUIRED_JSON.items():
        path = ext / rel
        if not path.is_file():
            errors.append({"code": "missing_required_input", "path": f"external_executor/{rel}"})
            continue
        try:
            value = load_json(path)
            if not isinstance(value, dict):
                raise TypeError("expected a JSON object")
            documents[label] = value
            major = schema_major(value.get("schema_version"))
            if major not in {None, 1}:
                errors.append({"code": "unsupported_schema_major", "path": f"external_executor/{rel}"})
        except Exception as exc:  # noqa: BLE001
            errors.append({"code": "invalid_json", "path": f"external_executor/{rel}", "message": str(exc)})

    for directory in ("figure", "table"):
        path = ext / directory
        if not path.is_dir():
            warnings.append({"code": "missing_asset_directory", "path": f"external_executor/{directory}/"})
        elif not any(item.is_file() for item in path.rglob("*")):
            warnings.append({"code": "empty_asset_directory", "path": f"external_executor/{directory}/"})

    for rel in OUTPUTS:
        try:
            output_path(ws, rel, rel)
        except Exception as exc:  # noqa: BLE001
            errors.append({"code": "write_boundary_error", "path": rel, "message": str(exc)})

    report = {
        "schema_version": "writer_handoff_preflight.v2",
        "status": "blocked" if errors else ("partial" if warnings else "pass"),
        "required_inputs": [f"external_executor/{value}" for value in REQUIRED_JSON.values()],
        "required_outputs": list(OUTPUTS),
        "errors": errors,
        "warnings": warnings,
        "input_fingerprint": canonical_hash(documents),
        "created_at": utc_now(),
    }
    destination = output_path(ws, args.output, OUTPUTS[0])
    dump_json_atomic(destination, report)
    print(json.dumps(report, ensure_ascii=False))
    return 2 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
