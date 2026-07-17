---
name: module-attribution
description: Attribute observed ResearchOS experiment behavior to implemented modules and bounded mechanisms using direct ablations, controlled diagnostics, implementation mappings, subset evidence, and explicitly labeled correlational hints. Use when `research-execution` dispatches Phase E2 after a usable per-iteration result diagnosis exists and the evidence surface is sufficient for module or mechanism analysis. Pin an attribution snapshot, inventory ours and baseline modules, normalize interventions, estimate paired ablation and interaction effects, assess confounds, and produce evidence-graded module/mechanism attributions plus keep/modify/drop/narrow recommendations. Do not run experiments, invent missing ablations, treat implementation presence as empirical support, promote correlation to causation, make the root iteration decision, change claims or scope, or write the realized method package.
---

# Module Attribution

Act as the evidence-bound mechanism analyst for one ResearchOS iteration. Determine which modules are implemented, which have measured intervention effects, which mechanisms are merely consistent with observations, and which questions remain unsupported. `research-execution` owns the iteration decision; `evidence-packaging` owns the final realized method.

<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->
<!-- Filled during project Skill specialization. -->
<!-- PROJECT-SPECIFIC-GUIDANCE:END -->

## Establish paths and ownership

1. Locate the nearest directory containing both `project.yaml` and `external_executor/`; call it `<workspace>`.
2. Treat the directory containing this file as `<skill-dir>`.
3. Read before writing:
   - `<workspace>/external_executor/AGENTS.md`;
   - `<workspace>/external_executor/allowed_paths.txt`;
   - `<workspace>/external_executor/handoff_pack.json#method_intent`;
   - `<workspace>/external_executor/result_pack.json#claim_evidence_matrix`;
   - `<workspace>/external_executor/result_pack.json#experiment_plan`;
   - `<workspace>/external_executor/result_pack.json#implementations`;
   - `<workspace>/external_executor/result_pack.json#baseline_reproduction`;
   - `<workspace>/external_executor/result_pack.json#experiment_runs`;
   - `<workspace>/external_executor/result_pack.json#result_diagnoses`;
   - the root-owned active iteration plan;
   - `<skill-dir>/references/attribution-policy.md`;
   - `<skill-dir>/references/evidence-hierarchy.md`;
   - `<skill-dir>/references/module-and-mechanism-contract.md`;
   - `<skill-dir>/references/output-contract.md`.
4. Stop with `blocked` when no active/current diagnosis can be resolved, the diagnosis is blocked, no implemented module identity can be recovered, metric direction or protocol identity is indeterminate for claimed intervention evidence, or the writable boundary cannot be determined.

Write only:

- `external_executor/module_attribution_preflight.json`;
- `external_executor/module_attribution_snapshot.json`;
- `external_executor/module_attribution_facts.json`;
- `external_executor/module_attribution_report.json`;
- versioned analysis artifacts under `external_executor/module_attribution/`;
- `result_pack.json#module_attributions` through the narrow apply script.

Do not change runs, raw results, implementations, configs, reviews, diagnosis records, experiment plans, iteration plans/decisions, claim boundaries, method specifications, executor status, manifest, budget, or sibling sections. Return control to `research-execution` after applying the report.

## Run deterministic preflight

```bash
python <skill-dir>/scripts/preflight_attribution.py --workspace <workspace> \
  --output external_executor/module_attribution_preflight.json
```

The preflight confirms:

- the current iteration and diagnosis are identifiable;
- the diagnosis gate is `ready_for_attribution` or a non-blocking `partial` with explicit limitations;
- implementation/module mappings and experiment runs exist;
- ablation, diagnostic, subset, and formal evidence classes remain distinguishable;
- metric direction, protocol fingerprint, method identity, and seed/repeat are recoverable where intervention effects are claimed;
- unsupported schema or path conditions do not prevent analysis.

A partial diagnosis may support partial attribution, but never silently upgrades weak evidence.

## Pin the attribution evidence snapshot

Read `references/evidence-hierarchy.md`, then run:

```bash
python <skill-dir>/scripts/build_attribution_snapshot.py --workspace <workspace> \
  --iteration-id <iteration-id> \
  --output external_executor/module_attribution_snapshot.json
```

The snapshot must preserve:

- current diagnosis and its input fingerprint;
- ours and baseline module identities, code/config mappings, and mechanism links;
- included/excluded intervention runs and reasons;
- method/variant, removed/disabled/added modules, intervention type, setting/subset, dataset/split, seed/repeat, protocol/fairness fingerprints, metric observations, and artifact references;
- diagnosis anomalies, confounds, and evidence requests relevant to attribution;
- one deterministic attribution input fingerprint.

Do not infer a module intervention from a variant name alone when explicit switch/mapping evidence is absent.

## Inventory modules and mechanisms

```bash
python <skill-dir>/scripts/inventory_modules.py \
  --snapshot <workspace>/external_executor/module_attribution_snapshot.json \
  --output <workspace>/external_executor/module_attribution/<iteration-id>/module_registry.json
```

Read `references/module-and-mechanism-contract.md`. Each module record distinguishes:

- `ours` or a named baseline;
- implemented, configured, executable, and empirically tested status;
- code paths, config keys, ablation switches, diagnostic switches, input/output contract;
- intended role and linked mechanism IDs;
- source authority and uncertainty.

Implementation presence is `implementation_fact`, not evidence of benefit.

## Normalize intervention evidence

```bash
python <skill-dir>/scripts/normalize_attribution_evidence.py \
  --snapshot <workspace>/external_executor/module_attribution_snapshot.json \
  --module-registry <module-registry.json> \
  --output <workspace>/external_executor/module_attribution/<iteration-id>/intervention_observations.json
```

Classify each observation as one of:

```text
direct_ablation
controlled_diagnostic
correlational_hint
implementation_fact
unsupported
```

A direct ablation requires an explicit intervention relative to a compatible reference variant. A controlled diagnostic requires a declared manipulation, held-constant comparison surface, and a metric relevant to the mechanism question. Subset performance without intervention is a correlational hint unless the plan defines a valid controlled contrast.

## Estimate direct module effects

Read `references/ablation-and-intervention-analysis.md`, then run:

```bash
python <skill-dir>/scripts/compute_ablation_effects.py \
  --observations <intervention-observations.json> \
  --output <workspace>/external_executor/module_attribution/<iteration-id>/ablation_effects.json
```

Pair full/reference and intervened observations only when they agree on:

```text
method family + protocol fingerprint + dataset/version + split + preprocessing
+ setting/subset + metric/direction/aggregation + seed/repeat + fairness fingerprint
```

For each module and setting, report:

- paired sample count and coverage;
- direction-adjusted effect of enabling/preserving the module;
- mean, median, dispersion, win/tie/loss and sign consistency;
- practical-threshold status when predeclared;
- exact intervention and reference variants;
- missing-pair and instability limitations.

Ablation effect means “difference under this intervention and setting,” not universal importance.

## Analyze interactions and confounds

Read `references/interaction-and-confounding.md`, then run:

```bash
python <skill-dir>/scripts/analyze_interactions.py \
  --observations <intervention-observations.json> \
  --ablation-effects <ablation-effects.json> \
  --output <workspace>/external_executor/module_attribution/<iteration-id>/interaction_and_confounds.json
```

When a complete factorial contrast exists, compute pairwise difference-in-differences for module interactions. Otherwise record interaction as unsupported; do not estimate it from two independent single-module effects.

Inspect:

- capacity, parameter-count, compute, memory, pretraining, data and tuning-budget changes;
- shared preprocessing, metric, early-stopping or checkpoint differences;
- module switches that alter multiple mechanisms;
- broken-path ablations that merely damage interfaces or optimization;
- non-comparable baseline module definitions;
- subset selection, leakage, seed imbalance and multiple-comparison risk.

## Build deterministic attribution facts

```bash
python <skill-dir>/scripts/build_attribution_facts.py \
  --snapshot <snapshot.json> \
  --module-registry <module-registry.json> \
  --ablation-effects <ablation-effects.json> \
  --interaction-analysis <interaction-and-confounds.json> \
  --output external_executor/module_attribution_facts.json
```

Facts identify:

- modules with positive, neutral, negative, mixed, or unsupported intervention evidence;
- setting/subset specificity;
- stable versus unstable effects;
- supported and unsupported interaction questions;
- implementation-only modules;
- baseline modules with measured or merely documented roles;
- mechanism links that have direct intervention support, indirect consistency, or no evidence.

Facts are inputs to interpretation, not final research decisions.

## Produce evidence-graded attribution

Read:

- `references/confidence-and-causality.md`;
- `references/recommendation-and-boundary.md`;
- `references/output-contract.md`.

Initialize:

```bash
python <skill-dir>/scripts/initialize_attribution_report.py --workspace <workspace> \
  --snapshot external_executor/module_attribution_snapshot.json \
  --facts external_executor/module_attribution_facts.json \
  --output external_executor/module_attribution_report.json
```

Complete these sections:

### Module attributions

For each relevant ours/baseline module, record:

- empirical status: `beneficial | neutral | harmful | mixed | implementation_only | unsupported`;
- evidence type and exact tested settings;
- estimated effect refs and counterevidence;
- confounds and interaction dependencies;
- confidence;
- bounded causal status.

### Mechanism attributions

For each mechanism hypothesis, record:

- linked module/intervention;
- `supported | consistent | weakened | contradicted | unresolved`;
- evidence type;
- alternative explanations;
- confidence and evidence refs;
- what additional controlled evidence would discriminate alternatives.

A module effect does not automatically prove the intended mechanism. Example: disabling a module may reduce accuracy because of parameter count, optimization disruption, or removal of multiple functions.

### Advisory refinement surface

Use only:

```text
keep
modify
drop
narrow
collect_evidence
```

Recommendations are local, evidence-bound suggestions. They do not edit method specs, claim boundaries, or iteration decisions.

## Compute the attribution gate

```bash
python <skill-dir>/scripts/compute_attribution_gate.py \
  --report <workspace>/external_executor/module_attribution_report.json \
  --write-back
```

Use:

- `ready_for_iteration_decision`: relevant modules/mechanisms have evidence-graded attribution, material confounds are propagated, and recommendations are bounded.
- `partial`: useful attribution exists, but coverage, pairing, interaction evidence, stability, or mechanism discrimination remains limited.
- `blocked`: no scientifically valid attribution surface exists, intervention identity is ambiguous, protocol/fairness mismatch is blocking, or central evidence is missing.

`ready_for_iteration_decision` does not mean the root must continue, modify, narrow, or stop. It only means the attribution evidence is ready for root consideration.

## Validate and apply narrowly

```bash
python <skill-dir>/scripts/validate_attribution_report.py --workspace <workspace> \
  --report external_executor/module_attribution_report.json

python <skill-dir>/scripts/apply_attribution_report.py --workspace <workspace> \
  --report external_executor/module_attribution_report.json
```

The apply script updates only `result_pack.json#module_attributions`, preserving prior iterations and sibling sections.

## Return to the root

```text
child_skill=module-attribution
status=complete|partial|blocked|failed
attribution_gate=ready_for_iteration_decision|partial|blocked
iteration_id=<id>
attribution_id=<id>
report=external_executor/module_attribution_report.json
beneficial_module_ids=<ids>
harmful_module_ids=<ids>
unsupported_mechanism_ids=<ids>
material_confound_ids=<ids>
recommendation_summary=<keep/modify/drop/narrow/collect_evidence counts>
recommended_next_action=return_for_iteration_decision|add_controlled_evidence|repair_or_rerun|human_review|stop_and_report
```

The recommendation is advisory. `research-execution` owns routing, budget, scope-change gates, claim-boundary changes, and the iteration decision.

## Evidence and safety rules

- Keep module identity, implementation fact, intervention effect, mechanism interpretation, and root decision separate.
- Preserve all negative, neutral, mixed and failed intervention evidence.
- Never infer causality from a plain correlation, one favorable seed, or subset association.
- Never call a broken execution path a harmful scientific module without validating intervention integrity.
- Never treat parameter-count or compute changes as mechanism-isolating ablations without controls.
- Never transfer a module effect across dataset, split, setting, metric or protocol without evidence.
- Never claim a baseline module is effective merely because its paper or code describes it.
- Never fabricate an ablation, module mapping, interaction, mechanism, confidence or evidence reference.
- Keep every attribution pre-audit; T7 remains the evidence-closure authority.

## Resource map

- `references/attribution-policy.md`: scope, ownership, non-goals and workflow.
- `references/evidence-hierarchy.md`: evidence classes, eligibility and allowed inference.
- `references/module-and-mechanism-contract.md`: module registry and mechanism-link schema.
- `references/ablation-and-intervention-analysis.md`: pairing, effect direction, stability and practical interpretation.
- `references/interaction-and-confounding.md`: factorial interactions, broken ablations and confound checks.
- `references/confidence-and-causality.md`: causal language, confidence and alternative explanations.
- `references/recommendation-and-boundary.md`: keep/modify/drop/narrow/collect-evidence semantics.
- `references/output-contract.md`: report, gate and result-pack mapping.
- `scripts/preflight_attribution.py`: validate prerequisites and authority.
- `scripts/build_attribution_snapshot.py`: pin the exact E2 evidence surface.
- `scripts/inventory_modules.py`: normalize ours/baseline module identities and mappings.
- `scripts/normalize_attribution_evidence.py`: convert run records into intervention observations.
- `scripts/compute_ablation_effects.py`: estimate paired direct effects.
- `scripts/analyze_interactions.py`: compute supported factorial interactions and confounds.
- `scripts/build_attribution_facts.py`: derive deterministic evidence-graded facts.
- `scripts/initialize_attribution_report.py`: create the interpretation envelope.
- `scripts/compute_attribution_gate.py`: derive the E2 gate.
- `scripts/validate_attribution_report.py`: enforce causality, evidence and schema rules.
- `scripts/apply_attribution_report.py`: update only `result_pack.module_attributions`.
