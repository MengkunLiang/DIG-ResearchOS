#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from _common import (
    canonical_json_hash,
    dump_json_atomic,
    get_nested,
    listify,
    load_json,
    nonempty,
    resolve_in_workspace,
    resolve_workspace,
    stable_id,
    unique_strings,
    utc_now,
)


def text_of(item: Any, keys: tuple[str, ...]) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def normalize_claim(item: Any, index: int, source: str) -> dict[str, Any]:
    statement = text_of(item, ("claim", "statement", "claim_text", "description", "hypothesis"))
    if not statement:
        statement = f"UNRESOLVED_CLAIM_{index + 1}"
    data = item if isinstance(item, dict) else {}
    required = bool(data.get("required", data.get("mandatory", True)))
    questions = unique_strings(
        listify(data.get("reviewer_questions"))
        + listify(data.get("reviewer_question"))
        + listify(data.get("questions"))
    )
    evidence = unique_strings(
        listify(data.get("evidence_needed"))
        + listify(data.get("required_evidence"))
        + listify(data.get("evidence"))
    )
    experiments = unique_strings(
        listify(data.get("experiment_ids"))
        + listify(data.get("planned_experiment_ids"))
        + listify(data.get("experiments"))
    )
    claim_id = str(data.get("claim_id") or stable_id("CLM", statement, index))
    unsupported_reason = data.get("unsupported_reason") or data.get("reason_if_unsupported")
    status = data.get("status")
    if status not in {"needs_design", "planned", "unsupported", "supported_upstream_only"}:
        status = "unsupported" if unsupported_reason else ("planned" if experiments else "needs_design")
    return {
        "claim_id": claim_id,
        "statement": statement,
        "required": required,
        "analysis_role": data.get("analysis_role", "confirmatory" if required else "exploratory"),
        "target_strength": data.get("target_strength", data.get("claim_strength", "unspecified")),
        "reviewer_questions": questions,
        "evidence_needed": evidence,
        "planned_experiment_ids": experiments,
        "interpretation_constraints": unique_strings(
            listify(data.get("interpretation_constraints")) + listify(data.get("constraints"))
        ),
        "must_not_claim": unique_strings(listify(data.get("must_not_claim"))),
        "unsupported_reason": unsupported_reason,
        "status": status,
        "source_refs": unique_strings(listify(data.get("source_refs")) + [source]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize the confirmed claim-to-evidence contract.")
    parser.add_argument("--workspace")
    parser.add_argument("--output", default="external_executor/claim_evidence_matrix.json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    ws = resolve_workspace(args.workspace)
    ext = ws / "external_executor"
    output = resolve_in_workspace(ws, args.output)
    handoff = load_json(ext / "handoff_pack.json")
    result = load_json(ext / "result_pack.json")
    alignment = result.get("context_alignment", {})
    scope = alignment.get("confirmed_execution_scope", {})

    sources: list[tuple[str, Any]] = [
        ("result_pack.json#context_alignment.confirmed_execution_scope.claim_evidence_matrix", scope.get("claim_evidence_matrix")),
        ("handoff_pack.json#context_reboost.claim_evidence_matrix", get_nested(handoff, "context_reboost.claim_evidence_matrix")),
    ]
    raw_claims: list[tuple[str, Any]] = []
    for source, value in sources:
        for item in listify(value):
            raw_claims.append((source, item))

    if not raw_claims:
        hypothesis = scope.get("central_hypothesis") or get_nested(handoff, "context_reboost.central_hypothesis")
        if nonempty(hypothesis):
            raw_claims.append(("confirmed_execution_scope.central_hypothesis", {"claim": hypothesis, "required": True}))

    items = [normalize_claim(item, index, source) for index, (source, item) in enumerate(raw_claims)]
    # Deduplicate by explicit ID; preserve first authoritative statement and merge lists.
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        cid = item["claim_id"]
        if cid not in merged:
            merged[cid] = item
            continue
        current = merged[cid]
        for key in ("reviewer_questions", "evidence_needed", "planned_experiment_ids", "interpretation_constraints", "must_not_claim", "source_refs"):
            current[key] = unique_strings(current.get(key, []) + item.get(key, []))
        current["required"] = current.get("required", False) or item.get("required", False)

    boundaries = unique_strings(
        listify(scope.get("claim_boundaries"))
        + listify(scope.get("must_not_claim"))
        + listify(get_nested(handoff, "context_reboost.claim_boundaries"))
    )
    matrix = {
        "schema_version": "claim_evidence_matrix.v1",
        "generated_at": utc_now(),
        "status": "complete" if merged else "blocked",
        "input_fingerprint": canonical_json_hash({"scope": scope, "handoff_claims": get_nested(handoff, "context_reboost.claim_evidence_matrix")}),
        "items": list(merged.values()),
        "global_claim_boundaries": boundaries,
        "required_claim_ids": [item["claim_id"] for item in merged.values() if item.get("required")],
        "unsupported_required_claim_ids": [
            item["claim_id"] for item in merged.values() if item.get("required") and item.get("status") == "unsupported"
        ],
        "notes": [],
    }

    if output.exists() and not args.force:
        old = load_json(output)
        if old.get("input_fingerprint") == matrix["input_fingerprint"]:
            return 0
        # Preserve human-authored details by claim ID while refreshing authority fields.
        old_map = {i.get("claim_id"): i for i in old.get("items", []) if isinstance(i, dict)}
        for item in matrix["items"]:
            prior = old_map.get(item["claim_id"])
            if not prior:
                continue
            for key in ("reviewer_questions", "evidence_needed", "planned_experiment_ids", "interpretation_constraints", "must_not_claim", "unsupported_reason", "status", "target_strength"):
                if prior.get(key):
                    item[key] = prior[key]
            item["source_refs"] = unique_strings(item.get("source_refs", []) + prior.get("source_refs", []))
        matrix["supersedes_fingerprint"] = old.get("input_fingerprint")

    dump_json_atomic(output, matrix)
    return 0 if merged else 1


if __name__ == "__main__":
    raise SystemExit(main())
