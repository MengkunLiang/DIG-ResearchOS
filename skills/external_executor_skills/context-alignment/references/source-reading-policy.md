# Source Reading Policy

Use progressive disclosure. Read enough to verify a named field; do not rebuild the whole research history.

## Fixed sources

Read when present:

```text
project.yaml
external_executor/AGENTS.md
external_executor/handoff_pack.json
external_executor/expected_outputs_schema.json
external_executor/allowed_paths.txt
literature/synthesis.md
ideation/hypotheses.md
ideation/exp_plan.yaml
ideation/idea_scorecard.yaml
ideation/risks.md
novelty/novelty_audit.md
```

Controls and handoff are required. A missing Pre-T5 source is not automatically blocking if the compiled field is explicit and non-materially verifiable, but it must limit confidence and appear in the inventory.

## Conditional sources

Read only for a targeted gap:

- `literature/synthesis_workbench.json`: mechanism or claim derivation is unclear;
- `literature/domain_map.json`: bridge-domain meaning is disputed;
- `literature/comparison_table.csv`: baseline identity or comparison family is unclear;
- `literature/paper_notes/`: a specific full-text mechanism claim needs verification;
- `literature/paper_notes_abstract/`: only when noting weak abstract-level support;
- `resources/`: an already supplied resource changes capability or baseline interpretation;
- `user_seeds/seed_external_resources.jsonl`: resource hint provenance;
- `user_seeds/bridge_domains.yaml`: bridge-domain search prior only.

## Reading rules

- Formulate the field-level question before opening a conditional source.
- Record every used source in `source_files_checked` and every unused optional source as `not_needed` only when it was inventoried.
- Do not treat seed directions, filenames, repository names, or README mentions as verified method/baseline equivalence.
- Abstract-only notes are weak evidence and cannot resolve a material mechanism conflict.
- Do not browse the web to repair missing Pre-T5 context during alignment unless the root explicitly authorizes a current-fact check.
- Do not edit any source file.

## Stop reading

Stop when every required confirmed-scope field has adequate provenance or when a blocking issue is established. More reading must answer a specific unresolved question; “more context may help” is insufficient.
