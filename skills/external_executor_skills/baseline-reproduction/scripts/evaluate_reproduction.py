#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from _common import dump_json_atomic, find_workspace, finite_number, is_within, load_json, stable_id, utc_now


def compare(value: float, ref: dict) -> dict:
    typ = ref.get("type", "none")
    passed = None
    discrepancy = None
    detail = "no reference rule"
    if typ == "absolute_tolerance":
        target = float(ref["value"]); tol = float(ref["tolerance"]); discrepancy = abs(value-target); passed = discrepancy <= tol; detail = f"abs diff {discrepancy} <= {tol}"
    elif typ == "relative_tolerance":
        target = float(ref["value"]); tol = float(ref["tolerance"]); discrepancy = abs(value-target)/max(abs(target), 1e-12); passed = discrepancy <= tol; detail = f"relative diff {discrepancy} <= {tol}"
    elif typ == "range":
        lo, hi = float(ref["lower"]), float(ref["upper"]); passed = lo <= value <= hi; detail = f"{lo} <= {value} <= {hi}"
    elif typ == "minimum":
        target = float(ref["value"]); passed = value >= target; detail = f"{value} >= {target}"
    elif typ == "maximum":
        target = float(ref["value"]); passed = value <= target; detail = f"{value} <= {target}"
    elif typ == "directional":
        passed = bool(ref.get("observed_pass", False)); detail = str(ref.get("description", "directional rule requires reviewed evidence"))
    return {"type": typ, "passed": passed, "discrepancy": discrepancy, "detail": detail, "reference": ref}


def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate technical reproduction evidence and comparability.")
    ap.add_argument("--plan-fragment", required=True)
    ap.add_argument("--run-record", required=True)
    ap.add_argument("--metrics", required=True)
    ap.add_argument("--environment", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    output = Path(args.output).resolve()
    workspace = find_workspace(output)
    if not is_within(output, workspace / "external_executor" / "raw_results"):
        raise SystemExit("Reproduction evaluations must be written under external_executor/raw_results")
    plan = load_json(Path(args.plan_fragment).resolve())
    run = load_json(Path(args.run_record).resolve())
    metrics = load_json(Path(args.metrics).resolve())
    env = load_json(Path(args.environment).resolve())
    findings = []
    comparisons = []
    primary = [m for m in metrics.get("items", []) if m.get("primary")]
    if run.get("status") != "completed":
        findings.append({"severity": "blocking", "id": "run_not_completed", "detail": run.get("status")})
    if any(not x.get("exists") for x in run.get("output_checks", [])):
        findings.append({"severity": "high", "id": "expected_output_missing"})
    if not metrics.get("items"):
        findings.append({"severity": "blocking", "id": "metrics_missing"})
    if not primary:
        findings.append({"severity": "high", "id": "primary_metric_missing"})
    if not run.get("environment_path") or not env.get("environment_fingerprint"):
        findings.append({"severity": "high", "id": "environment_incomplete"})
    if run.get("protocol_fingerprint") != plan.get("protocol_fingerprint"):
        findings.append({"severity": "blocking", "id": "protocol_fingerprint_mismatch"})
    if run.get("fairness_fingerprint") != plan.get("fairness_fingerprint"):
        findings.append({"severity": "blocking", "id": "fairness_fingerprint_mismatch"})
    for m in primary:
        if not finite_number(m.get("value")):
            findings.append({"severity": "blocking", "id": "nonfinite_primary_metric", "metric": m.get("name")})
            continue
        comparisons.append({"metric": m.get("name"), "observed": m.get("value"), **compare(float(m["value"]), m.get("reference", {}))})

    blocking = any(f["severity"] == "blocking" for f in findings)
    all_ref_pass = comparisons and all(c.get("passed") is True for c in comparisons if c.get("type") != "none") and all(c.get("type") != "none" for c in comparisons)
    any_ref_pass = any(c.get("passed") is True for c in comparisons)
    provenance_complete = all([run.get("argv"), run.get("stdout_path"), run.get("stderr_path"), run.get("source_manifest_sha256"), run.get("config_manifest_sha256") is not None, run.get("dataset", {}).get("split"), metrics.get("items"), env.get("environment_fingerprint")])
    repeat_complete = int(plan.get("repeats", 1)) <= max([m.get("count", 0) for m in metrics.get("items", [])] or [0]) or int(plan.get("repeats", 1)) == 1

    if blocking or run.get("status") != "completed":
        outcome, comp = "failed", "not_comparable"
    elif all_ref_pass and provenance_complete and repeat_complete:
        outcome, comp = "reproduced_within_tolerance", "formal_review_candidate"
    elif any_ref_pass and provenance_complete:
        outcome, comp = "reproduced_directionally", "conditional_comparison_only"
    elif provenance_complete and metrics.get("items"):
        outcome, comp = "partially_reproduced", "conditional_comparison_only"
    elif run.get("status") == "completed":
        outcome, comp = "executable_only", "smoke_only"
    else:
        outcome, comp = "failed", "not_comparable"
    evaluation_id = stable_id("EVAL", run.get("run_id"), outcome)
    payload = {
        "schema_version": "baseline_reproduction_evaluation.v1", "evaluation_id": evaluation_id,
        "generated_at": utc_now(), "reproduction_id": plan.get("reproduction_id"), "run_id": run.get("run_id"),
        "technical_outcome": outcome, "comparability_status": comp,
        "dimensions": {"executability": run.get("status") == "completed", "protocol_fidelity": not any(f["id"] == "protocol_fingerprint_mismatch" for f in findings), "fairness_fidelity": not any(f["id"] == "fairness_fingerprint_mismatch" for f in findings), "provenance_complete": provenance_complete, "repeat_sufficiency": repeat_complete, "reference_agreement": all_ref_pass if comparisons else None},
        "reference_comparisons": comparisons, "findings": findings,
        "review_required": True, "notes": ["formal_review_candidate is not formal approval."],
    }
    dump_json_atomic(output, payload)
    print(f"{outcome}: {comp}")
    return 0 if comp != "not_comparable" else 2


if __name__ == "__main__":
    raise SystemExit(main())
