#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from _common import dump_json_atomic, finite_number, load_json, read_simple_selector, utc_now


def extract_one(attempt: Path, metric: dict) -> dict:
    spec = metric.get("extractor", {})
    typ = spec.get("type")
    path = (attempt / "source" / spec.get("path", "metrics.json")).resolve(strict=False)
    if not str(path).startswith(str((attempt / "source").resolve())):
        raise ValueError("Metric path escapes source directory")
    if not path.exists():
        # Allow direct log paths in attempt root.
        alt = (attempt / spec.get("path", "")).resolve(strict=False)
        if str(alt).startswith(str(attempt.resolve())) and alt.exists():
            path = alt
        else:
            raise FileNotFoundError(path)
    raw = None
    matched = None
    if typ == "json":
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = read_simple_selector(data, spec.get("selector"))
    elif typ == "jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        selected = [read_simple_selector(row, spec.get("selector")) for row in rows]
        raw = selected
    elif typ == "csv":
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        column = spec.get("column") or spec.get("selector")
        raw = [row[column] for row in rows]
    elif typ == "regex":
        text = path.read_text(encoding="utf-8", errors="replace")
        pattern = spec.get("pattern")
        if not pattern:
            raise ValueError("regex extractor requires pattern")
        matches = list(re.finditer(pattern, text, flags=re.MULTILINE))
        if not matches:
            raise ValueError(f"Pattern did not match: {pattern}")
        group = int(spec.get("group", 1))
        matched = matches[-1].group(0)
        raw = matches[-1].group(group)
    else:
        raise ValueError(f"Unsupported extractor type: {typ}")

    values = raw if isinstance(raw, list) else [raw]
    numeric = [float(v) for v in values if finite_number(v)]
    if not numeric:
        raise ValueError(f"No finite numeric values for {metric.get('name')}")
    agg = metric.get("aggregation", "mean")
    if agg == "mean":
        value = sum(numeric) / len(numeric)
    elif agg == "median":
        s = sorted(numeric); n = len(s); value = s[n//2] if n % 2 else (s[n//2-1] + s[n//2]) / 2
    elif agg == "last":
        value = numeric[-1]
    elif agg == "min":
        value = min(numeric)
    elif agg == "max":
        value = max(numeric)
    else:
        raise ValueError(f"Unsupported aggregation: {agg}")
    return {
        "name": metric.get("name"), "primary": bool(metric.get("primary")), "value": value,
        "values": numeric, "count": len(numeric), "direction": metric.get("direction"),
        "units": metric.get("units", ""), "aggregation": agg, "source_path": str(path),
        "extractor": spec, "raw_match": matched, "reference": metric.get("reference", {}),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract declared baseline metrics into a normalized JSON record.")
    ap.add_argument("--attempt-dir", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    attempt = Path(args.attempt_dir).expanduser().resolve()
    spec = load_json(Path(args.spec).expanduser().resolve())
    items = []
    errors = []
    for metric in spec.get("metrics", []):
        try:
            items.append(extract_one(attempt, metric))
        except Exception as exc:
            errors.append({"metric": metric.get("name"), "error": str(exc)})
    payload = {"schema_version": "baseline_metrics.v1", "generated_at": utc_now(), "status": "complete" if items and not errors else "partial" if items else "failed", "items": items, "errors": errors}
    dump_json_atomic(Path(args.output).expanduser().resolve(), payload)
    print(payload["status"])
    return 0 if payload["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
