#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from _common import dump_json_atomic, load_json, stable_id, utc_now


def anomaly(category: str, severity: str, description: str, refs: list[str], **scope: Any) -> dict[str, Any]:
    aid = stable_id("ANOM", category, description, refs)
    return {
        "anomaly_id": aid, "category": category, "severity": severity,
        "scope": scope, "description": description, "evidence_refs": sorted(set(str(x) for x in refs if x)),
        "automatic": True, "status": "open",
        "claim_risk": {"info": "none", "warning": "medium", "material": "high", "blocking": "blocking"}[severity],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect deterministic diagnosis anomalies.")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--observations", required=True)
    parser.add_argument("--aggregates", required=True)
    parser.add_argument("--comparisons", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    snapshot = load_json(Path(args.snapshot).resolve())
    observations = load_json(Path(args.observations).resolve())
    aggregates = load_json(Path(args.aggregates).resolve())
    comparisons = load_json(Path(args.comparisons).resolve())
    items = []

    for run in snapshot.get("runs", []):
        if run.get("eligibility") == "excluded":
            sev = "blocking" if any(str(r).startswith(("missing_metric_direction", "protocol_mismatch", "fairness_mismatch")) for r in run.get("exclusion_reasons", [])) else "warning"
            items.append(anomaly("stale_or_unusable_run", sev, f"Run {run.get('run_id')} excluded: {run.get('exclusion_reasons')}", [run.get("evidence_id")], run_ids=[run.get("run_id")], experiment_ids=[run.get("experiment_id")], claim_ids=run.get("claim_ids", [])))

    for rejected in observations.get("rejected", []):
        items.append(anomaly("nonfinite_metric", "blocking", f"Metric {rejected.get('metric_name')} rejected for {rejected.get('reason')}", [rejected.get("observation_id"), rejected.get("run_evidence_id")], run_ids=[]))

    # Missing direction is material because rankings may invert.
    for obs in observations.get("items", []):
        if obs.get("direction") not in {"higher_is_better", "lower_is_better"}:
            items.append(anomaly("mixed_metric_direction", "blocking", f"Metric direction unresolved for {obs.get('metric_name')}", [obs.get("observation_id")], run_ids=[obs.get("run_id")], experiment_ids=[obs.get("experiment_id")], claim_ids=obs.get("claim_ids", [])))

    for agg in aggregates.get("items", []):
        refs = [agg.get("aggregate_id")] + agg.get("observation_ids", [])
        n = int(agg.get("n", 0))
        if n < 3:
            items.append(anomaly("insufficient_repeats", "warning" if n == 2 else "material", f"Aggregate {agg.get('aggregate_id')} has n={n}", refs, experiment_ids=[]))
        mean = agg.get("mean")
        sd = agg.get("stddev")
        if sd is not None:
            cv = abs(sd / mean) if mean not in (0, None) else math.inf
            if cv > 0.25:
                items.append(anomaly("high_variance", "material", f"High coefficient of variation ({cv:.3g})", refs, experiment_ids=[]))
        values = agg.get("values", [])
        if len(values) >= 4:
            vals = sorted(values)
            q1 = statistics.median(vals[:len(vals)//2])
            q3 = statistics.median(vals[(len(vals)+1)//2:])
            iqr = q3 - q1
            if iqr > 0:
                outs = [x for x in vals if x < q1 - 1.5*iqr or x > q3 + 1.5*iqr]
                if outs:
                    items.append(anomaly("extreme_outlier", "warning", f"IQR outliers detected: {outs}", refs, experiment_ids=[]))
        if len(values) >= 2 and len(set(round(float(v), 15) for v in values)) == 1:
            items.append(anomaly("suspicious_identical_values", "info", "All repeated values are exactly identical", refs, experiment_ids=[]))

    for cmp in comparisons.get("items", []):
        if cmp.get("pairing_status") != "paired":
            items.append(anomaly("seed_imbalance", "warning", f"Comparison {cmp.get('comparison_id')} has no paired seeds", [cmp.get("comparison_id")], experiment_ids=[]))

    # Coverage by setting/metric: ours with no baseline or baseline with no ours.
    surfaces = defaultdict(set)
    refs = defaultdict(list)
    for agg in aggregates.get("items", []):
        ck = agg.get("comparison_key", {})
        key = tuple(ck.get(k) for k in ("protocol_fingerprint", "dataset", "split", "setting", "metric_name", "eligibility", "run_type"))
        surfaces[key].add(agg.get("method_role"))
        refs[key].append(agg.get("aggregate_id"))
    for key, roles in surfaces.items():
        if "ours" in roles and "baseline" not in roles and key[5] == "formal_candidate":
            items.append(anomaly("missing_required_baseline", "material", f"No baseline aggregate for formal surface {key}", refs[key], experiment_ids=[]))

    # Deduplicate exact IDs.
    deduped = {item["anomaly_id"]: item for item in items}
    payload = {
        "schema_version": "diagnosis_anomalies.v1", "generated_at": utc_now(),
        "status": "complete", "iteration_id": snapshot.get("iteration_id"),
        "items": list(deduped.values()),
        "counts": {sev: sum(1 for x in deduped.values() if x["severity"] == sev) for sev in ("info", "warning", "material", "blocking")},
    }
    dump_json_atomic(Path(args.output).resolve(), payload)
    print(f"anomalies={len(deduped)} counts={payload['counts']}")
    return 2 if payload["counts"]["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
