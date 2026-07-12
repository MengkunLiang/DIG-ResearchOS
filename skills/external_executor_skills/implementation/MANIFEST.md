# implementation Skill Manifest

## Purpose

`implementation` is the Phase D2 Builder skill for ResearchOS. It converts one root-approved iteration delta and implementation specification into isolated, reviewable code/config/test artifacts. It stops at `ready_for_review`; it never approves itself or runs formal experiments.

## Directory tree

```text
implementation/
├── SKILL.md
├── MANIFEST.md
├── references/
│   ├── implementation-policy.md
│   ├── change-contract.md
│   ├── worktree-and-patch-policy.md
│   ├── tdd-and-verification.md
│   ├── research-code-contract.md
│   ├── drift-and-escalation.md
│   ├── secure-coding-and-secrets.md
│   └── output-contract.md
├── scripts/
│   ├── _common.py
│   ├── preflight_implementation.py
│   ├── build_change_contract.py
│   ├── prepare_worktree.py
│   ├── run_verification.py
│   ├── record_tdd_cycle.py
│   ├── generate_patch_bundle.py
│   ├── scan_change_scope.py
│   ├── validate_module_mapping.py
│   ├── initialize_implementation_report.py
│   ├── compute_implementation_gate.py
│   ├── validate_implementation_report.py
│   └── apply_implementation_report.py
└── tests/
    └── test_implementation_scripts.py
```

## Main file

### `SKILL.md`

Defines:

- use/do-not-use boundary;
- owned read/write paths;
- prerequisites and deterministic preflight;
- immutable change contract;
- before/worktree isolation;
- vertical-slice TDD and verification;
- research module/config/ablation traceability;
- patch and scope inspection;
- drift and baseline invalidation classification;
- deterministic implementation gate;
- narrow result-pack apply and root return.

## References

### `references/implementation-policy.md`

Defines the Builder role, prerequisites, permitted change classes, non-goals, Builder–Reviewer separation, resume semantics, bounded repair loops, and explicit no-op behavior.

### `references/change-contract.md`

Defines `implementation_change_contract.v1`, approved change records, module contracts, verification items, readiness criteria, and stable identity.

### `references/worktree-and-patch-policy.md`

Defines read-only `before/`, editable `worktree/`, exclusions, symlink policy, patch evidence, deletion rules, patch reproducibility, and non-commit Git behavior.

### `references/tdd-and-verification.md`

Defines agreed test seams, vertical slices, red–green evidence, exceptions, allowed/forbidden verification classes, freshness, mandatory checks, and anti-gaming rules.

### `references/research-code-contract.md`

Defines module-to-code traceability, entrypoint/config conventions, ablation semantics, baseline adapters, determinism, logging/metrics, checkpoints, data safety, and engineering-only additions.

### `references/drift-and-escalation.md`

Defines contribution, protocol, fairness, and baseline-reproduction impact; escalation triggers; and scope-change proposal fields.

### `references/secure-coding-and-secrets.md`

Defines safe argv execution, dependency authority, secret hygiene, dangerous patterns, path/symlink handling, binary/generated-file policy, data safety, and network restrictions.

### `references/output-contract.md`

Defines `implementation_report.v1`, implementation/module records, gate consistency, Builder/Reviewer boundary, artifact references, narrow result-pack mapping, and child return.

## Scripts

### `_common.py`

Workspace discovery, atomic JSON, allowed-path enforcement, fingerprints, stable IDs, active iteration/spec discovery, tree manifests, glob matching, safe environment, symlink checks, and artifact refs.

### `preflight_implementation.py`

Validates controls, alignment, resource readiness, experiment plan, protocol/fairness fingerprints, active iteration, implementation spec, scope requests, base source, schemas, and writable paths.

### `build_change_contract.py`

Normalizes root/spec inputs into one deterministic, immutable implementation change contract.

### `prepare_worktree.py`

Copies authorized source into read-only `before/` and editable `worktree/`, excludes generated/heavy content, rejects symlinks, and writes provenance.

### `run_verification.py`

Runs only contract-declared bounded verification commands using argv, sanitized environment, timeout/process-group handling, durable stdout/stderr, expected-output checks, and worktree fingerprinting.

### `record_tdd_cycle.py`

Links a valid expected-failure red record to a valid expected-success green record for the same behavior and implementation.

### `generate_patch_bundle.py`

Generates changed-file metadata, before/after checksums, line statistics, binary manifest, sensitivity hints, and unified text diff.

### `scan_change_scope.py`

Checks changed operations against approved targets and protected paths; detects heuristic secrets, dangerous patterns, dependency changes, binary/generated files, sensitive deletions, and protocol-facing paths.

### `validate_module_mapping.py`

Validates module IDs, code/test paths, config keys, ablation mappings, implementation status, and the prohibition on Builder empirical-support claims.

### `initialize_implementation_report.py`

Creates a complete report envelope and imports available patch, scope, mapping, TDD, verification, worktree, and artifact evidence.

### `compute_implementation_gate.py`

Derives `ready_for_review`, `needs_fix`, or `blocked` from contract completion, implementation records, patch/scope, module mapping, verification freshness, risks, and drift.

### `validate_implementation_report.py`

Validates schema, enum and gate consistency, change coverage, artifact paths, and the Builder/Reviewer authority boundary.

### `apply_implementation_report.py`

Atomically updates only `result_pack.implementations`, preserving all sibling sections and other implementation versions.

## Tests

### `tests/test_implementation_scripts.py`

Covers:

- preflight, contract generation, and source isolation;
- read-only before snapshot;
- real red/green/final subprocess verification;
- durable verification evidence and TDD linkage;
- module mapping validation;
- structured patch generation;
- scope scan;
- deterministic `ready_for_review` gate;
- report validation and narrow apply;
- unrelated result-pack preservation;
- secret/scope primitives;
- symlink detection.

## External dependencies

Runtime scripts use only the Python standard library. Project-specific verification commands may require tools already available in the target executor, but this skill does not install them.
