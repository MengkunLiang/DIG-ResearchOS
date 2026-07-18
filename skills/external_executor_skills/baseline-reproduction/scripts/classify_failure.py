#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

from _common import dump_json_atomic, find_workspace, is_within, load_json, stable_id, utc_now

RULES = [
    ("security_license_block", "block_execution", 0.95, [r"license.*(prohibit|forbid|not permitted)", r"security review.*blocked", r"permission denied.*restricted"]),
    ("dataset_unavailable", "mark_unavailable", 0.9, [r"dataset.*not found", r"no such file.*(?:data|dataset|split)", r"access denied.*dataset", r"download.*forbidden"]),
    ("out_of_memory", "repair", 0.95, [r"out of memory", r"cuda.*oom", r"cannot allocate memory", r"killed process.*oom"]),
    ("timeout", "rerun", 0.95, [r"timed out", r"timeout expired"]),
    ("numerical_instability", "repair", 0.85, [r"\bnan\b", r"\binf\b", r"overflow", r"diverg"]),
    ("obsolete_code", "repair", 0.8, [r"has no attribute", r"deprecated", r"module.*not found", r"cannot import name", r"unexpected keyword argument"]),
    ("environment_issue", "repair", 0.8, [r"no module named", r"shared object file", r"glibc", r"cuda driver", r"device not found"]),
    ("metric_missing", "repair", 0.8, [r"metric.*missing", r"expected output.*missing", r"no metric"]),
    ("metric_protocol_mismatch", "block_execution", 0.8, [r"metric.*mismatch", r"split.*mismatch", r"protocol.*mismatch"]),
    ("config_ambiguous", "request_replacement_review", 0.7, [r"ambiguous config", r"missing hyperparameter", r"unknown configuration"]),
    ("resource_incomplete", "block_execution", 0.8, [r"checkpoint.*not found", r"missing.*(?:weights|labels|vocab|preprocess)"]),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="Heuristically classify a baseline reproduction failure.")
    ap.add_argument("--run-record", required=True)
    ap.add_argument("--stdout", required=True)
    ap.add_argument("--stderr", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    output = Path(args.output).resolve()
    workspace = find_workspace(output)
    if not is_within(output, workspace / "external_executor" / "report" / "phase_D"):
        raise SystemExit("Failure classifications must be written under external_executor/report")
    run = load_json(Path(args.run_record).resolve())
    text = "\n".join([Path(args.stdout).read_text(errors="replace") if Path(args.stdout).exists() else "", Path(args.stderr).read_text(errors="replace") if Path(args.stderr).exists() else ""])[-200000:]
    if run.get("status") == "timed_out":
        category, action, confidence, evidence = "timeout", "rerun", 0.99, ["run_record.status=timed_out"]
    else:
        category, action, confidence, evidence = "runtime_error", "repair", 0.45, []
        lower = text.lower()
        for cat, act, conf, patterns in RULES:
            hits = []
            for pattern in patterns:
                m = re.search(pattern, lower, flags=re.I)
                if m:
                    hits.append(m.group(0)[:160])
            if hits:
                category, action, confidence, evidence = cat, act, conf, hits
                break
        if run.get("status") == "completed" and all(x.get("exists") for x in run.get("output_checks", [])):
            category, action, confidence, evidence = "unknown", "rerun", 0.3, ["process completed; failure likely evidence-level rather than execution-level"]
    failure_id = stable_id("FAIL", run.get("run_id"), category)
    payload = {
        "schema_version": "baseline_failure_classification.v1", "failure_id": failure_id,
        "generated_at": utc_now(), "run_id": run.get("run_id"), "reproduction_id": run.get("reproduction_id"),
        "primary_category": category, "secondary_categories": [], "recommended_action": action,
        "confidence": confidence, "heuristic": True, "evidence_snippets": evidence,
        "review_required": True, "notes": ["This is a heuristic proposal; inspect direct evidence before accepting."],
    }
    dump_json_atomic(output, payload)
    print(f"{category}: {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
