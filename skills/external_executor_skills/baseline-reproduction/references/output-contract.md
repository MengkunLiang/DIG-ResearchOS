# Baseline Reproduction Output Contract

## Child report

```json
{
  "schema_version": "baseline_reproduction_report.v1",
  "child_skill": "baseline-reproduction",
  "status": "complete|partial|blocked|failed",
  "generated_at": "",
  "input_fingerprint": "",
  "iteration_id": "",
  "protocol_fingerprint": "",
  "fairness_fingerprint": "",
  "plan_ref": "external_executor/baseline_reproduction_plan.json",
  "items": [],
  "repair_attempts": [],
  "failure_classifications": [],
  "baseline_risks": [],
  "claim_risks": [],
  "artifact_refs": [],
  "reproduction_gate": {
    "status": "pass|partial|blocked",
    "formal_comparison_ready": false,
    "reproduced_baseline_ids": [],
    "conditional_baseline_ids": [],
    "blocking_baseline_ids": [],
    "stale_baseline_ids": [],
    "blocking_issues": [],
    "next_action": "continue_to_method_refinement|continue_to_implementation|baseline_repair|human_review|stop_and_report"
  },
  "notes": []
}
```

## Reproduction item

```json
{
  "reproduction_id": "",
  "baseline_id": "",
  "baseline_name": "",
  "candidate_id": "",
  "requirement_ids": [],
  "required": true,
  "source_identity": {},
  "protocol_fingerprint": "",
  "fairness_fingerprint": "",
  "status": "planned|running|reproduced|partially_reproduced|executable_only|failed|unavailable|blocked|stale",
  "technical_outcome": "reproduced_within_tolerance|reproduced_directionally|partially_reproduced|executable_only|failed|unavailable|blocked",
  "comparability_status": "formal_review_candidate|conditional_comparison_only|smoke_only|not_comparable",
  "attempts": [],
  "selected_attempt_id": null,
  "aggregate_metrics": [],
  "reference_comparisons": [],
  "repair_ids": [],
  "failure_ids": [],
  "review": {
    "review_id": "",
    "verdict": "pass|needs_fix|blocked",
    "identity_fidelity": "exact|high|moderate|low|unknown",
    "mechanism_fidelity": "exact|high|moderate|low|unknown",
    "protocol_fidelity": "exact|high|moderate|low|unknown",
    "fairness_risk": "low|medium|high|blocking|unknown",
    "provenance_completeness": "complete|partial|insufficient",
    "approximation_level": "none|minor|material|unknown",
    "findings": [],
    "required_fixes": [],
    "evidence_refs": [],
    "approved_for": "formal_review_candidate|conditional_comparison_only|smoke_only|none"
  },
  "claim_risk_ids": [],
  "evidence_refs": [],
  "notes": []
}
```

## Gate consistency

- `pass`: every required active item is `reproduced`, has review `pass`, and approval `formal_review_candidate`; no blocking/stale required item exists.
- `partial`: at least one useful result exists, but one or more required items are conditional, incomplete, unavailable, failed, or stale; `formal_comparison_ready=false`.
- `blocked`: a required item is blocked by scope, access, security/license, protocol authority, or an exhausted non-repairable condition.
- Child `status=complete` may accompany gate `partial` or `blocked` when the Skill completed its analysis honestly.
- `failed` is reserved for unrecoverable Skill/tool failure, not baseline non-reproduction.

## Result-pack mapping

The apply script writes only:

```text
result_pack.baseline_reproduction <- report normalized as one section
```

It does not modify `experiment_runs`; later general experiment execution owns that section. Baseline run records remain referenced from `baseline_reproduction` and are registered in the global manifest by the root.

## Artifact reference

```json
{
  "artifact_id": "",
  "path": "external_executor/...",
  "sha256": "",
  "size_bytes": 0,
  "producer": "baseline-reproduction",
  "created_at": "",
  "evidence_level": "raw_result|diagnostic_hint|unsupported"
}
```

Artifact refs with `evidence_level=raw_result` must point under `external_executor/raw_results/baseline_reproduction/`. Deployment/source references point under `external_executor/expr/baseline_reproduction/`; approved input resources remain under `resources/` for by-hand local material or `resource/` for acquired/reimplemented material. `external_executor/workdir/` is not an approved input resource root.

## Child return

```text
child_skill=baseline-reproduction
status=complete|partial|blocked|failed
reproduction_gate=pass|partial|blocked
report=external_executor/baseline_reproduction_report.json
plan=external_executor/baseline_reproduction_plan.json
reproduced_baseline_ids=<ids>
conditional_baseline_ids=<ids>
blocking_baseline_ids=<ids>
stale_baseline_ids=<ids>
claim_risks=<ids-or-summary>
recommended_next_action=continue_to_method_refinement|continue_to_implementation|baseline_repair|human_review|stop_and_report
```
