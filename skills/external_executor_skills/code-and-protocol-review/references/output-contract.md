# Review Output Contract

## Contents

1. Required report shape
2. Review scope
3. Axis and finding records
4. Verification summary
5. Verdict invariants

## Required report shape

```json
{
  "schema_version": "external_executor_implementation_review.v1",
  "review_id": "",
  "iteration_id": "",
  "reviewed_at": "",
  "input_fingerprint": "",
  "requested_approval_level": "smoke | small_scale | formal",
  "review_scope": {},
  "axes": {},
  "findings": [],
  "verification_evidence": [],
  "review_status": "pass | needs_fix | blocked",
  "approved_for": "smoke | small_scale | formal | none",
  "required_fixes": [],
  "repair_owners": [],
  "confidence": {},
  "contribution_drift": "none | minor | major",
  "recommended_next_action": "experiment-run | baseline-reproduction | method-refinement | implementation | experiment-design | human_review"
}
```

## Review scope

```json
{
  "snapshot_ref": "",
  "changed_paths": [],
  "expanded_paths": [],
  "expansion_reasons": [],
  "implementation_spec_refs": [],
  "affected_experiments": [],
  "protocol_fingerprint": "",
  "baseline_refs": []
}
```

## Required axes

```text
spec_alignment
code_correctness
protocol_fairness
data_integrity
reproducibility
security_and_paths
contribution_drift
```

Each axis contains `status=pass|warning|fail|blocked|not_applicable`, findings, evidence refs, and confidence. `not_applicable` requires a reason and cannot be used for a mandatory axis at the requested level.

## Finding record

```json
{
  "finding_id": "REV-001",
  "axis": "protocol_fairness",
  "category": "seed_policy_mismatch",
  "severity": "info | warning | major | blocking",
  "status": "open | fixed_and_verified | accepted_constraint | false_positive | deferred_by_human",
  "message": "",
  "locations": [],
  "evidence_refs": [],
  "impact": {
    "execution": "",
    "fairness": "",
    "reproducibility": "",
    "claims": [],
    "scope": false
  },
  "required_fix": "",
  "repair_owner": "baseline-reproduction | method-refinement | implementation | experiment-design | research-execution",
  "blocks_run_levels": []
}
```

## Verification summary

Every evidence reference used by an axis/finding must resolve to a verification record or a pinned source artifact. Formal approval requires the evidence bundle validator to pass for the same input fingerprint.

## Confidence

```json
{
  "overall": "high | medium | low",
  "by_axis": {},
  "limiters": []
}
```

Confidence never overrides a failed gate.

## Verdict invariants

```text
pass:
  no open major/blocking finding
  approved_for != none
  approved_for does not exceed requested level
  mandatory axes pass for approved level
  current-snapshot verification evidence is sufficient

needs_fix:
  at least one open major finding or missing mandatory evidence
  required_fixes is non-empty
  approved_for is lower than requested or none

blocked:
  at least one open blocking finding or an unreviewable fixed-baseline/authority gap
  approved_for = none
  recommended_next_action = human_review or a root-owned escalation
```

Formal approval additionally requires `contribution_drift != major`, a protocol fingerprint, protocol comparison evidence, and pass on all seven axes.
