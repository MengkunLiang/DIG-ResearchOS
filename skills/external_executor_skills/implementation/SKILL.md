---
name: implementation
description: Implement an approved ResearchOS method, baseline adapter, bounded repair, training/evaluation entrypoint, configuration, ablation switch, diagnostic switch, logging path, or test in a controlled versioned worktree. Use when `research-execution` dispatches Phase D2 because an active iteration plan contains an approved implementation delta and an implementation specification or bounded repair contract is ready. Produce code, config, tests, patch evidence, module-to-code and ablation mappings, fresh verification records, scope/drift findings, and an `ready_for_review`, `needs_fix`, or `blocked` implementation gate. Do not redesign the research idea, self-approve the implementation, run formal experiments, change the locked protocol, install or download without authority, edit original resources, or implement an unapproved scope change.
---

# Implementation

Act as the Builder for one approved ResearchOS iteration. Convert a bounded implementation contract into reviewable code and evidence. The next skill, `code-and-protocol-review`, owns approval.

<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->
<!-- Filled during project Skill specialization. -->
<!-- PROJECT-SPECIFIC-GUIDANCE:END -->

## Establish paths and ownership

1. Locate the nearest directory containing both `project.yaml` and `external_executor/`; call it `<workspace>`.
2. Treat the directory containing this file as `<skill-dir>`.
3. Read before writing:
   - `<workspace>/external_executor/AGENTS.md`;
   - `<workspace>/external_executor/allowed_paths.txt`;
   - `<workspace>/external_executor/result_pack.json#context_alignment`;
   - `<workspace>/external_executor/result_pack.json#resource_readiness`;
   - `<workspace>/external_executor/result_pack.json#experiment_plan`;
   - the active root-owned iteration plan;
   - the current implementation specification or approved bounded repair record;
   - `<skill-dir>/references/implementation-policy.md`;
   - `<skill-dir>/references/change-contract.md`;
   - `<skill-dir>/references/output-contract.md`.
4. Stop with `blocked` if no active approved delta exists, the implementation specification is missing, a major scope change is unresolved, the protocol/fairness fingerprint is missing, the base source cannot be identified, or writable boundaries cannot be determined.

Write only:

- `external_executor/implementation_preflight.json`;
- `external_executor/implementation_change_contract.json`;
- `external_executor/implementation_report.json`;
- versioned baseline adapter and method implementation deployments under `external_executor/expr/implementation/`;
- `result_pack.json#implementations` through the narrow apply script.

Do not change root-owned status, manifest, budget, iteration decisions, experiment plan, protocol fingerprint, baseline reproduction records, review verdicts, experiment runs, diagnoses, or sibling-owned sections. Return control to `research-execution` after applying the report.

## Run deterministic preflight

```bash
python <skill-dir>/scripts/preflight_implementation.py --workspace <workspace> \
  --output external_executor/implementation_preflight.json
```

The preflight confirms:

- context alignment and resource readiness are non-blocking;
- the active iteration plan authorizes implementation;
- the implementation spec or bounded repair contract is present;
- protocol and fairness fingerprints are available;
- no unresolved major scope-change request exists;
- the base source and write roots are valid;
- relevant schemas are supported.

A blocker prevents code changes. A warning must be propagated into the change contract or report.

## Freeze the change contract

Read `references/change-contract.md`, then run:

```bash
python <skill-dir>/scripts/build_change_contract.py --workspace <workspace> \
  --output external_executor/implementation_change_contract.json
```

Before editing code, ensure the contract identifies:

- implementation and iteration IDs;
- base source and immutable input fingerprint;
- approved change IDs and exact target paths;
- intended module, adapter, config, entrypoint, switch, logging, or test changes;
- linked implementation-spec items and affected experiments;
- must-preserve behavior and forbidden changes;
- module interfaces, config keys, ablation/diagnostic switches, and test obligations;
- verification commands and expected outcomes;
- protocol, fairness, baseline-reproduction, and claim impact;
- permitted dependency changes, if any.

Do not modify the contract to legitimize an unplanned implementation discovered during coding. Escalate instead.

## Prepare an isolated, versioned worktree

Read `references/worktree-and-patch-policy.md`, then run:

```bash
python <skill-dir>/scripts/prepare_worktree.py --workspace <workspace> \
  --contract external_executor/implementation_change_contract.json
```

The implementation root is:

```text
external_executor/expr/implementation/<iteration-id>/<implementation-id>/
  before/
  worktree/
  verification/
  mappings/
  patches/
  implementation_provenance.json
```

`before/` is a read-only snapshot. Edit only this package's `worktree/` under `external_executor/expr/implementation/`. Never mutate acquired resources, baseline source snapshots, another `external_executor/expr/` deployment, or another iteration's evidence in place. Reject symlink escapes and preserve excluded/generated-file rules in provenance.

## Inspect before editing

Identify the smallest code surface that satisfies the contract:

- existing module boundaries and extension points;
- configuration conventions;
- training/evaluation interfaces;
- dataset, split, metric, seed, checkpoint, and output plumbing;
- baseline adapter boundaries;
- existing tests and test seams;
- logging and machine-readable metric outputs;
- dependency manifests and compatibility constraints.

Prefer wrappers, adapters, and local modules over invasive changes to third-party source. Do not copy whole repositories into the patch to avoid understanding their interfaces.

## Implement in vertical slices with evidence

Read `references/tdd-and-verification.md` and `references/research-code-contract.md`.

For behavior-changing work, use this sequence:

1. define the observable behavior and test seam;
2. add or identify a focused test that fails for the intended reason;
3. record the red verification;
4. implement the minimum approved vertical slice;
5. record the green verification;
6. refactor without changing the contract;
7. run targeted integration and regression checks.

A documented TDD exception is permitted for pure declarative config, generated scaffolding, or a change whose existing test already fails in the required way. It is not permission to skip verification.

Implement only approved classes of change:

- ours modules, losses, training/inference flow, and interfaces;
- baseline adapters that preserve baseline semantics;
- training/evaluation entrypoints and controlled wrappers;
- configuration and explicit protocol plumbing;
- ablation and diagnostic switches;
- deterministic seed, checkpoint, logging, and metric-output plumbing;
- unit, interface, shape, serialization, config, and integration tests;
- bounded compatibility or bug fixes authorized by the iteration plan.

Do not silently add a new research mechanism, stronger pretraining, extra data, favorable baseline-only defaults, new metric, changed split, broader hyperparameter search, or an undocumented training trick.

## Preserve research traceability

Each implemented research module must map to:

- implementation-spec/module ID;
- code path and public interface;
- config keys and defaults;
- input/output semantics;
- test paths;
- ablation or diagnostic switch;
- affected experiment IDs;
- implementation status and known limitation.

Each non-contribution engineering addition must be labeled separately. It must not be promoted later as a research contribution.

Generate and validate the mapping:

```bash
python <skill-dir>/scripts/validate_module_mapping.py --workspace <workspace> \
  --contract external_executor/implementation_change_contract.json \
  --mapping <implementation-root>/mappings/module_mapping.json \
  --output <implementation-root>/mappings/module_mapping_validation.json
```

## Verify without running experiments

Use `run_verification.py` for bounded engineering checks:

```bash
python <skill-dir>/scripts/run_verification.py --workspace <workspace> \
  --contract external_executor/implementation_change_contract.json \
  --verification-id <verification-id> \
  --phase red|green|final \
  --expect failure|success
```

Allowed checks include unit, interface, config, import, shape, serialization, type, lint, build, and bounded integration checks defined in the contract.

This skill must not run training, formal evaluation, benchmark comparison, large inference jobs, dataset download, package installation, or network-backed commands. `experiment-run` owns experiment execution. A test that invokes a tiny synthetic tensor or fixture is engineering evidence only.

Record red/green linkage when applicable:

```bash
python <skill-dir>/scripts/record_tdd_cycle.py \
  --red <red-verification.json> --green <green-verification.json> \
  --output <implementation-root>/verification/<cycle-id>.json
```

Never claim a test passes from memory or a previous code version. Final verification evidence must be fresh for the final worktree fingerprint.

## Generate patch and inspect scope

```bash
python <skill-dir>/scripts/generate_patch_bundle.py --workspace <workspace> \
  --contract external_executor/implementation_change_contract.json

python <skill-dir>/scripts/scan_change_scope.py --workspace <workspace> \
  --contract external_executor/implementation_change_contract.json \
  --output <implementation-root>/patches/scope_scan.json
```

The patch bundle must record added, modified, deleted, and binary files; before/after hashes; line statistics where meaningful; config, test, dependency, and protocol-sensitive changes; and a unified text diff.

Classify every changed path against the approved contract. An unauthorized path, embedded secret, protected-file edit, path escape, or unapproved dependency/protocol change is blocking.

## Assess drift and downstream invalidation

Read `references/drift-and-escalation.md` and classify:

```text
contribution_drift: none | minor | major
protocol_impact: none | nonmaterial | material
fairness_impact: none | controlled | uncertain | material
baseline_reproduction_impact: none | adapter_only | invalidates_selected | invalidates_all
```

Return `blocked` before further implementation when the work would:

- replace or materially alter the core mechanism;
- change the task, benchmark, dataset, split, primary metric, or contribution type;
- drop or replace a required baseline;
- introduce extra data/pretraining or asymmetric tuning;
- broaden dependency/network/system authority;
- require a material protocol or fairness change;
- transform the method into an unreviewed baseline variant.

Do not implement a major change first and document it afterward.

## Build, compute, validate, and apply the report

Initialize a complete report envelope:

```bash
python <skill-dir>/scripts/initialize_implementation_report.py --workspace <workspace> \
  --contract external_executor/implementation_change_contract.json \
  --output external_executor/implementation_report.json
```

Complete the mappings, verification records, patch summary, scope findings, drift assessment, risks, and artifact references. Then run:

```bash
python <skill-dir>/scripts/compute_implementation_gate.py \
  --report <workspace>/external_executor/implementation_report.json --write-back

python <skill-dir>/scripts/validate_implementation_report.py --workspace <workspace> \
  --report external_executor/implementation_report.json

python <skill-dir>/scripts/apply_implementation_report.py --workspace <workspace> \
  --report external_executor/implementation_report.json
```

Gate outcomes:

- `ready_for_review`: the approved delta is implemented, required mappings and fresh verification are complete, the patch is in scope, and no blocking drift exists;
- `needs_fix`: implementation is incomplete or a bounded test/mapping/scope issue remains repairable inside the approved contract;
- `blocked`: authority, path, secret, dependency, scope, protocol, fairness, or major-drift issue prevents safe review.

`ready_for_review` is not Reviewer approval and grants no run authority.

## Return to the root

Return:

```text
child_skill=implementation
status=complete|partial|blocked|failed
implementation_gate=ready_for_review|needs_fix|blocked
implementation_id=<id>
iteration_id=<id>
report=external_executor/implementation_report.json
implementation_root=external_executor/expr/implementation/<iteration>/<implementation>/
patch_bundle=<path>
module_mapping=<path>
changed_paths=<paths-or-count>
contribution_drift=none|minor|major
protocol_impact=none|nonmaterial|material
fairness_impact=none|controlled|uncertain|material
baseline_reproduction_impact=none|adapter_only|invalidates_selected|invalidates_all
blocking_issues=<ids>
recommended_next_action=continue_to_code_and_protocol_review|repair_implementation|human_review|stop_and_report
```

The recommendation is advisory. `research-execution` owns checkpointing, manifest registration, status, routing, budget, and scope-change decisions.

## Evidence and safety rules

- A patch and passing tests are implementation evidence, not experimental evidence.
- Do not self-assign `review_status=pass` or `approved_for=formal`.
- Preserve failed verification, superseded attempts, and repair history.
- Do not disable tests, weaken assertions, skip validation, or alter expected outputs merely to obtain green status.
- Do not execute third-party installers, downloaders, notebooks, containers, lifecycle hooks, or unreviewed shell scripts.
- Do not expose secrets to source code, configs, logs, subprocesses, or patches.
- Do not modify original resources or sibling artifacts.
- Do not claim a module is empirically supported; only record that it is implemented and test-covered.
- Do not create Git commits or push branches unless separately and explicitly authorized.

## Resource map

- `references/implementation-policy.md`: prerequisites, ownership, permitted changes, non-goals, resume, and repair behavior.
- `references/change-contract.md`: immutable approved-delta contract and field requirements.
- `references/worktree-and-patch-policy.md`: before/worktree isolation, exclusions, patch and provenance rules.
- `references/tdd-and-verification.md`: test seams, red-green evidence, command classes, and fresh-evidence rules.
- `references/research-code-contract.md`: module/config/entrypoint/ablation/logging requirements for research code.
- `references/drift-and-escalation.md`: drift, fairness, protocol, baseline invalidation, and escalation taxonomy.
- `references/secure-coding-and-secrets.md`: command, dependency, secret, symlink, data, and generated-file controls.
- `references/output-contract.md`: report, gate, result-pack mapping, and return contract.
- `scripts/preflight_implementation.py`: validate prerequisites, active plan/spec, controls, and paths.
- `scripts/build_change_contract.py`: compile the root/spec delta into a deterministic implementation contract.
- `scripts/prepare_worktree.py`: create read-only before snapshot and editable worktree.
- `scripts/run_verification.py`: execute only declared bounded verification commands with durable evidence.
- `scripts/record_tdd_cycle.py`: link red and green records for the same behavior.
- `scripts/generate_patch_bundle.py`: create structured before/after patch evidence and unified diff.
- `scripts/scan_change_scope.py`: detect unauthorized, sensitive, secret, dependency, and protocol changes.
- `scripts/validate_module_mapping.py`: validate module-to-code/config/test/ablation traceability.
- `scripts/initialize_implementation_report.py`: create a complete report envelope.
- `scripts/compute_implementation_gate.py`: derive the implementation gate deterministically.
- `scripts/validate_implementation_report.py`: enforce report and Builder/Reviewer boundary.
- `scripts/apply_implementation_report.py`: update only `result_pack.implementations` atomically.
