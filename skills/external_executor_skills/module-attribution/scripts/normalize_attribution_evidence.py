#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import dump_json_atomic, finite_number, load_json, stable_id, utc_now


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize module attribution intervention observations.")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--module-registry", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    snapshot = load_json(Path(args.snapshot).expanduser().resolve())
    registry = load_json(Path(args.module_registry).expanduser().resolve())
    known_modules = {str(x.get("module_id")) for x in registry.get("items", [])}
    observations = []
    rejected = []
    for run in snapshot.get("runs", []):
        if not run.get("eligible"):
            continue
        state_map = {str(k): bool(v) for k, v in run.get("module_states", {}).items() if str(k) in known_modules}
        intervention = run.get("intervention", {}) if isinstance(run.get("intervention"), dict) else {}
        intervention_type = str(intervention.get("type") or "").lower()
        if state_map and run.get("run_type") == "ablation":
            evidence_type = "direct_ablation"
        elif intervention and run.get("run_type") == "diagnostic":
            evidence_type = "controlled_diagnostic" if intervention.get("controlled", False) else "correlational_hint"
        elif run.get("run_type") in {"formal", "small_scale"}:
            evidence_type = "correlational_hint"
        else:
            evidence_type = "unsupported"
        for metric in run.get("metrics", []):
            value = finite_number(metric.get("value"))
            if value is None or not metric.get("direction"):
                rejected.append({"run_id": run.get("run_id"), "metric": metric.get("metric_name"), "reason": "nonfinite_or_unknown_direction"})
                continue
            obs_id = stable_id("INTV", run.get("run_id"), metric.get("metric_name"), run.get("seed"), state_map)
            observations.append({
                "intervention_id": obs_id, "run_evidence_id": run.get("evidence_id"), "run_id": run.get("run_id"),
                "experiment_id": run.get("experiment_id"), "claim_ids": run.get("claim_ids", []),
                "method_id": run.get("method_id"), "variant_id": run.get("variant_id"), "reference_variant_id": run.get("reference_variant_id"),
                "evidence_type": evidence_type, "intervention_type": intervention_type or ("module_state" if state_map else "none"),
                "module_states": state_map, "intervention": intervention,
                "setting": run.get("setting"), "subset": run.get("subset"), "dataset": run.get("dataset"), "dataset_version": run.get("dataset_version"),
                "split": run.get("split"), "preprocessing_fingerprint": run.get("preprocessing_fingerprint"),
                "protocol_fingerprint": run.get("protocol_fingerprint"), "fairness_fingerprint": run.get("fairness_fingerprint"),
                "seed": run.get("seed"), "repeat": run.get("repeat"), "run_type": run.get("run_type"),
                "metric_name": metric.get("metric_name"), "metric_value": value, "direction": metric.get("direction"),
                "aggregation": metric.get("aggregation"), "unit": metric.get("unit"), "source_ref": metric.get("source_ref"),
            })
    payload = {
        "schema_version": "attribution_intervention_observations.v1", "generated_at": utc_now(),
        "status": "complete" if observations else "partial", "items": observations, "rejected": rejected,
    }
    dump_json_atomic(Path(args.output).expanduser().resolve(), payload)
    print(f"observations={len(observations)} rejected={len(rejected)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
