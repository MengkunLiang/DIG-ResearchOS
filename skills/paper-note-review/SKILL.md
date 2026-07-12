---
name: paper-note-review
description: Recover section-level evidence from existing ResearchOS paper notes for one claim or manuscript section. Use when a draft, idea, or review needs grounded evidence without re-reading the entire literature corpus or inventing citations.
tools:
  - read_file
  - write_file
  - list_files
  - grep_search
  - build_section_evidence_supplement
  - finish_task
strict_tools: true
model_tier: medium
temperature: 0.1
allowed_read_prefixes:
  - user_inputs/paper-note-review/
  - literature/
  - drafts/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - literature/
  - drafts/
outputs_expected:
  evidence_report: literature/skill_note_evidence_report.md
  evidence_record: literature/skill_note_evidence_report.json
interaction:
  mode: guided
  language: zh-CN
  summary: 为一个论文 section 或具体 claim 回查已有笔记卡的精确 section，并给出可引用、仅背景或无法支持的明确结论。
  request_required: true
  request_prompt: 请说明要写/审查的 section 或 claim、语言，以及需要支持、质疑还是限定该主张。
  example_request: 为英文 Introduction 中“agent memory 会造成跨任务 carryover”的机制表述回查现有笔记，列出可用 citation、边界和不足证据。
  required_inputs:
    - id: evidence_question
      label: Claim 或章节问题
      description: 写明目标 section、待支持/质疑的主张、允许的措辞强度和任何已知 citation key。
      paths:
        - user_inputs/paper-note-review/question.md
      extensions: [.md]
      min_bytes: 40
      example: user_inputs/paper-note-review/question.md
  optional_inputs:
    - id: resource_index
      label: 论文资源索引
      description: 可选；有该文件时先生成 section evidence supplement，避免无目标扫描笔记目录。
      paths:
        - drafts/manuscript_resource_index.json
        - literature/synthesis_workbench.json
      extensions: [.json]
      min_bytes: 80
      example: drafts/manuscript_resource_index.json
  outputs:
    - id: evidence_report
      label: Section 证据报告
      path: literature/skill_note_evidence_report.md
      description: 精确 note section、可用/不可用证据、建议措辞和升级路径的可读报告。
    - id: evidence_record
      label: 结构化证据记录
      path: literature/skill_note_evidence_report.json
      description: 每个候选 source 的 note path、heading、证据等级、允许用法和 claim 结论。
---

# Section-Level Note Evidence Review

Read the evidence question. If a manuscript resource index exists, call `build_section_evidence_supplement` for the closest section first. Use the supplement/card path to run `grep_search` for the named headings, then `read_file` only for the relevant note section. Record exact paths and headings.

Classify each source as `claim_usable`, `background_or_boundary_only`, or `insufficient`. Never turn abstract-only or weak cards into mechanism proof. If exact sections do not support the requested wording, recommend a weaker wording, removal, or a bounded query plan with an explicit evidence gap; do not fabricate a citation.
