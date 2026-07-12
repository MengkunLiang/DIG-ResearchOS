---
name: experiment-design-review
description: Review a proposed research experiment for identification, controls, measurements, baseline fairness, feasibility, and stopping criteria. Use before external execution; this Skill never runs code or changes an executor project.
tools:
  - read_file
  - write_file
  - list_files
  - finish_task
strict_tools: true
model_tier: heavy
max_steps: 16
max_tokens_total: 130000
temperature: 0.15
allowed_read_prefixes:
  - user_inputs/experiment-design-review/
  - ideation/
  - literature/
  - experiments/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - ideation/
outputs_expected:
  design_review: ideation/skill_experiment_design_review.md
  design_matrix: ideation/skill_experiment_design_matrix.json
interaction:
  mode: guided
  language: zh-CN
  summary: 在运行实验前审查假设、对照、数据、指标、统计/资源风险和失败判据；不调用外部执行器。
  request_required: true
  request_prompt: 请说明要审查的实验目标、最担心的风险、可用资源和是否有必须保留的实验设置。
  example_request: 审查此机制验证实验是否能区分 memory carryover 和 task difficulty；给出最小消融、负对照和停止条件。
  required_inputs:
    - id: hypothesis_or_plan
      label: 假设或实验计划
      description: 至少包括待检验机制、干预、预期结果和已有资源；可使用 hypothesis-compiler 产物。
      paths:
        - user_inputs/experiment-design-review/plan.md
        - ideation/skill_hypothesis_test_plan.md
        - ideation/experiment_plan.md
      extensions: [.md]
      min_bytes: 80
      example: ideation/skill_hypothesis_test_plan.md
  optional_inputs:
    - id: evidence_context
      label: 文献或已有结果上下文
      description: 可选；用于检查基线公平性与已有证据边界，不代表新的实验结果。
      paths:
        - literature/synthesis.md
        - drafts/experiment_evidence_pack.json
      extensions: [.md, .json]
      min_bytes: 80
      example: literature/synthesis.md
  outputs:
    - id: design_review
      label: 实验设计审查
      path: ideation/skill_experiment_design_review.md
      description: 识别风险、混杂、必要对照、指标、可行性、停止与升级决策的可读审查。
    - id: design_matrix
      label: 实验设计矩阵
      path: ideation/skill_experiment_design_matrix.json
      description: 机器可读的 hypothesis-to-measurement、control、risk、kill-criterion 映射。
---

# Experiment Design Review

Review the plan as a falsification problem. For every claimed mechanism, specify the intervention, unit, comparison/control, confounds, measurable outcome, expected null/alternative outcome, data provenance requirement, minimum repetitions or uncertainty requirement if known, and kill criterion. Flag under-identification, unobservable latent variables, leakage, weak baselines, metric gaming, subgroup blindness, and resource infeasibility.

Do not manufacture power calculations, data availability, implementation status, or results. State when a proposed study can only show association or ablation evidence.
