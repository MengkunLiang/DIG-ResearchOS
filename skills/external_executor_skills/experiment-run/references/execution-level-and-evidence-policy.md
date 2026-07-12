# Execution Level and Evidence Policy

Use this reference to separate computational scale from experimental purpose and evidentiary use.

## Three independent labels

| Field | Meaning | Values |
| --- | --- | --- |
| `run_type` | What is being executed | smoke, small_scale, formal, ablation, robustness, diagnostic, efficiency |
| `execution_level` | Approval and resource tier | smoke, small_scale, formal |
| `analysis_role` | Planned inferential role | confirmatory, diagnostic, exploratory |

Never infer one label solely from another except for the level-named run types.

## Approval lattice

```text
none < smoke < small_scale < formal
```

The review must pass and its `approved_for` rank must be at least the requested `execution_level`. Formal execution always requires exactly `approved_for=formal`; a lower-level successful run cannot self-upgrade.

## Evidence-use classification

| Condition | `evidence_use` |
| --- | --- |
| Completed smoke | `engineering_only` |
| Completed small-scale | `diagnostic_only` |
| Completed formal, real data, confirmatory, full provenance | `pre_audit_candidate` |
| Completed formal diagnostic/exploratory | `diagnostic_only` |
| Toy, synthetic, or dry-run at any level | `engineering_only` |
| Failed, cancelled, unusable, or stale | `none` |

`pre_audit_candidate` means only that T7 may audit it. It is not an accepted paper claim.

## Formal provenance gate

A formal run is usable only when all are present and valid:

- formal review approval bound to the same input fingerprint;
- protocol fingerprint;
- exact command and config;
- code and resource/data version identities;
- real dataset ID, version, and split;
- seed and repeat index;
- environment and hardware fingerprint;
- raw log and structured metric output;
- evaluator or metric implementation identity;
- start/end time and exit information;
- artifact checksums.

Missing one of these makes the record `unusable`, even when the process exits zero.

## No result-based promotion

Do not change `analysis_role`, `execution_level`, primary metric, selected seed, or evidence use after observing results. New inferential use requires a new plan version, review, and run.
