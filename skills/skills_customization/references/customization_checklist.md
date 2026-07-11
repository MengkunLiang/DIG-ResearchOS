# Skill Customization Checklist

Customize exactly these target skills when present in `template_manifest.json#copied_skills`:

- `research_execution`
- `context_alignment`
- `resource_and_baseline_mining`
- `baseline_reproduction`
- `experiment_design`
- `method_refinement`
- `implementation`
- `code_and_protocol_review`
- `experiment_iteration`
- `result_diagnosis`
- `module_attribution`
- `figure_table_packaging`
- `writer_handoff`

Do not customize `skills_customization` as a target.

For every target skill:

- Keep valid YAML frontmatter.
- Keep the frontmatter `name` equal to the directory name.
- Keep the eight boundary sections: Use for, Do not use for, Reads, Writes, Workflow, Output contract, Evidence rules, Stop conditions.
- Preserve allowed paths and the rule that external executor outputs stay under `external_executor/`.
- Replace generic placeholders with project-specific context from `handoff_pack.json`.
- Mention concrete required baselines and claim risks when the skill is responsible for them.
- Mention concrete claim ids, metrics, seeds, datasets, or experiment materials when available.
- Keep long project summaries in `references/project_context.md` or phase-specific references.

Project facts to extract from `handoff_pack.json`:

- `context_reboost.project_goal`
- `context_reboost.central_hypothesis`
- `context_reboost.method_mechanism`
- `method_intent`
- `baseline_matrix`
- `claim_evidence_matrix`
- `experiment_contract.required_baselines`
- `experiment_contract.metrics`
- `experiment_contract.seeds`
- `executor_outputs_contract.required_fields`
- `allowed_paths`
- `writer_handoff_contract`

Per-skill specialization map:

- `research_execution`: project goal, root execution order, required baselines, minimum experiment loop, result_pack required fields, final Codex handoff instruction.
- `context_alignment`: source files to check, context mismatch risks, required baseline and method-intent alignment rules.
- `resource_and_baseline_mining`: concrete baseline names, why each baseline is required, dataset/metric compatibility checks, unavailable-baseline claim risks.
- `baseline_reproduction`: required baseline reproduction order, metrics, seeds, run acceptance criteria, forbidden baseline substitutions.
- `experiment_design`: claim ids, claim-to-evidence matrix, metrics, ablations, negative controls, minimum experiment loop.
- `method_refinement`: draft-only method intent, allowed refinement boundaries, scope drift and final method evidence rules.
- `implementation`: allowed paths, expected method modules, external material locations under `external_executor/expr/`, implementation review handoff.
- `code_and_protocol_review`: fairness checks, path/hash/provenance checks, baseline parity, metric direction, seed/split consistency.
- `experiment_iteration`: run levels, retry/stop rules, partial-result handling, artifact indexing expectations.
- `result_diagnosis`: claim risks, expected evidence patterns, failure modes, overclaim controls.
- `module_attribution`: method modules, ablation mapping, module-to-claim boundaries.
- `figure_table_packaging`: final framework figure constraints, table/figure provenance, claim-safe captions.
- `writer_handoff`: writer handoff contract, must-not-claim boundaries, result_pack/status/manifest final assembly.

Reference placement guidance:

- Put shared long project summaries in each target skill's `references/project_context.md` when a target needs more than a few bullets.
- Put phase-specific schemas/checklists in the target skill's `references/` directory instead of expanding `SKILL.md`.
- Reuse existing `scripts/` and `assets/`; do not delete or rename them.
- Keep `SKILL.md` concise enough that Codex can follow it as an execution instruction, not a project archive.

Required final report:

```json
{
  "version": "1.0",
  "semantics": "external_executor_skill_customization_report",
  "handoff_pack": "external_executor/handoff_pack.json",
  "customized_skills": [],
  "unchanged_or_skipped": [],
  "project_specific_fields_used": [],
  "next_instruction": "python -m researchos.cli resume --workspace <workspace>"
}
```

For single-task debugging, the next command after this report is present can also be
`python -m researchos.cli run-task T5-EXPR-MATERIAL-GATE --workspace <workspace>`.
