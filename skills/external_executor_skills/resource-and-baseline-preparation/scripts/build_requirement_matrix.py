#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import (
    assert_write_allowed,
    canonical_json_hash,
    dump_json_atomic,
    get_nested,
    listify,
    load_json,
    relpath,
    resolve_in_workspace,
    resolve_workspace,
    stable_id,
    utc_now,
)


def text_name(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        return value.strip() or fallback
    if isinstance(value, dict):
        for key in ("name", "baseline_name", "dataset", "benchmark", "metric", "id"):
            if value.get(key):
                return str(value[key])
    return fallback


def requirement(resource_type: str, name: str, *, required: bool = True, purpose: str = "", source: Any = None, protocol: Any = None) -> dict[str, Any]:
    req_id = stable_id("REQ", resource_type, name)
    criteria = ["immutable version or checksum is recorded", "license/access status is recorded", "candidate is reviewed against this requirement"]
    if resource_type == "baseline_implementation":
        criteria += ["baseline identity and defining mechanism are evidenced", "target task/dataset/split/metric compatibility is assessed"]
    elif resource_type in {"dataset", "dataset_split", "benchmark_definition"}:
        criteria += ["official dataset/benchmark identity and version are evidenced", "split/preprocessing/evaluation semantics are recorded"]
    elif resource_type in {"metric_implementation", "evaluation_protocol"}:
        criteria += ["metric direction and aggregation are explicit", "evaluation behavior is compatible with the target protocol"]
    return {
        "requirement_id": req_id,
        "name": name,
        "resource_type": resource_type,
        "required": required,
        "minimum_loop_dependency": required,
        "purpose": purpose,
        "claim_ids": [],
        "baseline_id": stable_id("BASE", name) if resource_type == "baseline_implementation" else None,
        "expected_identity": source if isinstance(source, dict) else ({"source_value": source} if source not in (None, "") else {}),
        "expected_interface": {},
        "expected_protocol": protocol if isinstance(protocol, dict) else ({"summary": protocol} if protocol not in (None, "") else {}),
        "accepted_source_classes": ["official_author_repo", "official_benchmark_repo", "author_recognized", "third_party_reproduction", "executor_reimplementation"],
        "acceptance_criteria": criteria,
        "replacement": {"allowed": False, "requires_review": True, "equivalence_criteria": []},
        "missing_blocks_execution": required,
        "source_refs": [],
        "status": "open",
        "notes": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic Phase B requirement scaffold.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/resource_requirement_matrix.json")
    parser.add_argument("--manual-requirements", help="Optional JSON file containing an items array to append")
    args = parser.parse_args()

    workspace = resolve_workspace(args.workspace)
    output = resolve_in_workspace(workspace, args.output)
    result_pack = load_json(workspace / "external_executor" / "result_pack.json")
    handoff = load_json(workspace / "external_executor" / "handoff_pack.json")
    alignment = result_pack.get("context_alignment", {})
    if alignment.get("status") not in {"pass", "mismatch"}:
        raise SystemExit("context_alignment must be pass or mismatch")
    scope = alignment.get("confirmed_execution_scope")
    if not isinstance(scope, dict):
        raise SystemExit("confirmed_execution_scope is missing")

    items: list[dict[str, Any]] = []
    for index, baseline in enumerate(listify(get_nested(scope, "required_baselines", default=[])), 1):
        name = text_name(baseline, f"required-baseline-{index}")
        req = requirement("baseline_implementation", name, purpose="Required comparison baseline", source=baseline)
        if isinstance(baseline, dict):
            req["claim_ids"] = listify(baseline.get("claim_ids"))
            replacement = baseline.get("replacement") or {}
            if isinstance(replacement, dict):
                req["replacement"].update(replacement)
            req["expected_protocol"] = baseline.get("protocol", baseline.get("expected_protocol", {}))
        items.append(req)

    protocol = get_nested(scope, "benchmark_protocol_summary", "benchmark_protocol", default={})
    if isinstance(protocol, dict):
        benchmark_name = text_name(protocol.get("benchmark") or protocol, "confirmed-benchmark")
        items.append(requirement("benchmark_definition", benchmark_name, purpose="Canonical benchmark and task definition", source=protocol, protocol=protocol))
        datasets = listify(protocol.get("datasets") or protocol.get("dataset"))
        for idx, dataset in enumerate(datasets, 1):
            name = text_name(dataset, f"dataset-{idx}")
            items.append(requirement("dataset", name, purpose="Formal experiment dataset", source=dataset, protocol=protocol))
        split = protocol.get("split") or protocol.get("splits")
        if split:
            items.append(requirement("dataset_split", f"{benchmark_name}-split", purpose="Official or confirmed data split", source=split, protocol=protocol))
        metrics = listify(protocol.get("metrics") or protocol.get("metric"))
        for idx, metric in enumerate(metrics, 1):
            name = text_name(metric, f"metric-{idx}")
            items.append(requirement("metric_implementation", name, purpose="Evaluation metric implementation", source=metric, protocol=protocol))
        preprocessing = protocol.get("preprocessing") or protocol.get("tokenization") or protocol.get("feature_construction")
        if preprocessing:
            items.append(requirement("preprocessing", f"{benchmark_name}-preprocessing", purpose="Protocol-compatible preprocessing", source=preprocessing, protocol=protocol))
    elif protocol:
        items.append(requirement("benchmark_definition", "confirmed-benchmark", purpose="Canonical benchmark and task definition", source=protocol, protocol=protocol))

    method_intent = get_nested(handoff, "method_intent", default={})
    if isinstance(method_intent, dict):
        checkpoints = listify(method_intent.get("required_checkpoints") or method_intent.get("pretrained_assets"))
        for idx, checkpoint in enumerate(checkpoints, 1):
            name = text_name(checkpoint, f"checkpoint-{idx}")
            items.append(requirement("checkpoint", name, purpose="Method or baseline initialization asset", source=checkpoint))

    # Deduplicate by stable semantic ID, preserving first occurrence.
    deduped = {}
    for item in items:
        deduped.setdefault(item["requirement_id"], item)
    items = list(deduped.values())

    if args.manual_requirements:
        manual_path = resolve_in_workspace(workspace, args.manual_requirements)
        manual = load_json(manual_path)
        for item in manual.get("items", []):
            if not isinstance(item, dict) or not item.get("requirement_id"):
                raise SystemExit("manual requirement items need requirement_id")
            if item["requirement_id"] in {i["requirement_id"] for i in items}:
                raise SystemExit(f"duplicate manual requirement ID: {item['requirement_id']}")
            items.append(item)

    missing_detail_ids = [i["requirement_id"] for i in items if not i.get("expected_identity") or not i.get("acceptance_criteria")]
    status = "partial" if missing_detail_ids else "complete"
    payload = {
        "schema_version": "resource_requirement_matrix.v1",
        "generated_at": utc_now(),
        "status": status,
        "input_fingerprint": canonical_json_hash({"scope": scope, "method_intent": method_intent}),
        "items": items,
        "needs_human_completion": missing_detail_ids,
        "source_refs": ["external_executor/result_pack.json#context_alignment.confirmed_execution_scope", "external_executor/handoff_pack.json#method_intent"],
        "notes": ["This is a deterministic scaffold. The skill must refine semantic acceptance criteria before candidate approval."],
    }
    assert_write_allowed(workspace, output)
    dump_json_atomic(output, payload)
    print(f"{status}: wrote {len(items)} requirements to {relpath(workspace, output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
