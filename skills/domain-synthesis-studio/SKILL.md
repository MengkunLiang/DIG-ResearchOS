---
name: domain-synthesis-studio
description: Build a complete, evidence-bounded domain synthesis from an initial research brief and available ResearchOS literature artifacts, optionally searching for missing literature before producing method families, mechanisms, tensions, boundaries, and survey or idea handoffs. Use when a researcher wants an end-to-end field understanding rather than a standalone note matrix.
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
  - user_inputs/domain-synthesis-studio/
  - user_seeds/
  - literature/
  - ideation/
  - drafts/survey/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - user_inputs/domain-synthesis-studio/
  - literature/
  - drafts/survey/
outputs_expected:
  domain_report: literature/domain_synthesis_report.md
  domain_record: literature/domain_synthesis.json
  method_family_map: literature/method_family_map.md
  mechanism_tension_map: literature/mechanism_tension_map.md
  evidence_boundary_register: literature/evidence_boundary_register.json
  research_questions: literature/actionable_research_questions.md
  workflow_manifest: literature/domain_synthesis_manifest.json
interaction:
  mode: guided
  language: zh-CN
  summary: 从研究问题出发，先确认是否需要补检文献，再完成方法家族、机制、张力、证据边界与下一步研究路径的领域综合；完成后可选择进入 Survey 写作准备。
  request_required: true
  request_prompt: 请说明要综合的领域、核心研究问题、时间/语言边界，以及希望服务综述、Idea、Related Work 还是实验设计。
  example_request: 综合 LLM agent memory 的方法家族、机制证据和边界条件；先判断现有语料是否够，不够则自动补检，再决定是否准备英文 Survey。
  required_inputs:
    - id: domain_brief
      label: 领域问题与使用目标
      description: 给出研究对象、核心问题、范围、目标读者和本次综合将支持的后续决策；无需预先准备全部文献。
      paths:
        - user_inputs/domain-synthesis-studio/brief.md
        - ideation/skill_scope_brief.md
      extensions: [.md]
      min_bytes: 60
      example: user_inputs/domain-synthesis-studio/brief.md
  optional_inputs:
    - id: existing_evidence
      label: 已有文献与综合材料
      description: 可选；可列出 paper notes、PDF、DOI、comparison table 或既有 synthesis 路径。缺失时系统会先询问是否补检，而不会假设语料充分。
      paths:
        - user_inputs/domain-synthesis-studio/evidence.md
        - literature/synthesis.md
      extensions: [.md]
      min_bytes: 20
      example: user_inputs/domain-synthesis-studio/evidence.md
  outputs:
    - id: domain_report
      label: 领域综合报告
      path: literature/domain_synthesis_report.md
      description: 可直接阅读的领域结构、方法家族、机制、张力、边界和下一步决策说明。
    - id: domain_record
      label: 领域综合结构记录
      path: literature/domain_synthesis.json
      description: 机器可读的来源、证据等级、方法家族、张力、transfer 和行动建议。
    - id: method_family_map
      label: 方法家族图谱
      path: literature/method_family_map.md
      description: 各家族的问题、设计 rationale、典型论文、比较轴和覆盖边界。
    - id: mechanism_tension_map
      label: 机制与张力图
      path: literature/mechanism_tension_map.md
      description: 共享机制、支持/反驳证据、替代解释和跨论文张力。
    - id: evidence_boundary_register
      label: 证据边界登记册
      path: literature/evidence_boundary_register.json
      description: FULL/PARTIAL/ABSTRACT/METADATA/unsupported 条目的可主张范围和升级动作。
    - id: research_questions
      label: 可行动研究问题
      path: literature/actionable_research_questions.md
      description: 区分可进入 Idea 的证据支持问题、需补检的问题和不应主张的检索缺口。
    - id: workflow_manifest
      label: 领域综合工作流清单
      path: literature/domain_synthesis_manifest.json
      description: 输入、自动补检尝试、阶段产物、证据限制、Survey/Idea handoff 和恢复信息。
workflow:
  kind: integrated
  summary: 先判断现有证据是否足以综合；必要时经用户确认补检，再形成可审计领域综合，并询问是否进入 Survey 准备。
  phases:
    - id: intake_and_inventory
      label: 研究范围与现有材料盘点
      objective: 明确问题、现有材料、语言边界和后续用途，不把目录中未声明的文件当作证据。
      operations: [read brief, inventory declared artifacts, classify evidence levels]
    - id: retrieval_decision
      label: 文献补检决策
      objective: 先向用户说明当前语料是否足够，并询问是否允许系统针对缺口进行补检。
      operations: [coverage assessment, ask human, scoped query portfolio]
      human_gate: true
    - id: evidence_acquisition
      label: 定向检索与可读性补齐
      objective: 对获准的缺口做来源可追溯检索；缺少全文时保留为 discovery/abstract evidence 而非强结论。
      operations: [source search, deduplicate leads, record evidence limits]
    - id: synthesis
      label: 领域综合与结构化映射
      objective: 形成方法家族、贡献空间、机制、张力、边界和可行动问题。
      operations: [paper-card review, synthesis workbench, evidence audit]
    - id: next_path_gate
      label: Survey 或 Idea 路径选择
      objective: 展示语料充分性与产物，并询问继续准备 Survey、进入 Idea、补读或结束。
      operations: [artifact manifest, ask human, write handoff]
      human_gate: true
---

# Domain Synthesis Studio

Start from the verified brief and intake packet. In `intake_and_inventory`, record
which documents are FULL/PARTIAL/ABSTRACT/METADATA and what cannot yet support a
mechanism or causal claim. Do not scan unrelated conventional paths.

Before any search, call `update_skill_workflow` for `retrieval_decision` and ask the
human whether to (a) synthesize the present corpus only, (b) authorize a scoped
literature supplement, or (c) upload/identify specific missing sources. If search is
authorized, use source-returning tools with a small explicit query portfolio and write
the retained/duplicate/excluded records plus their provenance to the manifest. Search
leads without section-level reading are discovery evidence only.

Use existing paper cards and, when available, `build_synthesis_workbench`; inspect its
output rather than treating its clusters as final scholarly judgments. Produce every
declared artifact. The research-question report must classify each item as
evidence-supported opportunity, untested boundary, missing comparison, retrieval
deficiency, or unsupported speculation. State plainly that retrieval coverage is not a
research gap.

For workspace-backed notes, read the canonical roots only: `literature/deep_read_notes/`,
`literature/shallow_read_notes/`, and `literature/bridge_notes/`. Keep their Evidence
Permission distinct in the synthesis: deep/bridge notes can support bounded mechanism
or design-rationale discussion; shallow notes extend coverage and comparison but cannot
by themselves establish a mechanism, causal finding, or implementation detail. Old
`paper_notes*` directory names are migration inputs, never a second live corpus.

At `next_path_gate`, present corpus sufficiency, unsupported areas, and the precise
handoff options. If the human selects Survey preparation, write that explicit decision
and an evidence package pointer into `domain_synthesis_manifest.json`; do not write a
Survey manuscript without a separate Survey workflow decision.
