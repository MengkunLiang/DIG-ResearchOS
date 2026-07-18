#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import statistics
from collections import defaultdict
from pathlib import Path

from _common import dump_json_atomic, load_json, stable_id, utc_now


def normalized(item: dict) -> float:
    value = float(item["metric_value"])
    return value if item["direction"] == "higher_is_better" else -value


def base_key(item: dict) -> tuple:
    def comparable(value: object) -> object:
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return value

    return (
        item.get("implementation_id"), item.get("method_id"), item.get("pair_id"), item.get("protocol_fingerprint"),
        comparable(item.get("dataset")), comparable(item.get("dataset_version")), comparable(item.get("split")),
        item.get("preprocessing_fingerprint"), comparable(item.get("setting")), comparable(item.get("subset")), item.get("metric_name"), item.get("direction"),
        item.get("aggregation"), item.get("seed"), item.get("repeat"), item.get("fairness_fingerprint"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze factorial interactions and attribution confounds.")
    parser.add_argument("--observations", required=True)
    parser.add_argument("--ablation-effects", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    obs_data = load_json(Path(args.observations).expanduser().resolve())
    effects = load_json(Path(args.ablation_effects).expanduser().resolve())
    observations = obs_data.get("items", [])
    by_key = defaultdict(list)
    for item in observations:
        by_key[base_key(item)].append(item)
    interaction_pairs = defaultdict(list)
    unsupported = []
    for key, group in by_key.items():
        modules = sorted({m for item in group for m in item.get("module_states", {})})
        for a, b in itertools.combinations(modules, 2):
            cells = {}
            for item in group:
                sa = item.get("module_states", {}).get(a)
                sb = item.get("module_states", {}).get(b)
                if sa is None or sb is None:
                    continue
                other = tuple(sorted((m, item.get("module_states", {}).get(m)) for m in modules if m not in {a, b}))
                cells.setdefault(other, {})[(bool(sa), bool(sb))] = item
            found = False
            for other, cell in cells.items():
                if all(k in cell for k in ((True, True), (False, True), (True, False), (False, False))):
                    found = True
                    full = normalized(cell[(True, True)])
                    no_a = normalized(cell[(False, True)])
                    no_b = normalized(cell[(True, False)])
                    none = normalized(cell[(False, False)])
                    value = full - no_a - no_b + none
                    # Aggregate factorial interaction across matched seeds/repeats while
                    # preserving every other comparability dimension.
                    implementation, method, _pair_id, protocol, dataset, dataset_version, split, preprocessing, setting, subset, metric, direction, aggregation, _seed, _repeat, fairness = key
                    gkey = (a, b, implementation, method, protocol, dataset, dataset_version, split, preprocessing, setting, subset, metric, direction, aggregation, fairness)
                    interaction_pairs[gkey].append({"value": value, "runs": [cell[x]["run_id"] for x in ((True, True), (False, True), (True, False), (False, False))], "refs": [cell[x]["intervention_id"] for x in ((True, True), (False, True), (True, False), (False, False))]})
            if not found:
                unsupported.append({"module_ids": [a, b], "comparison_key": list(key), "reason": "missing_complete_factorial"})
    interactions = []
    for gkey, pairs in interaction_pairs.items():
        a, b, implementation, method, protocol, dataset, dataset_version, split, preprocessing, setting, subset, metric, direction, aggregation, fairness = gkey
        values = [x["value"] for x in pairs]
        iid = stable_id("INTER", *gkey)
        interactions.append({
            "interaction_id": iid, "module_ids": [a, b], "paired_n": len(values), "values": values,
            "setting_key": {"implementation_id": implementation, "method_id": method, "protocol_fingerprint": protocol, "dataset": dataset, "dataset_version": dataset_version, "split": split, "preprocessing_fingerprint": preprocessing, "setting": setting, "subset": subset, "metric_name": metric, "direction": direction, "aggregation": aggregation, "fairness_fingerprint": fairness},
            "mean_interaction": statistics.mean(values), "median_interaction": statistics.median(values),
            "stddev": statistics.stdev(values) if len(values) > 1 else None,
            "interaction_status": "synergistic" if statistics.mean(values) > 0 else ("antagonistic_or_redundant" if statistics.mean(values) < 0 else "neutral"),
            "run_ids": [rid for x in pairs for rid in x["runs"]], "evidence_refs": [ref for x in pairs for ref in x["refs"]],
            "evidence_type": "direct_ablation",
        })
    confounds = []
    for item in effects.get("items", []):
        if item.get("paired_n", 0) < 2:
            confounds.append({"confound_id": stable_id("CONF", item.get("effect_id"), "repeats"), "family": "seed_imbalance", "severity": "moderate", "status": "open", "summary": "Fewer than two paired repeats", "effect_ids": [item.get("effect_id")], "evidence_refs": item.get("evidence_refs", [])})
        if item.get("sign_consistency", 0) < 0.75:
            confounds.append({"confound_id": stable_id("CONF", item.get("effect_id"), "instability"), "family": "intervention_integrity", "severity": "material", "status": "open", "summary": "Ablation effect has low sign consistency", "effect_ids": [item.get("effect_id")], "evidence_refs": item.get("evidence_refs", [])})
    for item in observations:
        intervention = item.get("intervention", {}) if isinstance(item.get("intervention"), dict) else {}
        if intervention.get("changes_capacity") or intervention.get("parameter_delta"):
            confounds.append({"confound_id": stable_id("CONF", item.get("intervention_id"), "capacity"), "family": "capacity", "severity": "material", "status": "open", "summary": "Intervention changes capacity without a recorded control", "effect_ids": [], "evidence_refs": [item.get("intervention_id")]})
        if intervention.get("changes_compute"):
            confounds.append({"confound_id": stable_id("CONF", item.get("intervention_id"), "compute"), "family": "compute", "severity": "material", "status": "open", "summary": "Intervention changes compute without a recorded control", "effect_ids": [], "evidence_refs": [item.get("intervention_id")]})
        if len([m for m, state in item.get("module_states", {}).items() if state is False]) > 1 and item.get("evidence_type") == "direct_ablation":
            confounds.append({"confound_id": stable_id("CONF", item.get("intervention_id"), "multifunction"), "family": "multi_function_switch", "severity": "moderate", "status": "open", "summary": "Run disables multiple modules; unique attribution requires factorial contrasts", "effect_ids": [], "evidence_refs": [item.get("intervention_id")]})
    payload = {
        "schema_version": "module_interaction_analysis.v1", "generated_at": utc_now(),
        "status": "complete", "interaction_effects": {"status": "complete" if interactions else "partial", "items": interactions},
        "confounds": {"status": "complete", "items": confounds}, "unsupported_interactions": unsupported,
    }
    dump_json_atomic(Path(args.output).expanduser().resolve(), payload)
    print(f"interactions={len(interactions)} confounds={len(confounds)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
