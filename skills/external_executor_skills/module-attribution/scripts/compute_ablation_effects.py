#!/usr/bin/env python3
from __future__ import annotations

import argparse
import statistics
from collections import defaultdict
from pathlib import Path

from _common import dump_json_atomic, load_json, stable_id, utc_now


def normalized_value(item: dict) -> float:
    value = float(item["metric_value"])
    return value if item["direction"] == "higher_is_better" else -value


def key_without_state(item: dict) -> tuple:
    return (
        item.get("method_id"), item.get("protocol_fingerprint"), item.get("dataset"), item.get("dataset_version"),
        item.get("split"), item.get("preprocessing_fingerprint"), item.get("setting"), item.get("subset"),
        item.get("metric_name"), item.get("direction"), item.get("aggregation"), item.get("seed"), item.get("repeat"),
        item.get("fairness_fingerprint"),
    )


def median_abs_deviation(values: list[float]) -> float | None:
    if not values:
        return None
    med = statistics.median(values)
    return statistics.median([abs(x - med) for x in values])


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute paired module ablation effects.")
    parser.add_argument("--observations", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    data = load_json(Path(args.observations).expanduser().resolve())
    items = data.get("items", [])
    by_key = defaultdict(list)
    for item in items:
        by_key[key_without_state(item)].append(item)
    effects_by_group = defaultdict(list)
    unpaired = []
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        modules = sorted({m for item in group for m in item.get("module_states", {})})
        for mid in modules:
            enabled = [x for x in group if x.get("module_states", {}).get(mid) is True]
            disabled = [x for x in group if x.get("module_states", {}).get(mid) is False]
            if not enabled or not disabled:
                continue
            # Pair only when every other declared module state is identical.
            pairs = []
            for on in enabled:
                for off in disabled:
                    other = (set(on.get("module_states", {})) | set(off.get("module_states", {}))) - {mid}
                    if all(on.get("module_states", {}).get(x) == off.get("module_states", {}).get(x) for x in other):
                        pairs.append((on, off))
            if not pairs:
                unpaired.append({"module_id": mid, "comparison_key": list(key), "reason": "other_module_states_differ"})
                continue
            # Deterministically use unique run pairs.
            seen = set()
            for on, off in pairs:
                pid = (on["run_id"], off["run_id"], mid)
                if pid in seen:
                    continue
                seen.add(pid)
                effect = normalized_value(on) - normalized_value(off)
                context_states = tuple(sorted((x, on.get("module_states", {}).get(x)) for x in other))
                group_key = (
                    mid, context_states, on.get("method_id"), on.get("protocol_fingerprint"), on.get("dataset"), on.get("dataset_version"),
                    on.get("split"), on.get("setting"), on.get("subset"), on.get("metric_name"), on.get("direction"),
                    on.get("aggregation"), on.get("fairness_fingerprint"),
                )
                effects_by_group[group_key].append({"effect": effect, "enabled": on, "disabled": off})
    output_items = []
    for group_key, pairs in effects_by_group.items():
        mid, context_states, method, protocol, dataset, dataset_version, split, setting, subset, metric, direction, aggregation, fairness = group_key
        values = [x["effect"] for x in pairs]
        positive = sum(v > 0 for v in values)
        negative = sum(v < 0 for v in values)
        neutral = len(values) - positive - negative
        effect_id = stable_id("EFF", *group_key)
        output_items.append({
            "effect_id": effect_id, "module_id": mid, "method_id": method,
            "setting_key": {"protocol_fingerprint": protocol, "dataset": dataset, "dataset_version": dataset_version, "split": split, "setting": setting, "subset": subset, "metric_name": metric, "direction": direction, "aggregation": aggregation, "fairness_fingerprint": fairness, "other_module_states": dict(context_states)},
            "paired_n": len(values), "effects": values, "mean_effect": statistics.mean(values), "median_effect": statistics.median(values),
            "stddev": statistics.stdev(values) if len(values) > 1 else None, "mad": median_abs_deviation(values),
            "min_effect": min(values), "max_effect": max(values), "positive_pairs": positive, "neutral_pairs": neutral, "negative_pairs": negative,
            "sign_consistency": max(positive, neutral, negative) / len(values),
            "enabled_run_ids": [x["enabled"]["run_id"] for x in pairs], "disabled_run_ids": [x["disabled"]["run_id"] for x in pairs],
            "evidence_refs": [x["enabled"]["intervention_id"] for x in pairs] + [x["disabled"]["intervention_id"] for x in pairs],
            "evidence_type": "direct_ablation",
        })
    payload = {
        "schema_version": "module_ablation_effects.v1", "generated_at": utc_now(),
        "status": "complete" if output_items else "partial", "items": output_items, "unpaired": unpaired,
    }
    dump_json_atomic(Path(args.output).expanduser().resolve(), payload)
    print(f"effects={len(output_items)} unpaired={len(unpaired)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
