---
name: paper-identifier-resolver
description: Resolve DOI, arXiv, OpenAlex, Semantic Scholar, or title identifiers into source-traceable paper records and a conservative BibTeX candidate library. Use when a researcher has pasted paper identifiers rather than PDFs and needs verified metadata, unresolved-item reporting, and reusable records.
tools:
  - read_file
  - write_file
  - process_seed_paper
  - crossref_get_work
  - crossref_search
  - openalex_get_work
  - openalex_search
  - semantic_scholar_get_paper
  - semantic_scholar_search
  - arxiv_search
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.1
allowed_read_prefixes:
  - user_inputs/paper-identifier-resolver/
  - user_seeds/
  - literature/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - user_seeds/
  - literature/
outputs_expected:
  records: literature/skill_identifier_records.jsonl
  report: literature/skill_identifier_report.md
  bibliography: literature/skill_identifier_references.bib
interaction:
  mode: guided
  language: zh-CN
  summary: 粘贴 DOI、arXiv ID、OpenAlex/Semantic Scholar ID 或论文标题，解析为带来源和未解决项的标准论文记录。
  request_required: false
  request_prompt: 可选：说明这些论文要作为种子、Related Work、综述语料还是特定 claim 的候选来源。
  example_request: 将这些 DOI 和 arXiv ID 解析为英文 Related Work 候选；不要把无法核验的标题写进参考文献。
  required_inputs:
    - id: identifiers
      label: 论文标识符清单
      description: 一行一个 DOI、arXiv ID、OpenAlex/Semantic Scholar ID 或完整标题；可在行尾补充用途说明。
      paths:
        - user_inputs/paper-identifier-resolver/identifiers.md
      extensions: [.md]
      min_bytes: 5
      example: user_inputs/paper-identifier-resolver/identifiers.md
  optional_inputs:
    - id: resolution_context
      label: 解析范围与用途
      description: 可选；说明目标语言、年份范围、研究主题或每项要作为 seed/背景/比较对象的角色。
      paths:
        - user_inputs/paper-identifier-resolver/context.md
      extensions: [.md]
      min_bytes: 10
      example: user_inputs/paper-identifier-resolver/context.md
  outputs:
    - id: records
      label: 标准论文记录
      path: literature/skill_identifier_records.jsonl
      description: 每行一条含原始标识符、解析来源、标准元数据、可访问线索和解析状态的记录。
    - id: report
      label: 标识符解析报告
      path: literature/skill_identifier_report.md
      description: 区分已核验、候选匹配、冲突和无法解析项目，并说明后续动作。
    - id: bibliography
      label: 保守 BibTeX 候选库
      path: literature/skill_identifier_references.bib
      description: 只包含从实际 provider 响应得到且字段足以形成条目的记录；不为未解析项目补造引用。
---

# Paper Identifier Resolver

Read the identifier list line by line. Normalize prefixes such as `doi:`, DOI URLs,
`arXiv:`, and provider URLs without changing their original text. Treat
`10.48550/arXiv.<id>` as an arXiv identifier and resolve it through the arXiv route
before attempting Crossref: it is a registered arXiv DOI but is not necessarily a
Crossref work. For recognized DOI and arXiv IDs, call `process_seed_paper` with
`source="doi"` or `source="arxiv_id"`; use `arxiv_search` only for broader title/topic
fallback, never by passing its provider-only `id:` syntax. For every item, use the most
direct registered provider tool first and record the provider, requested ID, returned ID,
timestamp-independent metadata fields, and match confidence. A title search is only a
candidate match until title/author/year evidence makes the match unambiguous.

For a confirmed DOI/arXiv/title, call `process_seed_paper` to register the source as a
reusable seed record. Do not silently treat an API/network failure as a missing paper.
Write one JSONL record for every input item, including unresolved and ambiguous items.
The report must list exact identifiers requiring human confirmation. Produce BibTeX only
from fields actually returned by a provider; preserve missing fields rather than guessing
venue, pages, author order, or publication year. Do not download PDFs in this Skill.

Finish only after the three declared outputs exist and the report names the verified,
ambiguous, and unresolved counts.
