---
name: literature-query-plan
description: Build a transparent, multilingual and reproducible literature query portfolio from a scoped research brief. Use before web search when the researcher needs explicit source coverage, language policy, inclusion logic, and targeted evidence gaps.
tools:
  - read_file
  - write_file
  - list_files
  - finish_task
strict_tools: true
model_tier: medium
temperature: 0.2
allowed_read_prefixes:
  - user_inputs/literature-query-plan/
  - ideation/
  - literature/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - literature/
outputs_expected:
  query_plan: literature/skill_query_plan.md
  query_plan_json: literature/skill_query_plan.json
interaction:
  mode: guided
  language: zh-CN
  summary: 在实际联网检索前生成可审计的 query 组合、来源/语言策略、去重逻辑和证据缺口计划。
  request_required: true
  request_prompt: 请说明要支持的研究问题或论文 section、时间范围、目标语言和希望优先的来源。
  example_request: 为英文 Related Work 设计 2022-2026 的 query 组合；正文为英文，保留中文文献作为中国情境或政策证据，不把它们混入英文主检索。
  required_inputs:
    - id: scope
      label: 研究范围或检索问题
      description: 使用 research-scope 产物或上传独立问题简报；必须写明要回答的证据问题。
      paths:
        - user_inputs/literature-query-plan/brief.md
        - ideation/skill_scope_brief.md
      extensions: [.md]
      min_bytes: 80
      example: ideation/skill_scope_brief.md
  optional_inputs:
    - id: current_synthesis
      label: 当前综合/缺口
      description: 可选；用于排除已覆盖材料并为确切 claim 或 section 定向补检。
      paths:
        - literature/synthesis.md
        - literature/missing_areas.md
      extensions: [.md]
      min_bytes: 80
      example: literature/missing_areas.md
  outputs:
    - id: query_plan
      label: 检索计划
      path: literature/skill_query_plan.md
      description: 按证据问题、来源、语言、检索式、筛选/去重和停止条件组织的可读计划。
    - id: query_plan_json
      label: 结构化检索计划
      path: literature/skill_query_plan.json
      description: 机器可读的 query portfolio、语言策略和预期覆盖记录。
---

# Reproducible Query Planning

Write the declared Markdown and JSON outputs. Define a language policy explicitly: `manuscript_language=en` means Chinese queries/literature are excluded from the English main-evidence pool unless the request explicitly retains them for a named contextual/policy role; `zh` mirrors that policy; `mixed` keeps source language and role per record. Do not claim that a language has been searched yet.

For every query cluster include the evidence question, source(s), query text, language, year range, inclusion/exclusion rules, expected note-card sections, deduplication keys, coverage target, and stop/escalation condition. Reserve broad search for corpus construction; for a single weak claim, prescribe section-level note recovery first.
