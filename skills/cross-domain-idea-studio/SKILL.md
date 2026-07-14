---
name: cross-domain-idea-studio
description: Develop evidence-bounded cross-domain research directions by retrieving bridge evidence, extracting transferable mechanisms, auditing migration risk, and submitting candidates to a human selection gate before hypothesis compilation. Use when a researcher wants a defensible cross-domain idea rather than a loose analogy.
tools:
  - read_file
  - write_file
  - write_structured_file
  - list_files
  - grep_search
  - multi_source_search
  - openalex_search
  - semantic_scholar_search
  - crossref_search
  - fetch_paper_pdf
  - extract_pdf_text
  - extract_paper_sections
  - save_paper_note
  - compute_idea_novelty_signal
  - analyze_idea_concentration
  - ask_human
  - update_skill_workflow
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.35
allowed_read_prefixes:
  - project.yaml
  - user_inputs/cross-domain-idea-studio/
  - user_seeds/
  - literature/
  - ideation/
  - drafts/survey/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - user_inputs/cross-domain-idea-studio/
  - literature/bridge_notes/
  - ideation/
outputs_expected:
  bridge_plan: ideation/cross_domain_bridge_plan.json
  evidence_matrix: ideation/cross_domain_evidence_matrix.md
  transfer_cards: ideation/cross_domain_transfer_cards.md
  candidate_pool: ideation/cross_domain_candidate_pool.json
  risk_register: ideation/cross_domain_risk_register.md
  selection: ideation/cross_domain_selection.json
  workflow_manifest: ideation/cross_domain_manifest.json
interaction:
  mode: guided
  language: zh-CN
  summary: 从目标问题出发，检索/读取桥接领域证据，提炼可迁移机制、检查失配风险，生成跨域候选并在人工选择后交给 hypothesis compiler。
  request_required: true
  request_prompt: 请说明目标问题、已知目标域证据、拟探索或避免的桥接领域、约束和希望的创新类型。
  example_request: 用 organizational learning 的机制启发 LLM agent memory 研究；先核验迁移条件，再生成可证伪的跨域候选。
  required_inputs:
    - id: target_problem
      label: 目标问题与约束
      description: 说明目标研究对象、痛点、现有证据、不可违反的边界和期望的贡献；不能只给一个领域名。
      paths:
        - user_inputs/cross-domain-idea-studio/target_problem.md
        - ideation/skill_scope_brief.md
      extensions: [.md]
      min_bytes: 80
      example: user_inputs/cross-domain-idea-studio/target_problem.md
  optional_inputs:
    - id: target_evidence
      label: 目标域综合或文献卡
      description: 可选；提供后所有候选需优先链接其具体机制、张力和边界。缺失时会被标为 preliminary 并请求补检/补料。
      paths:
        - user_inputs/cross-domain-idea-studio/target_evidence.md
        - literature/synthesis.md
      extensions: [.md]
      min_bytes: 40
      example: literature/synthesis.md
  outputs:
    - id: bridge_plan
      label: 跨域桥接检索计划
      path: ideation/cross_domain_bridge_plan.json
      description: 源域、目标域、检索问题、迁移条件和阅读优先级。
    - id: evidence_matrix
      label: 跨域证据矩阵
      path: ideation/cross_domain_evidence_matrix.md
      description: 源域机制、论文/section 锚点、目标映射、证据等级和不匹配。
    - id: transfer_cards
      label: 可迁移机制卡
      path: ideation/cross_domain_transfer_cards.md
      description: 每张卡说明源机制、目标映射、迁移风险、替代解释和需要的区分实验。
    - id: candidate_pool
      label: 跨域候选池
      path: ideation/cross_domain_candidate_pool.json
      description: 候选方向、来源、证据、由模型写出的 2-3 条可证伪候选假设、评分边界和选择状态。
    - id: risk_register
      label: 迁移风险登记册
      path: ideation/cross_domain_risk_register.md
      description: 语义失配、单位不一致、因果过度迁移、资源缺口和不能主张的内容。
    - id: selection
      label: 人工选择记录
      path: ideation/cross_domain_selection.json
      description: 用户选择、合并、重构或要求继续补证据的结果；未选择不得冒充 hypothesis handoff。
    - id: workflow_manifest
      label: 跨域 Idea 工作流清单
      path: ideation/cross_domain_manifest.json
      description: 检索、笔记、证据状态、候选 lineage、Gate 和可恢复点。
workflow:
  kind: integrated
  summary: 先证明桥接机制值得讨论，再生成受证据约束的跨域候选；跨域不是唯一 Idea 来源，也不等于可迁移结论。
  phases:
    - id: target_contract
      label: 目标问题与目标域证据
      objective: 固定目标问题、目标域证据、约束和需要解决的机制张力。
      operations: [read target brief, inventory target evidence, identify unknowns]
    - id: bridge_retrieval
      label: Bridge 计划与定向检索
      objective: 与用户确认桥接方向后，建立小而可解释的 bridge query portfolio 和来源记录。
      operations: [ask human, bridge search, retain/exclude audit]
      human_gate: true
    - id: transfer_audit
      label: 迁移机制与失配审计
      objective: 从桥接来源提取机制、条件、反例和目标映射，不把类比写成证据。
      operations: [section evidence, transfer cards, migration-risk review]
    - id: candidate_jury
      label: 候选治理与人工选择
      objective: 生成可证伪候选、评分范围和 unsupported 项，并让用户选择/合并/重构。
      operations: [candidate pool, concentration audit, ask human]
      human_gate: true
---

# Cross-domain Idea Studio

Treat the target domain and bridge domain symmetrically as evidence problems. A bridge
paper may inspire a candidate but does not prove target-domain validity. For each
transfer card, state the source mechanism, source anchor, target mapping, mismatch,
evidence level, alternative explanation, and minimum discriminating observation.

Use only the canonical Paper Note roots when existing ResearchOS notes are relevant:
`literature/deep_read_notes/` for mainline full/partial reading, `literature/bridge_notes/`
for bridge full/partial reading, and `literature/shallow_read_notes/` for abstract-level
recall. Do not read or create retired `paper_notes*` directories. Shallow notes may
surface a Bridge lead or required reading upgrade, but cannot establish a transfer
mechanism or target-domain validity.

Generate candidates only after the transfer audit. When target or bridge evidence is
missing, output `preliminary`, `needs_retrieval`, or `unsupported` rather than a scored
novelty claim. Never fabricate a current-project dataset, baseline, metric, command,
budget, performance estimate, AUUC/Qini value, or experimental result. Concrete details
are allowed only when allowed artifacts explicitly source them.

The final selection file must preserve the human decision. Do not claim that a selected
candidate has entered hypothesis compilation unless the decision says so; recommend the
separate `hypothesis-compiler` Skill as the next explicit action.

## Native T4 handoff boundary

The files produced here are Bridge evidence and optional Candidate Seeds. They are not
native T4 Population artifacts: do not write `ideation/evidence/`, `ideation/populations/`,
`ideation/evolution/`, `ideation/portfolio.json`, or `ideation/final_cards/`. When the
researcher wants these Seeds considered with other routes, hand them to the T4 Evidence
Routing flow. It preserves the Bridge escape hatch, decides whether the evidence permits a
Candidate, independently scores any resulting Candidate, and keeps unsupported transfers
visible rather than forcing a merge. A selected native Candidate produces a Pre-Novelty
brief first; formal hypotheses and an experiment plan remain a T4.5-only outcome.
