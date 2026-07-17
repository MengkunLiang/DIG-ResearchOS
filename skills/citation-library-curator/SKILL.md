---
name: citation-library-curator
description: Audit and curate a user-supplied BibTeX library into a separate, source-traceable reference candidate set with duplicate, malformed, unresolved, and verification-status reporting. Use before citation provenance audit, paper writing, or submission preparation.
tools:
  - read_file
  - write_file
  - crossref_get_work
  - crossref_search
  - openalex_get_work
  - semantic_scholar_get_paper
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.05
allowed_read_prefixes:
  - user_inputs/citation-library-curator/
  - literature/
  - drafts/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - literature/
outputs_expected:
  curated_bibliography: literature/skill_curated_references.bib
  audit_report: literature/skill_reference_library_audit.md
  audit_record: literature/skill_reference_library_audit.json
interaction:
  mode: guided
  language: zh-CN
  summary: 整理用户上传的 BibTeX 库，保留来源、重复/冲突/缺失字段与可核验状态，不修改原始库。
  request_required: false
  request_prompt: 可选：说明目标语言、引用风格、重点领域或是否只做审计而不生成候选库。
  example_request: 审计这个 BibTeX 库，优先解决 DOI 冲突和重复 key；没有 provider 证据的条目不要自动补字段。
  required_inputs:
    - id: bibliography
      label: 原始 BibTeX 文献库
      description: 上传需要审计的 `.bib` 文件。原文件只读，Skill 会写独立候选库。
      paths:
        - user_inputs/citation-library-curator/references.bib
        - literature/related_work.bib
      extensions: [.bib]
      min_bytes: 40
      example: user_inputs/citation-library-curator/references.bib
  optional_inputs:
    - id: curation_policy
      label: 整理策略
      description: 可选；说明允许的来源、是否保留 preprint、目标 venue 或必须保留的 citation key。
      paths:
        - user_inputs/citation-library-curator/policy.md
      extensions: [.md]
      min_bytes: 10
      example: user_inputs/citation-library-curator/policy.md
  outputs:
    - id: curated_bibliography
      label: 整理后的 BibTeX 候选库
      path: literature/skill_curated_references.bib
      description: 独立候选库；只保留原始条目或基于实际 provider 响应的可追溯修复，不覆盖输入库。
    - id: audit_report
      label: 引用库审计报告
      path: literature/skill_reference_library_audit.md
      description: 按 key 列出重复、冲突、缺失字段、验证状态、保留/暂缓理由和人工确认项。
    - id: audit_record
      label: 引用库审计结构记录
      path: literature/skill_reference_library_audit.json
      description: 机器可读的条目状态、来源、修复动作和未解决项目。
---

# Citation Library Curation

Read the entire supplied bibliography before proposing changes. Preserve every original entry in the audit record. Group possible duplicates by DOI first, then by normalized title/author/year only when the source evidence supports that grouping. Query provider metadata only to verify or repair explicit candidate fields; a provider failure or an ambiguous title match is a reported uncertainty, not permission to invent metadata.

Never overwrite the source `.bib`, silently rename a key, or replace a preprint with a different work. The curated bibliography must document key-preservation decisions in the audit report. For incomplete but important entries, preserve their original fields in the candidate library and mark them for human confirmation rather than fabricating authors, venue, pages, DOI, or year.
