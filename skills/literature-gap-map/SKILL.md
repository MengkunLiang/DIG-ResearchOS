---
name: literature-gap-map
description: Build an evidence-bounded map of unresolved research questions, boundary conditions, and missing comparisons from existing ResearchOS literature artifacts. Use before ideation or a related-work section when a researcher needs to distinguish retrieval gaps from evidence-supported research opportunities.
tools:
  - read_file
  - list_files
  - grep_search
  - write_file
  - finish_task
strict_tools: true
model_tier: medium
temperature: 0.15
allowed_read_prefixes:
  - user_inputs/literature-gap-map/
  - literature/
  - ideation/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - literature/
outputs_expected:
  gap_report: literature/skill_gap_map.md
  gap_record: literature/skill_gap_map.json
interaction:
  mode: guided
  language: zh-CN
  summary: 从已有笔记、综合和比较表中识别被证据支持的未解问题、边界条件和缺失对照，并区分“尚未检到”与“可主张研究缺口”。
  request_required: true
  request_prompt: 请说明要定位的主题、目标决策（综述、Idea、实验或 Related Work）和不应被误当成研究缺口的范围。
  example_request: 基于这些笔记定位 treatment heterogeneity 的机制边界和缺失 baseline；将检索覆盖不足与真正可进入 T4 的问题分开。
  required_inputs:
    - id: evidence_sources
      label: 文献证据来源清单
      description: 一行一个 workspace 相对路径，指向 paper notes、PDF note cards、synthesis、比较矩阵或已核验记录；建议至少 3 项。
      paths:
        - user_inputs/literature-gap-map/sources.md
      extensions: [.md]
      min_bytes: 20
      example: user_inputs/literature-gap-map/sources.md
  optional_inputs:
    - id: gap_scope
      label: 缺口定义与使用场景
      description: 可选；给出研究问题、目标场景、理论/方法/实验边界以及未来会如何使用该 map。
      paths:
        - user_inputs/literature-gap-map/context.md
      extensions: [.md]
      min_bytes: 10
      example: user_inputs/literature-gap-map/context.md
  outputs:
    - id: gap_report
      label: 证据约束研究缺口图
      path: literature/skill_gap_map.md
      description: 将可观察的不足、证据强度、反例/边界、需补检 section 和可进入 Idea 的问题分别列出。
    - id: gap_record
      label: 缺口图结构记录
      path: literature/skill_gap_map.json
      description: 机器可读的 gap 候选、来源/section 锚点、分类、证据等级、反证风险和推荐动作。
---

# Literature Gap Map

First validate that every listed source exists. Read the supplied materials by decision
relevance and retain a path plus section or field anchor for every gap candidate. Use
`grep_search` only to locate supporting passages; read the relevant source before
recording a conclusion. Do not interpret a missing note, a metadata-only record, or
limited retrieval coverage as proof that a scholarly gap exists.

For each entry, classify it as an observed limitation, an untested boundary condition,
a missing comparison, an unresolved mechanism, a retrieval/coverage deficiency, or
unsupported speculation. Record supporting and countervailing evidence separately,
the strongest wording currently allowed, what exact additional paper/section/evidence
would upgrade it, and whether it is suitable for background, a T4 candidate input, or
only further search.

Write both declared outputs. The report may suggest targeted follow-up queries or
paper-note sections, but must not turn a gap map into a novelty claim or final
hypothesis. Missing or contradictory evidence is a visible result, not a reason to
invent a research story.
