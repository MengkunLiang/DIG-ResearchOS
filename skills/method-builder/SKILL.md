---
name: method-builder
description: Guide external executors to implement methods from a ResearchOS experiment contract while respecting edit scope.
allowed_tools:
  - read_file
  - finish_task
max_steps: 10
max_tokens_total: 80000
temperature: 0.3
---

# Method Builder

This skill provides implementation-planning guidance for external executors. It should produce method design, implementation plan, ablation plan, and risk notes. It should not write unscoped code inside the ResearchOS main repository.
