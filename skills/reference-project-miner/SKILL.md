---
name: reference-project-miner
description: Mine local reference research-agent systems into transferable ResearchOS pattern cards, transfer matrix, and review docs.
allowed_tools:
  - read_file
  - list_files
  - mine_reference_projects
  - finish_task
allowed_read_prefixes:
  - ""
  - /mnt/data/reference/
allowed_write_prefixes:
  - researchos_reference/
  - docs/
max_steps: 12
max_tokens_total: 120000
temperature: 0.2
outputs_expected:
  pattern_cards: researchos_reference/pattern_cards.jsonl
  transfer_matrix: researchos_reference/transfer_matrix.csv
  reference_review: docs/reference_project_review.md
---

# Reference Project Miner

Use this skill before major pipeline redesign work. Its job is not to invent science; it converts external research-agent system designs into ResearchOS-readable pattern artifacts.

## Workflow

1. Call `mine_reference_projects`.
2. Read `researchos_reference/pattern_cards.jsonl`, `researchos_reference/transfer_matrix.csv`, and `docs/reference_project_review.md`.
3. Check whether any reference repository is marked `reference_missing`.
4. Summarize which patterns should guide Pre-T5, T5-T7, T8/T9, and runtime resume behavior.
5. Call `finish_task`.

## Interpretation Rules

- Treat pattern cards as methodology hints for LLM agents, not hardcoded scientific conclusions.
- Tools and validators should handle mechanical checks: file existence, schema, hashes, mock flags, allowed paths, and traceability.
- LLM agents should handle knowledge-heavy interpretation: literature meaning, benchmark relevance, claim wording, and writing quality.
- If a reference repo is missing, report that explicitly instead of guessing from memory.

