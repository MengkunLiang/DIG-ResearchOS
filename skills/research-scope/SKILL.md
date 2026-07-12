---
name: research-scope
description: Normalize a research topic and user materials into a bounded, evidence-aware scope brief before literature search or ideation. Use when a researcher needs to turn an informal topic into clear questions, exclusions, success criteria, and an upload inventory.
tools:
  - read_file
  - write_file
  - list_files
  - finish_task
strict_tools: true
model_tier: medium
max_steps: 12
max_tokens_total: 90000
temperature: 0.2
allowed_read_prefixes:
  - user_inputs/research-scope/
  - literature/
  - ideation/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - ideation/
outputs_expected:
  scope_brief: ideation/skill_scope_brief.md
  scope_record: ideation/skill_scope_brief.json
interaction:
  mode: guided
  language: zh-CN
  summary: 将研究主题和用户材料收敛为可检索、可证伪且边界明确的研究范围；不会把猜测写成已证实结论。
  request_required: true
  request_prompt: 请说明目标论文或项目类型、希望解决的问题、目标读者、语言和任何不可触碰的约束。
  example_request: 将“研究 LLM agent 的长期表现”收敛为一份英文实证论文的研究范围，明确数据、因果边界和不做的方向。
  required_inputs:
    - id: topic_brief
      label: 主题与现有材料
      description: 包含至少一个问题描述、已有观察/方法线索、目标读者或场景；可附链接摘要但不要放凭据。
      paths:
        - user_inputs/research-scope/brief.md
      extensions: [.md]
      min_bytes: 80
      example: user_inputs/research-scope/brief.md
  optional_inputs:
    - id: constraints
      label: 约束或种子假设
      description: 可选；预算、数据许可、时间范围、必须保留/排除的立场或已有假设。
      paths:
        - user_inputs/research-scope/constraints.md
        - user_seeds/seed_constraints.md
      extensions: [.md]
      min_bytes: 20
      example: user_inputs/research-scope/constraints.md
  outputs:
    - id: scope_brief
      label: 研究范围简报
      path: ideation/skill_scope_brief.md
      description: 面向研究者的研究问题、边界、证据需求、输入清单与下一步建议。
    - id: scope_record
      label: 结构化范围记录
      path: ideation/skill_scope_brief.json
      description: 供检索、idea 和写作 Skills 复用的目标、约束、术语和未决问题。
---

# Research Scope

Read the supplied brief before inferring any scope. Produce both declared outputs. Separate `observed`, `proposed`, and `unknown` statements. The brief must contain: primary and secondary questions, unit of analysis, target setting, in-scope and out-of-scope boundaries, available versus required evidence, likely search concepts, evaluation success criteria, ethical/licensing constraints, and unresolved decisions.

Do not invent data access, causal identification, literature consensus, or results. When an ambiguity changes the research design materially, record it as a decision for the user rather than silently selecting one.
