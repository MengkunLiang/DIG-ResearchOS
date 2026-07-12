---
name: resource-and-baseline-preparation
description: Prepare and statically validate the datasets, benchmarks, baseline implementations, evaluation code, preprocessing assets, checkpoints, environments, protocols, and references required by the confirmed ResearchOS external-executor scope. Use when `research-execution` dispatches Phase B after non-blocking context alignment, when resource readiness is missing or stale, or when authorized local search, GitHub acquisition, or baseline reimplementation is needed. Produce a requirement matrix, provenance-rich inventory, candidate and review records, material gaps, propagated risks, and a `ready`, `partial`, or `blocked` readiness gate. Do not run baseline experiments, redesign claims, silently replace required baselines, execute unreviewed third-party code, or broaden network, dataset, license, path, or reimplementation authority.
---

# Resource and Baseline Preparation

Prepare the minimum experiment loop without confusing resource availability with experimental reproduction. This skill owns Phase B static preparation and review; `baseline-reproduction` owns executed baseline evidence.

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
   - `<skill-dir>/references/acquisition-policy.md`;
   - `<skill-dir>/references/resource-requirement-contract.md`;
   - `<skill-dir>/references/output-contract.md`.
4. Stop with `blocked` when context alignment is absent or blocking, the confirmed execution scope is missing, the acquisition policy is internally inconsistent, or the writable resource boundary cannot be determined. If the policy is absent in a legacy handoff, use the ResearchOS default policy: public GitHub access, public dataset download, and baseline reimplementation are allowed within `allowed_paths.txt`, license review, and security review.

Write only:

- `external_executor/resource_preflight.json`;
- `external_executor/resource_requirement_matrix.json`;
- `external_executor/resource_local_inventory.json`;
- `external_executor/resource_search_records.json`;
- `external_executor/resource_preparation_report.json`;
- authorized staged/acquired/reimplemented material under `external_executor/workdir/resources/`;
- the Phase B result-pack sections listed in `references/output-contract.md`, through the narrow apply script.

Do not change `executor_status.json`, `run_manifest.json`, budgets, iteration decisions, context alignment, experiment plans, baseline reproduction records, or sibling-owned sections. Return control to `research-execution` after applying the report.

## Run deterministic preflight

Run:

```bash
python <skill-dir>/scripts/preflight_resources.py --workspace <workspace> \
  --output external_executor/resource_preflight.json
```

The preflight must confirm:

- `context_alignment.status` is `pass` or non-blocking `mismatch`;
- `confirmed_execution_scope` contains required baselines, benchmark/protocol information, minimum loop, claim constraints, and acquisition policy;
- acquisition mode, capability flags, and allowed domains agree;
- local output and `workdir/resources/` paths are writable under policy;
- no unsupported major schema is required.

A preflight warning prompts targeted review. A preflight blocker prevents acquisition and reimplementation. A scaffold-only `external_executor/expr/`, missing `resources/baseline_candidates.jsonl`, or missing `literature/baseline_map.json` is not a blocker; this skill owns discovering/acquiring/reimplementing missing resources.

## Build the resource requirement matrix

Run:

```bash
python <skill-dir>/scripts/build_requirement_matrix.py --workspace <workspace> \
  --output external_executor/resource_requirement_matrix.json
```

Then complete or refine the matrix using `references/resource-requirement-contract.md`. Create one requirement per independently verifiable resource, covering as applicable:

- benchmark definition and official protocol;
- dataset, version, legal access, split, labels, and checksums;
- required and optional baseline implementations;
- metric and evaluation implementation, direction, and aggregation;
- preprocessing/tokenization/feature construction;
- pretrained checkpoints or initialization assets;
- environment/runtime constraints;
- protocol/reference material needed to resolve implementation details.

Every requirement must state whether it is required, what minimum-loop dependency it serves, accepted source classes, exact compatibility expectations, acceptance criteria, replacement authority, and whether its absence blocks execution. Distinguish `missing`, `incomplete`, `incompatible`, `not_runnable`, `protocol_nonequivalent`, `restricted`, and `untrusted_source`.

Do not silently weaken a requirement because a convenient candidate exists.

## Search and inventory local material first

Run:

```bash
python <skill-dir>/scripts/inventory_local_resources.py --workspace <workspace> \
  --output external_executor/resource_local_inventory.json
```

Inspect in this order:

```text
external_executor/expr/
resources/
user_seeds/
external_executor/workdir/resources/  # only verifiable prior material
```

It is valid for `external_executor/expr/` to contain only the generated README and checklist. Record that as an inventory fact and continue to authorized remote acquisition or reimplementation for unsatisfied requirements.

Use `references/resource-review-checklist.md` to map candidates to requirements. Inspect provenance, fixed version, license, README, configuration, entry points, dependency manifests, dataset split, preprocessing, metric implementation, benchmark protocol, checkpoints, symlinks, submodules, and minimum-loop coverage. Do not execute third-party setup, download, shell, notebook, training, or evaluation code during inventory.

When a local candidate is accepted for controlled work, copy it without mutating the source:

```bash
python <skill-dir>/scripts/stage_local_resource.py --workspace <workspace> \
  --source <workspace-relative-source> --candidate-id <candidate-id>
```

Reject escaping or unresolved symlinks. Preserve source and staged checksums in provenance.

Initialize the durable report envelope after preflight, matrix creation, and local inventory:

```bash
python <skill-dir>/scripts/initialize_resource_report.py --workspace <workspace> \
  --output external_executor/resource_preparation_report.json
```

The initializer preserves already reviewed child-owned sections by default. Use `--force` only when the root has invalidated the entire Phase B checkpoint.

## Search and acquire remote resources only when authorized

Read `references/source-ranking-and-search.md` before remote search.

Use remote search only for requirements still unsatisfied after local review and only when all of these are true:

- acquisition mode is `github_allowed` or `github_and_reimplementation`;
- `network_allowed=true`;
- the domain is explicitly allowed;
- dataset download is separately authorized when the resource contains data;
- the query does not expose private manuscript text, unpublished results, secrets, or private paths.

For ResearchOS T5 external execution, public GitHub search/acquisition and public dataset download are allowed by default unless a narrower handoff policy explicitly forbids them.

Prefer, in order:

1. official author or project repository linked by the paper/project page;
2. official benchmark or dataset organization;
3. author-recognized implementation;
4. high-confidence third-party reproduction with explicit protocol mapping;
5. other candidates only as documented alternatives, not silent equivalents.

For each candidate, record source class, repository URL, paper/project identity match, immutable commit or release, license, task/dataset/split/metric/protocol compatibility, dependencies, compute assumptions, maintenance state, security findings, and selection or rejection reason.

Acquire only an immutable revision and do not initialize submodules or execute repository content:

```bash
python <skill-dir>/scripts/acquire_github_resource.py --workspace <workspace> \
  --repo-url https://github.com/<owner>/<repo>.git \
  --revision <commit-or-tag> \
  --candidate-id <candidate-id>
```

Immediately perform static review:

```bash
python <skill-dir>/scripts/static_review_repository.py --workspace <workspace> \
  --path <workspace>/external_executor/workdir/resources/github/<candidate-id> \
  --output <workspace>/external_executor/workdir/resources/github/<candidate-id>/static_review.json
```

Treat static review as risk discovery, not proof of safety. Never run a fetched install script merely because the scan passed.

## Reimplement a baseline only as the final authorized path

Read `references/baseline-reimplementation.md` before proceeding.

Reimplementation is permitted only when:

- mode is `github_and_reimplementation`;
- `baseline_reimplementation_allowed=true`;
- local and authorized remote searches are exhausted and recorded;
- core algorithm, objective, dataset/split, metric, and benchmark protocol are recoverable;
- license or access restrictions do not prohibit the work;
- the requirement does not demand an official implementation specifically.

For ResearchOS T5 external execution, baseline reimplementation is allowed by default after local and authorized remote searches are exhausted and recorded.

Create a provenance-first package:

```bash
python <skill-dir>/scripts/scaffold_reimplementation.py --workspace <workspace> \
  --requirement-id <requirement-id> \
  --baseline-name <baseline-name> \
  --source <paper-or-supplement-reference> \
  --source <benchmark-protocol-reference>
```

Before writing algorithm code, complete the generated specification, assumptions, paper-to-code map, fidelity risks, and validation plan. Keep engineering repairs separate from algorithmic choices. Provide independent configuration, unified evaluation integration points, sanity tests, and explicit uncertainty.

A reimplementation must be labeled `executor_reimplementation` or `approximate_reproduction`; never `official`, `author_implementation`, or `protocol_equivalent` without evidence.

Validate the package before considering it a candidate:

```bash
python <skill-dir>/scripts/validate_reimplementation_package.py --workspace <workspace> \
  --path <reimplementation-package> --mode candidate
```

If the central mechanism or protocol cannot be recovered, mark the baseline unavailable rather than inventing missing details.

## Perform an independent resource review

Read `references/resource-review-checklist.md` and `references/repository-static-review.md`.

Review each candidate against its requirement, not against convenience. When independent workers are supported, use a reviewer that reads the requirement, source material, repository/data metadata, static review, adapters, and provenance directly. Otherwise perform a separate sequential review after acquisition work is complete.

For each reviewed candidate record:

- `verdict`: `pass | needs_fix | blocked`;
- identity/mechanism fidelity;
- task, dataset, split, preprocessing, metric, and protocol fidelity;
- metric direction and aggregation;
- fairness risks, including extra data, pretraining, tuning, or compute advantage;
- data leakage and evaluation contamination risks;
- source version, checksum, license, access, and redistribution status;
- security and dependency risks;
- adapter/patch effects;
- reimplementation assumptions and approximation level;
- evidence references;
- `approved_for`, using only the vocabulary in the output contract.

A candidate may be approved for `static_inspection` or `smoke_preparation` while still being rejected for `baseline_reproduction` or `formal_comparison`.

## Compute the readiness gate

Assemble `external_executor/resource_preparation_report.json` using `references/output-contract.md`, then run:

```bash
python <skill-dir>/scripts/compute_resource_readiness.py --workspace <workspace> \
  --report <workspace>/external_executor/resource_preparation_report.json \
  --write-back
```

Use these outcomes:

- `ready`: every minimum-loop required resource has a passing review and suitable approval; no unresolved blocking license, security, access, identity, or protocol issue remains.
- `partial`: the minimum loop is feasible, but an approximate baseline, optional gap, constrained dataset, moderate fidelity risk, or claim-limiting condition remains.
- `blocked`: a required dataset, baseline identity, core protocol, license, security condition, permission, or access constraint prevents a valid minimum loop.

Never mark `ready` merely because files exist. Never delete, replace, or downgrade a required baseline. If replacement is not already authorized, record a replacement request or scope-change blocker and return to the root.

## Validate and apply narrowly

Run:

```bash
python <skill-dir>/scripts/validate_resource_report.py --workspace <workspace> \
  --report external_executor/resource_preparation_report.json

python <skill-dir>/scripts/apply_resource_report.py --workspace <workspace> \
  --report external_executor/resource_preparation_report.json
```

The apply script updates only:

```text
result_pack.resource_requirement_matrix
result_pack.resources
result_pack.baseline_candidates
result_pack.dataset_inventory
result_pack.material_gaps
result_pack.resource_risks
result_pack.resource_readiness
```

If validation fails, fix the report. Do not bypass the validator or edit sibling sections.

## Return to the root

Return a compact child result:

```text
child_skill=resource-and-baseline-preparation
status=complete|partial|blocked|failed
resource_readiness=ready|partial|blocked
report=external_executor/resource_preparation_report.json
matrix=external_executor/resource_requirement_matrix.json
approved_requirement_ids=<ids>
constrained_requirement_ids=<ids>
blocking_requirement_ids=<ids>
claim_constraints=<ids-or-summary>
recommended_next_action=continue_to_experiment_design|continue_with_constraints|human_review|stop_and_report
```

The recommendation is advisory. `research-execution` owns manifest registration, executor status, checkpointing, scope-change gates, and the next dispatch.

## Evidence, safety, and boundary rules

- Files, immutable revisions, metadata, and checksums are facts; model summaries are navigation aids.
- Separate source discovery, acquisition, static review, protocol review, and executed reproduction.
- Never execute unreviewed third-party code, install hooks, notebooks, containers, or download scripts.
- Never expose secrets in URLs, logs, search queries, manifests, or subprocess environments.
- Never fabricate a dataset, split, checkpoint, metric, paper result, license, repository identity, or official status.
- A different split, metric, preprocessing, task, or simplified algorithm is not an equivalent required baseline.
- Preserve rejected candidates, search failures, approximations, and material gaps as evidence.
- Keep original material read-only; use staged copies, wrappers, adapters, or patches under authorized paths.
- Do not write experiment results or claim support. Phase B produces readiness evidence only.

## Resource map

- `references/acquisition-policy.md`: authorization modes, privacy, network, dataset, license, and immutable acquisition rules.
- `references/resource-requirement-contract.md`: requirement taxonomy, fields, deficiency states, and blocking semantics.
- `references/source-ranking-and-search.md`: source tiers, query construction, candidate ranking, and search records.
- `references/baseline-reimplementation.md`: reimplementation preconditions, package contract, fidelity labels, and stop rules.
- `references/resource-review-checklist.md`: identity, protocol, fairness, license, access, and approval review.
- `references/repository-static-review.md`: static risk categories and non-execution review policy.
- `references/output-contract.md`: Phase B report, result-pack mapping, gate consistency, and child return contract.
- `scripts/preflight_resources.py`: validate Phase B prerequisites, authority, capability, and write boundaries.
- `scripts/build_requirement_matrix.py`: create a deterministic requirement scaffold from confirmed scope.
- `scripts/inventory_local_resources.py`: inventory local candidates without executing them.
- `scripts/initialize_resource_report.py`: create or refresh the durable Phase B report envelope without overwriting reviewed sections.
- `scripts/stage_local_resource.py`: copy accepted local material into controlled workdir with provenance.
- `scripts/acquire_github_resource.py`: fetch one immutable GitHub revision without submodules or code execution.
- `scripts/static_review_repository.py`: inspect repository metadata and risky patterns statically.
- `scripts/scaffold_reimplementation.py`: create a provenance-first baseline reimplementation package.
- `scripts/validate_reimplementation_package.py`: validate reimplementation completeness and labels.
- `scripts/compute_resource_readiness.py`: derive the Phase B gate from requirements and reviews.
- `scripts/validate_resource_report.py`: enforce the report and readiness contract.
- `scripts/apply_resource_report.py`: atomically update only Phase B result-pack sections.
