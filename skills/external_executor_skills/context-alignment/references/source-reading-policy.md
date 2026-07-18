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
- `literature/bridge_domain_plan.json`, `literature/cross_domain_catalogs/index.json`, and a named
  `literature/cross_domain_catalogs/<bridge-id>/bridge_context.json`: a confirmed Cross-domain transfer, its intended question, or its reading boundary needs interpretation;
- `literature/cross_domain_catalogs/<bridge-id>/paper_catalog.json`: a bridge lead is relevant to a baseline, mechanism contrast, external-validity condition, or a follow-up reading decision. The catalog is deliberately useful even when no paper has been deeply read; it is distinct from `literature/bridge_notes/`, which contains actual Bridge reading notes.
- `literature/comparison_table.csv`: baseline identity or comparison family is unclear;
- `literature/deep_read_notes/`: a specific full-text mechanism claim needs verification;
- `literature/shallow_read_notes/`: only when noting weak abstract-level support;
- `resources/`: a supplied, acquired, or reimplemented resource changes capability or baseline interpretation;
- `user_seeds/seed_external_resources.jsonl`: resource hint provenance;
- `user_seeds/bridge_domains.yaml`: bridge-domain search prior only.
- `ideation/hypothesis_brief.yaml` and `ideation/selected/t45_search_targets.json`: Pre-Novelty lineage and search-scope context only. They may explain how the selected Candidate reached T4.5, but they cannot replace formal hypotheses, the experiment plan, or the novelty audit.

## Reading rules

- Formulate the field-level question before opening a conditional source.
- Record every used source in `source_files_checked` and every unused optional source as `not_needed` only when it was inventoried.
- Do not treat seed directions, filenames, repository names, or README mentions as verified method/baseline equivalence.
- Abstract-only notes are weak evidence and cannot resolve a material mechanism conflict.
- A bridge catalog record with an abstract may inform a transfer hypothesis, analogy, baseline search, boundary condition, validation design, or reading priority. It is not direct support for a mechanism, experimental result, or method equivalence. A metadata-only catalog record is a discovery lead only. When a catalog links a canonical note, that note—not the catalog—is authoritative for claim use.
- Never copy bridge catalog prose into an experiment claim or silently discard a confirmed bridge merely because it lacks a full-text note. Record the bridge as `contextual`,
  `abstract_level`, `metadata_lead`, or `claim_usable_note` according to what actually exists.
- Do not browse the web to repair missing Pre-T5 context during alignment unless the root explicitly authorizes a current-fact check.
- Do not edit any source file.

## Stop reading

Stop when every required confirmed-scope field has adequate provenance or when a blocking issue is established. More reading must answer a specific unresolved question; “more context may help” is insufficient.
