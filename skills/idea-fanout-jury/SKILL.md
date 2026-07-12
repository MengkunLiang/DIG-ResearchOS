---
name: idea-fanout-jury
description: Generate, compare, and score evidence-bounded research directions from a problem brief and ResearchOS literature artifacts. Use when a researcher wants a transparent idea fan-out and human-readable jury report before choosing a direction.
tools:
  - read_file
  - write_file
  - list_files
  - analyze_idea_concentration
  - compute_idea_novelty_signal
  - finish_task
strict_tools: true
model_tier: heavy
temperature: 0.4
allowed_read_prefixes:
  - user_inputs/idea-fanout-jury/
  - literature/
  - ideation/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - ideation/
outputs_expected:
  jury_report: ideation/skill_idea_jury.md
  jury_data: ideation/skill_idea_jury.json
interaction:
  mode: guided
  language: zh-CN
  summary: 从问题、文献综合和已有 idea 中生成候选研究方向，并把依据、风险和评分完整展示给人选择。
  request_required: true
  request_prompt: 请说明研究问题、希望保留或避免的方向，以及本次要探索还是要做可执行选择。
  example_request: 围绕 LLM agent uplift 的因果机制生成 6 个可测试方向，优先可用公开日志验证的思路。
  required_inputs:
    - id: problem_brief
      label: 研究问题与约束
      description: 至少包含研究对象、核心问题、已有假设或限制，以及希望输出的候选数量或选择偏好。
      paths:
        - user_inputs/idea-fanout-jury/problem.md
        - ideation/seed_ideas.md
      extensions: [.md]
      min_bytes: 80
      example: user_inputs/idea-fanout-jury/problem.md
  optional_inputs:
    - id: literature_synthesis
      label: 文献综合材料
      description: 可选；提供后每个方向都要链接到实际综合或文献笔记证据，而非模型记忆。
      paths:
        - literature/synthesis.md
        - literature/domain_map.json
      extensions: [.md, .json]
      min_bytes: 80
      example: literature/synthesis.md
  outputs:
    - id: jury_report
      label: 完整候选与评分报告
      path: ideation/skill_idea_jury.md
      description: 以中文展示每个方向、证据、假设、可证伪实验、风险、评分和推荐操作。
    - id: jury_data
      label: 结构化候选数据
      path: ideation/skill_idea_jury.json
      description: 机器可读的候选、评分、证据路径和选择状态，可供后续 T4/T5 使用。
---

# Transparent Idea Fanout and Jury

Read the verified brief and any selected literature artifacts. Generate diverse candidates through direct synthesis, seed refinement, mechanism challenge, reverse operation, subgroup failure, and missing-area exploration. Do not pretend that a novelty signal proves novelty.

For each candidate, report: the problem it changes, causal or mechanistic rationale, exact source artifact and note/section anchor when available, falsifiable hypothesis, smallest viable test, expected failure mode, overlap risk, and 1–5 scores for evidence grounding, novelty plausibility, testability, impact, and risk. Call `compute_idea_novelty_signal` only when `literature/domain_map.json` is available. Call `analyze_idea_concentration` after building the candidate set and explain any origin/family imbalance.

Write both declared outputs. The Markdown report must be complete enough for a human to choose, merge, request reanalysis, or reject a direction without opening a hidden prompt. Finish with the output paths and a short recommended next action.
