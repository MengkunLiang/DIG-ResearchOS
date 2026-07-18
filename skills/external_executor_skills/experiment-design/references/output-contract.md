# Output Contract

## Standalone artifacts

```text
external_executor/report/experiment_design_preflight.json
external_executor/report/claim_evidence_matrix.json
external_executor/report/protocol_snapshot.json
external_executor/report/protocol_fingerprint.json
external_executor/report/protocol_change_impact.json         # when needed
external_executor/experiment_plan.json
external_executor/report/experiment_plan_validation.json
external_executor/report/experiment_plan_dag_validation.json
external_executor/report/experiment_design_gate.json
external_executor/report/experiment_design_report.json
external_executor/report/experiment_design_report_validation.json
```

## Narrow result-pack ownership

This skill may update only:

```text
result_pack.json#claim_evidence_matrix
result_pack.json#experiment_plan
```

The protocol snapshot, fingerprint, review, gate, resource constraints, and plan version are embedded in `experiment_plan` and also preserved as standalone artifacts.

Do not update:

```text
context_alignment
resource_requirement_matrix
resources
baseline_candidates
dataset_inventory
material_gaps
resource_risks
resource_readiness
baseline_reproduction
experiment_runs
implementation_reviews
result_diagnoses
module_attributions
iteration_decisions
executor_status
run_manifest
```

## Claim matrix shape

Required fields:

```text
schema_version
generated_at
status
input_fingerprint
items
global_claim_boundaries
required_claim_ids
unsupported_required_claim_ids
```

Each item contains claim ID, statement, required flag, analysis role, reviewer questions, evidence needed, planned experiment IDs, constraints, unsupported reason/status, and source refs.

## Experiment plan shape

Required fields:

```text
schema_version
plan_version
generated_at
status
input_fingerprint
protocol_snapshot
protocol_fingerprint
claim_evidence_matrix
experiments
execution_dag
budget
estimated_budget
early_stop
resource_constraints
plan_review
design_gate
```

## Gate shape

```json
{
  "status": "ready | partial | blocked",
  "minimum_loop_planned": true,
  "protocol_locked": true,
  "dag_valid": true,
  "plan_review_status": "pass | needs_fix | blocked",
  "blocking_issues": [],
  "constraints": [],
  "next_action": "continue_to_phase_d | continue_to_phase_d_with_constraints | return_to_experiment_design_or_human_review"
}
```

## Child completion versus design readiness

These are separate axes:

- `child status=complete`, `design_readiness=ready`: Phase C completed and can proceed.
- `child status=complete`, `design_readiness=partial`: Phase C completed with explicit constraints.
- `child status=blocked`, `design_readiness=blocked`: the skill successfully established that the plan cannot proceed.
- `child status=failed`: the skill itself could not produce or validate its report.

## Root return

Return:

```text
child_skill=experiment-design
status=complete|partial|blocked|failed
design_readiness=ready|partial|blocked
plan=<path>
protocol_fingerprint=<sha256>
plan_version=<integer>
protocol_version=<integer>
blocking_issues=<list>
constraints=<list>
recommended_next_action=<enum>
```

Only the root records checkpoint completion, marks old runs stale, updates manifest/status, creates iteration plans, or dispatches Phase D children.
