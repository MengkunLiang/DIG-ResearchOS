---
name: figure_table_packaging
description: Package ResearchOS external executor figures, tables, captions, and inventory with evidence references.
allowed_tools:
  - "read_file"
  - "list_files"
  - "grep_search"
  - "glob_files"
  - "write_file"
  - "append_file"
  - "bash_run"
  - "finish_task"
allowed_read_prefixes:
  - ""
  - "external_executor/"
  - "experiments/"
  - "ideation/"
  - "literature/"
  - "novelty/"
  - "resources/"
  - "user_seeds/"
allowed_write_prefixes:
  - "external_executor/"
max_steps: 20
max_tokens_total: 100000
temperature: 0.2
---

# Figure Table Packaging

## Use for

Use after diagnosis and module attribution to prepare final framework figure specifications, result tables, diagnostic figures, and evidence-bound captions for T7 audit.

## Do not use for

- Do not create paper-ready claims.
- Do not show unimplemented or unsupported modules as core framework components.
- Do not include result figures without raw result provenance.

## Reads

- `result_pack.realized_method_package`
- `result_pack.result_diagnosis`
- `result_pack.module_attribution`
- `external_executor/raw_results/`
- `external_executor/configs/`
- `external_executor/logs/`
- `external_executor/skills/figure_table_packaging/references/figure_table_contract.md`
- `external_executor/skills/figure_table_packaging/assets/framework_figure_spec_template.json`

## Writes

- `external_executor/figures/`
- `external_executor/tables/`
- `result_pack.final_framework_figure`
- `result_pack.figure_table_inventory`

## Workflow

- Follow `references/figure_table_contract.md`.
- Create or reference a final framework figure only from realized method, code structure, module attribution, and claim boundary.
- Tie every figure/table element to source artifacts and claim ids.
- Draft captions as evidence-bound notes for T7, not final manuscript prose.

## Output contract

- `final_framework_figure` must map visible modules to implemented code and evidence.
- `figure_table_inventory` must include source result/config/log or method package references.

## Evidence rules

- Result tables require raw metrics; framework figures require realized method and code/module mapping.
- Mark missing or unaudited figures as unusable by T8 until T7 passes.

## Stop conditions

- Stop with partial inventory if required raw results or module mappings are missing.
