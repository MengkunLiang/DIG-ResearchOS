---
name: paper-comparison
description: Compare two or more note cards, DOI/arXiv/OpenAlex identifiers, direct PDF URLs, uploaded PDFs, or an explicitly scoped topic-plus-count request on a user-defined research question, method, evidence, limitation, and contribution axis.
tools:
  - read_file
  - list_files
  - grep_search
  - write_file
  - fetch_paper_pdf
  - fetch_paper_metadata
  - openalex_search
  - crossref_search
  - semantic_scholar_search
  - arxiv_search
  - search_papers
  - multi_source_search
  - extract_pdf_text
  - extract_paper_sections
  - save_paper_note
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.15
allowed_read_prefixes:
  - user_inputs/paper-comparison/
  - literature/
  - drafts/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - literature/
intake_tools:
  - fetch_paper_pdf
  - fetch_paper_metadata
  - openalex_search
  - crossref_search
  - semantic_scholar_search
  - arxiv_search
  - search_papers
  - multi_source_search
outputs_expected:
  comparison_report: literature/skill_paper_comparison.md
  comparison_record: literature/skill_paper_comparison.json
interaction:
  mode: guided
  language: zh-CN
  summary: 比较两篇或多篇已有论文阅读笔记、PDF、DOI/arXiv/OpenAlex 标识，或按明确主题和数量检索得到的论文；按问题、机制、方法、证据、局限和可组合性给出可追溯结论。
  request_required: true
  request_prompt: 请说明要比较的研究问题、比较维度和希望得到的决策，例如选择基线、定位 gap 或合并机制。
  example_request: 比较这些笔记卡如何处理 treatment heterogeneity，指出可作为 baseline 的方法和不能直接合并的假设。
  required_inputs:
    - id: sources
      label: 待比较的来源清单
      description: 一行一个来源；可为 workspace 相对的 paper note/PDF 路径、DOI、arXiv/OpenAlex ID、直接 PDF URL 或精确标题。也可在 request 中明确“主题 + 数量”，由系统先检索候选并保留检索记录；至少需要两项来源或一个明确主题数量请求。
      paths:
        - user_inputs/paper-comparison/sources.md
      extensions: [.md]
      min_bytes: 20
      example: user_inputs/paper-comparison/sources.md
  optional_inputs:
    - id: comparison_context
      label: 比较框架或目标决策
      description: 可选；给出固定比较轴、目标投稿方向、研究场景或希望避免的结论。
      paths:
        - user_inputs/paper-comparison/context.md
      extensions: [.md]
      min_bytes: 10
      example: user_inputs/paper-comparison/context.md
  outputs:
    - id: comparison_report
      label: 论文比较报告
      path: literature/skill_paper_comparison.md
      description: 人类可读的比较矩阵、共同点/冲突、证据边界、可组合性和下一步建议。
    - id: comparison_record
      label: 论文比较结构记录
      path: literature/skill_paper_comparison.json
      description: 机器可读的来源路径、比较字段、相同/不同点、证据等级和未决项。
---

# Source-Grounded Paper Comparison

Read the source list first and classify each entry as a workspace path, PDF, DOI/arXiv/
OpenAlex identifier, URL, exact title, or an explicit topic-plus-count request. Resolve
identifiers with metadata/search tools, and attempt PDF retrieval only to
`user_inputs/paper-comparison/` when the user authorized that source. For a topic-plus-
count request, write the exact query, returned candidates, selection rule, and access
outcome before comparing; do not silently broaden the topic or replace unread papers
with model memory. If a source is missing, malformed, inaccessible, or not a readable
note/PDF, write a focused follow-up request and retain the limitation. Extract only what
the actual sources support and retain each source path plus section/field anchors.

The report must distinguish observed facts, analytical comparisons, and proposed
research implications. Compare at least: problem/unit of analysis, mechanism or design
rationale, method/intervention, data/evaluation, evidence status, limitations, and
nearest usable role (baseline, mechanism source, boundary case, or background). State
whether a proposed merger is evidence-supported, merely plausible, or currently
unsupported. Do not create BibTeX entries or say a source supports a claim without an
anchor.
