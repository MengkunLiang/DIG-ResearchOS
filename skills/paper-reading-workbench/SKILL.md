---
name: paper-reading-workbench
description: Organize one or more researcher-provided PDFs, DOI, arXiv identifiers, or existing paper records into prioritized reading cards, section-level answers, and a cross-paper learning summary. Use when a researcher wants a practical reading workspace rather than a single PDF note card.
tools:
  - read_file
  - write_file
  - write_structured_file
  - list_files
  - fetch_paper_metadata
  - openalex_search
  - semantic_scholar_search
  - crossref_search
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
  - user_inputs/paper-reading-workbench/
  - literature/
  - user_seeds/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - user_inputs/paper-reading-workbench/
  - literature/skill_reading_workbench/
outputs_expected:
  reading_index: literature/skill_reading_workbench/reading_index.md
  paper_cards: literature/skill_reading_workbench/paper_cards.json
  question_answers: literature/skill_reading_workbench/question_answers.md
  learning_summary: literature/skill_reading_workbench/cross_paper_summary.md
  workflow_manifest: literature/skill_reading_workbench/manifest.json
interaction:
  mode: guided
  language: zh-CN
  summary: 上传/列出多篇 PDF、DOI、arXiv 或已有记录，按问题自动建立阅读队列、来源锚定卡片与跨论文学习总结。
  request_required: true
  request_prompt: 请说明要读哪些论文、希望回答的研究问题、优先轴和是否接受系统先解析 DOI/PDF 后再问补料。
  example_request: 阅读这组 PDF，比较它们的 memory mechanism、实验边界和最值得复用的设计 rationale。
  required_inputs:
    - id: reading_request
      label: 阅读任务与问题
      description: 说明论文标识或上传路径、要回答的问题、优先级和希望的阅读深度。
      paths:
        - user_inputs/paper-reading-workbench/request.md
      extensions: [.md]
      min_bytes: 40
      example: user_inputs/paper-reading-workbench/request.md
  optional_inputs:
    - id: source_list
      label: PDF/DOI/标题/记录清单
      description: 可选；一行一个 workspace 相对路径或稳定标识。也可在交互中逐项上传/补充。
      paths:
        - user_inputs/paper-reading-workbench/sources.md
      extensions: [.md]
      min_bytes: 10
      example: user_inputs/paper-reading-workbench/sources.md
  outputs:
    - id: reading_index
      label: 阅读队列与来源索引
      path: literature/skill_reading_workbench/reading_index.md
      description: 每篇论文的解析状态、访问等级、阅读优先级、问题和链接。
    - id: paper_cards
      label: 阅读卡结构记录
      path: literature/skill_reading_workbench/paper_cards.json
      description: 论文元数据、卡片字段、页码/section 覆盖、证据等级与未回答问题。
    - id: question_answers
      label: 定向问题回答
      path: literature/skill_reading_workbench/question_answers.md
      description: 面向用户问题的论文级回答、来源锚点、未知项和需要补读的 section。
    - id: learning_summary
      label: 跨论文学习总结
      path: literature/skill_reading_workbench/cross_paper_summary.md
      description: 共同机制、分歧、边界和可进一步比较/综合的线索。
    - id: workflow_manifest
      label: 阅读工作台清单
      path: literature/skill_reading_workbench/manifest.json
      description: 输入解析、PDF 可读性、阅读覆盖、产物和恢复点。
workflow:
  kind: integrated
  summary: 将不完整的论文输入先解析成可读队列，再按用户问题生成受证据约束的卡片和跨论文总结。
  phases:
    - id: source_contract
      label: 论文清单与问题解析
      objective: 确认待读对象、阅读问题、优先级和期待深度。
      operations: [read request, parse source list, prioritize papers]
    - id: access_and_intake
      label: 标识解析、PDF 获取与补料
      objective: 尝试解析稳定标识和读取 PDF；缺失时请用户上传或修正，而非用模型记忆替代。
      operations: [metadata lookup, PDF access, ask human]
      human_gate: true
    - id: evidence_reading
      label: 分层阅读与问题取证
      objective: 完整/部分/摘要/metadata 分级阅读，记录页码和 section 覆盖。
      operations: [text extraction, section evidence, paper cards]
    - id: learning_summary
      label: 跨论文学习总结
      objective: 汇总共同机制、差异、边界和未解决问题，不把摘要当作全文证据。
      operations: [cross-paper synthesis, artifact manifest]
---

# Paper Reading Workbench

Prioritize papers according to the researcher’s stated question, not only citation or
search ranking. For each paper state access level, page/section coverage, extraction
status, evidence level, and open uncertainty. A missing PDF is not permission to infer
methods or results from metadata. Abstract-only readings may produce cautious coverage
hints but cannot answer detailed mechanism, dataset, metric, or causal questions.
