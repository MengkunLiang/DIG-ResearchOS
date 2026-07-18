# Implementation Policy

## Purpose

The implementation skill is a bounded Builder. It converts one root-approved iteration delta and one implementation specification into reviewable code and evidence. It does not own research redesign, protocol approval, experiment execution, result interpretation, or final workflow routing.

## Preconditions

Implementation may start only when all are present and non-blocking:

- `result_pack.context_alignment`;
- `result_pack.resource_readiness`;
- a versioned experiment plan with protocol and fairness fingerprints;
- an active root-owned iteration plan;
- an implementation specification or bounded repair contract;
- identifiable base source;
- writable paths under `allowed_paths.txt`;
- no unresolved major scope-change request affecting this delta.

A missing prerequisite is not an invitation to infer one.

## Owned outputs

The skill owns one versioned implementation package and these child artifacts:

```text
external_executor/report/implementation_preflight.json
external_executor/report/implementation_change_contract.json
external_executor/report/implementation_report.json
external_executor/expr/implementation/**
result_pack.implementations
```

The root owns global state, manifest registration, iteration decisions, budget, scope gates, and next dispatch. The Reviewer owns approval. The run skill owns experiments.

## Permitted change classes

- ours module, loss, training, inference, or interface implementation;
- baseline adapter or wrapper that preserves baseline semantics;
- explicit config and command-line plumbing;
- training/evaluation entrypoints;
- seed, checkpoint, logging, metric-output, and output-directory plumbing;
- ablation and diagnostic switches;
- unit, interface, shape, serialization, config, and bounded integration tests;
- approved compatibility and bug repairs;
- approved diagnostic instrumentation.

## Non-goals

Do not:

- change the central hypothesis, task, benchmark, contribution type, dataset split, or primary metric;
- replace or drop a required baseline;
- create a new method while claiming it is an implementation detail;
- decide that a protocol or fairness change is acceptable;
- run training or formal evaluation;
- use test results as empirical claim support;
- approve the implementation;
- install dependencies or download assets without separate authority;
- mutate original resources, snapshots, or previous implementation evidence;
- commit, push, or open a pull request without explicit authorization.

## Builder–Reviewer separation

The Builder may report:

- what changed;
- which contract item it implements;
- what tests ran and their raw outcomes;
- which paths and fingerprints changed;
- known limitations and drift risks;
- a deterministic `ready_for_review`, `needs_fix`, or `blocked` gate.

The Builder may not report:

```text
review_status=pass
approved_for=smoke|small_scale|formal
formal_run_ready=true
protocol_approved=true
fairness_approved=true
```

Those fields belong to `code-and-protocol-review` or the root.

## Resume and attempts

One implementation ID identifies one approved semantic delta. Re-running the same ID may repair its worktree while preserving:

- prior verification records;
- prior patch summaries;
- failed TDD cycles;
- scope findings;
- report history where retained by the project.

A changed iteration plan, implementation spec, protocol fingerprint, fairness fingerprint, or base source creates a new input fingerprint and normally a new implementation version. Do not overwrite a package whose inputs no longer match.

## Repair behavior

`needs_fix` may loop within this skill only when the repair remains inside the frozen contract. Examples:

- a focused unit test fails;
- a required mapping is incomplete;
- a changed path is accidentally outside the approved glob but can be reverted;
- a logging/config interface is incomplete;
- a test fixture needs a non-semantic correction.

Return to the root when repair would change research semantics, protocol, fairness, resource authority, dependencies, or approved paths.

## No-op implementation

A no-op is valid only when the contract explicitly authorizes it, for example when inspection proves the requested behavior already exists. The report must include evidence, unchanged fingerprints, and a reason. An empty patch without explicit no-op authority is incomplete.
