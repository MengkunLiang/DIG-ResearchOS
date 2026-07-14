---
name: context-alignment
description: Validate and normalize the ResearchOS T5 external-executor handoff before resource preparation or experiments. Use when `research-execution` dispatches Phase A, when handoff/control inputs changed, when resuming with a stale alignment checkpoint, or when checking project goal, hypothesis, method intent, baselines, experiment minimum loop, claim boundaries, resource policy, executor capabilities, allowed paths, and result-pack schema for conflicts or missing authority. Produce an evidence-backed `context_alignment` section with pass, mismatch, or blocked status. Do not use to redesign the research idea, prepare resources, implement methods, run experiments, or resolve material scope conflicts without human review.
---

# Context Alignment

Confirm that the external executor understands one safe, executable research scope. Align; do not optimize, reinterpret, or silently repair the research design.

<!-- PROJECT-SPECIFIC-GUIDANCE:BEGIN -->
<!-- Filled during project Skill specialization. -->
<!-- PROJECT-SPECIFIC-GUIDANCE:END -->

## Establish paths and ownership

1. Locate the nearest directory containing `project.yaml` and `external_executor/`; call it `<workspace>`.
2. Treat the directory containing this file as `<skill-dir>`.
3. Read `<workspace>/external_executor/AGENTS.md` and `<workspace>/external_executor/allowed_paths.txt` before any write.
4. Write only:
   - `external_executor/preflight_context.json`;
   - `external_executor/context_source_inventory.json`;
   - `external_executor/context_alignment_report.json`;
   - `result_pack.json#context_alignment` through the narrow apply script.

Do not change handoff, Pre-T5 source artifacts, executor status, manifest, budgets, or another result-pack section. Return control to `research-execution` after the report is applied.

## Read the alignment policy

Read these references before analysis:

- `references/authority-and-conflict-policy.md` for domain-specific authority and conflict severity;
- `references/alignment-checklist.md` for required axes and escalation rules;
- `references/output-contract.md` for the report shape and gate semantics;
- `references/source-reading-policy.md` before opening optional deep source material.

Never use one global file-priority list across security, Schema, research semantics, and experiment evidence.

## Run deterministic preflight

Run:

```bash
python <skill-dir>/scripts/preflight_context.py --workspace <workspace> \
  --output external_executor/preflight_context.json

python <skill-dir>/scripts/inventory_sources.py --workspace <workspace> \
  --output external_executor/context_source_inventory.json
```

Inspect both outputs. Stop with `blocked` when a required control file is missing or malformed, a major schema version is unsupported, allowed paths cannot be determined, the handoff omits execution-critical structures after supported reboost/top-level fallbacks, or the requested acquisition mode contradicts declared authority/capability.

Treat preflight warnings as prompts for evidence review, not automatic blockers. A scaffold-only `external_executor/expr/`, missing `resources/baseline_candidates.jsonl`, missing `literature/baseline_map.json`, or an empty `novelty/required_baselines.json` is not a Phase A blocker.

## Build a field-level evidence map

Start with structured handoff fields; then cross-check only the source files needed for each field.

Confirm these execution fields:

| Axis | Fields |
| --- | --- |
| Control plane | allowed paths, forbidden paths, output schema, acquisition policy, executor capabilities, budget, stop conditions |
| Research semantics | project goal, central hypothesis, core mechanism, must-preserve components, candidate components, allowed refinements, forbidden scope changes |
| Experiment contract | required baselines, replacement policy, benchmark/dataset/split/metric, minimum experiment loop, claim-evidence matrix |
| Claim boundary | strong/weak/unsupported claim conditions, must-not-claim, novelty boundary, writer handoff contract |

For every confirmed field, record one or more source references. Distinguish:

- directly stated value;
- compiled handoff value confirmed by source;
- compiled value accepted with a documented non-material assumption;
- unresolved or materially conflicting value.

Use the ResearchOS default resource-acquisition policy when no explicit policy is present: public GitHub access, public dataset download, and baseline reimplementation are allowed within `allowed_paths.txt`, license review, and security review. Do not fill other unknown execution-critical values from general domain knowledge.

## Read sources progressively

Read fixed high-value sources first:

```text
project.yaml
literature/synthesis.md
ideation/hypotheses.md
ideation/exp_plan.yaml
ideation/idea_scorecard.yaml
ideation/risks.md
novelty/novelty_audit.md
```

If `ideation/hypothesis_brief.yaml` or `ideation/selected/t45_search_targets.json` exists, treat it as lineage and search-scope context only. It is a Pre-Novelty draft and cannot authorize an experiment, replace `ideation/hypotheses.md`, or relax a novelty/claim boundary.

Native T4 artifacts such as `ideation/selected/selected_candidate.json`,
`ideation/portfolio.json`, `ideation/final_cards/portfolio_cards.json`, and
`ideation/evolution/` may be read only to trace the selected Candidate, Evidence
Permission, Family, Mutation/Crossover lineage, and human decision. They are not an
execution authority and must not be rewritten by an external executor. `idea_scorecard.yaml`
is a compatibility projection when present; the post-T4.5 `hypotheses.md`, `exp_plan.yaml`,
novelty audit, and handoff contract remain the authoritative inputs for external execution.

Read `synthesis_workbench.json`, `domain_map.json`, `comparison_table.csv`, paper notes, resources, and user seeds only when a named field is missing, ambiguous, or contradicted. Do not re-run literature review or load entire directories without a targeted question.

## Classify mismatches by axis

Create one mismatch record per field-level issue. Use the severity and resolution vocabulary in `references/authority-and-conflict-policy.md`.

Examples:

- missing optional explanation with an unambiguous handoff value -> `warning`, document assumption;
- baseline appears in novelty audit but not in handoff baseline matrix -> `material`, block;
- handoff and exp plan use different seed counts but the minimum loop remains clear -> `warning` or `material` depending claim impact;
- allowed path in handoff exceeds `allowed_paths.txt` -> `blocking`, use the stricter control and stop;
- core mechanism differs between hypothesis and method intent -> `blocking`, request human review;
- unavailable executor capability required by acquisition policy -> `blocking`.

Never mark a mismatch resolved merely because one source was read later. Record why the selected interpretation is authorized and whether it changes downstream claims, baselines, protocol, scope, permissions, or novelty.

## Confirm executor capability fit

Compare the compiled policy with observed or declared executor capabilities:

- network and allowed domains;
- dataset download and restricted-access capability;
- baseline reimplementation authority;
- available tools and child skills;
- write paths;
- compute/time budget when declared;
- supported result/handoff schema major versions.

Absence of optional capability detail may be a warning. If `executor_capabilities.json` is absent but `executor_selection.json` names `codex_cli`, `claude_code_window`, or `manual`, treat public network access, GitHub acquisition, dataset download support, and baseline reimplementation support as declared by the T5 gate. Absence of a required capability after this selection fallback is blocking.

## Produce the alignment report

Create `external_executor/context_alignment_report.json` using `references/output-contract.md`.

The confirmed execution scope must contain:

- project goal;
- central hypothesis;
- core mechanism and must-preserve components;
- required baselines and replacement constraints;
- benchmark protocol summary;
- minimum experiment loop;
- claim boundaries and must-not-claim;
- resource-acquisition policy, using the ResearchOS default when the handoff omits it;
- allowed/forbidden paths;
- iteration budget and stop conditions;
- output schema version;
- field-level provenance.

Use these gate outcomes:

- `pass`: no mismatch remains; execution-critical fields are confirmed.
- `mismatch`: only non-blocking mismatches remain, each with an authorized resolution or explicit constraint.
- `blocked`: any unresolved material/blocking mismatch, missing authority, unsupported major schema, or missing minimum-loop field remains.

Set `next_action` to `continue_to_phase_b`, `continue_with_constraints`, `human_review`, or `stop_and_report` consistently with status.

## Validate and apply narrowly

Run:

```bash
python <skill-dir>/scripts/validate_alignment_report.py --workspace <workspace> \
  --report external_executor/context_alignment_report.json

python <skill-dir>/scripts/apply_alignment_report.py --workspace <workspace> \
  --report external_executor/context_alignment_report.json
```

The apply script updates only `result_pack.json#context_alignment`. If validation fails, fix the report; do not bypass the validator or edit unrelated result-pack sections.

## Return to the root

Return a compact child result containing:

```text
child_skill=context-alignment
status=complete|partial|blocked|failed
alignment_status=pass|mismatch|blocked
report=external_executor/context_alignment_report.json
evidence_refs=<paths>
blocking_issues=<ids>
recommended_next_action=continue_to_phase_b|continue_with_constraints|human_review|stop_and_report
```

The recommendation is advisory. `research-execution` owns checkpointing, manifest updates, executor status, and the next dispatch.

## Evidence and safety rules

- Quote or paraphrase source meaning with file-level provenance; do not fabricate exact source locations.
- Separate “missing” from “contradictory,” and “unknown” from “false.”
- Keep confidence separate from gate status.
- Preserve all mismatch records, including resolved warnings.
- Never relax `AGENTS.md`, allowed paths, acquisition policy, privacy, license, or Schema controls. The ResearchOS default acquisition policy is an explicit T5 policy, not an inference.
- Never rewrite hypothesis, method intent, novelty boundary, experiment plan, or claim boundary.
- Never search the web during alignment unless the handoff explicitly requires a current external fact and the root authorizes it; resource discovery, GitHub acquisition, dataset download, and baseline reimplementation are later Phase B responsibilities.

## Resource map

- `references/authority-and-conflict-policy.md`: authority by axis, severity, and permitted resolution.
- `references/alignment-checklist.md`: field checklist, capability fit, and preflight gates.
- `references/output-contract.md`: report and result-pack section contract.
- `references/source-reading-policy.md`: fixed/optional sources and progressive reading.
- `scripts/preflight_context.py`: validate controls, versions, policy, and internal handoff consistency.
- `scripts/inventory_sources.py`: create a deterministic source inventory and fingerprint.
- `scripts/validate_alignment_report.py`: enforce report structure and gate consistency.
- `scripts/apply_alignment_report.py`: atomically update only `result_pack.context_alignment`.
