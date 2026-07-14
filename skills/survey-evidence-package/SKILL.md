---
name: survey-evidence-package
description: Prepare a human-approved, evidence-audited survey package from a domain synthesis before any Survey prose is drafted. It checks corpus sufficiency, proposes taxonomy and storyline, identifies weak classes, and can request or perform scoped literature supplementation. Use when a researcher wants a safe bridge from T3.5-style synthesis to T3.6 survey writing.
tools:
  - read_file
  - write_file
  - write_structured_file
  - list_files
  - grep_search
  - multi_source_search
  - openalex_search
  - semantic_scholar_search
  - arxiv_search
  - crossref_search
  - build_survey_state
  - expand_corpus_for_survey
  - ask_human
  - update_skill_workflow
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.15
allowed_read_prefixes:
  - project.yaml
  - user_inputs/survey-evidence-package/
  - literature/
  - drafts/survey/
  - user_seeds/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - user_inputs/survey-evidence-package/
  - literature/
  - drafts/survey/
outputs_expected:
  corpus_sufficiency: drafts/survey/corpus_sufficiency_report.md
  taxonomy_candidates: drafts/survey/taxonomy_candidates.json
  storyline: drafts/survey/survey_storyline.md
  survey_package: drafts/survey/survey_evidence_package.json
  supplementation_plan: drafts/survey/survey_supplementation_plan.md
  workflow_manifest: drafts/survey/survey_evidence_package_manifest.json
interaction:
  mode: guided
  language: zh-CN
  summary: 在写 Survey 前审计语料充分性、taxonomy、故事线和弱证据；用户可选择先补检、仅保守写作或确认进入 T3.6。
  request_required: true
  request_prompt: 请说明 Survey 的中心问题、目标读者/venue、希望的语言与范围，以及是否允许系统针对薄弱 taxonomy 类做定向检索。
  example_request: 基于现有领域综合准备英文 Survey；先告诉我哪些 taxonomy 类证据不够，并允许我确认后补检。
  required_inputs:
    - id: survey_intent
      label: Survey 意图与范围
      description: 说明中心问题、读者、边界、语言/模板偏好和是否准备现在进入 Survey 写作。
      paths:
        - user_inputs/survey-evidence-package/intent.md
      extensions: [.md]
      min_bytes: 60
      example: user_inputs/survey-evidence-package/intent.md
  optional_inputs:
    - id: synthesis_sources
      label: 领域综合与文献证据
      description: 可选；优先使用现有 synthesis/workbench/note cards。若没有，系统会要求先运行领域综合或上传可审计来源。
      paths:
        - literature/domain_synthesis.json
        - literature/synthesis_workbench.json
        - literature/synthesis.md
      extensions: [.json, .md]
      min_bytes: 60
      example: literature/domain_synthesis.json
  outputs:
    - id: corpus_sufficiency
      label: Survey 语料充分性报告
      path: drafts/survey/corpus_sufficiency_report.md
      description: FULL/PARTIAL/ABSTRACT/METADATA 覆盖、taxonomy 类缺口、不可主张边界和推荐动作。
    - id: taxonomy_candidates
      label: Taxonomy 候选
      path: drafts/survey/taxonomy_candidates.json
      description: 可审计 taxonomy 选项、分类维度、paper links、覆盖程度与冲突。
    - id: storyline
      label: Survey 叙事与章节主张
      path: drafts/survey/survey_storyline.md
      description: 问题、rationale、taxonomy 演进、每节问题和证据限制；不是最终 TeX。
    - id: survey_package
      label: Survey 证据包
      path: drafts/survey/survey_evidence_package.json
      description: 后续 T3.6 计划可读取的证据索引、taxonomy 决策、弱证据和已确认约束。
    - id: supplementation_plan
      label: 定向补检/补读计划
      path: drafts/survey/survey_supplementation_plan.md
      description: 哪些类需要何种来源、为什么、可查询什么以及补齐后允许升级的结论。
    - id: workflow_manifest
      label: Survey 证据工作流清单
      path: drafts/survey/survey_evidence_package_manifest.json
      description: 输入、查询尝试、证据限制、人工决策和 T3.6 handoff 状态。
workflow:
  kind: integrated
  summary: 在写作前先审计综述的证据与 taxonomy；允许定向补检，但不把补检线索直接升级为 Survey 论证。
  phases:
    - id: survey_contract
      label: Survey 意图与证据盘点
      objective: 明确综述问题、目标读者、语言和现有综合产物。
      operations: [read intent, inventory synthesis, classify evidence]
    - id: sufficiency_review
      label: 语料充分性与 taxonomy 候选
      objective: 判断是否能形成可解释 taxonomy，识别薄弱或空白类。
      operations: [coverage audit, taxonomy candidates, weak-evidence register]
    - id: supplementation_gate
      label: 补检/保守范围决策
      objective: 让用户选择只用当前语料、授权定向补检、上传材料或停止。
      operations: [supplementation plan, ask human, decision record]
      human_gate: true
    - id: survey_handoff
      label: Survey 写作交接
      objective: 输出可审计 evidence package，并仅在用户确认后标记可进入 T3.6。
      operations: [storyline, survey package, handoff manifest]
      human_gate: true
---

# Survey Evidence Package

Do not write a survey manuscript in this Skill. Treat it as the pre-writing evidence
gate that a researcher can inspect. Create a corpus sufficiency report before choosing
a taxonomy. It must distinguish unavailable retrieval, metadata-only lead,
abstract-only coverage, and full/partial evidence suitable for bounded discussion.

If the human authorizes supplementation, make small source-returning searches and
record the exact query, retained source, and what it can upgrade. Do not convert search
results into central taxonomy evidence without a readable source/card. The final
handoff must state whether it is ready for conservative Survey planning, ready only
after targeted reading, or not ready.
