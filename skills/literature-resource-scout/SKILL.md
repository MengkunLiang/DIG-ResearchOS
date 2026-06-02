---
name: literature-resource-scout
description: Guide T2/T3 resource-aware scouting for datasets, benchmarks, baselines, code artifacts, and reproducibility hints.
allowed_tools:
  - read_file
  - list_files
  - finish_task
max_steps: 10
max_tokens_total: 80000
temperature: 0.3
---

# Literature Resource Scout

This skill is guidance for LLM agents. Use deterministic search/list/read tools to collect resource candidates, then let the LLM judge relevance.

Do not hardcode which datasets or baselines are correct. Record candidates, access status, linked papers, possible role, license hints, and risks so T4/T5 can reason from evidence.

