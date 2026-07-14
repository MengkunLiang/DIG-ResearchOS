---
name: citation-provenance-audit
description: Audit whether manuscript citations and claim wording are traceable to available BibTeX and paper-note evidence. Use before paper polishing, revision, or submission when citation hallucination risk must be made explicit.
tools:
  - read_file
  - write_file
  - list_files
  - grep_search
  - audit_manuscript_claims
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.1
allowed_read_prefixes:
  - user_inputs/citation-provenance-audit/
  - drafts/
  - literature/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - drafts/
outputs_expected:
  provenance_report: drafts/skill_citation_provenance_audit.md
  provenance_record: drafts/skill_citation_provenance_audit.json
interaction:
  mode: guided
  language: zh-CN
  summary: 审计稿件中的 citation key、可用 BibTeX、笔记 provenance 和允许的主张强度，显式暴露不能支持的引用。
  request_required: true
  request_prompt: 请说明要审计的稿件语言、目标 section 或高风险 claim，以及是否只报告不修改原稿。
  example_request: 审计英文 Related Work 和 Introduction；只报告 citation provenance，不改动 paper.tex。
  required_inputs:
    - id: manuscript
      label: 待审计论文 LaTeX
      description: 原稿只读；审计结果写入独立报告，不会静默修订正文。
      paths:
        - user_inputs/citation-provenance-audit/manuscript.tex
        - drafts/paper.tex
      extensions: [.tex]
      min_bytes: 120
      example: drafts/paper.tex
    - id: bibliography
      label: 已有 BibTeX
      description: citation key 必须来自这个文件；没有 BibTeX 不得假设引用存在。
      paths:
        - user_inputs/citation-provenance-audit/references.bib
        - literature/related_work.bib
      extensions: [.bib]
      min_bytes: 40
      example: literature/related_work.bib
  optional_inputs:
    - id: note_index
      label: 文献笔记索引
      description: 可选；用于回查 citation 对应的 section-level evidence。
      paths:
        - literature/synthesis_workbench.json
        - drafts/manuscript_resource_index.json
      extensions: [.json]
      min_bytes: 80
      example: literature/synthesis_workbench.json
  outputs:
    - id: provenance_report
      label: 引用 provenance 报告
      path: drafts/skill_citation_provenance_audit.md
      description: 按 citation/claim 列出 BibTeX、笔记 section、允许措辞和需修复问题。
    - id: provenance_record
      label: 结构化 provenance 记录
      path: drafts/skill_citation_provenance_audit.json
      description: 机器可读的 citation key、来源、支持等级、风险和修复动作。
---

# Citation Provenance Audit

First run `audit_manuscript_claims` on the selected manuscript. Then check every in-text key against the supplied bibliography. For high-risk claims, locate the cited paper's note and exact section before classifying support. The report must separate: missing key, key exists but no note provenance, note supports only background/boundary wording, and note supports the actual claim.

Never create BibTeX entries, replace a citation based on model memory, or claim a paper was read when its note is absent. Preserve the original manuscript; recommend exact repairs and downgraded wording instead.
