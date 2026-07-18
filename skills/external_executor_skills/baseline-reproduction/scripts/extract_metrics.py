#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from _common import dump_csv_atomic, dump_json_atomic, find_workspace, finite_number, is_within, load_json, read_simple_selector, relpath, resolve_in_workspace, sha256_file, slugify, utc_now


RAW_METRIC_FIELDS = [
    "schema_version",
    "extracted_at",
    "reproduction_id",
    "baseline_id",
    "candidate_id",
    "attempt",
    "dataset_name",
    "dataset_version",
    "dataset_split",
    "dataset_path",
    "metric_name",
    "value_index",
    "value",
    "units",
    "aggregation",
    "source_path",
    "extractor_type",
    "selector",
    "reference_type",
]


def candidate_paths(attempt: Path, evidence_dir: Path | None, result_dir: Path | None, rel: str) -> list[Path]:
    paths = []
    if evidence_dir:
        paths.append((evidence_dir / rel).resolve(strict=False))
    if result_dir:
        paths.extend([(result_dir / "outputs" / rel).resolve(strict=False), (result_dir / rel).resolve(strict=False)])
    paths.extend([(attempt / "source" / rel).resolve(strict=False), (attempt / rel).resolve(strict=False)])
    return paths


def extract_one(attempt: Path, evidence_dir: Path | None, result_dir: Path | None, metric: dict) -> dict:
    spec = metric.get("extractor", {})
    typ = spec.get("type")
    rel = spec.get("path", "metrics.json")
    path = None
    for candidate in candidate_paths(attempt, evidence_dir, result_dir, rel):
        allowed_roots = [attempt.resolve()]
        if evidence_dir:
            allowed_roots.append(evidence_dir.resolve())
        if result_dir:
            allowed_roots.append(result_dir.resolve())
        if any(is_within(candidate, root) for root in allowed_roots) and candidate.exists():
            path = candidate
            break
    if path is None:
        raise FileNotFoundError(rel)
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


def relative_or_absolute(workspace: Path, path_text: str) -> str:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        return path_text
    try:
        return relpath(workspace, path)
    except ValueError:
        return path_text


def dataset_slug(dataset: dict) -> str:
    parts = [str(dataset.get(key, "")).strip() for key in ("name", "version", "split")]
    return slugify("-".join(part for part in parts if part), "dataset")


def write_raw_metric_csvs(workspace: Path, result_dir: Path, spec: dict, items: list[dict], extracted_at: str) -> list[dict]:
    dataset = spec.get("dataset", {}) if isinstance(spec.get("dataset"), dict) else {}
    refs = []
    for item in items:
        metric_dataset = item.get("dataset") if isinstance(item.get("dataset"), dict) else dataset
        metric_name = str(item.get("name") or "metric")
        metric_slug = slugify(metric_name, "metric")
        csv_path = result_dir / "raw_metrics" / dataset_slug(metric_dataset) / f"{metric_slug}.csv"
        extractor = item.get("extractor", {}) if isinstance(item.get("extractor"), dict) else {}
        reference = item.get("reference", {}) if isinstance(item.get("reference"), dict) else {}
        rows = []
        for index, value in enumerate(item.get("values", []), 1):
            rows.append({
                "schema_version": "baseline_raw_metric.v1",
                "extracted_at": extracted_at,
                "reproduction_id": spec.get("reproduction_id", ""),
                "baseline_id": spec.get("baseline_id", ""),
                "candidate_id": spec.get("candidate_id", ""),
                "attempt": spec.get("attempt", ""),
                "dataset_name": metric_dataset.get("name", ""),
                "dataset_version": metric_dataset.get("version", ""),
                "dataset_split": metric_dataset.get("split", ""),
                "dataset_path": metric_dataset.get("path", ""),
                "metric_name": metric_name,
                "value_index": index,
                "value": value,
                "units": item.get("units", ""),
                "aggregation": item.get("aggregation", ""),
                "source_path": relative_or_absolute(workspace, str(item.get("source_path", ""))),
                "extractor_type": extractor.get("type", ""),
                "selector": extractor.get("selector") or extractor.get("column") or extractor.get("pattern") or "",
                "reference_type": reference.get("type", ""),
            })
        dump_csv_atomic(csv_path, RAW_METRIC_FIELDS, rows)
        item["raw_csv_path"] = relpath(workspace, csv_path)
        item["raw_csv_sha256"] = sha256_file(csv_path)
        item["raw_csv_rows"] = len(rows)
        refs.append({"metric": metric_name, "dataset": metric_dataset, "path": item["raw_csv_path"], "sha256": item["raw_csv_sha256"], "rows": len(rows)})
    return refs


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract declared baseline metrics into a normalized JSON record.")
    ap.add_argument("--attempt-dir", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    attempt = Path(args.attempt_dir).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    workspace = find_workspace(output)
    if not is_within(attempt, workspace / "external_executor" / "expr"):
        raise SystemExit("Metric extraction attempt-dir must be under external_executor/expr")
    if not is_within(output, workspace / "external_executor" / "report"):
        raise SystemExit("Normalized metric reports must be written under external_executor/report")
    spec = load_json(Path(args.spec).expanduser().resolve())
    result_dir = None
    evidence_dir = None
    if isinstance(spec, dict) and spec.get("result_dir"):
        result_dir = resolve_in_workspace(workspace, str(spec["result_dir"]))
    if isinstance(spec, dict) and spec.get("evidence_dir"):
        evidence_dir = resolve_in_workspace(workspace, str(spec["evidence_dir"]))
    if not result_dir or not is_within(result_dir, workspace / "external_executor" / "raw_results"):
        raise SystemExit("Raw metric CSV files must be written under external_executor/raw_results")
    if evidence_dir and not is_within(evidence_dir, workspace / "external_executor" / "report"):
        raise SystemExit("Metric evidence inputs must be under external_executor/report")
    items = []
    errors = []
    for metric in spec.get("metrics", []):
        try:
            items.append(extract_one(attempt, evidence_dir, result_dir, metric))
        except Exception as exc:
            errors.append({"metric": metric.get("name"), "error": str(exc)})
    generated_at = utc_now()
    raw_csv_refs = write_raw_metric_csvs(workspace, result_dir, spec, items, generated_at) if items else []
    payload = {
        "schema_version": "baseline_metrics.v1",
        "generated_at": generated_at,
        "status": "complete" if items and not errors else "partial" if items else "failed",
        "items": items,
        "raw_metric_csv_refs": raw_csv_refs,
        "errors": errors,
    }
    dump_json_atomic(output, payload)
    print(payload["status"])
    return 0 if payload["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
