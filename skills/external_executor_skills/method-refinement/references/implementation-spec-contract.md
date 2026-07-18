# Method Implementation Specification Contract

## Purpose

The implementation specification is the executable design contract between `method-refinement` and `implementation`. It must be concrete enough to code and test while remaining independent of any one code patch.

## Envelope

```json
{
  "schema_version": "method_implementation_spec.v1",
  "spec_id": "method-spec-...",
  "spec_version": 1,
  "iteration_id": "iteration-...",
  "status": "needs_review | complete | needs_fix | blocked",
  "intent_fingerprint": "",
  "protocol_fingerprint": "",
  "plan_version": 1,
  "trigger": {},
  "research_contract": {},
  "system_boundary": {},
  "modules": [],
  "objectives_and_losses": [],
  "training_flow": [],
  "inference_flow": [],
  "data_and_protocol_interfaces": {},
  "baseline_and_fairness_constraints": {},
  "configuration_contract": {},
  "logging_and_provenance_contract": {},
  "ablation_and_diagnostic_controls": [],
  "acceptance_checks": [],
  "non_contribution_engineering": [],
  "unresolved_design_decisions": [],
  "scope_boundary": {},
  "change_log": []
}
```

## Research contract

Must preserve:

- central hypothesis;
- contribution type;
- core mechanism;
- must-preserve component IDs and invariants;
- claim boundary;
- allowed refinement surface;
- forbidden changes.

This section is a binding summary, not a new scientific interpretation.

## Module record

```json
{
  "module_id": "M1",
  "name": "",
  "contribution_role": "core | supporting | engineering | diagnostic",
  "status": "planned | retained | modified | added | deprecated",
  "purpose": "",
  "mechanism_ref": "",
  "inputs": [{"name": "", "type_or_shape": "", "semantics": ""}],
  "outputs": [{"name": "", "type_or_shape": "", "semantics": ""}],
  "invariants": [],
  "implementation_notes": [],
  "code_targets": [],
  "config_keys": [],
  "ablation_switch": {},
  "diagnostic_hooks": [],
  "tests": [],
  "failure_modes": [],
  "evidence_links": [],
  "source_refs": []
}
```

Core and supporting modules require non-empty inputs, outputs, invariants, config keys, tests, and a controlled ablation or a documented reason why direct ablation is impossible.

## Loss/objective record

Record name, mathematical or operational definition, role, inputs, coefficient/config key, optimization direction, numerical stability rules, implementation target, and ablation/diagnostic mapping.

## Training and inference flows

Each ordered step contains:

```text
step
name
description
module_ids
inputs
outputs
state_updates
config_dependencies
failure_handling
```

Training-only operations must not silently leak into inference. Evaluation-time preprocessing must match the protocol.

## Protocol interfaces

Bind rather than duplicate:

- protocol fingerprint;
- dataset/version/split;
- preprocessing;
- primary metric/direction/aggregation;
- seed and repeat policy;
- evaluation entry point;
- approved baseline/resource IDs;
- tuning and compute fairness.

Any intentional deviation requires an explicit change record and root authorization.

## Deployment and raw outputs

Implementation targets for ours modules, baseline adapters, entrypoints, and runnable configs must resolve under `external_executor/expr/`. Experiment logs, metric files, run records, checkpoints, and other run-produced outputs must resolve under `external_executor/raw_results/`. Prepared datasets, checkpoints, benchmark resources, and externally acquired baselines are read from `resources/`, not from `external_executor/expr/`.

## Configuration contract

For every config key record:

```text
key
type
default
allowed_values_or_range
owner_module
is_ablation_switch
is_claim_sensitive
source
```

A claim-sensitive default cannot be changed after confirmatory results are observed without a new plan/protocol decision.

## Acceptance checks

At minimum cover:

- module import/shape/interface tests;
- deterministic config parsing;
- loss and gradient sanity;
- training/inference path separation;
- ablation switch behavior;
- logging and raw-artifact emission;
- protocol and metric binding;
- no unauthorized path/network behavior;
- no silent fallback that changes the method.

## Non-contribution engineering

Record optimization tricks, caching, batching, precision, initialization, scheduler, checkpointing, and reliability fixes separately. State whether fairness requires applying them to baselines or controlling them in comparisons.
