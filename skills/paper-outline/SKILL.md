---
name: paper-outline
description: Build an evidence-bounded academic paper outline, contribution map, and readiness report from a research brief. Use before drafting when the user needs a clear argument structure without turning assumptions or unverified literature into paper claims.
tools:
  - read_file
  - write_file
  - list_files
  - build_manuscript_resource_index
  - plan_manuscript_sections
  - plan_manuscript_evidence
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.2
allowed_read_prefixes:
  - user_inputs/paper-outline/
  - literature/
  - ideation/
  - experiments/
  - drafts/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - drafts/
outputs_expected:
  outline: drafts/outline.md
  writing_storyline: drafts/writing_storyline.md
  evidence_map: drafts/outline_evidence_map.md
  readiness: drafts/outline_readiness.md
interaction:
  mode: guided
  language: zh-CN
  summary: 在写正文之前建立问题-贡献-证据-章节的完整论证骨架，并明确哪些内容尚不能写成结论。
  request_required: true
  request_prompt: 请说明论文类型、目标读者/会议、语言、核心贡献设想，以及希望包含的章节或限制。
  example_request: 为一篇英文 empirical paper 建立 NeurIPS 风格大纲，突出机制分析并保留 limitations。
  required_inputs:
    - id: research_brief
      label: 研究简介
      description: 至少包括问题、方法或观察、现有证据和目标读者；可直接使用已有项目的假设或 synthesis。
      paths:
        - user_inputs/paper-outline/brief.md
        - ideation/hypotheses.md
      extensions: [.md]
      min_bytes: 80
      example: user_inputs/paper-outline/brief.md
  optional_inputs:
    - id: literature_synthesis
      label: 文献综合或笔记索引
      description: 可选；用于 Related Work 和 gap 的可追溯定位。
      paths:
        - literature/synthesis.md
        - literature/notes_index.json
      extensions: [.md, .json]
      min_bytes: 80
      example: literature/synthesis.md
    - id: writing_style
      label: 已确认的写作风格（项目模式）
      description: 可选；若项目已有 writing_style.json，outline 会采用对应 venue profile。没有时会在运行中请求目标 venue、语言和叙事取向。
      paths:
        - drafts/writing_style.json
        - user_inputs/paper-outline/writing_style.json
      extensions: [.json]
      min_bytes: 20
      example: drafts/writing_style.json
    - id: evidence
      label: 结果或证据材料
      description: 可选；提供后可将具体结果映射到贡献，未提供的项目必须标注为待验证。
      paths:
        - drafts/experiment_evidence_pack.json
        - user_inputs/paper-outline/evidence.md
      extensions: [.json, .md]
      min_bytes: 40
      example: drafts/experiment_evidence_pack.json
  outputs:
    - id: outline
      label: 论文大纲
      path: drafts/outline.md
      description: 各章节论点、证据槽位、读者问题和限制的写作蓝图。
    - id: writing_storyline
      label: Venue-aware 论证主线
      path: drafts/writing_storyline.md
      description: 将问题、rationale/根因、洞见、设计、证据、替代解释与限制串成可审计研究故事。
    - id: evidence_map
      label: 大纲证据映射
      path: drafts/outline_evidence_map.md
      description: 将每项贡献映射到来源；缺口明确标成不可主张而不是补造内容。
    - id: readiness
      label: 写作就绪报告
      path: drafts/outline_readiness.md
      description: 列出可立即写的章节、缺失材料和进入 paper-write 前需要补齐的项目。
---

# Evidence-Bounded Paper Outline

Read the verified research brief, the intake packet, and any existing project artifacts. Existing project files are candidates, not a guarantee that the research question, target venue, evidence, or citation support is sufficient. If target venue/language or a required rationale/evidence fact is missing, write `user_inputs/paper-outline/_followup_request.md` with the exact question and preferred answer/file path, then call `ask_human`; do not guess.

Resolve `drafts/writing_style.json` when it exists. Otherwise, after a real response, create a minimal `drafts/writing_style.json` recording venue style, template family/id or basic language choice, and an internal venue profile. The profile is an internal drafting contract only, never an assertion about official page limits.

Build the resource index, section plan, and evidence plan first. Then write an argument-led outline: each section must state the reader question, intended claim, supporting artifact paths, citations that already exist, and what must be called a limitation or open question. Also write `drafts/writing_storyline.md` with headings for the resolved profile: research problem; why it matters or technical/data bottleneck; prior-work tension; root reason; core insight; design choice/method mapping; contribution claims; evidence and ablation map; alternative explanations; limitations/boundary conditions; and reviewer questions. For UTD/IS/INFORMS, make the theory/rationale -> mechanism -> design principle -> evidence -> implication chain explicit. For CCF-A venues, make the technical bottleneck -> insight -> method module -> result/ablation/analysis/failure-evidence chain explicit.

Write the evidence map separately from prose. Do not invent references, quantitative outcomes, figures, baselines, or theorem statements. The readiness report must distinguish `ready`, `background_only`, `needs_section_search`, and `unsupported`; for a section with insufficient evidence, recommend reopening the relevant literature-note section rather than making a broad unsourced claim. Finish by naming all four outputs.
