---
name: reference-project-miner
description: Mine local reference research-agent repositories into traceable, transferable ResearchOS pattern cards and a transfer matrix. Use when a user supplies local source locations and wants engineering lessons, not copied implementation or unsupported claims.
tools:
  - read_file
  - write_file
  - list_files
  - mine_reference_projects
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.2
allowed_read_prefixes:
  - user_inputs/reference-project-miner/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - researchos_reference/
  - docs/
outputs_expected:
  pattern_cards: researchos_reference/pattern_cards.jsonl
  transfer_matrix: researchos_reference/transfer_matrix.csv
  reference_review: docs/reference_project_review.md
interaction:
  mode: guided
  language: zh-CN
  summary: 从用户列明的本地参考项目中提取可迁移工程模式，并保留源码位置、边界和不适用条件。
  request_required: true
  request_prompt: 请说明要比较的本地项目、希望学习的机制，以及是否只生成审查而不建议迁移。
  example_request: 对比两套本地 research-agent 项目的恢复、日志和 artifact 校验机制，给出可迁移矩阵。
  required_inputs:
    - id: source_manifest
      label: 参考项目清单
      description: 一行一个可访问的本地目录，并附上项目用途或希望检查的模块；不要上传私密凭据。
      paths:
        - user_inputs/reference-project-miner/sources.md
      extensions: [.md]
      min_bytes: 20
      example: user_inputs/reference-project-miner/sources.md
  optional_inputs: []
  outputs:
    - id: pattern_cards
      label: 可迁移模式卡片
      path: researchos_reference/pattern_cards.jsonl
      description: 每张卡片保留参考源码位置、模式、收益、风险和适用前提。
    - id: transfer_matrix
      label: 迁移矩阵
      path: researchos_reference/transfer_matrix.csv
      description: 比较每个模式在 ResearchOS 中的适配价值、代价和边界。
    - id: reference_review
      label: 人类可读审查报告
      path: docs/reference_project_review.md
      description: 汇总可迁移项、拒绝项和后续验证建议。
---

# Reference Project Miner

Read the verified manifest first. The manifest is the only material you may read directly. Pass its explicitly approved local roots to `mine_reference_projects`; do not use `read_file` or `list_files` on an external absolute path, and do not fall back to an unstated default repository. Separate facts observed by the deterministic tool from your transfer judgment. Do not copy project code, credentials, licenses, or undocumented behaviour into ResearchOS. Write the declared outputs and make every recommendation point to a source location and a testable migration hypothesis.
