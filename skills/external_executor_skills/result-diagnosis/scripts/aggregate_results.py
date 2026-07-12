#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from _common import canonical_hash, dump_json_atomic, load_json, stable_id, utc_now


def median_abs_deviation(values: list[float]) -> float:
    med = statistics.median(values)
    return statistics.median([abs(x - med) for x in values])


def group_key(obs: dict[str, Any]) -> tuple[Any, ...]:
    return (
        obs.get("protocol_fingerprint"), obs.get("fairness_fingerprint"), obs.get("dataset"),
        obs.get("dataset_version"), obs.get("split"), obs.get("preprocessing_fingerprint"),
        obs.get("setting"), obs.get("metric_name"), obs.get("direction"), obs.get("aggregation"),
        obs.get("method_id"), obs.get("method_role"), obs.get("eligibility"), obs.get("run_type"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate comparable metric observations.")
    parser.add_argument("--observations", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    data = load_json(Path(args.observations).resolve())
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for obs in data.get("items", []):
        if obs.get("eligibility") != "excluded":
            groups[group_key(obs)].append(obs)
    items = []
    for key, rows in sorted(groups.items(), key=lambda kv: str(kv[0])):
        values = [float(r["value"]) for r in rows]
        n = len(values)
        mean = statistics.fmean(values)
        median = statistics.median(values)
        sd = statistics.stdev(values) if n >= 2 else None
        se = sd / math.sqrt(n) if sd is not None else None
        ci = [mean - 1.96 * se, mean + 1.96 * se] if se is not None else None
        agg_id = stable_id("AGG", key, canonical_hash(values))
        item = {
            "aggregate_id": agg_id,
            "comparison_key": {
                "protocol_fingerprint": key[0], "fairness_fingerprint": key[1], "dataset": key[2],
                "dataset_version": key[3], "split": key[4], "preprocessing_fingerprint": key[5],
                "setting": key[6], "metric_name": key[7], "direction": key[8], "aggregation": key[9],
                "eligibility": key[12], "run_type": key[13],
            },
            "method_id": key[10], "method_role": key[11],
            "n": n, "values": values,
            "seeds": [r.get("seed") for r in rows], "repeat_ids": [r.get("repeat_id") for r in rows],
            "observation_ids": [r.get("observation_id") for r in rows],
            "run_evidence_ids": [r.get("run_evidence_id") for r in rows],
            "mean": mean, "median": median, "stddev": sd,
            "median_absolute_deviation": median_abs_deviation(values),
            "min": min(values), "max": max(values), "range": max(values) - min(values),
            "standard_error": se, "descriptive_normal_95_interval": ci,
            "repeat_sufficiency": "point_only" if n == 1 else ("minimal" if n == 2 else "descriptive"),
        }
        items.append(item)
    payload = {
        "schema_version": "diagnosis_metric_aggregates.v1", "generated_at": utc_now(),
        "status": "complete" if items else "blocked", "iteration_id": data.get("iteration_id"),
        "observations_fingerprint": canonical_hash(data.get("items", [])), "items": items,
    }
    dump_json_atomic(Path(args.output).resolve(), payload)
    print(f"{payload['status']}: groups={len(items)}")
    return 2 if not items else 0


if __name__ == "__main__":
    raise SystemExit(main())
