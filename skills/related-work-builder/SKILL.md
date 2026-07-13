---
name: related-work-builder
description: Build a source-grounded Related Work section package from a target paper position, literature artifacts, and verified bibliography. It produces a narrative section draft plus citation and evidence audits without inventing citations or positioning claims. Use when a researcher needs a defensible related-work section rather than a generic literature summary.
tools:
  - read_file
  - write_file
  - write_structured_file
  - list_files
  - grep_search
  - extract_paper_sections
  - build_manuscript_resource_index
  - plan_manuscript_evidence
  - ask_human
  - update_skill_workflow
  - finish_task
strict_tools: true
model_tier: heavy
temperature: 0.15
allowed_read_prefixes:
  - user_inputs/related-work-builder/
  - literature/
  - ideation/
  - drafts/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - user_inputs/related-work-builder/
  - drafts/
outputs_expected:
  related_work_tex: drafts/sections/related_work.tex
  evidence_map: drafts/related_work_evidence_map.json
  citation_audit: drafts/related_work_citation_audit.md
  claim_boundary: drafts/related_work_claim_boundary.md
  workflow_manifest: drafts/related_work_manifest.json
interaction:
  mode: guided
  language: zh-CN
  summary: 将论文定位、已有文献卡和参考库转为一段可回查的 Related Work 草稿，并交付引用审计和可主张边界。
  request_required: true
  request_prompt: 请说明论文的研究问题、相对已有工作的定位、希望覆盖的文献家族、目标 venue 风格和禁止夸大的表述。
  example_request: 为我的英文论文生成 Related Work，区分 agent memory、retrieval augmentation 和 organizational learning；不要把桥接文献写成直接方法比较。
  required_inputs:
    - id: positioning_brief
      label: 论文定位与 Related Work 目标
      description: 必须说明本论文解决的问题、已有工作关系、欲覆盖的范围、目标风格与不能宣称的贡献。
      paths:
        - user_inputs/related-work-builder/positioning.md
        - drafts/writing_storyline.md
      extensions: [.md]
      min_bytes: 80
      example: user_inputs/related-work-builder/positioning.md
  optional_inputs:
    - id: evidence_pack
      label: 文献卡、矩阵或引用库
      description: 可选；建议给出 synthesis、comparison、paper notes 或 BibTeX。没有可验证来源时 Skill 会停在补证据而不写强定位。
      paths:
        - user_inputs/related-work-builder/evidence.md
        - literature/synthesis.md
        - literature/related_work.bib
      extensions: [.md, .bib]
      min_bytes: 20
      example: literature/synthesis.md
  outputs:
    - id: related_work_tex
      label: Related Work TeX 草稿
      path: drafts/sections/related_work.tex
      description: 按研究脉络、差异和边界组织的章节草稿，包含仅经审计的引用键。
    - id: evidence_map
      label: Related Work 证据映射
      path: drafts/related_work_evidence_map.json
      description: 每段主张的 note/section/BibTeX 来源、证据等级和允许措辞。
    - id: citation_audit
      label: 引用审计
      path: drafts/related_work_citation_audit.md
      description: 已验证键、缺失来源、abstract-only 限制和需人工核对的引用。
    - id: claim_boundary
      label: 定位主张边界
      path: drafts/related_work_claim_boundary.md
      description: 可以写、需要弱化、不能写和需要补证据的定位句。
    - id: workflow_manifest
      label: Related Work 工作流清单
      path: drafts/related_work_manifest.json
      description: 输入、来源选择、段落-证据绑定、待补料项和恢复信息。
workflow:
  kind: integrated
  summary: 先审核论文定位与文献证据，再写 Related Work；引用和比较结论必须可回查。
  phases:
    - id: positioning_contract
      label: 论文定位合同
      objective: 明确论文主张、相关工作范围、目标 venue 风格和禁用措辞。
      operations: [read positioning brief, identify required evidence]
    - id: evidence_binding
      label: 文献卡与引用绑定
      objective: 找到每个段落可用的 note/section/BibTeX 证据，缺失时请求补料。
      operations: [resource index, evidence mapping, ask human]
      human_gate: true
    - id: section_draft
      label: Related Work 草稿与审计
      objective: 写出有逻辑的对比叙事，并交付引用、定位和弱证据审计。
      operations: [section draft, citation audit, claim boundary]
---

# Related Work Builder

Use literature evidence to explain a research story, not to manufacture a novelty
claim. Each sentence that compares the current paper with prior work must name the
comparison axis and retain a source anchor in the evidence map. An adjacent or
cross-domain work can supply conceptual background but must not be presented as a
direct empirical baseline unless the project evidence says so.

When the positioning brief lacks a decision-critical statement, request it through the
guided follow-up protocol. Do not invent a method, result, baseline, metric, or
contribution merely to make the section read smoothly.
