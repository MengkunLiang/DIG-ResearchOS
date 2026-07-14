# Reboost protocol

## Contents

1. Purpose and ownership
2. Input contract
3. Source precedence
4. Evidence and provenance
5. Reconciliation procedure
6. Method-intent compilation
7. Completion gates
8. Failure handling

## 1. Purpose and ownership

Research reboost owns the transformation from Pre-T5 research artifacts into `external_executor/handoff_pack.json`. It does not own project-specific skill compilation, resource acquisition, code implementation, experiment execution, result interpretation, realized-method generation, or writer prose.

Reboost and Method Intent Drafting are one module with one output. The method-intent pass consumes the already reconciled context and cannot independently override it.

## 2. Input contract

Required files:

| Source role | Project-relative path | Primary decisions |
| --- | --- | --- |
| project | `project.yaml` | project identity, task, user constraints |
| synthesis | `literature/synthesis.md` | field synthesis, method families, research gap |
| synthesis workbench | `literature/synthesis_workbench.json` | structured literature evidence and confidence |
| domain map | `literature/domain_map.json` | domain and bridge relationships |
| comparison table | `literature/comparison_table.csv` | comparable methods, modules, datasets, metrics |
| hypotheses | `ideation/hypotheses.md` | central hypothesis and mechanism candidates |
| experiment plan | `ideation/exp_plan.yaml` | planned datasets, metrics, protocols, ablations |
| idea scorecard | `ideation/idea_scorecard.yaml` | idea quality, feasibility, weaknesses |
| risks | `ideation/risks.md` | scientific and execution risks |
| novelty audit | `ideation/novelty_audit.md` | nearest work, required baselines, novelty and claim boundaries |

Optional backtracking sources:

- `literature/deep_read_notes/`
- `literature/shallow_read_notes/`
- `resources/`
- `user_seeds/seed_external_resources.jsonl`
- `user_seeds/bridge_domains.yaml`
- `ideation/hypothesis_brief.yaml`
- `ideation/selected/t45_search_targets.json`

Read optional sources only to close a named evidence gap. Abstract-only notes may motivate retrieval or mark uncertainty, but cannot establish implementation details or strong mechanism claims.
Pre-Novelty selection files preserve Candidate lineage and the scope of the T4.5 review. They are trace context only: they never replace the post-T4.5 `hypotheses.md`, `exp_plan.yaml`, or novelty audit as authority for an executable handoff.

## 3. Source precedence

Precedence is decision-specific, not a universal trust ranking:

1. Explicit current user constraints and `project.yaml` control project scope and authorization.
2. `novelty_audit.md` controls required baselines, nearest-work distinctions, novelty ceilings, and must-not-claim boundaries.
3. `exp_plan.yaml` controls protocol details, datasets, metrics, seeds, and planned tests where consistent with higher constraints.
4. `hypotheses.md` controls the initial central hypothesis and intended mechanism; `idea_scorecard.yaml` and `risks.md` qualify feasibility and risks.
5. Literature synthesis artifacts control field background, prior mechanisms, and comparison evidence.
6. Optional notes and seeds provide supporting detail or retrieval hints, not automatic authority.

Never erase a conflict after applying precedence. Record the competing statements, selected resolution, rationale, affected fields, and whether human review is required in `known_context_mismatches`.

If two sources at the same authority materially disagree and no explicit rule resolves them, set `generation_status` to `needs_review` or `blocked`.

## 4. Evidence and provenance

Use project-relative source paths. Assign each source a stable `source_id`, such as `SRC_PROJECT`, `SRC_EXP_PLAN`, or `SRC_NOVELTY`.

Every decision-bearing object should contain `source_refs`. A source reference has:

- `source_id`: an ID in `source_manifest`;
- `locator`: heading, key path, table row, or line/page range;
- `note`: what the cited location supports;
- `support_type`: `direct`, `reconciled`, or `inferred`.

Use `inferred` only for conservative connections. If an inference changes scope, contribution type, required baselines, or the central mechanism, it requires human review and cannot appear in a completed pack.

Hash available sources with SHA-256. Hashes establish which source version was compiled; they do not prove semantic correctness.

## 5. Reconciliation procedure

Perform these passes in order:

1. Scope pass: determine task, setting, target outputs, constraints, and exclusions.
2. Hypothesis pass: isolate the central hypothesis, assumptions, rationale, and falsification conditions.
3. Novelty pass: extract nearest work, required baselines, distinguishing mechanism, claim ceiling, and prohibited claims.
4. Protocol pass: extract datasets, metrics, comparisons, seeds, ablations, robustness, efficiency, and failure tests.
5. Risk pass: identify infeasibility, data leakage, fairness, compute, resource, and contribution-drift risks.
6. Cross-source pass: compare the novelty audit, experiment plan, hypotheses, comparison table, and scorecard. Record all material mismatches.
7. Execution pass: convert decisions into stable matrices, experiments, gates, budgets, paths, and handoff requirements.

Do not reduce the result to prose. The matrices and ID relationships are the executable part of the contract.

## 6. Method-intent compilation

Compile method intent only after context reconciliation.

For every module, define classification, intended role, mechanism, inputs, outputs, dependencies, implementation constraints, linked claims, and planned ablations. A core module must be supported by the central hypothesis or novelty distinction. A candidate module must remain removable without silently changing the central contribution. A supporting module must not be promoted as a research contribution.

The algorithm flow is an intended sequence, not verified code. Allowed refinements describe changes the executor may make without changing the contribution. Forbidden silent changes require review and include at least:

- replacing the core mechanism;
- dropping a required baseline;
- changing task, benchmark, or contribution type;
- weakening a fairness or evaluation constraint;
- treating an engineering optimization as the paper contribution.

Each mechanism should map to an ablation or diagnostic with observations expected under support and non-support. If no feasible test exists, record an unresolved item and lower the related claim ceiling.

The initial framework sketch guides implementation only. It must declare `must_not_be_used_directly_by_t8=true`.

## 7. Completion gates

A `completed` pack requires:

- every required source available, read, and hashed;
- no blocking context mismatch or unresolved item;
- one central hypothesis with falsification criteria;
- at least one method module, baseline, claim, required experiment, and ordered gate;
- all novelty-required baseline IDs present as `required` in `baseline_matrix`;
- every claim linked to a required experiment and evaluation criteria;
- every module/claim/baseline/source reference resolvable;
- a bounded iteration policy;
- an execution contract with relative allowed/write paths and startup pointers;
- a writer handoff contract that requires audited realized results;
- schema and semantic validation with no errors.

Warnings are allowed only when they do not require review or change execution meaning.

## 8. Failure handling

| Condition | Required handling |
| --- | --- |
| Required source missing | Set `blocked`; identify the missing path and affected fields. |
| Required source unreadable or malformed | Set `blocked`; preserve the path and parse failure. |
| Novelty audit and experiment plan disagree on baseline | Keep novelty baseline required; record mismatch; require review if protocol or budget changes materially. |
| Hypothesis and novelty audit imply different contributions | Set `blocked`; do not invent a merged contribution. |
| Optional source unavailable | Continue if no required decision depends on it; record omission. |
| Baseline unavailable | Keep it required unless an allowed substitution is explicit; otherwise require review or block. |
| Claim lacks feasible evidence | Lower claim ceiling or mark unsupported; never invent an experiment result. |
| Schema validation fails | Do not publish the pack as ready. |
| Semantic validation fails | Repair references/contracts or set a non-completed status with explicit unresolved items. |
