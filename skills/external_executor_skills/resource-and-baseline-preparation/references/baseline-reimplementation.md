# Baseline Reimplementation

## Purpose

Reimplementation is the third and final controlled fallback for a required baseline whose usable implementation cannot be obtained from local `resources/` material or from authorized public remote sources. It is not a shortcut to a simpler comparator.

## Preconditions

All must hold:

- policy mode is `github_and_reimplementation`;
- `baseline_reimplementation_allowed=true`;
- local resource search under `resources/` is complete;
- authorized public remote search and acquisition attempts are complete;
- search and rejection records exist;
- paper/supplement/protocol sources are available;
- core algorithm, training objective, data/split, metric, and evaluation protocol are recoverable;
- license/access terms permit implementation;
- required compute is plausible or explicitly constrained;
- no requirement states “official implementation only.”

## Package layout

```text
resources/reproduction/<baseline>/
  README.md
  REIMPLEMENTATION_SPEC.md
  provenance.json
  assumptions.json
  paper_to_code_map.json
  configs/
  src/
  tests/
```

Static review and validation reports are written under `external_executor/report/phase_B/`.

## Specification content

Before code, define:

- canonical baseline identity;
- source paper/supplement/protocol versions;
- algorithm steps and equations;
- inputs, outputs, shapes, and semantics;
- losses/objectives;
- training loop and inference flow;
- data preprocessing and split;
- metric implementation and direction;
- hyperparameters explicitly stated by sources;
- unspecified details and chosen assumptions;
- fairness controls relative to ours and other baselines;
- validation and sanity-test plan;
- expected deviations and claim risk.

## Paper-to-code mapping

Map every defining mechanism to a code path and test. Separate:

- algorithmic behavior required by the source;
- engineering adapter or compatibility code;
- unresolved assumption;
- optional optimization.

Do not hide an assumption inside a default config.

## Labels

Use only:

- `executor_reimplementation`: implementation aims to match the recoverable source specification;
- `approximate_reproduction`: material details remain uncertain or protocol equivalence cannot be fully established.

Forbidden labels without external evidence:

- `official`;
- `author_implementation`;
- `exact_reproduction`;
- `protocol_equivalent`;
- `paper_result_reproduced`.

Phase B cannot claim result reproduction because it does not run the baseline experiment.

## Required validation

A candidate package needs:

- non-empty source implementation;
- independent config;
- evaluation integration point;
- sanity/unit tests for defining mechanisms;
- paper-to-code map;
- complete assumptions ledger;
- provenance and checksums;
- no forbidden label;
- review of algorithm and protocol fidelity;
- explicit approval scope.

The later `baseline-reproduction` skill must still execute, repair, and compare the baseline.

If the package cannot pass candidate validation or independent review, mark the requirement blocked or unavailable. Do not loop into another approximate implementation unless the root approves a scope or baseline decision.

## Stop rules

Mark unavailable and stop when:

- the defining mechanism cannot be distinguished from related methods;
- the training objective or evaluation protocol is missing in a claim-critical way;
- dataset/split cannot be legally or technically recovered;
- the metric cannot be reconstructed;
- assumptions would determine the main result;
- license/access forbids implementation or use;
- required changes would silently create a new method.
