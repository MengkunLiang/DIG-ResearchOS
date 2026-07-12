---
name: paper-claim-audit
description: Audit manuscript numeric and strength-of-claim statements against a normalized ResearchOS evidence pack and result-to-claim mapping. Use before paper polishing, revision, compilation, or submission when the user needs a deterministic evidence-boundary report.
tools:
  - read_file
  - write_file
  - audit_paper_claims
  - finish_task
strict_tools: true
model_tier: medium
max_steps: 12
max_tokens_total: 80000
temperature: 0.1
allowed_read_prefixes:
  - user_inputs/paper-claim-audit/
  - drafts/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - drafts/
outputs_expected:
  audit_markdown: drafts/paper_claim_audit.md
  audit_json: drafts/paper_claim_audit.json
interaction:
  mode: guided
  language: zh-CN
  summary: 用证据包和 claim mapping 审计论文中的数值与强断言；失败会被保留，不会自动粉饰为通过。
  request_required: false
  request_prompt: 可选：说明希望优先检查的章节或声明类型。
  example_request: 重点检查摘要和实验部分是否有超出证据的提升幅度。
  required_inputs:
    - id: manuscript
      label: 论文 LaTeX 稿件
      description: 需要审计的完整稿件；若放在 user_inputs，先复制到标准 drafts 路径再运行工具。
      paths:
        - user_inputs/paper-claim-audit/paper.tex
        - drafts/paper.tex
      extensions: [.tex]
      min_bytes: 80
      example: drafts/paper.tex
    - id: evidence_pack
      label: 实验证据包
      description: 标准化的可用指标和 mock 状态；没有它不能判断数值声明。
      paths:
        - user_inputs/paper-claim-audit/experiment_evidence_pack.json
        - drafts/experiment_evidence_pack.json
      extensions: [.json]
      min_bytes: 20
      example: drafts/experiment_evidence_pack.json
    - id: result_mapping
      label: 结果到声明映射
      description: 列出允许和禁止的措辞，防止把弱/模拟结果写成强结论。
      paths:
        - user_inputs/paper-claim-audit/result_to_claim.json
        - drafts/result_to_claim.json
      extensions: [.json]
      min_bytes: 20
      example: drafts/result_to_claim.json
  optional_inputs: []
  outputs:
    - id: audit_markdown
      label: 声明审计报告
      path: drafts/paper_claim_audit.md
      description: 面向人阅读的失败、警告和证据边界。
    - id: audit_json
      label: 结构化审计结果
      path: drafts/paper_claim_audit.json
      description: 可供后续修订 Skill 和自动化检查读取。
---

# Evidence Claim Audit

If a verified input was supplied under `user_inputs/paper-claim-audit/`, copy its exact content to the matching standard `drafts/` path before invoking the audit. Record that staging action in the final summary; do not modify its semantics. Read `drafts/writing_style.json` and `drafts/writing_storyline.md` when present so the final summary can identify which venue-specific contribution chain is affected; their presence never changes the deterministic evidence verdict.

If a necessary material is absent after deterministic readiness passes, write `user_inputs/paper-claim-audit/_followup_request.md` with the exact required evidence path and call `ask_human`; do not reinterpret an unsupported result as a pass.

Run `audit_paper_claims` with the standard three `drafts/` paths. Read both generated outputs. Treat every `FAIL`, mock-only marker, unsupported strong claim, or forbidden wording violation as a real failure. Summarize only what the deterministic audit found, point to the exact report paths, and finish. Do not edit the paper in this Skill; use `paper-polish` or `paper-revision` after the user reviews the report.
