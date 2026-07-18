# Experiment Plan Contract

## Plan envelope

The standalone plan uses:

```json
{
  "schema_version": "experiment_plan.v1",
  "plan_version": 1,
  "status": "needs_review | complete | needs_fix | blocked",
  "protocol_snapshot": {},
  "protocol_fingerprint": {},
  "claim_evidence_matrix": {},
  "experiments": [],
  "execution_dag": {},
  "budget": {},
  "estimated_budget": {},
  "early_stop": {},
  "resource_constraints": {},
  "plan_review": {},
  "design_gate": {}
}
```

The plan is versioned. A changed claim boundary, approved resource, material protocol, required diagnostic, or budget may require a new plan version.

## Experiment record

Each experiment contains:

- stable `experiment_id`;
- name and `experiment_kind`;
- `analysis_role`: `confirmatory | diagnostic | exploratory`;
- `run_type`: `smoke | small_scale | formal | ablation | robustness | diagnostic | efficiency`;
- claim IDs and reviewer question;
- evidence needed;
- dataset/version/split;
- variants and baseline/resource references;
- mechanism reference, target module IDs, and `attribution_contract` for ablations;
- primary/secondary metrics and directions;
- seeds, seed count, and repeats;
- protocol fingerprint;
- preconditions and dependencies;
- expected artifacts;
- cost estimate;
- decision rule;
- positive, negative, and inconclusive interpretation;
- failure handling, risks, and source references.

Each required ablation `attribution_contract` contains:

- `target_module_ids`;
- one stable `reference_variant_id`;
- at least two `variant_contracts` with exact `variant_id`, `reference_variant_id`, `module_states`, and controlled `intervention`;
- pairing dimensions covering implementation, protocol, dataset/version/split, preprocessing, setting/subset, metric, seed/repeat, and fairness;
- required run fields including `pair_id`, module state, intervention identity, fingerprints, and metric directions.

The root considers the ablation complete only when every required variant exists in a comparable completed pair for every planned seed/repeat surface.

## Minimum experiment package

The project-specific package is determined by claims. Phase C should nevertheless account for these categories when applicable:

1. required baseline reproduction prerequisites;
2. ours smoke validation;
3. main formal comparison for each required empirical claim;
4. mechanism ablation or other direct mechanism test;
5. module attribution evidence;
6. claim-relevant robustness/generalization/sensitivity/efficiency;
7. failure or limitation analysis;
8. reproducibility artifacts.

Absence of a category is acceptable only when the claim does not require it or when the claim is explicitly narrowed/unsupported.

## Baseline reproduction plans

Planning a reproduction does not claim reproduction success. Bind the plan to:

- approved candidate/resource ID;
- protocol fingerprint;
- expected config/evaluation entry points;
- reproduction fidelity criteria;
- failure classification and claim-risk propagation.

## Smoke and small-scale plans

Smoke evidence validates engineering only. Small-scale evidence validates feasibility and early signal only. Neither is formal evidence unless a separately declared protocol explicitly defines it as the target claim setting.

## Formal plans

A formal experiment must have:

- complete protocol;
- required resources;
- review precondition;
- primary metric, direction, aggregation, seed/repeat policy;
- expected raw and structured artifacts;
- predeclared interpretation rules;
- cost within budget.

## Version and change log

When a plan changes, record:

- previous plan/protocol version;
- trigger and authorized decision;
- affected claims and experiments;
- whether old evidence remains comparable, becomes diagnostic only, or becomes stale;
- required reruns;
- budget impact.

The root owns iteration decisions and stale marking. This skill only describes impact.
