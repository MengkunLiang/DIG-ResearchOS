#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any

from _common import canonical_hash, dump_json_atomic, load_json, stable_id, utc_now


def surface_key(item: dict[str, Any]) -> tuple[Any, ...]:
    surface = item.get("surface") or item.get("comparison_key") or {}
    return tuple(surface.get(k) for k in ("protocol_fingerprint", "dataset", "dataset_version", "split", "preprocessing_fingerprint", "setting", "metric_name", "direction", "aggregation", "eligibility", "run_type"))


def utility(mean: float, direction: str | None) -> float:
    return mean if direction == "higher_is_better" else -mean


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile deterministic diagnosis facts.")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--aggregates", required=True)
    parser.add_argument("--comparisons", required=True)
    parser.add_argument("--anomalies", required=True)
    parser.add_argument("--observations")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    snapshot = load_json(Path(args.snapshot).resolve())
    aggs = load_json(Path(args.aggregates).resolve())
    comps = load_json(Path(args.comparisons).resolve())
    anomalies = load_json(Path(args.anomalies).resolve())
    observations = load_json(Path(args.observations).resolve()) if args.observations else {"items": []}

    baseline_groups = defaultdict(list)
    for agg in aggs.get("items", []):
        if agg.get("method_role") == "baseline":
            baseline_groups[surface_key(agg)].append(agg)
    strongest = []
    for key, rows in sorted(baseline_groups.items(), key=lambda kv: str(kv[0])):
        direction = key[7]
        ranked = sorted(rows, key=lambda x: utility(float(x["mean"]), direction), reverse=True)
        top = ranked[0]
        margin = utility(float(top["mean"]), direction) - utility(float(ranked[1]["mean"]), direction) if len(ranked) > 1 else None
        bid = stable_id("BASE", key, top.get("method_id"), top.get("aggregate_id"))
        strongest.append({
            "strongest_baseline_id": bid,
            "surface": top.get("comparison_key"),
            "baseline_method_id": top.get("method_id"),
            "aggregate_id": top.get("aggregate_id"),
            "mean": top.get("mean"), "n": top.get("n"), "margin_to_next": margin,
            "ranking_rule": direction, "repeat_sufficiency": top.get("repeat_sufficiency"),
            "evidence_refs": [top.get("aggregate_id")],
        })

    setting_facts = []
    for cmp in comps.get("items", []):
        outcome = cmp.get("numeric_outcome")
        fid = stable_id("FACT", cmp.get("comparison_id"), outcome)
        setting_facts.append({
            "fact_id": fid, "surface": cmp.get("surface"),
            "ours_method_id": cmp.get("ours_method_id"), "baseline_method_id": cmp.get("baseline_method_id"),
            "numeric_outcome": outcome, "direction_adjusted_delta": cmp.get("direction_adjusted_delta"),
            "relative_improvement": cmp.get("relative_improvement"), "paired_seed_count": cmp.get("paired_seed_count"),
            "comparison_id": cmp.get("comparison_id"), "evidence_refs": cmp.get("evidence_refs", []),
        })

    payload = {
        "schema_version": "diagnosis_statistics.v1", "generated_at": utc_now(),
        "status": "complete" if (aggs.get("items") or anomalies.get("items")) else "blocked",
        "iteration_id": snapshot.get("iteration_id"),
        "input_fingerprint": canonical_hash({"snapshot": snapshot.get("input_fingerprint"), "aggregates": aggs.get("items", []), "comparisons": comps.get("items", []), "anomalies": anomalies.get("items", [])}),
        "metric_observations": observations,
        "metric_summaries": aggs,
        "method_comparisons": comps,
        "strongest_baselines": {"status": "complete" if strongest else "partial", "items": strongest},
        "setting_facts": {"status": "complete" if setting_facts else "partial", "items": setting_facts},
        "anomalies": anomalies,
    }
    dump_json_atomic(Path(args.output).resolve(), payload)
    print(f"{payload['status']}: strongest={len(strongest)} facts={len(setting_facts)}")
    return 0 if snapshot.get("runs") else 2


if __name__ == "__main__":
    raise SystemExit(main())
