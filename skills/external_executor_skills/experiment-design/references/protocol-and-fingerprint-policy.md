# Protocol and Fingerprint Policy

## Purpose

The protocol fingerprint defines which results are directly comparable. A result is not merely associated with a plan version; it is bound to the exact protocol that determines fairness and interpretation.

## Fingerprinted fields

The canonical protocol includes:

1. benchmark name, task, version, and protocol reference;
2. dataset identity, version, split, split reference, and preprocessing;
3. primary and secondary metrics, direction, units, aggregation, and selection rule;
4. baseline identities, pinned resources, configs, approximation labels, and fairness constraints;
5. ours configuration policy, initialization, and training-budget rule;
6. evaluation scripts, statistics, uncertainty, missing-run, and failed-run policies;
7. seeds, repeats, and selection policy;
8. hyperparameter search, tuning split, tuning budget, fixed parameters, and fairness rule;
9. compute/run/time/cost budgets;
10. early-stop and reporting policies.

The script computes:

```text
sha256(canonical-json(protocol))
```

Generated timestamps, notes, and the hash itself are excluded from the protocol payload.

## Protocol completion

A formal plan is blocked when any field needed for fair comparison or claim interpretation is unresolved. Typical blockers are:

- unknown dataset split or preprocessing;
- missing primary metric or direction;
- unspecified aggregation across seeds/repeats;
- baseline resource or config not bound;
- no seed/repeat policy;
- no total-run budget;
- unclear evaluation implementation;
- missing tuning-fairness rule when tuning is allowed.

Unknown optional reporting details may remain a warning when they cannot affect comparison or claim meaning.

## Versioning

Create a new protocol version when the canonical protocol changes. Preserve the old snapshot and fingerprint. Do not edit an old fingerprint in place after runs have been produced.

### Material changes

These normally invalidate or downgrade affected formal evidence:

- task or benchmark;
- dataset identity/version, split, or preprocessing;
- primary metric, direction, aggregation, or model-selection rule;
- baseline identity, implementation/config, or fairness budget;
- evaluation script or statistical/uncertainty method;
- seed/repeat selection policy;
- hyperparameter search policy, tuning split, budget, or fairness rule;
- information access, extra data, checkpoint, or pretraining conditions.

### Usually nonmaterial changes

These may be recorded without invalidating results when semantics truly remain unchanged:

- typo or prose clarification;
- adding a source citation;
- adding an output filename or visualization instruction;
- changing optional metadata that does not affect execution or interpretation.

When uncertain, classify as material and request review.

## Comparing versions

`compare_protocol_versions.py` produces an impact report. It does not mutate runs. The root skill owns stale marking and rerun routing.

Never place results from different material protocol versions in one direct comparison without an explicit bridge analysis approved by review.
