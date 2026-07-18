# Baseline Reproduction Policy

## Purpose

This Skill executes and repairs already approved baseline material under an already locked experiment protocol. It bridges static resource readiness and the later integrated code/protocol review.

## Owned work

The Skill may:

- copy an approved baseline candidate into a controlled deployment directory under `external_executor/expr/`;
- configure and execute the baseline command authorized by the current iteration plan;
- capture normalized metrics, environment, hardware, resource/version identity, run records, and process reports under `external_executor/report/phase_D/baseline_reproduction/`;
- capture stdout/stderr logs, baseline-produced outputs, per-dataset/per-metric raw metric CSV files, and other original experiment outputs under `external_executor/raw_results/baseline_reproduction/`;
- perform bounded compatibility, adapter, configuration, seed, logging, and metric-extraction repairs;
- compare reproduced outputs with a predeclared reference or sanity rule;
- classify failures and record claim risks;
- prepare evidence for `code-and-protocol-review`.

The Skill may not:

- search for, download, or choose new resources;
- replace or drop a required baseline;
- change benchmark, task, dataset, split, primary metric, aggregation, or claim role;
- alter, replace, skip, or partially implement around the baseline's core idea, core module, core objective, or core design just to make execution succeed;
- add favorable extra data, pretraining, tuning budget, or compute;
- modify proposed-method code or experiment conclusions;
- approve formal comparison or write paper claims.

## Attempt lifecycle

```text
planned
  -> prepared
  -> running
  -> completed | failed | timed_out | cancelled
  -> evaluated
  -> reviewed
  -> accepted | needs_fix | blocked | stale
```

Before review, repairs are made directly to the deployed baseline copy under `external_executor/expr/baselines/<baseline-id>/...` and must remain inside the original baseline's core idea, modules, and design. Once an attempt has a run record, its code and evidence are historical state. A repair creates a new attempt by copying the prior deployed source, or an explicitly selected earlier attempt, so fixes accumulate without mutating history. Corrected output does not erase the failed path.

## Fingerprint invalidation

A reproduction is stale when any claim-relevant input changes, including:

- source commit/tree or reimplementation package hash;
- baseline adapter or patch;
- dataset version, split, labels, or preprocessing;
- metric implementation, direction, aggregation, or evaluation script;
- baseline config, seed/repeat rule, training budget, checkpoint, or initialization;
- environment/runtime with plausible numerical or semantic impact;
- protocol or fairness fingerprint.

Documentation-only changes may remain valid when the root can prove they do not affect execution or interpretation.

## Evidence levels

- `engineering_evidence`: command runs, shapes load, outputs exist.
- `reproduction_candidate`: complete run and metric provenance, including raw metric CSV files, under the locked protocol.
- `formal_review_candidate`: passing independent reproduction review; still requires the later code/protocol gate.
- `unsupported`: missing or invalid provenance, protocol mismatch, or unreviewed substitution.

## Honest stop states

- `partial` is correct when useful engineering or diagnostic evidence exists but formal comparison is not ready.
- `blocked` is correct when an external constraint prevents a valid required-baseline attempt.
- `failed` means the Skill itself could not complete its bounded work because of an unrecoverable tool/runtime error; a baseline that cannot be reproduced after valid attempts is usually `complete` analysis with gate `partial` or `blocked`.
