# Research Execution Loop

Start from `external_executor/AGENTS.md`. Then read the handoff pack, expected
schema, allowed paths, and this root skill.

Required order:

1. `context_alignment`
2. `resource_and_baseline_mining`
3. `baseline_reproduction`
4. `experiment_design`
5. `method_refinement`
6. `implementation`
7. `code_and_protocol_review`
8. `experiment_iteration`
9. `result_diagnosis`
10. `module_attribution`
11. `method_refinement` again if results require a minor refinement or claim narrowing
12. `figure_table_packaging`
13. `writer_handoff`

Loop rules:

- Baseline reproduction comes before new method implementation.
- Every implementation or config change must be reviewed before formal runs.
- Run smoke checks before small-scale validation, and small-scale validation before formal runs.
- If review fails, return to implementation or mark blocked.
- If results change the realized method, update `realized_method_package` before packaging figures.
- `writer_handoff` writes the final result pack last.
