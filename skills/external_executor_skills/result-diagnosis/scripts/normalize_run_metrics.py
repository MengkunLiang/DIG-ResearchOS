#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import dump_json_atomic, finite_number, load_json, metric_direction, stable_id, utc_now


def metric_items(run: dict[str, Any]) -> list[tuple[str, Any]]:
    metrics = run.get("metrics")
    if isinstance(metrics, dict):
        return list(metrics.items())
    if isinstance(metrics, list):
        out = []
        for item in metrics:
            if isinstance(item, dict) and (item.get("name") or item.get("metric")):
                out.append((str(item.get("name") or item.get("metric")), item))
        return out
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize embedded run metrics into flat observations.")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    snapshot_path = Path(args.snapshot).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    snapshot = load_json(snapshot_path)
    direction_by_metric = {}
    for contract in snapshot.get("metric_contracts", []):
        if not isinstance(contract, dict):
            continue
        name = contract.get("name") or contract.get("metric") or contract.get("metric_name")
        direction = metric_direction(contract.get("direction"))
        if name and direction:
            direction_by_metric[str(name)] = direction
    observations, rejected = [], []
    for run in snapshot.get("runs", []):
        for name, raw in metric_items(run):
            if isinstance(raw, dict):
                value = raw.get("value", raw.get("mean", raw.get("score")))
                direction = metric_direction(raw.get("direction"))
                unit = raw.get("unit")
                aggregation = raw.get("aggregation") or "reported"
                source_ref = raw.get("source_ref") or run.get("metric_output_ref") or run.get("evidence_id")
            else:
                value = raw
                direction = None
                unit = None
                aggregation = "reported"
                source_ref = run.get("metric_output_ref") or run.get("evidence_id")
            number = finite_number(value)
            oid = stable_id("OBS", run.get("evidence_id"), name, run.get("seed"), run.get("repeat_id"))
            if number is None:
                rejected.append({"observation_id": oid, "run_evidence_id": run.get("evidence_id"), "metric_name": name, "raw_value": value, "reason": "non_numeric_or_nonfinite"})
                continue
            if direction is None:
                # Some source records expose a run-level metric direction map.
                source = run.get("source_record", {})
                directions = source.get("metric_directions", {}) if isinstance(source, dict) else {}
                direction = metric_direction(directions.get(name)) if isinstance(directions, dict) else None
            if direction is None:
                direction = direction_by_metric.get(str(name))
            obs = {
                "observation_id": oid,
                "run_evidence_id": run.get("evidence_id"),
                "run_id": run.get("run_id"),
                "iteration_id": run.get("iteration_id"),
                "experiment_id": run.get("experiment_id"),
                "claim_ids": run.get("claim_ids", []),
                "method_id": run.get("method_id"),
                "method_role": run.get("method_role"),
                "run_type": run.get("run_type"),
                "analysis_role": run.get("analysis_role"),
                "eligibility": run.get("eligibility"),
                "setting": run.get("setting"),
                "dataset": run.get("dataset"),
                "dataset_version": run.get("dataset_version"),
                "split": run.get("split"),
                "preprocessing_fingerprint": run.get("preprocessing_fingerprint"),
                "protocol_fingerprint": run.get("protocol_fingerprint"),
                "fairness_fingerprint": run.get("fairness_fingerprint"),
                "seed": run.get("seed"),
                "repeat_id": run.get("repeat_id"),
                "metric_name": str(name),
                "value": number,
                "direction": direction,
                "unit": unit,
                "aggregation": aggregation,
                "source_ref": source_ref,
            }
            observations.append(obs)
    payload = {
        "schema_version": "diagnosis_metric_observations.v1",
        "generated_at": utc_now(),
        "status": "complete" if observations and not rejected else ("partial" if observations else "blocked"),
        "iteration_id": snapshot.get("iteration_id"),
        "snapshot_fingerprint": snapshot.get("input_fingerprint"),
        "items": observations,
        "rejected": rejected,
    }
    dump_json_atomic(output, payload)
    print(f"{payload['status']}: observations={len(observations)} rejected={len(rejected)}")
    # A failure-only iteration intentionally has no metric observations but
    # still must proceed to a diagnosis and repair decision.
    return 0 if snapshot.get("runs") else 2


if __name__ == "__main__":
    raise SystemExit(main())
