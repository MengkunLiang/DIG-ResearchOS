#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

REQUIRED = [
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


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "external_executor/result_pack.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = [field for field in REQUIRED if field not in data]
    if missing:
        print("missing required fields: " + ", ".join(missing))
        return 1
    print("result pack required fields present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
