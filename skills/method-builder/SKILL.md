---
name: method-builder
execution_scope: internal_only
execution_owner: 项目专属 external-executor Skill Suite
description: 为外部执行器提供基于 ResearchOS 实验契约的方法实现指导，并严格遵守允许的编辑范围。
allowed_tools:
  - read_file
  - finish_task
temperature: 0.3
---

# Method Builder

This skill provides implementation-planning guidance for external executors. It should produce method design, implementation plan, ablation plan, and risk notes. It should not write unscoped code inside the ResearchOS main repository.
