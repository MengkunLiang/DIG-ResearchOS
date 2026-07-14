---
name: literature-review-studio
description: Run a guided, evidence-aware literature review workflow from review question and seed materials through query planning, source retrieval, citation expansion, reading coverage, synthesis, taxonomy readiness, and an optional Survey handoff. Use when a researcher needs a complete review workspace rather than a single search or writing action.
tools:
  - read_file
  - write_file
  - write_structured_file
  - list_files
  - grep_search
  - multi_source_search
  - semantic_scholar_search
  - arxiv_search
  - openalex_search
  - crossref_search
  - deduplicate_papers
  - score_papers
  - detect_duplicate_queries
  - build_verified_papers
  - build_access_audit
  - build_deep_read_queue
  - build_domain_map
  - fetch_paper_pdf
  - extract_pdf_text
  - extract_paper_sections
  - save_paper_note
  - build_synthesis_workbench
  - ask_human
  - update_skill_workflow
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.2
allowed_read_prefixes:
  - project.yaml
  - user_inputs/literature-review-studio/
  - user_seeds/
  - literature/
  - drafts/survey/
  - ideation/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - user_inputs/literature-review-studio/
  - literature/
  - drafts/survey/
outputs_expected:
  review_manifest: literature/review_studio_manifest.json
  corpus_inventory: literature/review_corpus_inventory.json
  query_portfolio: literature/review_query_portfolio.md
  evidence_matrix: literature/review_evidence_matrix.csv
  review_synthesis: literature/review_synthesis.md
  gap_tension_map: literature/review_gap_and_tension_map.md
  bibliography: literature/review_bibliography.bib
  survey_readiness: drafts/survey/review_studio_readiness.md
interaction:
  mode: guided
  language: zh-CN
  summary: 从综述问题和种子材料开始，按可确认的检索范围建立文献池、阅读卡、证据矩阵与领域综合，并在语料充分时询问是否进入 Survey 写作准备。
  request_required: true
  request_prompt: 请说明综述问题、目标读者、时间/语言/venue 边界、已有核心文献，以及希望只做领域综合还是准备完整 Survey。
  example_request: 为英文 survey 梳理 LLM agent memory 的方法 taxonomy 与机制边界；先检索并验证文献，再由我确认 taxonomy 后进入写作。
  required_inputs:
    - id: review_brief
      label: 综述问题与范围
      description: 明确综述主题、研究问题、目标读者、纳入/排除范围、时间和语言偏好；无须预先提供完整语料。
      paths:
        - user_inputs/literature-review-studio/review_brief.md
      extensions: [.md]
      min_bytes: 80
      example: user_inputs/literature-review-studio/review_brief.md
  optional_inputs:
    - id: seeds
      label: 种子论文或已有材料
      description: 可选；可列 DOI、标题、PDF、已有 paper notes 或引用库。种子会保护性保留，但仍须验证元数据和证据等级。
      paths:
        - user_inputs/literature-review-studio/seeds.md
        - user_seeds/seed_papers.jsonl
      extensions: [.md, .jsonl]
      min_bytes: 10
      example: user_inputs/literature-review-studio/seeds.md
  outputs:
    - id: review_manifest
      label: 综述工作流清单
      path: literature/review_studio_manifest.json
      description: 范围、查询、来源、统计、证据等级、排除项、阶段产物、Gate 与恢复点。
    - id: corpus_inventory
      label: 已验证语料清单
      path: literature/review_corpus_inventory.json
      description: 论文元数据、来源、访问级别、阅读处置、证据等级和纳入原因。
    - id: query_portfolio
      label: 检索组合与去重报告
      path: literature/review_query_portfolio.md
      description: 查询意图、领域、相似度、保留/合并/排除原因和来源贡献。
    - id: evidence_matrix
      label: 综述证据矩阵
      path: literature/review_evidence_matrix.csv
      description: 论文、方法、机制、比较轴、证据状态、局限和来源锚点的可导入 CSV。
    - id: review_synthesis
      label: 综述级领域综合
      path: literature/review_synthesis.md
      description: 方法 taxonomy、设计 rationale、机制、张力、边界和研究议程。
    - id: gap_tension_map
      label: 缺口与张力图
      path: literature/review_gap_and_tension_map.md
      description: 明确分离研究机会、未测边界、缺失比较、检索不足和 unsupported 项。
    - id: bibliography
      label: 审计后的参考文献库
      path: literature/review_bibliography.bib
      description: 仅包含来源可验证、键可追溯的引用条目。
    - id: survey_readiness
      label: Survey 就绪评估
      path: drafts/survey/review_studio_readiness.md
      description: taxonomy 覆盖、全文/弱证据比例、缺口、是否可启动 Survey Plan 与需要的人工决策。
workflow:
  kind: integrated
  summary: 把综述问题转为可审计语料与综合；每次扩大检索或进入写作前都由研究者确认，避免“一键”跳过证据门。
  phases:
    - id: review_contract
      label: 综述范围与检索授权
      objective: 固定综述问题、语言/时间边界、种子保护策略和检索授权。
      operations: [read brief, inventory seeds, ask human]
      human_gate: true
    - id: query_and_retrieval
      label: 查询组合、检索与验证
      objective: 构建 query portfolio，展示去重/来源贡献，保留可验证元数据与失败原因。
      operations: [query portfolio, source search, deduplication, metadata audit]
    - id: reading_coverage
      label: 阅读队列与证据卡
      objective: 区分全文、部分全文、摘要和元数据，建立有限且可解释的阅读覆盖。
      operations: [access audit, reading queue, paper cards]
    - id: synthesis_and_taxonomy
      label: 综述综合与 taxonomy
      objective: 建立方法家族、机制、张力、边界和 Survey 就绪判断。
      operations: [evidence matrix, synthesis workbench, taxonomy readiness]
    - id: survey_handoff_gate
      label: Survey 写作路径确认
      objective: 让用户选择继续 Survey Plan、先补检/补读、仅保留领域综合或进入 Idea。
      operations: [artifact manifest, ask human, handoff record]
      human_gate: true
---

# Literature Review Studio

This is a staged review workflow, not a promise of a publishable Survey from a topic
string. First ask the human to confirm the review scope and whether network retrieval
is authorized. Show the query portfolio, deduplication, source contribution, failed
verification, and evidence coverage before making synthesis claims.

For every retained paper preserve stable identifiers and source provenance. Full and
partial text can support bounded substantive claims when their cards have anchors;
abstract-only and metadata-only records may support coverage, trend, discovery, or
upgrade priorities only. Never turn a retrieval count, citation count, or tool ranking
into a scholarly verdict.

At the final gate, ask whether to prepare Survey writing. If the corpus is insufficient,
offer a targeted retrieval/reading plan rather than forcing taxonomy prose. Do not write
the Survey manuscript in this Skill; hand off only after a human has approved the
taxonomy and corpus scope.
