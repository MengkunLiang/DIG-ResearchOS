---
name: benchmark-repo-miner
description: Guide benchmark, dataset, and baseline repository mining before external experiment handoff.
allowed_tools:
  - read_file
  - list_files
  - finish_task
max_steps: 10
max_tokens_total: 80000
temperature: 0.3
---

# Benchmark Repo Miner

Use this skill to structure benchmark and baseline repo evidence. The output should be candidate cards and risk notes, not a final scientific judgment.

Prioritize reproducibility metadata: official implementation, third-party implementation, dataset split, metric, seed reporting, compute requirements, and license/access constraints.

