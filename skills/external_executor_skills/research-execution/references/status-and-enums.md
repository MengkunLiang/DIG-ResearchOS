# Status and Enums

Use these values unless the workspace's supported `expected_outputs_schema.json` defines a stricter compatible subset.

## Contents

1. Core executor state
2. Section state
3. Review and run enums
4. Evidence enums
5. Drift and action enums
6. Minimal executor status envelope
7. Result-pack required keys

## Core executor state

```text
running | completed | partial | blocked | failed
```

- `running`: a safe next action exists and execution is active.
- `completed`: mandatory work is complete and final validation passes.
- `partial`: usable auditable evidence exists, but mandatory work or provenance is incomplete.
- `blocked`: a prerequisite, permission, resource, scope, security, or license issue prevents the required next action.
- `failed`: execution was attempted but an unrecoverable failure left no reliable experimental result.

## Section state

```text
not_started | running | complete | partial | blocked | unavailable | stale
```

A required section key must exist even when work is unavailable. Represent it with explicit status, empty items, and blocking issues; never fabricate content.

## Review and run enums

```text
review_status: pass | needs_fix | blocked
approved_for: smoke | small_scale | formal | none
run_type: smoke | small_scale | formal | ablation | robustness | diagnostic | efficiency
run_status: planned | running | completed | failed | cancelled | stale | unusable
analysis_role: confirmatory | diagnostic | exploratory
```

## Evidence enums

```text
evidence_level:
  raw_result | audited_candidate | diagnostic_hint |
  method_definition | abstract_only | unsupported

claim_strength:
  strong | moderate | weak | unsupported

attribution_basis:
  direct_ablation | controlled_diagnostic | correlational_hint |
  implementation_fact | unsupported
```

## Drift and action enums

```text
contribution_drift: none | minor | major

required_action:
  none | update_method | rerun_experiment | rerun_novelty |
  human_review | narrow_claim | stop

iteration_decision:
  continue_same_idea | minor_method_fix | module_reweight |
  baseline_repair | add_diagnostic_run | claim_narrowing |
  scope_change_request | stop_and_report
```

## Minimal executor status envelope

```json
{
  "schema_version": "external_executor_status.v1",
  "executor_status": "running",
  "current_phase": "A",
  "current_step": "A1",
  "iteration_id": null,
  "completed_checkpoints": [],
  "stale_checkpoints": [],
  "active_blockers": [],
  "budget": {},
  "iteration_loop": {"current_iteration": 0, "max_iterations": 10, "last_decision_id": null, "outcome": "not_started"},
  "input_fingerprint": null,
  "next_action": "context-alignment",
  "updated_at": ""
}
```

## Result-pack required keys

At minimum preserve:

```text
schema_version
executor_status
context_alignment
resource_requirement_matrix
resources
baseline_candidates
dataset_inventory
material_gaps
resource_risks
resource_readiness
baseline_reproduction
claim_evidence_matrix
experiment_plan
experiment_runs
implementation_reviews
result_diagnoses
module_attributions
iteration_decisions
realized_method_package
framework_figure
figure_table_inventory
```

Optional sections include scope-change requests, failed trials, replacement baselines, additional resources, manual notes, and open blockers.
