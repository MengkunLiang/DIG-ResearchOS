---
name: literature-comparison-studio
description: Turn a researcher’s comparison question plus DOI, title, PDF, paper-note, or source list into a traceable pairwise, corpus, or method-family comparison with evidence anchors and a decision-ready report. Use when paper-comparison alone is too manual because source preparation is incomplete.
tools:
  - read_file
  - write_file
  - write_structured_file
  - list_files
  - grep_search
  - fetch_paper_metadata
  - openalex_search
  - crossref_search
  - semantic_scholar_search
  - arxiv_search
  - search_papers
  - multi_source_search
  - fetch_paper_pdf
  - extract_pdf_text
  - extract_paper_sections
  - save_paper_note
  - ask_human
  - update_skill_workflow
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.15
allowed_read_prefixes:
  - user_inputs/literature-comparison-studio/
  - literature/
  - user_seeds/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - user_inputs/literature-comparison-studio/
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
  comparison_report: literature/comparison_studio.md
  comparison_csv: literature/comparison_studio.csv
  comparison_record: literature/comparison_studio.json
  comparison_claims: literature/comparison_claims.md
  workflow_manifest: literature/comparison_studio_manifest.json
interaction:
  mode: guided
  language: zh-CN
  summary: 输入比较目的与 DOI、标题、PDF 或论文阅读笔记，或只给明确主题和目标篇数；系统会检索、解析和尽量补齐可读来源，标注可回查的证据位置，并输出可用于综述、baseline 选择或机制定位的对比结论。
  request_required: true
  request_prompt: 请说明要比较什么、为何比较、希望得到什么决策，以及采用两篇、指定语料还是方法家族模式。
  example_request: 检索并比较 treatment heterogeneity 主题的 4 篇近年论文，优先获取可读全文，判断哪些可作为 baseline，哪些机制不能直接合并。
  required_inputs:
    - id: comparison_request
      label: 比较问题与决策目标
      description: 说明比较对象或其标识、比较目的、需要的轴和禁止推断的内容；可给 DOI/标题列表，也可明确主题、目标篇数、时间范围和来源偏好。
      paths:
        - user_inputs/literature-comparison-studio/request.md
      extensions: [.md]
      min_bytes: 50
      example: user_inputs/literature-comparison-studio/request.md
  optional_inputs:
    - id: source_manifest
      label: 来源文件或标识清单
      description: 可选；一行一个 DOI、arXiv/OpenAlex ID、标题、PDF URL、workspace PDF、paper note 或 record 路径。缺失项会进入定向检索/解析或精确补料，而不是被模型记忆替代。
      paths:
        - user_inputs/literature-comparison-studio/sources.md
      extensions: [.md]
      min_bytes: 10
      example: user_inputs/literature-comparison-studio/sources.md
  outputs:
    - id: comparison_report
      label: 文献比较报告
      path: literature/comparison_studio.md
      description: 人类可读的比较矩阵、共同点、冲突、局限、可组合性和决策建议。
    - id: comparison_csv
      label: 文献比较矩阵 CSV
      path: literature/comparison_studio.csv
      description: 每行一个来源、每列一个显式比较轴，未知项保持 unknown。
    - id: comparison_record
      label: 文献比较结构记录
      path: literature/comparison_studio.json
      description: 来源标识、paper-card 路径、section 锚点、证据等级和结论限制。
    - id: comparison_claims
      label: 可用比较主张
      path: literature/comparison_claims.md
      description: 允许写入综述/Related Work 的措辞、来源锚点与不可主张边界。
    - id: workflow_manifest
      label: 比较工作流清单
      path: literature/comparison_studio_manifest.json
      description: 输入解析、下载/读取状态、未解析来源、阶段产物和恢复信息。
workflow:
  kind: integrated
  summary: 从比较问题和不完整来源开始，先解析并补齐可读证据，再给出来源锚定的比较与决策边界。
  phases:
    - id: comparison_contract
      label: 比较合同与对象解析
      objective: 确认比较模式、决策问题、对象和必须保留的未知项。
      operations: [read request, parse identifiers, validate source roles]
    - id: source_readiness
      label: 来源解析与补料
      objective: 尝试解析 DOI/标题/PDF/笔记卡，或按已授权主题和篇数检索候选；无法解析或无全文时保留访问状态并向用户发出精确补料请求。
      operations: [metadata lookup, source retrieval, ask human]
      human_gate: true
    - id: evidence_extraction
      label: 证据卡与 section 锚点
      objective: 为关键比较轴建立 paper-card 或 section 级锚点，严格标注证据等级。
      operations: [extract text, section evidence, note normalization]
    - id: comparison_and_audit
      label: 对比、限制与决策
      objective: 输出共同点、差异、冲突、可组合性和可用主张，不用标题/摘要推断实验细节。
      operations: [comparison matrix, evidence audit, artifact manifest]
---

# Literature Comparison Studio

Use the source manifest or an explicitly scoped topic-plus-count request only as a
starting point. For topic retrieval, persist the query, requested count, candidate list,
selection rule, and access results before extracting evidence. A DOI/title result is
metadata evidence; a PDF is not evidence until the relevant text/section has been read. Create
or reuse lightweight paper cards only within the declared workspace paths. If a
decision-critical comparison axis cannot be sourced, leave it `unknown`, place it in
the missing-evidence ledger, and ask the human for an upload or a narrower question.

Every nontrivial matrix cell and every statement in `comparison_claims.md` needs a
source file plus note field or section anchor. Explicitly distinguish observed facts,
analytical comparison, and proposed research implication. Never create a dataset,
metric, baseline, result, or citation from a title, common convention, or model memory.
