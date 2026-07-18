#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable

from _common import (
    canonical_json_hash,
    dump_json_atomic,
    file_ref,
    listify,
    load_json,
    relpath,
    resolve_in_workspace,
    resolve_workspace,
    sha256_file,
    unique_strings,
    utc_now,
)

RAW_EXTENSIONS = {".csv", ".tsv", ".json", ".jsonl"}
DIMENSION_KEYS = {
    "run_id", "experiment_id", "iteration_id", "implementation_id", "dataset", "dataset_id",
    "split", "subset", "method", "method_id", "method_name", "method_role", "baseline_id",
    "variant", "variant_id", "seed", "repeat", "repeat_index", "run_type", "analysis_role",
    "protocol_fingerprint", "metric", "metric_id", "metric_name", "name", "direction",
    "metric_direction", "status", "run_status", "evidence_use", "data_kind", "value",
}
NORMALIZED_FIELDS = [
    "dataset", "split", "metric", "metric_id", "metric_direction", "value", "method_id",
    "method_role", "variant", "baseline_id", "seed", "repeat_index", "run_id", "experiment_id",
    "iteration_id", "implementation_id", "run_type", "analysis_role", "protocol_fingerprint",
    "status", "evidence_use", "data_kind", "source_file",
]
AGGREGATE_FIELDS = [
    "table_kind", "dataset", "split", "metric", "metric_direction", "method_id", "method_role",
    "variant", "baseline_id", "n", "mean", "std", "min", "max", "run_ids", "experiment_ids",
    "protocol_fingerprint", "source_files",
]


def snapshot_result(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        key: entry.get("value")
        for key, entry in snapshot.get("section_digests", {}).items()
        if isinstance(entry, dict) and "value" in entry
    }


def section_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "runs", "records"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
    return []


def scalar(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        for key in ("id", "name", "path", "value"):
            if value.get(key) not in (None, ""):
                return str(value[key])
        return None
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return None


def number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def context_from(record: dict[str, Any], parent: dict[str, Any] | None = None) -> dict[str, Any]:
    context = dict(parent or {})
    dataset = record.get("dataset")
    if isinstance(dataset, dict):
        context["dataset"] = scalar(dataset.get("id") or dataset.get("name") or dataset.get("dataset_id"))
        context["split"] = scalar(dataset.get("split") or dataset.get("subset")) or context.get("split")
    elif dataset not in (None, ""):
        context["dataset"] = scalar(dataset)
    aliases = {
        "dataset": ("dataset_id",),
        "split": ("split", "subset"),
        "method_id": ("method_id", "method_name", "method"),
        "method_role": ("method_role",),
        "baseline_id": ("baseline_id",),
        "variant": ("variant", "variant_id"),
        "seed": ("seed",),
        "repeat_index": ("repeat_index", "repeat"),
        "run_id": ("run_id",),
        "experiment_id": ("experiment_id",),
        "iteration_id": ("iteration_id",),
        "implementation_id": ("implementation_id",),
        "run_type": ("run_type",),
        "analysis_role": ("analysis_role",),
        "protocol_fingerprint": ("protocol_fingerprint",),
        "status": ("run_status", "status"),
        "evidence_use": ("evidence_use", "evidence_level"),
        "data_kind": ("data_kind",),
        "metric_direction": ("metric_direction", "direction"),
    }
    for target, keys in aliases.items():
        for key in keys:
            value = scalar(record.get(key))
            if value is not None:
                context[target] = value
                break
    run_record = record.get("run_record")
    if isinstance(run_record, dict):
        context = context_from(run_record, context)
    return context


def infer_method(context: dict[str, Any], source: str) -> None:
    baseline_id = context.get("baseline_id")
    method_id = context.get("method_id") or baseline_id or context.get("variant")
    role = str(context.get("method_role") or "").lower()
    combined = " ".join(str(value or "") for value in (method_id, baseline_id, context.get("variant"), source)).lower()
    if not role:
        if baseline_id or "baseline" in combined:
            role = "baseline"
        elif any(token in combined for token in ("ours", "our_method", "our-method", "m1", "proposed")):
            role = "ours"
        else:
            role = "unknown"
    context["method_role"] = role
    context["method_id"] = str(method_id or ("ours" if role == "ours" else baseline_id or "unknown"))


def metric_record(
    context: dict[str, Any], *, metric: Any, value: Any, metric_id: Any = None, source: str
) -> dict[str, Any] | None:
    numeric = number(value)
    metric_name = scalar(metric)
    if numeric is None or not metric_name:
        return None
    row = {key: context.get(key) for key in NORMALIZED_FIELDS}
    row.update({
        "metric": metric_name,
        "metric_id": scalar(metric_id) or metric_name,
        "value": numeric,
        "source_file": source,
    })
    infer_method(row, source)
    row["dataset"] = row.get("dataset") or "unknown"
    row["split"] = row.get("split") or "unknown"
    row["metric_direction"] = row.get("metric_direction") or "unknown"
    return row


def json_metrics(value: Any, context: dict[str, Any], source: str) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from json_metrics(item, context, source)
        return
    if not isinstance(value, dict):
        return
    current = context_from(value, context)
    direct = metric_record(
        current,
        metric=value.get("metric") or value.get("metric_name") or value.get("name"),
        metric_id=value.get("metric_id"),
        value=value.get("value"),
        source=source,
    )
    if direct:
        yield direct
    records = value.get("records")
    if isinstance(records, list):
        for item in records:
            yield from json_metrics(item, current, source)
    metrics = value.get("metrics")
    if isinstance(metrics, dict):
        nested_records = metrics.get("records")
        if isinstance(nested_records, list):
            for item in nested_records:
                yield from json_metrics(item, context_from(metrics, current), source)
        for key, item in metrics.items():
            if key == "records":
                continue
            metric_value = item
            metric_context = current
            if isinstance(item, dict):
                metric_value = item.get("value") if item.get("value") is not None else item.get("mean")
                metric_context = context_from(item, current)
            row = metric_record(metric_context, metric=key, value=metric_value, source=source)
            if row:
                yield row
            elif isinstance(item, (dict, list)):
                yield from json_metrics(item, current, source)
    for key, item in value.items():
        if key in {"records", "metrics", "run_record"}:
            continue
        if isinstance(item, (dict, list)):
            yield from json_metrics(item, current, source)


def csv_metrics(path: Path, source: str, base: dict[str, Any]) -> Iterable[dict[str, Any]]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        for raw in reader:
            current = context_from(raw, base)
            metric_name = raw.get("metric") or raw.get("metric_name") or raw.get("name")
            if metric_name and raw.get("value") not in (None, ""):
                row = metric_record(current, metric=metric_name, metric_id=raw.get("metric_id"), value=raw.get("value"), source=source)
                if row:
                    yield row
                continue
            for key, item in raw.items():
                if key in DIMENSION_KEYS:
                    continue
                row = metric_record(current, metric=key, value=item, source=source)
                if row:
                    yield row


def run_contexts(result: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    runs = section_items(result.get("experiment_runs") or result.get("runs"))
    by_path: dict[str, dict[str, Any]] = {}
    for run in runs:
        context = context_from(run)
        metrics = run.get("metrics")
        if isinstance(metrics, dict):
            context = context_from(metrics, context)
        refs: list[str] = []
        for key in ("metric_output_ref", "raw_result_ref", "raw_results", "artifacts"):
            for item in listify(run.get(key)):
                if isinstance(item, dict) and item.get("path"):
                    refs.append(str(item["path"]))
                elif isinstance(item, str):
                    refs.append(item)
        for value in refs:
            by_path[value] = context
    return runs, by_path


def context_for_file(source: str, runs: list[dict[str, Any]], by_path: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if source in by_path:
        return dict(by_path[source])
    for run in runs:
        run_id = str(run.get("run_id") or "")
        if run_id and run_id in source:
            return context_from(run)
    return {}


def run_ref_paths(runs: list[dict[str, Any]], keys: tuple[str, ...]) -> list[str]:
    paths: list[str] = []
    for run in runs:
        for key in keys:
            for value in listify(run.get(key)):
                if isinstance(value, dict) and value.get("path"):
                    paths.append(str(value["path"]))
                elif isinstance(value, str):
                    paths.append(value)
    return unique_strings(paths)


def pinned_artifacts(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item["path"]): item
        for item in snapshot.get("manifest_artifacts", [])
        if isinstance(item, dict) and item.get("path")
    }


def read_raw(path: Path, source: str, base: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    try:
        if path.suffix.lower() in {".csv", ".tsv"}:
            return list(csv_metrics(path, source, base)), None
        if path.suffix.lower() == ".jsonl":
            rows: list[dict[str, Any]] = []
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip():
                    rows.extend(json_metrics(json.loads(line), base, source))
            return rows, None
        return list(json_metrics(load_json(path), base, source)), None
    except Exception as exc:  # noqa: BLE001
        return [], str(exc)


def deduplicate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = canonical_json_hash({name: row.get(name) for name in NORMALIZED_FIELDS})
        if key not in seen:
            seen.add(key)
            output.append(row)
    return output


def is_stale(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "").lower() in {"failed", "stale", "superseded", "invalid", "unusable", "cancelled"}


def table_kind(row: dict[str, Any]) -> str:
    run_type = str(row.get("run_type") or "").lower()
    role = str(row.get("analysis_role") or "").lower()
    experiment = str(row.get("experiment_id") or "").lower()
    variant = str(row.get("variant") or "").lower()
    if run_type == "ablation" or "ablation" in role or "ablation" in experiment or "abl" in experiment:
        return "ablation"
    if run_type in {"robustness", "diagnostic", "efficiency", "small_scale", "smoke"} or role in {"diagnostic", "exploratory", "robustness", "efficiency"}:
        return "other"
    if row.get("method_role") in {"ours", "baseline"}:
        return "main"
    if variant and variant not in {"full", "ours", "our_method", "m1_full"}:
        return "ablation"
    return "other"


def aggregate(rows: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    keys = (
        "dataset", "split", "metric", "metric_direction", "method_id", "method_role",
        "variant", "baseline_id", "protocol_fingerprint",
    )
    for row in rows:
        if is_stale(row) or table_kind(row) != kind:
            continue
        groups.setdefault(tuple(row.get(key) for key in keys), []).append(row)
    output: list[dict[str, Any]] = []
    for group_key, records in sorted(groups.items(), key=lambda item: tuple(str(value or "") for value in item[0])):
        values = [float(item["value"]) for item in records]
        payload = dict(zip(keys, group_key))
        payload.update({
            "table_kind": kind,
            "n": len(values),
            "mean": statistics.fmean(values),
            "std": statistics.stdev(values) if len(values) > 1 else 0.0,
            "min": min(values),
            "max": max(values),
            "run_ids": ";".join(unique_strings(item.get("run_id") for item in records)),
            "experiment_ids": ";".join(unique_strings(item.get("experiment_id") for item in records)),
            "source_files": ";".join(unique_strings(item.get("source_file") for item in records)),
        })
        output.append(payload)
    return output


def write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def clear_owned_tables(table_dir: Path) -> list[str]:
    """Remove outputs owned by this builder so a rerun cannot publish stale tables."""
    removed: list[str] = []
    for filename in ("all_results.csv", "main_comparison.csv", "ablation_results.csv", "other_experiments.csv"):
        path = table_dir / filename
        if path.is_file():
            path.unlink()
            removed.append(filename)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize raw experimental results and build traceable comparison tables.")
    parser.add_argument("--workspace")
    parser.add_argument("--snapshot", default="external_executor/report/phase_F/final_evidence_snapshot.json")
    parser.add_argument("--raw-dir", default="external_executor/raw_results")
    parser.add_argument("--table-dir", default="external_executor/table")
    parser.add_argument("--output", default="external_executor/report/phase_F/result_table_build_report.json")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    snapshot = load_json(resolve_in_workspace(workspace, args.snapshot))
    result = snapshot_result(snapshot)
    raw_dir = resolve_in_workspace(workspace, args.raw_dir)
    table_dir = resolve_in_workspace(workspace, args.table_dir)
    table_dir.mkdir(parents=True, exist_ok=True)
    removed_stale_outputs = clear_owned_tables(table_dir)
    runs, by_path = run_contexts(result)

    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    pinned = pinned_artifacts(snapshot)
    source_files = sorted(path for path in raw_dir.rglob("*") if path.is_file()) if raw_dir.is_dir() else []
    for path in source_files:
        source = relpath(workspace, path)
        if path.suffix.lower() not in RAW_EXTENSIONS:
            skipped.append({"path": source, "reason": "unsupported_non_tabular_raw_artifact"})
            continue
        pinned_ref = pinned.get(source)
        if not pinned_ref:
            skipped.append({"path": source, "reason": "not_pinned_in_final_evidence_snapshot"})
            continue
        expected_sha = pinned_ref.get("actual_sha256") or pinned_ref.get("expected_sha256")
        if pinned_ref.get("checksum_valid") is False or (expected_sha and sha256_file(path) != expected_sha):
            skipped.append({"path": source, "reason": "snapshot_checksum_mismatch"})
            continue
        parsed, error = read_raw(path, source, context_for_file(source, runs, by_path))
        rows.extend(parsed)
        if error:
            skipped.append({"path": source, "reason": f"parse_error:{error}"})
        elif not parsed:
            skipped.append({"path": source, "reason": "no_numeric_metric_records"})
    rows = deduplicate(rows)

    outputs: list[dict[str, Any]] = []
    if rows:
        all_path = table_dir / "all_results.csv"
        write_csv(all_path, NORMALIZED_FIELDS, rows)
        outputs.append({
            "kind": "normalized", "row_count": len(rows),
            "source_files": unique_strings(row.get("source_file") for row in rows),
            **file_ref(workspace, all_path, evidence_level="derived_table"),
        })
    for kind, filename in (
        ("main", "main_comparison.csv"),
        ("ablation", "ablation_results.csv"),
        ("other", "other_experiments.csv"),
    ):
        aggregate_rows = aggregate(rows, kind)
        if not aggregate_rows:
            continue
        path = table_dir / filename
        write_csv(path, AGGREGATE_FIELDS, aggregate_rows)
        outputs.append({
            "kind": kind, "row_count": len(aggregate_rows),
            "source_files": unique_strings(
                source for row in aggregate_rows for source in str(row.get("source_files") or "").split(";")
            ),
            **file_ref(workspace, path, evidence_level="derived_table"),
        })

    present_kinds = {item["kind"] for item in outputs}
    missing = [kind for kind in ("main", "ablation", "other") if kind not in present_kinds]
    report = {
        "schema_version": "result_table_build_report.v1",
        "generated_at": utc_now(),
        "snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_fingerprint": snapshot.get("snapshot_fingerprint"),
        "status": "complete" if "main" in present_kinds else ("partial" if outputs else "unavailable"),
        "raw_results_root": args.raw_dir,
        "source_file_count": len(source_files),
        "normalized_record_count": len(rows),
        "tables": outputs,
        "missing_table_kinds": missing,
        "skipped_sources": skipped,
        "removed_stale_outputs": removed_stale_outputs,
        "provenance": {
            "config_refs": run_ref_paths(runs, ("config_ref", "config_refs")),
            "log_refs": run_ref_paths(runs, ("raw_log_ref", "log_ref", "log_refs")),
            "metric_output_refs": run_ref_paths(runs, ("metric_output_ref", "metric_output_refs")),
            "table_generator_ref": "evidence-packaging/scripts/build_result_tables.py",
        },
        "aggregation": {
            "group_by": ["dataset", "split", "metric", "method_id", "variant", "protocol_fingerprint"],
            "statistics": ["n", "mean", "sample_std", "min", "max"],
            "stale_or_failed_records_excluded": True,
            "unknown_metric_direction_preserved": True,
            "only_snapshot_pinned_sources_consumed": True,
        },
        "report_fingerprint": None,
    }
    report["report_fingerprint"] = canonical_json_hash({key: value for key, value in report.items() if key != "report_fingerprint"})
    dump_json_atomic(resolve_in_workspace(workspace, args.output), report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
