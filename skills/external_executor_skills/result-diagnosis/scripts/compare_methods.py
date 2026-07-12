#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from _common import canonical_hash, dump_json_atomic, load_json, stable_id, utc_now


def surface_key(agg: dict[str, Any]) -> tuple[Any, ...]:
    ck = agg.get("comparison_key", {})
    return tuple(ck.get(k) for k in ("protocol_fingerprint", "fairness_fingerprint", "dataset", "dataset_version", "split", "preprocessing_fingerprint", "setting", "metric_name", "direction", "aggregation", "eligibility", "run_type"))


def utility(value: float, direction: str | None) -> float:
    return value if direction == "higher_is_better" else -value


def paired_values(observations: list[dict[str, Any]], ours_id: str, base_id: str, key: tuple[Any, ...]) -> list[dict[str, Any]]:
    fields = ("protocol_fingerprint", "fairness_fingerprint", "dataset", "dataset_version", "split", "preprocessing_fingerprint", "setting", "metric_name", "direction", "aggregation", "eligibility", "run_type")
    relevant = [o for o in observations if tuple(o.get(f) for f in fields) == key and o.get("method_id") in {ours_id, base_id}]
    by = defaultdict(dict)
    for obs in relevant:
        pair = obs.get("seed") if obs.get("seed") is not None else obs.get("repeat_id")
        if pair is not None:
            by[str(pair)][str(obs.get("method_id"))] = obs
    pairs = []
    for pid, row in by.items():
        if ours_id in row and base_id in row:
            direction = row[ours_id].get("direction")
            raw_diff = row[ours_id]["value"] - row[base_id]["value"]
            adjusted = raw_diff if direction == "higher_is_better" else -raw_diff
            pairs.append({"pair_id": pid, "ours_observation_id": row[ours_id]["observation_id"], "baseline_observation_id": row[base_id]["observation_id"], "raw_difference": raw_diff, "direction_adjusted_difference": adjusted})
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare ours with baseline aggregates on matching surfaces.")
    parser.add_argument("--aggregates", required=True)
    parser.add_argument("--observations", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    aggs = load_json(Path(args.aggregates).resolve())
    obs_data = load_json(Path(args.observations).resolve())
    observations = obs_data.get("items", [])
    groups = defaultdict(list)
    for agg in aggs.get("items", []):
        groups[surface_key(agg)].append(agg)
    items = []
    for key, rows in sorted(groups.items(), key=lambda kv: str(kv[0])):
        ours = [r for r in rows if r.get("method_role") == "ours"]
        baselines = [r for r in rows if r.get("method_role") == "baseline"]
        for ours_agg in ours:
            for base_agg in baselines:
                direction = key[8]
                raw_delta = ours_agg["mean"] - base_agg["mean"]
                adjusted_delta = raw_delta if direction == "higher_is_better" else -raw_delta
                denominator = abs(base_agg["mean"])
                relative = adjusted_delta / denominator if denominator > 1e-15 else None
                pairs = paired_values(observations, str(ours_agg["method_id"]), str(base_agg["method_id"]), key)
                diffs = [p["direction_adjusted_difference"] for p in pairs]
                ties_eps = 1e-12
                wins = sum(d > ties_eps for d in diffs)
                losses = sum(d < -ties_eps for d in diffs)
                ties = len(diffs) - wins - losses
                sd = statistics.stdev(diffs) if len(diffs) >= 2 else None
                effect = statistics.fmean(diffs) / sd if sd and sd > 0 else None
                cid = stable_id("CMP", ours_agg["aggregate_id"], base_agg["aggregate_id"])
                items.append({
                    "comparison_id": cid,
                    "surface": ours_agg["comparison_key"],
                    "ours_method_id": ours_agg["method_id"], "baseline_method_id": base_agg["method_id"],
                    "ours_aggregate_id": ours_agg["aggregate_id"], "baseline_aggregate_id": base_agg["aggregate_id"],
                    "ours_mean": ours_agg["mean"], "baseline_mean": base_agg["mean"],
                    "raw_delta": raw_delta, "direction_adjusted_delta": adjusted_delta,
                    "relative_improvement": relative,
                    "numeric_outcome": "win" if adjusted_delta > ties_eps else ("loss" if adjusted_delta < -ties_eps else "tie"),
                    "paired_seed_count": len(pairs), "paired_values": pairs,
                    "paired_mean_difference": statistics.fmean(diffs) if diffs else None,
                    "paired_median_difference": statistics.median(diffs) if diffs else None,
                    "paired_difference_stddev": sd, "paired_standardized_difference": effect,
                    "win_tie_loss": {"wins": wins, "ties": ties, "losses": losses},
                    "pairing_status": "paired" if pairs else "unpaired",
                    "evidence_refs": [ours_agg["aggregate_id"], base_agg["aggregate_id"]] + [x for p in pairs for x in (p["ours_observation_id"], p["baseline_observation_id"])],
                })
    payload = {
        "schema_version": "diagnosis_method_comparisons.v1", "generated_at": utc_now(),
        "status": "complete" if items else "partial", "iteration_id": aggs.get("iteration_id"),
        "input_fingerprint": canonical_hash({"aggregates": aggs.get("items", []), "observations": observations}),
        "items": items,
    }
    dump_json_atomic(Path(args.output).resolve(), payload)
    print(f"{payload['status']}: comparisons={len(items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
