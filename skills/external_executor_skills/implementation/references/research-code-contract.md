# Research Code Contract

## Purpose

Research code must be executable later by `experiment-run`, auditable by `code-and-protocol-review`, and traceable to the implementation specification. Engineering convenience must not obscure scientific semantics.

## Module traceability

Every research module records:

- stable module ID and name;
- intended role from the implementation spec;
- code path and public interface;
- input/output shapes and semantics;
- config keys and defaults;
- test paths;
- ablation/diagnostic switch;
- affected experiment IDs;
- limitations and assumptions.

Implemented does not mean empirically supported.

## Entrypoints

Training and evaluation entrypoints should accept or resolve explicitly:

- config path or structured config;
- dataset/version and split identifiers;
- seed/repeat;
- output directory;
- checkpoint input/output;
- run mode;
- enabled/disabled ablation and diagnostic switches;
- device/precision when relevant;
- protocol fingerprint or a path to the locked protocol.

Do not hide claim-critical settings in source constants.

## Configuration

- use one documented source of truth per run;
- record defaults and overrides;
- avoid environment-dependent silent defaults;
- distinguish baseline and ours configs;
- make shared fairness settings explicit;
- do not mutate config after a run starts;
- represent booleans and enums unambiguously;
- keep secrets out of configs.

## Ablation switches

A required module needs an observable off-path unless the implementation spec explicitly explains why removal is structurally impossible. The off-path must:

- remove or neutralize the intended mechanism;
- preserve unrelated capacity/compute as much as feasible;
- avoid switching to a different hidden method;
- be testable without a formal run;
- be recorded in module mapping.

## Baseline adapters

Adapters may translate paths, config formats, dataset interfaces, logging, or metric outputs. They must not silently alter:

- baseline algorithm or objective;
- default capacity or architecture;
- training budget;
- data/pretraining;
- split or preprocessing;
- primary metric semantics;
- hyperparameter search budget.

Any semantic baseline change belongs to baseline repair/reproduction review and may invalidate prior reproduction.

## Determinism and seeds

- expose seed through config/entrypoint;
- seed each supported framework and data-loader path when feasible;
- record deterministic limitations;
- do not hard-code one favorable seed;
- do not make formal repeat counts an implementation constant.

## Logging and metrics

Engineering must enable later provenance:

- unique run output directory;
- raw stdout/stderr or framework log;
- machine-readable metric output;
- config snapshot;
- dataset/split identity;
- seed/repeat;
- code/implementation ID;
- protocol fingerprint;
- start/end status and failure details.

Do not print only a rounded final score. Do not use log text as the only source when a structured output can be emitted.

## Checkpoints

- separate input checkpoints from generated checkpoints;
- never overwrite an input checkpoint;
- store version/identity metadata;
- use atomic or temporary-file writes when practical;
- test serialization compatibility with a small fixture;
- do not include large checkpoints in the implementation patch.

## Data safety

- keep formal dataset access in later run tooling;
- use tiny fixtures or synthetic tensors for implementation tests;
- do not copy restricted data into source or test artifacts;
- prevent test fixtures from leaking examples across train/evaluation semantics;
- keep preprocessing behavior configurable and traceable.

## Engineering additions

Non-contribution additions such as caching, mixed precision, batching, device handling, or numerical guards must be recorded as engineering facts. If they can affect fairness or results, classify and propagate them for review rather than calling them neutral.
