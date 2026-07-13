---
name: research-landscape-report
description: Produce a factual research-landscape report from verified literature artifacts, including domain structure, method families, evidence coverage, tensions, resource availability, and evidence-bounded research opportunities. Use when a researcher needs a decision dashboard without turning retrieval gaps or rankings into scholarly claims.
tools:
  - read_file
  - write_file
  - write_structured_file
  - list_files
  - grep_search
  - build_domain_map
  - build_synthesis_workbench
  - ask_human
  - update_skill_workflow
  - finish_task
strict_tools: true
model_tier: heavy
temperature: 0.15
allowed_read_prefixes:
  - user_inputs/research-landscape-report/
  - literature/
  - ideation/
  - drafts/survey/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - user_inputs/research-landscape-report/
  - literature/
outputs_expected:
  landscape_report: literature/research_landscape_report.md
  landscape_data: literature/research_landscape.json
  opportunity_register: literature/research_opportunity_register.md
  evidence_coverage: literature/research_landscape_coverage.json
  workflow_manifest: literature/research_landscape_manifest.json
interaction:
  mode: guided
  language: zh-CN
  summary: 将已验证文献组织为可核验的领域地图、方法家族、证据覆盖、张力、资源线索和研究机会登记册。
  request_required: true
  request_prompt: 请说明想观察的领域问题、决策用途，以及是否允许将当前 corpus 的不足明确标为“需补检”而非研究缺口。
  example_request: 展示 causal representation learning 的方法结构、机制张力和可进入 T4 的证据支持机会；不要把检索空白写成 novelty。
  required_inputs:
    - id: landscape_question
      label: 领域观察问题
      description: 说明领域、分析维度、目标决策和不应被误解为结论的范围。
      paths:
        - user_inputs/research-landscape-report/question.md
      extensions: [.md]
      min_bytes: 50
      example: user_inputs/research-landscape-report/question.md
  optional_inputs:
    - id: existing_corpus
      label: 现有领域语料
      description: 可选；可以是 domain map、synthesis、workbench 或 paper notes。无充分语料时会停在补检建议。
      paths:
        - literature/domain_map.json
        - literature/synthesis_workbench.json
      extensions: [.json]
      min_bytes: 40
      example: literature/domain_map.json
  outputs:
    - id: landscape_report
      label: 领域地图报告
      path: literature/research_landscape_report.md
      description: 领域结构、方法家族、证据级别、张力、资源和用户决策解释。
    - id: landscape_data
      label: 领域地图结构数据
      path: literature/research_landscape.json
      description: 可视化/UI 可复用的节点、家族、证据状态、关系和来源锚点。
    - id: opportunity_register
      label: 研究机会登记册
      path: literature/research_opportunity_register.md
      description: 区分证据支持机会、未测边界、缺失比较、检索不足与 unsupported 推测。
    - id: evidence_coverage
      label: 证据覆盖统计
      path: literature/research_landscape_coverage.json
      description: 语料、访问等级、family 覆盖、弱证据和需补检项的事实统计。
    - id: workflow_manifest
      label: 领域地图工作流清单
      path: literature/research_landscape_manifest.json
      description: 输入、生成/复用的领域映射、覆盖限制、产物和下一步。
workflow:
  kind: integrated
  summary: 用已验证的文献结构解释领域状态与研究机会，不把图谱信号或检索缺口包装成学术结论。
  phases:
    - id: landscape_scope
      label: 领域问题与证据盘点
      objective: 确定观察维度、现有语料和可用证据等级。
      operations: [read question, inventory corpus, classify evidence]
    - id: mapping_and_coverage
      label: 领域映射与覆盖审计
      objective: 构建或复用引用/方法结构，展示覆盖和薄弱位置。
      operations: [domain map, synthesis workbench, coverage statistics]
    - id: opportunities_and_gate
      label: 机会登记与下一步选择
      objective: 形成带证据边界的机会/补检登记册，并询问是补检、综合、Survey 还是 T4。
      operations: [opportunity register, artifact manifest, ask human]
      human_gate: true
---

# Research Landscape Report

Use visual/map terminology carefully. Citation connections and clustering are discovery
signals, not direct measures of importance or quality. The opportunity register must
separate evidence-supported open questions from coverage deficiencies and unsupported
speculation. Do not infer performance trends, causal mechanisms, or resource
availability from title, venue, or graph position alone.
