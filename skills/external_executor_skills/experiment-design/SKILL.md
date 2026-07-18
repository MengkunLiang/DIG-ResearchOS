---
name: experiment-design
description: Convert the confirmed ResearchOS external-executor scope and Phase B resource readiness into a versioned, claim-bound, budget-feasible experiment plan with a locked protocol fingerprint, mechanism-linked ablations, an execution DAG, predeclared interpretation rules, and a deterministic Phase C readiness gate. Use when `research-execution` dispatches Phase C after resource readiness is `ready` or constrained `partial`, when the experiment plan is missing or stale, or when an approved claim boundary, protocol, resource, or diagnostic requirement changes. Do not implement or run experiments, change the research idea, fetch resources, select results after seeing outcomes, silently alter primary metrics or seeds, or overwrite sibling-owned result-pack sections.
---

# Experiment Design

Design the evidence package before implementation and execution. Start from claims and reviewer questions, lock the comparison protocol, then create only the experiments needed to test those claims within the approved resources and budget.

<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->
<!-- Filled during project Skill specialization. -->
<!-- PROJECT-SPECIFIC-GUIDANCE:END -->

## Establish paths and ownership

1. Locate the nearest directory containing both `project.yaml` and `external_executor/`; call it `<workspace>`.
2. Treat the directory containing this file as `<skill-dir>`.
3. Read before any write:
   - `<workspace>/external_executor/AGENTS.md`;
   - `<workspace>/external_executor/allowed_paths.txt`;
   - `<workspace>/external_executor/handoff_pack.json`;
   - `<workspace>/external_executor/result_pack.json#context_alignment`;
   - all Phase B sections in `result_pack.json`;
   - `<skill-dir>/references/experiment-plan-contract.md`;
   - `<skill-dir>/references/claim-evidence-design.md`;
   - `<skill-dir>/references/protocol-and-fingerprint-policy.md`;
   - `<skill-dir>/references/output-contract.md`.
4. Stop with `blocked` when context alignment is blocking, resource readiness makes the minimum loop infeasible, required Phase B sections are absent, or the Phase C output boundary cannot be determined.

Write only:

- `external_executor/report/experiment_design_preflight.json`;
- `external_executor/report/claim_evidence_matrix.json`;
- `external_executor/report/protocol_snapshot.json`;
- `external_executor/report/protocol_fingerprint.json`;
- `external_executor/report/protocol_change_impact.json` when comparing versions;
- `external_executor/experiment_plan.json`;
- deterministic validation and gate reports owned by this skill;
- `external_executor/report/experiment_design_report.json`;
- `result_pack.json#claim_evidence_matrix` and `result_pack.json#experiment_plan` through the narrow apply script.

Do not change executor status, run manifest, iteration decisions, resources, baseline reproduction, implementation, run, diagnosis, attribution, or packaging sections. Return control to `research-execution` after applying the report.

## Run deterministic preflight

Run:

```bash
python <skill-dir>/scripts/preflight_experiment_design.py --workspace <workspace> \
  --output external_executor/report/experiment_design_preflight.json
```

The preflight confirms:

- `context_alignment.status` is `pass` or non-blocking `mismatch`;
- `resource_readiness.status` is `ready` or constrained `partial`;
- the minimum experiment loop is feasible;
- resource requirement, baseline candidate, dataset, gap, and risk sections are present;
- claims or the central hypothesis are available;
- benchmark/protocol and budget inputs can be traced;
- all Phase C writes are allowed.

Warnings require explicit completion in the design artifacts. A blocker prevents planning from being approved, but the skill should still write an honest blocked report when possible.

## Build the claim–evidence matrix

Read `references/claim-evidence-design.md`, then run:

```bash
python <skill-dir>/scripts/build_claim_evidence_matrix.py --workspace <workspace> \
  --output external_executor/report/claim_evidence_matrix.json
```

For each claim, establish:

```text
claim
  -> reviewer question
  -> evidence needed
  -> planned experiment
  -> expected artifact
  -> interpretation boundary
```

A required claim must end in one of two states:

- `planned`, with one or more experiment IDs;
- `unsupported`, with an explicit reason and downstream claim restriction.

Do not create a generic experiment merely to fill a table. Every confirmatory experiment must answer a named reviewer question. Keep confirmatory, diagnostic, and exploratory roles distinct.

## Lock the protocol before creating formal experiments

Read `references/protocol-and-fingerprint-policy.md` and `references/statistical-and-reporting-policy.md`.

Build the protocol snapshot:

```bash
python <skill-dir>/scripts/build_protocol_snapshot.py --workspace <workspace> \
  --output external_executor/report/protocol_snapshot.json
```

Complete any unresolved fields using authorized project evidence, not domain guesses. The protocol must cover:

- benchmark, task, dataset version and split;
- preprocessing and data resource references;
- primary and secondary metrics, direction, units, and aggregation;
- baseline identities, resource references, configs, and fairness constraints;
- seeds, repeats, and non-cherry-picking selection policy;
- hyperparameter search and tuning fairness;
- evaluation scripts, uncertainty/statistics, failure and missing-run handling;
- compute, run, time, and monetary budgets;
- early-stop and reporting policies.

Calculate and write the fingerprint:

```bash
python <skill-dir>/scripts/fingerprint_protocol.py --workspace <workspace> \
  --protocol external_executor/report/protocol_snapshot.json \
  --output external_executor/report/protocol_fingerprint.json --write-back
```

Do not approve a formal plan while protocol fields required for fairness or interpretation remain unresolved.

When revising an existing protocol, compare versions:

```bash
python <skill-dir>/scripts/compare_protocol_versions.py --workspace <workspace> \
  --old <old-protocol.json> --new external_executor/report/protocol_snapshot.json \
  --plan external_executor/experiment_plan.json \
  --output external_executor/report/protocol_change_impact.json
```

Dataset, split, preprocessing, primary metric, direction, aggregation, baseline/config, seed policy, evaluation, statistics, or tuning-policy changes are material. Generate a new version and return invalidation guidance to the root; do not silently mix results across material protocol versions.

## Build the versioned experiment plan

Read `references/experiment-plan-contract.md`, `references/budget-and-dag-policy.md`, and `references/plan-review-checklist.md`, then run:

```bash
python <skill-dir>/scripts/build_experiment_plan.py --workspace <workspace> \
  --claims external_executor/report/claim_evidence_matrix.json \
  --protocol external_executor/report/protocol_snapshot.json \
  --fingerprint external_executor/report/protocol_fingerprint.json \
  --output external_executor/experiment_plan.json
```

The generated scaffold must be reviewed and completed. It should cover, as required by the confirmed scope:

- required baseline reproduction or repair prerequisites;
- ours smoke and, when justified, small-scale validation;
- main formal comparison for each required claim;
- mechanism-linked ablations;
- module attribution experiments;
- claim-relevant robustness, sensitivity, failure, efficiency, or diagnostic work.

Each experiment records at least:

- stable experiment ID and versioned plan reference;
- `confirmatory`, `diagnostic`, or `exploratory` role;
- run type and experiment kind;
- claim and reviewer-question mapping;
- dataset, split, variants, baseline/resource refs, metric and direction;
- seed/repeat policy and protocol fingerprint;
- preconditions, dependencies, expected artifacts, and cost estimate;
- predeclared decision and positive/negative/inconclusive interpretation rules;
- propagated resource risks and claim constraints.

An ablation must map to a named mechanism and explicit target module IDs. Materialize an `attribution_contract` with a full/reference variant, an intervention variant, exact module states, reference IDs, and pairing dimensions. Removal alone is not sufficient when replacement, neutralization, capacity matching, or a controlled diagnostic better tests the mechanism.

Treat the ablation as runnable only when preprocessing and fairness fingerprints, metric directions, seeds/repeats, and both variant contracts are resolved. Downstream loop completion requires every planned pair to survive into terminal run records; an experiment ID with only one completed variant is incomplete.

## Build a feasible execution DAG and budget

Use `references/budget-and-dag-policy.md`.

Declare:

- max refinement rounds;
- max runs/trials, wall-clock, GPU-hours, and cost;
- estimated cost per experiment and total plan cost;
- priority and dependencies;
- valid parallel groups;
- experiment-level and project-level early-stop rules.

The DAG describes evidence prerequisites, not child-skill invocation. Child routing remains owned by `research-execution`.

Do not mark experiments parallel when one consumes another's output, when they contend for an exclusive resource, or when their fairness depends on a shared decision that has not been locked.

## Perform independent plan review

Validate the DAG:

```bash
python <skill-dir>/scripts/validate_plan_dag.py --workspace <workspace> \
  --plan external_executor/experiment_plan.json \
  --output external_executor/report/experiment_plan_dag_validation.json
```

Validate the plan contract:

```bash
python <skill-dir>/scripts/validate_experiment_plan.py --workspace <workspace> \
  --plan external_executor/experiment_plan.json \
  --output external_executor/report/experiment_plan_validation.json
```

Review against `references/plan-review-checklist.md`. The review must reject:

- required claims without planned evidence or an unsupported declaration;
- formal experiments with unresolved protocol fields;
- baseline experiments without approved resource references;
- ablations without mechanism mapping or controlled variants;
- post-hoc metric, seed, or result-selection freedom;
- missing negative or inconclusive interpretation rules;
- unpropagated resource approximation, fairness, license, or access risk;
- cyclic dependencies or a plan exceeding the declared budget.

Fix the plan rather than bypassing validation.

## Compute the Phase C gate

Run:

```bash
python <skill-dir>/scripts/compute_design_gate.py --workspace <workspace> \
  --plan external_executor/experiment_plan.json \
  --plan-validation external_executor/report/experiment_plan_validation.json \
  --dag-validation external_executor/report/experiment_plan_dag_validation.json \
  --output external_executor/report/experiment_design_gate.json --write-back
```

Gate outcomes:

- `ready`: required claims, formal protocol, resources, DAG, and budget are complete;
- `partial`: the minimum loop is valid, but explicit resource approximations, optional evidence gaps, or claim constraints remain;
- `blocked`: a required claim, formal protocol, baseline binding, budget, DAG, or minimum-loop prerequisite is invalid.

`partial` never means that missing evidence may be silently ignored. Its constraints must be carried into iteration planning, execution, diagnosis, and T7 claim limits.

## Assemble, validate, and apply narrowly

Run:

```bash
python <skill-dir>/scripts/assemble_experiment_design_report.py --workspace <workspace> \
  --output external_executor/report/experiment_design_report.json

python <skill-dir>/scripts/validate_experiment_design_report.py --workspace <workspace> \
  --report external_executor/report/experiment_design_report.json

python <skill-dir>/scripts/apply_experiment_design_report.py --workspace <workspace> \
  --report external_executor/report/experiment_design_report.json
```

The apply script updates only:

```text
result_pack.json#claim_evidence_matrix
result_pack.json#experiment_plan
```

If validation fails, repair the component artifact and rerun validation. Do not manually overwrite sibling sections.

## Return to the root

Return a compact child result:

```text
child_skill=experiment-design
status=complete|partial|blocked|failed
design_readiness=ready|partial|blocked
plan=external_executor/experiment_plan.json
protocol_fingerprint=<sha256>
plan_version=<n>
protocol_version=<n>
blocking_issues=<ids>
constraints=<items>
recommended_next_action=continue_to_phase_d|continue_to_phase_d_with_constraints|return_to_experiment_design_or_human_review
```

The recommendation is advisory. `research-execution` owns checkpointing, manifest registration, executor status, iteration creation, stale-result marking, and next dispatch.

## Evidence and safety rules

- Plan from claims, not from desired tables or hoped-for outcomes.
- Predeclare primary metrics, direction, seed/repeat policy, aggregation, and interpretation rules before formal results exist.
- Do not choose seeds, thresholds, baselines, subsets, or metrics after seeing outcomes unless the run is explicitly exploratory and cannot support the original confirmatory claim.
- Preserve diagnostic and exploratory value without relabeling it as confirmatory evidence.
- Do not invent dataset, baseline, compute, statistical, or implementation details. Mark unresolved fields and block the relevant gate.
- Bind every baseline experiment to an approved Phase B resource or an explicit unavailable/replacement decision.
- Bind every ablation to a mechanism and every formal experiment to one protocol fingerprint.
- Do not implement, execute, diagnose, attribute, or package results in this skill.
- Do not change the central hypothesis, benchmark, task, contribution type, required baseline, or claim boundary. Emit a blocker or scope-change need and return to the root.

## Resource map

- `references/experiment-plan-contract.md`: experiment and plan schema, roles, fields, versioning, and minimum package.
- `references/claim-evidence-design.md`: claim-first planning and reviewer-question mapping.
- `references/protocol-and-fingerprint-policy.md`: immutable protocol contents, versioning, and material-change rules.
- `references/budget-and-dag-policy.md`: cost accounting, dependency graph, parallelism, and early stop.
- `references/statistical-and-reporting-policy.md`: metric, seed, uncertainty, aggregation, and anti-cherry-picking rules.
- `references/plan-review-checklist.md`: independent Phase C review and gate criteria.
- `references/output-contract.md`: report shapes, narrow ownership, and root return contract.
- `scripts/preflight_experiment_design.py`: validate Phase C prerequisites and calculate input fingerprint.
- `scripts/build_claim_evidence_matrix.py`: normalize claims and evidence obligations.
- `scripts/build_protocol_snapshot.py`: assemble a versioned protocol snapshot.
- `scripts/fingerprint_protocol.py`: calculate deterministic protocol and component hashes.
- `scripts/build_experiment_plan.py`: scaffold the claim-bound plan and execution DAG.
- `scripts/validate_plan_dag.py`: reject unknown dependencies, cycles, and invalid parallel groups.
- `scripts/validate_experiment_plan.py`: enforce plan, protocol, budget, evidence, and anti-post-hoc rules.
- `scripts/compare_protocol_versions.py`: classify protocol changes and invalidation impact.
- `scripts/compute_design_gate.py`: derive `ready`, `partial`, or `blocked` from validated evidence.
- `scripts/assemble_experiment_design_report.py`: build the durable child report.
- `scripts/validate_experiment_design_report.py`: validate report and gate consistency.
- `scripts/apply_experiment_design_report.py`: atomically update only owned result-pack sections.
