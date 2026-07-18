#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import Any

from _common import assert_write_allowed, canonical_hash, dump_json_atomic, get_nested, listify, load_json, relpath, resolve_in_workspace, resolve_workspace, stable_id, utc_now
from preflight_reproduction import find_active_iteration


def candidate_approved(candidate: dict) -> bool:
    values = candidate.get("approved_for", [])
    if isinstance(values, str):
        values = [values]
    return bool(set(values) & {"baseline_reproduction", "formal_comparison"})


def baseline_name(candidate: dict) -> str:
    return str(candidate.get("baseline_name") or candidate.get("name") or candidate.get("baseline_id") or candidate.get("candidate_id") or "baseline")


def extract_protocol(plan: dict) -> dict:
    protocol = plan.get("protocol") if isinstance(plan.get("protocol"), dict) else {}
    if not protocol:
        protocol = plan.get("protocol_contract") if isinstance(plan.get("protocol_contract"), dict) else {}
    return protocol


def main() -> int:
    ap = argparse.ArgumentParser(description="Build deterministic baseline reproduction plan scaffold.")
    ap.add_argument("--workspace")
    ap.add_argument("--preflight", default="external_executor/report/phase_D/baseline_reproduction_preflight.json")
    ap.add_argument("--output", default="external_executor/report/phase_D/baseline_reproduction_plan.json")
    args = ap.parse_args()
    ws = resolve_workspace(args.workspace)
    output = resolve_in_workspace(ws, args.output)
    result = load_json(ws / "external_executor" / "result_pack.json")
    preflight = load_json(resolve_in_workspace(ws, args.preflight))
    if preflight.get("status") == "blocked":
        raise SystemExit("Reproduction preflight is blocked")

    experiment_plan = result.get("experiment_plan", {})
    protocol = extract_protocol(experiment_plan)
    protocol_fp = preflight.get("protocol_fingerprint")
    fairness_fp = preflight.get("fairness_fingerprint") or stable_id("FAIR", protocol_fp, protocol)
    iteration = find_active_iteration(result) or {}
    allowed_baselines = set(iteration.get("baseline_ids", []))

    candidates = result.get("baseline_candidates", {}).get("items", [])
    requirements = {i.get("requirement_id"): i for i in result.get("resource_requirement_matrix", {}).get("items", []) if isinstance(i, dict)}
    items = []
    for cand in candidates:
        if not isinstance(cand, dict) or not candidate_approved(cand):
            continue
        bid = str(cand.get("baseline_id") or stable_id("BASE", baseline_name(cand)))
        if allowed_baselines and bid not in allowed_baselines and cand.get("candidate_id") not in allowed_baselines:
            continue
        req_ids = listify(cand.get("requirement_ids"))
        required = any(requirements.get(rid, {}).get("required", False) for rid in req_ids) if req_ids else bool(cand.get("required", True))
        source_path = cand.get("local_path") or cand.get("path") or get_nested(cand, "source.path", default="")
        source_class = cand.get("source_class") or get_nested(cand, "source.class", default="other")
        revision = cand.get("resolved_revision") or cand.get("revision") or get_nested(cand, "source.revision", default="")
        source_sha = cand.get("sha256") or cand.get("manifest_sha256") or get_nested(cand, "source.sha256", default="")
        name = baseline_name(cand)
        repro_id = stable_id("REPRO", bid, cand.get("candidate_id"), protocol_fp, fairness_fp)

        metrics = []
        proto_metrics = listify(protocol.get("metrics") or protocol.get("metric") or experiment_plan.get("metrics"))
        for idx, metric in enumerate(proto_metrics, 1):
            if isinstance(metric, str):
                metric = {"name": metric}
            if not isinstance(metric, dict):
                metric = {"name": f"metric-{idx}"}
            metrics.append({
                "name": metric.get("name") or metric.get("metric") or f"metric-{idx}",
                "primary": bool(metric.get("primary", idx == 1)),
                "direction": metric.get("direction", "higher"),
                "units": metric.get("units", ""),
                "aggregation": metric.get("aggregation", "mean"),
                "extractor": metric.get("extractor", {"type": "json", "path": "metrics.json", "selector": metric.get("name") or "value"}),
                "reference": metric.get("reference", {"type": "none", "source_refs": []}),
            })
        dataset = {
            "name": protocol.get("dataset") if isinstance(protocol.get("dataset"), str) else get_nested(protocol, "dataset.name", default=""),
            "version": get_nested(protocol, "dataset.version", default=protocol.get("dataset_version", "")),
            "path": get_nested(protocol, "dataset.path", default=""),
            "split": protocol.get("split") or get_nested(protocol, "dataset.split", default=""),
            "preprocessing": protocol.get("preprocessing", ""),
            "checksum": get_nested(protocol, "dataset.checksum", default=""),
        }
        argv = cand.get("reproduction_argv") or get_nested(cand, "execution.argv", default=[])
        working = cand.get("working_directory") or get_nested(cand, "execution.working_directory", default=".")
        item = {
            "reproduction_id": repro_id,
            "baseline_id": bid,
            "baseline_name": name,
            "required": required,
            "candidate_id": cand.get("candidate_id"),
            "requirement_ids": req_ids,
            "source": {"class": source_class, "path": source_path, "revision": revision, "sha256": source_sha, "resource_review_ids": listify(cand.get("review_ids"))},
            "protocol_fingerprint": protocol_fp,
            "fairness_fingerprint": fairness_fp,
            "dataset": dataset,
            "metrics": metrics,
            "execution": {
                "authorized": bool(argv and iteration),
                "argv": argv,
                "working_directory": working,
                "allowed_executables": cand.get("allowed_executables", ["python", "python3"]),
                "timeout_seconds": int(cand.get("timeout_seconds", 3600)),
                "memory_limit_mb": cand.get("memory_limit_mb"),
                "cpu_time_limit_seconds": cand.get("cpu_time_limit_seconds"),
                "expected_outputs": listify(cand.get("expected_outputs")),
                "allowed_env_names": listify(cand.get("allowed_env_names")),
                "env_overrides": cand.get("env_overrides", {}),
                "network_required": bool(cand.get("network_required", False)),
            },
            "config": {"paths": listify(cand.get("config_paths")), "parameters": cand.get("parameters", {}), "seed_parameter": cand.get("seed_parameter"), "repeat_parameter": cand.get("repeat_parameter")},
            "seeds": listify(cand.get("seeds") or protocol.get("seeds")),
            "repeats": int(cand.get("repeats") or protocol.get("repeats") or 1),
            "repair_policy": {"max_attempts": int(cand.get("max_repair_attempts", 3)), "allowed_classes": cand.get("allowed_repair_classes", ["environment_compatibility", "path_adapter", "config_adapter", "seed_plumbing", "logging_repair", "metric_extraction_repair"])},
            "claim_dependencies": listify(cand.get("claim_ids")),
            "non_reproduction_consequence": cand.get("non_reproduction_consequence", "Blocks unqualified comparison against this baseline"),
            "status": "planned",
            "blocking_issues": [],
            "notes": ["Complete project-specific command, metric extractor, reference rule, and config before execution."],
        }
        missing = []
        if not source_path:
            missing.append("source.path")
        if not item["execution"]["argv"]:
            missing.append("execution.argv")
        if not dataset.get("split"):
            missing.append("dataset.split")
        if not metrics:
            missing.append("metrics")
        if not protocol_fp:
            missing.append("protocol_fingerprint")
        if missing:
            item["status"] = "incomplete"
            item["blocking_issues"] = [{"id": "missing_plan_field", "field": x} for x in missing]
            item["execution"]["authorized"] = False
        items.append(item)

    status = "blocked" if not items else ("partial" if any(i["status"] != "planned" for i in items) else "complete")
    payload = {
        "schema_version": "baseline_reproduction_plan.v1",
        "status": status,
        "generated_at": utc_now(),
        "input_fingerprint": canonical_hash({"candidates": candidates, "experiment_plan": experiment_plan, "iteration": iteration}),
        "iteration_id": iteration.get("iteration_id") or iteration.get("id"),
        "protocol_fingerprint": protocol_fp,
        "fairness_fingerprint": fairness_fp,
        "items": items,
        "notes": ["Deterministic scaffold; project-specific completion and review are required before execution."],
    }
    assert_write_allowed(ws, output)
    dump_json_atomic(output, payload)
    print(f"{status}: wrote {len(items)} items to {relpath(ws, output)}")
    return 2 if status == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
