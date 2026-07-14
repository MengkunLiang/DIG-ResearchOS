---
name: hypothesis-compiler
description: Compile a user-selected research direction into falsifiable hypotheses, mechanisms, boundary conditions, and an evidence-aware test plan. Use after idea selection and before any external experiment execution.
tools:
  - read_file
  - write_file
  - list_files
  - grep_search
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.25
allowed_read_prefixes:
  - user_inputs/hypothesis-compiler/
  - ideation/
  - literature/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - ideation/
outputs_expected:
  hypotheses: ideation/skill_hypotheses.md
  test_plan: ideation/skill_hypothesis_test_plan.md
interaction:
  mode: guided
  language: zh-CN
  summary: 将一个明确选择的 idea 编译成可证伪的假设、替代解释、边界和最低成本验证计划。
  request_required: true
  request_prompt: 请说明已选择的方向、希望保留的机制表述、可用数据/资源和不可接受的风险。
  example_request: 将选定的 D2 编译成 2-3 个可证伪假设，优先使用现有 agent trace 数据，不把相关性写成因果。
  required_inputs:
    - id: selected_direction
      label: 已选择的方向
      description: 可以是 Gate1 选择文件、selected idea brief 或包含用户选择与理由的 markdown。
      paths:
        - user_inputs/hypothesis-compiler/selection.md
        - ideation/selected_idea_brief.md
        - ideation/hypothesis_brief.yaml
      extensions: [.md, .yaml]
      min_bytes: 80
      example: ideation/selected_idea_brief.md
  optional_inputs:
    - id: candidate_pool
      label: 候选与接地材料
      description: 可选；用于保留原有反事实、最近工作和未解决风险。
      paths:
        - ideation/_candidate_directions.json
        - ideation/_pass2_grounding_review.json
      extensions: [.json]
      min_bytes: 80
      example: ideation/_candidate_directions.json
  outputs:
    - id: hypotheses
      label: 假设编译稿
      path: ideation/skill_hypotheses.md
      description: 每条假设的机制、预测、替代解释、边界和不可主张范围。
    - id: test_plan
      label: 假设测试计划
      path: ideation/skill_hypothesis_test_plan.md
      description: 最低验证、对照、指标、失败判据、资源约束和停止条件。
---

# Falsifiable Hypothesis Compilation

When `ideation/hypothesis_brief.yaml` exists, treat it as a Pre-Novelty draft bundle. It preserves the selected Candidate's draft hypotheses, source lineage, and evidence boundary; it is not proof that the hypotheses are novel or ready for execution. Read `ideation/selected/t45_search_targets.json` and `ideation/selected/hypothesis_lineage.json` when available, and preserve the recorded Candidate and paper-note paths.

When a claim needs source verification, reopen only the referenced canonical Paper Note:
`literature/deep_read_notes/` or `literature/bridge_notes/` can support bounded
full/partial-reading claims; `literature/shallow_read_notes/` can support background,
coverage, or an explicit reading-upgrade request only. Do not resurrect or scan retired
`paper_notes*` directories as an additional evidence source.

Build a hypothesis table before prose. Each hypothesis must name the intervention/condition, predicted outcome, mechanism, alternative explanation, discriminating observation, boundary condition, measurement, baseline/control, and kill criterion. Distinguish assumptions from evidence. Re-open a cited note section when a mechanism or boundary is asserted; otherwise mark the statement as proposed. When combining material from more than one Candidate, do not concatenate sentences: first record a Compatibility Check, a Gene Donor Map, the source Candidate IDs, and the reason that one coherent Core Thesis is possible.

Do not create an experiment result, numerical effect, available dataset, or causal identification claim. The test plan is an execution guide only and must not invoke external executors.

## Native T4 compatibility

When the source is a native T4 Candidate, use `ideation/selected/selected_candidate.json`,
`ideation/hypothesis_brief.yaml`, its lineage, and the referenced canonical Paper Notes as
read-only inputs. Do not rewrite `ideation/evidence/`, `ideation/populations/`,
`ideation/evolution/`, `ideation/portfolio.json`, or `ideation/final_cards/`; those preserve
P0/P1, Family, Mutation, Crossover, and Portfolio history. This Skill can prepare a
researcher-facing hypothesis draft, but it cannot upgrade a Pre-Novelty brief into formal
`ideation/hypotheses.md` or `ideation/exp_plan.yaml`. That authority belongs to a passing
T4.5 novelty/collision audit.
