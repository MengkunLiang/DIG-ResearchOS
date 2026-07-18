#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from _common import (
    canonical_hash,
    dump_json_atomic,
    file_entry,
    load_json,
    output_path,
    resolve_workspace,
    stable_id,
    utc_now,
)


def optional_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = load_json(path)
    return value if isinstance(value, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Pin final core state and all figure/table assets for Writer Handoff.")
    parser.add_argument("--workspace")
    parser.add_argument("--output")
    args = parser.parse_args()
    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    paths = {
        "result_pack": ext / "result_pack.json",
        "executor_status": ext / "executor_status.json",
        "run_manifest": ext / "report/run_manifest.json",
        "handoff_pack": ext / "handoff_pack.json",
        "expected_outputs_schema": ext / "expected_outputs_schema.json",
    }
    documents = {name: optional_json(path) for name, path in paths.items()}
    core_files = {name: file_entry(ws, path) for name, path in paths.items() if path.is_file()}
    assets: list[dict[str, Any]] = []
    for kind in ("figure", "table"):
        root = ext / kind
        for path in sorted(item for item in root.rglob("*") if item.is_file()) if root.is_dir() else []:
            assets.append({"kind": kind, **file_entry(ws, path)})

    identity = {"core_files": core_files, "assets": assets, "documents": documents}
    fingerprint = canonical_hash(identity)
    snapshot = {
        "schema_version": "writer_handoff_snapshot.v2",
        "handoff_id": stable_id("WH", fingerprint[:16]),
        "input_fingerprint": fingerprint,
        "core_files": core_files,
        "assets": assets,
        "documents": documents,
        "source_paths": {name: entry["path"] for name, entry in core_files.items()},
        "created_at": utc_now(),
    }
    destination = output_path(
        ws,
        args.output,
        "external_executor/report/writer_handoff_snapshot.json",
    )
    dump_json_atomic(destination, snapshot)
    print(json.dumps({"handoff_id": snapshot["handoff_id"], "assets": len(assets)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
