# Routing and Gates

Use this reference when selecting a child skill, recording an iteration decision, or deciding whether to pause.

## Contents

1. Routing invariants
2. Phase transitions
3. Loop decision table
4. Human-review gates
5. Finalization routes

## Routing invariants

- `research-execution` is the only route owner.
- Dispatch one child, validate its outputs, checkpoint, then decide again.
- Route from durable Artifact state, never from chat recollection.
- A child may recommend a next action but may not invoke another child.
- Route to the earliest invalid prerequisite when resuming.
- Do not rerun a valid expensive stage solely because it precedes the current phase.

## Phase transitions

| Current evidence | Gate | Next action |
| --- | --- | --- |
| Alignment absent/stale | Required controls readable | `context-alignment` |
| Alignment `blocked` | None | Record blocker and stop |
| Alignment pass/non-blocking mismatch | Resource policy explicit | `resource-and-baseline-preparation` |
| Resource readiness `blocked` | None | Package partial state or stop |
| Resource readiness `partial` | Minimum loop feasible and constraints explicit | `experiment-design` with constraints |
| Resource readiness `ready` | Core resources approved | `experiment-design` |
| Plan absent/stale | Protocol inputs stable | `experiment-design` |
| Plan valid | Iteration budget remains | Create iteration plan |
| Implementation delta exists | Scope approved | `implementation` |
| Code/protocol changed | Review inputs complete | `code-and-protocol-review` |
| Review `needs_fix` | Owner identifiable | Route to baseline repair, method refinement, or implementation |
| Review `blocked` | None | Stop or request human review |
| Review pass | Requested run level approved | `experiment-run` |
| New usable runs | Provenance complete enough to inspect | `result-diagnosis` |
| Diagnosis complete | Attribution evidence sufficient | `module-attribution` |
| Diagnosis complete, evidence insufficient | Attribution marked unsupported | Root iteration decision |
| Iteration stop | Evidence snapshot pinned | `evidence-packaging` |
| Evidence package valid | Handoff fields resolvable | `writer-handoff` |

## Loop decision table

| Decision | Required evidence | Route effect |
| --- | --- | --- |
| `continue_same_idea` | Pending approved experiment and remaining budget | Create next iteration plan |
| `minor_method_fix` | Diagnosis identifies a non-scope-changing defect | `method-refinement` if spec changes, then `implementation` |
| `module_reweight` | Controlled evidence supports adjustment within allowed refinements | `method-refinement` then `implementation` |
| `baseline_repair` | Baseline failure classification and repair path | `baseline-reproduction`; implementation only for approved adapter/repair |
| `add_diagnostic_run` | Specific unresolved reviewer question | Version `experiment_plan` through `experiment-design` |
| `claim_narrowing` | Evidence identifies a supported subset/boundary | Update claim boundary; trigger novelty review if contribution meaning changes |
| `scope_change_request` | Proposed material change and impact analysis | Pause for human review before implementation |
| `stop_and_report` | Stop condition or no justified next action | Pin snapshot and package evidence |

One iteration decision has one primary value. Store secondary actions separately so routing remains deterministic.

## Human-review gates

Pause before acting on:

- central hypothesis or core mechanism changes;
- task, benchmark, contribution type, or required-baseline changes;
- an unauthorized replacement baseline;
- major contribution drift;
- post-novelty audit requirement;
- new network, credential, dataset, license, compute, or path authority;
- unsupported major Artifact schema;
- formal execution whose fairness cannot be established.

The request must contain the proposed change, reason, affected claims, affected baselines/experiments, evidence, risks, rollback path, and the exact approval needed.

## Finalization routes

- `completed`: final validation passes and all mandatory work is complete.
- `partial`: auditable evidence exists but one or more mandatory evidence/package elements are incomplete.
- `blocked`: a prerequisite or authority prevents the minimum loop or next required action.
- `failed`: execution encountered an unrecoverable failure and produced no reliable experiment result, though diagnostic artifacts remain.

Always run Phase F as far as evidence permits. Missing work must be explicit, not omitted or invented.
