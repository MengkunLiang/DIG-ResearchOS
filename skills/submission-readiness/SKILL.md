---
name: submission-readiness
description: Review an already compiled research manuscript for submission readiness, covering traceable artifacts, citation/claim checks, PDF status, anonymity, and unresolved blockers. Use after paper-compile and before actual venue submission.
tools:
  - read_file
  - write_file
  - list_files
  - audit_manuscript_claims
  - finish_task
strict_tools: true
model_tier: medium
temperature: 0.1
allowed_read_prefixes:
  - user_inputs/submission-readiness/
  - drafts/
  - submission/
  - literature/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - submission/
outputs_expected:
  readiness_report: submission/skill_submission_readiness.md
  readiness_record: submission/skill_submission_readiness.json
interaction:
  mode: guided
  language: zh-CN
  summary: 对已编译投稿材料做最终就绪检查，区分通过、警告、阻塞项和人工确认项；不替用户提交论文。
  request_required: true
  request_prompt: 请说明目标会议/期刊、是否匿名投稿、需要检查的政策或截止要求，以及是否允许仅输出报告。
  example_request: 为匿名 NeurIPS 投稿做最终检查，核对 PDF、引用、作者信息和实验 claim；只生成报告。
  required_inputs:
    - id: compiled_bundle
      label: 已编译投稿包
      description: 必须已有真实 PDF 和 compile report；此 Skill 不会把未编译 TeX 说成可投稿。
      paths:
        - user_inputs/submission-readiness/compile_report.json
        - submission/compile_report.json
        - drafts/survey/survey_compile_report.json
      extensions: [.json]
      min_bytes: 80
      example: submission/compile_report.json
  optional_inputs:
    - id: manuscript
      label: 稿件源码
      description: 可选；用于额外运行机械 claim/citation 审计。
      paths:
        - submission/bundle/main.tex
        - drafts/paper.tex
      extensions: [.tex]
      min_bytes: 120
      example: submission/bundle/main.tex
    - id: venue_rules
      label: 目标 venue 规则
      description: 可选；用户提供的官方要求或内部 checklist，不把模型记忆当作政策。
      paths:
        - user_inputs/submission-readiness/venue_rules.md
      extensions: [.md]
      min_bytes: 40
      example: user_inputs/submission-readiness/venue_rules.md
  outputs:
    - id: readiness_report
      label: 投稿就绪报告
      path: submission/skill_submission_readiness.md
      description: 清晰列出通过项、warning、blocker、人工确认和证据路径。
    - id: readiness_record
      label: 结构化就绪记录
      path: submission/skill_submission_readiness.json
      description: 机器可读的检查结果、路径、严重性和下一步。
---

# Submission Readiness Review

Read the intake packet first. For standalone material, stage `user_inputs/submission-readiness/compile_report.json` into `submission/compile_report.json` only when the user supplied a real report and preserve its exact content; otherwise ask for the missing compiled bundle through `user_inputs/submission-readiness/_followup_request.md` and `ask_human`. In a project workspace, inspect the existing report and PDF rather than assuming that file existence proves submission readiness.

Verify the compile report records a successful real PDF and that referenced files exist. Check the supplied venue rules only as supplied text; do not invent policy requirements. When source exists, run the mechanical manuscript audit and carry hard failures into the readiness report.

Classify every item as `pass`, `warning`, `blocker`, or `human_confirmation_required`. Check anonymity conservatively: if names, affiliations, acknowledgements, repository links, or identifying comments are present, report them for human review rather than guessing venue policy. Do not submit, upload, remove author information, or alter the compiled bundle.
