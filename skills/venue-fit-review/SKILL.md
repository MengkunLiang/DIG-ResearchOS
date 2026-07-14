---
name: venue-fit-review
description: Review a LaTeX manuscript against a user-supplied conference or journal scope, format, and evaluation expectations, producing a source-aware venue-fit and revision-priority report without changing the manuscript. Use before choosing a venue, polishing a draft, or preparing a submission.
tools:
  - read_file
  - list_files
  - audit_manuscript_claims
  - audit_writing_craft
  - write_file
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.1
allowed_read_prefixes:
  - user_inputs/venue-fit-review/
  - drafts/
  - submission/
  - literature/
  - experiments/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - drafts/
outputs_expected:
  fit_report: drafts/skill_venue_fit_review.md
  fit_record: drafts/skill_venue_fit_review.json
interaction:
  mode: guided
  language: zh-CN
  summary: 对照人工提供的期刊/会议范围、格式和评审期待，审查论文的研究故事、贡献、技术细节、实验和合规风险，给出可执行修订优先级。
  request_required: true
  request_prompt: 请说明目标 venue、当前稿件成熟度，以及最希望核验的契合点，例如技术贡献、理论深度、实验完整性或篇幅。
  example_request: 审查这篇面向 NeurIPS 的稿件，重点判断技术贡献是否清楚、实验是否支持 claim、篇幅是否过度采用 UTD 叙事风格。
  required_inputs:
    - id: manuscript_path
      label: LaTeX 稿件路径
      description: 写入 workspace 相对路径，通常是 drafts/paper.tex 或 submission/main.tex；文件须已存在。
      paths:
        - user_inputs/venue-fit-review/manuscript.md
      extensions: [.md]
      min_bytes: 12
      example: user_inputs/venue-fit-review/manuscript.md
    - id: venue_requirements
      label: Venue 要求或人工整理准则
      description: 上传官方 CFP、作者指南摘要、评审重点或研究团队的 venue checklist；Skill 不会伪造最新规则。
      paths:
        - user_inputs/venue-fit-review/venue.md
      extensions: [.md]
      min_bytes: 40
      example: user_inputs/venue-fit-review/venue.md
  optional_inputs:
    - id: evidence_context
      label: 证据、审稿意见或投稿约束
      description: 可选；列出 bibliography、实验 evidence pack、旧审稿意见、页数限制或禁止修改的部分。
      paths:
        - user_inputs/venue-fit-review/context.md
      extensions: [.md]
      min_bytes: 10
      example: user_inputs/venue-fit-review/context.md
  outputs:
    - id: fit_report
      label: Venue 契合度审查报告
      path: drafts/skill_venue_fit_review.md
      description: 将范围/贡献/叙事/方法/实验/格式分开审查，并按阻断、重要和可选动作排序。
    - id: fit_record
      label: Venue 契合度结构记录
      path: drafts/skill_venue_fit_review.json
      description: 机器可读的人工要求、可核验稿件证据、finding、严重度、依赖项和修订建议。
---

# Venue Fit Review

Read `manuscript.md` to obtain exactly one existing workspace-relative `.tex` path,
then read that manuscript and the supplied venue requirements. Treat the requirements
file as the source of truth for venue-specific rules; it may be incomplete, so record
unknowns rather than asserting current official limits or policies. Inspect relevant
bibliography/evidence materials only when they are listed in the optional context or
needed to substantiate a specific finding.

Run `audit_manuscript_claims` and `audit_writing_craft` against the chosen manuscript
where their input contracts permit, preserving their raw finding paths and status in
the review. Evaluate scope fit separately from writing quality. For CCF-A ML
conferences, prioritize a precise technical contribution, formal or algorithmic
clarity where appropriate, reproducible experimental evidence, bounded claims, and a
compact story; for journal/UTD-like work, evaluate theory/motivation, identification
or rationale, mechanism, boundary conditions, and a coherent research narrative.
These are review lenses, not invented venue rules.

Do not edit the manuscript, bibliography, or venue guide. Write both outputs with
blocking issues, evidence-supported strengths, uncertainty, and a revision order that
states required inputs for each action. Finish by naming the two outputs and every
unverified venue condition.
