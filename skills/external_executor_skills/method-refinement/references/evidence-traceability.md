# Evidence Traceability

## Required chain

Every contribution-relevant element should be navigable through:

```text
claim
  -> mechanism
  -> method module(s)
  -> config/default
  -> ablation or diagnostic control
  -> experiment ID
  -> expected artifact
```

The method specification does not prove the claim. It ensures the implementation will expose the controls needed to test it.

## Traceability table

Each row should contain:

```text
claim_id
mechanism_ref
module_ids
config_keys
ablation_switch_or_diagnostic
experiment_ids
expected_artifacts
interpretation_boundary
```

## Rules

- A core module without a mechanism reference is underspecified.
- A claimed mechanism without a controllable test is a design risk and must be explained.
- A config key that changes the claimed mechanism is claim-sensitive.
- A supporting engineering trick cannot be presented as the core mechanism.
- Capacity, compute, pretraining, data, and tuning differences must be exposed as possible confounds.
- Direct ablation, controlled replacement, capacity matching, and diagnostic probes are stronger than uncontrolled removal.
- Correlational diagnostics do not establish causal mechanism.
