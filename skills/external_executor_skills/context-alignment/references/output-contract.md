# Context Alignment Output Contract

Use this structure for `external_executor/context_alignment_report.json` and `result_pack.json#context_alignment`.

## Contents

1. Required report shape
2. Source and axis records
3. Mismatch records
4. Confirmed execution scope
5. Gate invariants

## Required report shape

```json
{
  "schema_version": "external_executor_context_alignment.v1",
  "status": "pass | mismatch | blocked",
  "alignment_fingerprint": "sha256 digest",
  "checked_at": "RFC3339 timestamp",
  "source_files_checked": [],
  "axes": {},
  "mismatches": [],
  "assumptions": [],
  "blocking_issues": [],
  "confirmed_execution_scope": {},
  "field_provenance": {},
  "confidence": {},
  "next_action": "continue_to_phase_b | continue_with_constraints | human_review | stop_and_report"
}
```

## Source record

```json
{
  "path": "workspace-relative/path",
  "role": "control | compiled_handoff | hypothesis | protocol | novelty | literature | risk | optional_detail",
  "sha256": "",
  "status": "checked | missing | unreadable | not_needed",
  "fields_used": []
}
```

## Axis record

Required axes:

```text
control_plane
research_semantics
experiment_contract
claim_boundary
capability_fit
```

Each axis contains `status`, `fields_checked`, `findings`, `evidence_refs`, and `confidence`.

## Mismatch record

```json
{
  "mismatch_id": "CTX-001",
  "axis": "experiment_contract",
  "field": "required_baselines",
  "severity": "info | warning | material | blocking",
  "compared_values": [],
  "source_refs": [],
  "impact": {
    "execution": "",
    "claims": [],
    "baselines": [],
    "protocol": false,
    "scope": false,
    "novelty": false,
    "permissions": false
  },
  "resolution_status": "confirmed_same | accepted_compiled_value | accepted_stricter_control | recorded_constraint | requires_human_review | unresolved",
  "resolution": "",
  "downstream_constraints": [],
  "requires_human_review": false
}
```

## Blocking issue

```json
{
  "blocker_id": "CTX-B001",
  "type": "missing_control | unsupported_schema | material_conflict | missing_authority | capability_gap | minimum_loop_undefined",
  "message": "",
  "source_refs": [],
  "required_resolution": ""
}
```

## Confirmed execution scope

Required fields:

```text
project_goal
central_hypothesis
core_mechanism
must_preserve_components
candidate_components
allowed_refinements
forbidden_scope_changes
required_baselines
replacement_constraints
benchmark_protocol
minimum_experiment_loop
claim_boundaries
must_not_claim
writer_handoff_contract
resource_acquisition_policy
allowed_paths
forbidden_paths
iteration_budget
stop_conditions
output_schema_version
```

Unknown noncritical subfields may be `null` with an assumption. Execution-critical fields cannot be silently null in `pass` or `mismatch`.

`field_provenance` maps every required field to one or more source paths and a derivation label: `direct`, `compiled_and_confirmed`, or `compiled_with_constraint`.

## Confidence

```json
{
  "overall": "high | medium | low",
  "by_axis": {
    "control_plane": "high | medium | low",
    "research_semantics": "high | medium | low",
    "experiment_contract": "high | medium | low",
    "claim_boundary": "high | medium | low",
    "capability_fit": "high | medium | low"
  },
  "confidence_limiters": []
}
```

Confidence does not override status. A low-confidence material field is blocking.

## Gate invariants

```text
pass:
  mismatches is empty
  blocking_issues is empty
  next_action = continue_to_phase_b

mismatch:
  mismatches is non-empty
  no material/blocking mismatch remains unresolved
  blocking_issues is empty
  next_action = continue_with_constraints

blocked:
  blocking_issues is non-empty or a material/blocking mismatch is unresolved
  next_action = human_review or stop_and_report
```

The report is an execution contract, not a narrative essay. Keep explanations concise and fields machine-consumable.
