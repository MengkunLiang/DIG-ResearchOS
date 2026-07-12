---
name: paper-peer-review
description: Produce an evidence-aware, venue-sensitive peer-review report and prioritized revision plan for an academic LaTeX manuscript without rewriting the manuscript. Use after a draft exists and before revision, compilation, or submission.
tools:
  - read_file
  - write_file
  - audit_manuscript_claims
  - audit_writing_craft
  - finish_task
strict_tools: true
model_tier: heavy
temperature: 0.15
allowed_read_prefixes:
  - user_inputs/paper-peer-review/
  - drafts/
  - literature/
  - experiments/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - drafts/
outputs_expected:
  review_report: drafts/skill_peer_review.md
  review_record: drafts/skill_peer_review.json
  revision_plan: drafts/skill_peer_review_priorities.md
interaction:
  mode: guided
  language: zh-CN
  summary: 对已有 LaTeX 稿件做证据约束的同行审阅，输出贡献、方法、实验、写作、引用和投稿风险的优先级修订计划。
  request_required: true
  request_prompt: 请说明目标会议/期刊、语言、稿件类型、想要的审稿视角，以及是否需要双盲或 rebuttal 前检查。
  example_request: 以 NeurIPS 审稿人视角审阅英文稿，重点找 technical contribution、ablation、claim strength 和可复现性问题；不要改写原稿。
  required_inputs:
    - id: manuscript
      label: 待审阅的 LaTeX 稿件
      description: 完整主稿；Skill 只读审阅，不会改写该文件。
      paths:
        - user_inputs/paper-peer-review/paper.tex
        - drafts/paper.tex
      extensions: [.tex]
      min_bytes: 120
      example: drafts/paper.tex
  optional_inputs:
    - id: bibliography
      label: 参考文献库
      description: 可选；用于识别稿件 citation key 与引用边界风险。
      paths:
        - user_inputs/paper-peer-review/references.bib
        - literature/related_work.bib
      extensions: [.bib]
      min_bytes: 40
      example: literature/related_work.bib
    - id: evidence_pack
      label: 实验证据与论证材料
      description: 可选；提供后可区分“尚未证明”与“稿件没有利用现有证据”。
      paths:
        - user_inputs/paper-peer-review/evidence.md
        - drafts/experiment_evidence_pack.json
      extensions: [.md, .json]
      min_bytes: 40
      example: drafts/experiment_evidence_pack.json
    - id: venue_context
      label: venue/模板或审稿重点
      description: 可选；官方规则必须由用户提供或标为待核验，Skill 不以模型记忆伪造政策。
      paths:
        - user_inputs/paper-peer-review/venue_context.md
        - drafts/writing_style.json
      extensions: [.md, .json]
      min_bytes: 20
      example: drafts/writing_style.json
  outputs:
    - id: review_report
      label: 同行审阅报告
      path: drafts/skill_peer_review.md
      description: 按贡献、rationale、方法、实验、引用、写作、局限和投稿风险列出可核验的主要/次要问题。
    - id: review_record
      label: 同行审阅结构记录
      path: drafts/skill_peer_review.json
      description: 机器可读的问题、严重度、证据路径、建议修复和人工确认项。
    - id: revision_plan
      label: 修订优先级计划
      path: drafts/skill_peer_review_priorities.md
      description: 按 blocker、major、minor、可选增强排序，说明每项需要补证据、重写还是人工决策。
---

# Evidence-Aware Peer Review

Read the intake packet and manuscript before reviewing. Run `audit_manuscript_claims`
and `audit_writing_craft` first; treat deterministic failures as findings rather than
rewriting around them. Inspect the actual manuscript and any supplied evidence/venue
context. Use a venue-aware review lens: UTD/IS-style work needs a coherent
phenomenon/theory/rationale-to-mechanism story, while CCF-A ML work needs a precise
technical bottleneck, method-component contribution, and credible main/ablation/analysis
evidence chain.

Classify every finding as `blocker`, `major`, `minor`, `optional`, or
`human_confirmation_required`. Each finding needs a concrete manuscript location,
observed basis, why it affects validity/clarity/novelty, and a repair type: evidence,
analysis, prose, citation, experiment, or decision. Do not invent venue policy, reviewer
consensus, missing experiments, numerical results, or citations. Do not edit the
manuscript; `paper-revision` handles approved changes later.
