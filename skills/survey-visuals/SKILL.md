---
name: survey-visuals
description: Generate deterministic, data-derived academic figures for a ResearchOS survey from its comparison table. Use when the survey corpus has enough structured metadata; the Skill writes a manifest and skips rather than inventing decorative visuals when data is insufficient.
tools:
  - build_survey_figures
  - read_file
  - finish_task
strict_tools: true
model_tier: medium
max_steps: 8
max_tokens_total: 50000
temperature: 0.0
allowed_read_prefixes:
  - user_inputs/survey-visuals/
  - literature/
  - drafts/survey/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - literature/
  - drafts/survey/
outputs_expected:
  visual_manifest: drafts/survey/figures/survey_visual_manifest.json
interaction:
  mode: guided
  language: zh-CN
  summary: 从 comparison table 生成 150 DPI、英文学术标签、可复现的综述图；数据不足会明确跳过，不生成装饰图。
  request_required: false
  request_prompt: 可选：说明希望优先呈现语料演进、方法 taxonomy，或只生成 manifest。
  example_request: 为英文 survey 生成可用于 Taxonomy 和 Comparative Analysis 的数据图；不要绘制没有数据依据的概念图。
  required_inputs:
    - id: comparison_table
      label: 文献对照表
      description: 每张图需要至少 8 条有效 year/method_family 数据；字段、覆盖或样本量不足会写 skipped manifest。
      paths:
        - user_inputs/survey-visuals/comparison_table.csv
        - literature/comparison_table.csv
      extensions: [.csv]
      min_bytes: 30
      example: literature/comparison_table.csv
  optional_inputs:
    - id: survey_plan
      label: 综述计划
      description: 可选；用于记录图可服务的 taxonomy/comparison section，不改变图中的数据。
      paths:
        - drafts/survey/survey_plan.json
      extensions: [.json]
      min_bytes: 80
      example: drafts/survey/survey_plan.json
  outputs:
    - id: visual_manifest
      label: 综述图 manifest
      path: drafts/survey/figures/survey_visual_manifest.json
      description: 图路径、数据来源、字体、DPI、可插入 section 与跳过原因的可审计记录。
---

# Deterministic Survey Visuals

Read the intake packet before work. For a standalone CSV upload, stage its exact content at `literature/comparison_table.csv` and record that staging action; in a project workspace, inspect the existing table's fields and coverage first. If the table lacks a required column or has too few valid rows, write `user_inputs/survey-visuals/_followup_request.md` only when the user can realistically supply the missing source data; otherwise produce the explicit skipped manifest.

Call `build_survey_figures` once with the declared comparison table. Read the manifest and report generated versus skipped outputs, including valid-row coverage. Do not write LaTeX sections or claim that a figure demonstrates more than its source columns support. A figure may be inserted later only when the manifest lists an existing path and the surrounding survey text explains the data and source. Do not lower the sample threshold merely to fill layout space.
