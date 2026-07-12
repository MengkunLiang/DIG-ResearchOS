# Module and Mechanism Contract

## Module registry

```json
{
  "module_id": "M1",
  "owner_method_id": "ours",
  "name": "",
  "module_kind": "core|auxiliary|loss|preprocessing|adapter|regularizer|training_trick|baseline_component|other",
  "intended_role": "",
  "inputs": [],
  "outputs": [],
  "code_paths": [],
  "config_keys": [],
  "ablation_switches": [],
  "diagnostic_switches": [],
  "mechanism_ids": [],
  "implementation_status": "implemented|partial|declared_only|unknown",
  "empirical_test_status": "tested|partially_tested|untested|unknown",
  "source_refs": [],
  "notes": []
}
```

Module IDs must remain stable across iterations when semantic identity is unchanged. A renamed module is not a new module unless its role or behavior materially changed.

## Mechanism registry

```json
{
  "mechanism_id": "MECH-...",
  "name": "",
  "hypothesis": "",
  "linked_module_ids": [],
  "predicted_observations": [],
  "falsifying_observations": [],
  "planned_experiment_ids": [],
  "claim_ids": [],
  "source_refs": []
}
```

## Baseline modules

Baseline components require the same identity discipline. Source-code presence or a paper description establishes only `implementation_fact`. Baseline module effectiveness requires an eligible baseline ablation or controlled diagnostic.

## Multi-function modules

When one switch disables multiple functions:

- list every affected mechanism;
- mark intervention specificity as `low` or `mixed`;
- do not attribute the full effect to one intended mechanism;
- request a more discriminating intervention if claim-critical.

## Compound variants

A compound variant can support:

- a joint package effect;
- a pairwise interaction when the full factorial contrast exists;
- no unique single-module effect without additional contrasts.
