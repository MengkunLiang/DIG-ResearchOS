#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import dump_json_atomic, load_json, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize module and mechanism registry.")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    snapshot = load_json(Path(args.snapshot).expanduser().resolve())
    modules = []
    known = set()
    for item in snapshot.get("modules", []):
        mid = str(item.get("module_id", ""))
        if not mid or mid in known:
            continue
        known.add(mid)
        record = dict(item)
        record.setdefault("owner_method_id", "ours")
        record.setdefault("implementation_status", "unknown")
        record.setdefault("empirical_test_status", "unknown")
        record.setdefault("code_paths", [])
        record.setdefault("config_keys", [])
        record.setdefault("ablation_switches", [])
        record.setdefault("diagnostic_switches", [])
        record.setdefault("mechanism_ids", [])
        record.setdefault("source_refs", [])
        modules.append(record)
    tested_ids = set()
    for run in snapshot.get("runs", []):
        if not run.get("eligible"):
            continue
        for mid in run.get("module_states", {}):
            tested_ids.add(str(mid))
        intervention = run.get("intervention", {})
        for mid in intervention.get("module_ids", []) if isinstance(intervention, dict) else []:
            tested_ids.add(str(mid))
    for item in modules:
        if item["module_id"] in tested_ids:
            item["empirical_test_status"] = "tested"
        elif item.get("implementation_status") == "implemented":
            item["empirical_test_status"] = "untested"
    mechanisms = snapshot.get("mechanisms", [])
    payload = {
        "schema_version": "module_registry.v1", "generated_at": utc_now(),
        "status": "complete" if modules else "blocked", "items": modules,
        "mechanisms": {"status": "complete" if mechanisms else "partial", "items": mechanisms},
    }
    dump_json_atomic(Path(args.output).expanduser().resolve(), payload)
    print(f"modules={len(modules)} mechanisms={len(mechanisms)}")
    return 0 if modules else 2


if __name__ == "__main__":
    raise SystemExit(main())
