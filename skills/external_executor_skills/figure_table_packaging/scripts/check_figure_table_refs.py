#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "external_executor/result_pack.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = []
    for item in data.get("figure_table_inventory") or []:
        if not isinstance(item, dict):
            continue
        figure_id = item.get("figure_id") or item.get("table_id") or item.get("id") or "<unknown>"
        evidence = item.get("source_result") or item.get("source_config") or item.get("evidence_refs")
        if not evidence:
            missing.append(str(figure_id))
    if missing:
        print("inventory items missing evidence refs: " + ", ".join(missing))
        return 1
    print("figure/table inventory evidence refs present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
