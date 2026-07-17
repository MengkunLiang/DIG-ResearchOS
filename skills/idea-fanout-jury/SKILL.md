---
name: idea-fanout-jury
description: Generate, compare, and score evidence-calibrated research directions from a problem brief and ResearchOS literature artifacts. Use when a researcher wants a transparent, creativity-enabled idea fan-out and human-readable jury report before choosing a direction.
tools:
  - read_file
  - write_file
  - list_files
  - analyze_idea_concentration
  - compute_idea_novelty_signal
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.4
allowed_read_prefixes:
  - project.yaml
  - user_inputs/idea-fanout-jury/
  - user_seeds/
  - literature/
  - ideation/
  - drafts/survey/
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
  example_request: 围绕当前研究问题的因果机制生成 6 个可测试方向，优先可用公开数据或日志验证的思路。
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

Read the verified brief and only the selected/declared project artifacts. Generate diverse candidates through direct synthesis, seed refinement, mechanism challenge, reverse operation, subgroup failure, and missing-area exploration. Do not pretend that a novelty signal proves novelty.

Before generating candidates, read the intake packet and the actual selected problem/literature inputs. Do not probe unrelated conventional filenames. If the problem brief lacks a decision-critical constraint, or literature materials are required for the requested certainty but absent, write `user_inputs/idea-fanout-jury/_followup_request.md`, call `ask_human`, and wait. The follow-up must name the missing fact, explain why it matters, and give an upload path or answer format.

When literature synthesis or paper cards are absent, produce an explicitly labelled **preliminary, evidence-insufficient concept set**. General scholarly knowledge, counterfactual reasoning, and structural cross-domain analogy may still create a genuinely non-obvious concept, but every such element must be marked `conjectural` and `verification_required=true` with a concrete reading or validation upgrade. Do not state or imply novelty, rank candidates by unsupported scores, or invent a current-project dataset, benchmark, split, baseline, metric, AUUC/Qini value, compute budget, seed, command, or expected numerical improvement. Use `unknown` or `proposed_not_verified`, state the evidence needed, and ask the human whether to add literature evidence before treating a concept as selectable. Concrete metrics and experimental details remain allowed when an allowed input or audited artifact explicitly supplies them; record the exact path and section/field.

For each evidence-supported candidate, report: the problem it changes, causal or mechanistic rationale, exact source artifact and note/section anchor, falsifiable hypothesis, smallest viable test, expected failure mode, overlap risk, and 1–5 scores for evidence grounding, novelty plausibility, testability, impact, and risk. Call `compute_idea_novelty_signal` only when `literature/domain_map.json` is available. Call `analyze_idea_concentration` after building the candidate set and explain any origin/family imbalance. For evidence-insufficient concepts, replace the scorecard with an explicit missing-evidence ledger rather than fabricating scores.

Write both declared outputs. The Markdown report must be complete enough for a human to choose, merge, request reanalysis, or reject a direction without opening a hidden prompt. Finish with the output paths and a short recommended next action.

## Native T4 boundary

This Skill is an exploratory input to T4, not a replacement for the native Evolutionary Pipeline. Its outputs may be supplied to Evidence Routing as researcher-provided Seeds, but it must not create, modify, or claim to select
`ideation/evidence/`, `ideation/populations/`, `ideation/evolution/`,
`ideation/portfolio.json`, or `ideation/final_cards/`. Native T4 alone forms multi-route P0, creates Mutation/Crossover Children O0, independently scores the union, updates P1, and presents the MMR Portfolio. If a researcher wants to combine pieces from different native Candidates, direct them to Gate1: the runtime first requires a Compatibility Check and Gene Donor Map, then a second confirmation before a Human-composed Candidate can be scored.

For starting, resuming, inspecting, or safely handing off native T4 itself, use the `t4-evolution` Skill. This exploratory Skill remains an optional Seed producer and never becomes a substitute for the native Population workflow.

When handing off an exploratory concept, label parametric-knowledge content as
`conjectural` and `verification_required=true`. A minimal IdeaSeed may contain only problem, thesis, candidate mechanism, contribution sketch, one falsifiable prediction, main uncertainty, and Route origin. It must never present a model memory as a citation, supported mechanism, verified dataset, metric, result, or novelty conclusion. Native T4 may repair format differences, retain degraded or unscored Candidates, and continue other Routes, but this Skill must not claim that such recovery has verified the concept.
