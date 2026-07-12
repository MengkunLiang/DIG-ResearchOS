---
name: pdf-note-card
description: Turn one user-uploaded research PDF into a source-traceable, section-aware standalone literature note card. Use when a researcher wants to upload a paper and obtain a conservative ResearchOS note before comparison, synthesis, citation work, or idea generation.
tools:
  - read_file
  - write_file
  - process_seed_paper
  - extract_paper_sections
  - extract_pdf_text
  - finish_task
strict_tools: true
model_tier: heavy
temperature: 0.15
allowed_read_prefixes:
  - user_inputs/pdf-note-card/
  - user_seeds/
  - literature/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - user_seeds/
  - literature/
outputs_expected:
  note_index: literature/skill_pdf_note_cards/index.md
  note_record: literature/skill_pdf_note_cards/index.json
interaction:
  mode: guided
  language: zh-CN
  summary: 上传一篇 PDF，生成带 section 锚点、证据等级、方法/结果/限制和引用边界的独立文献笔记卡。
  request_required: false
  request_prompt: 可选：说明这篇论文的用途，例如用于 Related Work、方法比较、某个 claim，或 idea 机制分析。
  example_request: 为这篇 PDF 建立中文笔记卡，重点提取方法、实验、局限和可用于 uplift 机制分析的 section。
  required_inputs:
    - id: paper_pdf
      label: 单篇研究论文 PDF
      description: 上传真实可读 PDF。Skill 会读取 section；扫描件、损坏文件或只含封面会被明确标为不可充分阅读。
      paths:
        - user_inputs/pdf-note-card/paper.pdf
      extensions: [.pdf]
      min_bytes: 1024
      example: user_inputs/pdf-note-card/paper.pdf
  optional_inputs:
    - id: paper_context
      label: 阅读目的或已知标识符
      description: 可选；粘贴 DOI、arXiv ID、已知标题、研究问题或希望优先核验的 section。它不会替代 PDF 本身。
      paths:
        - user_inputs/pdf-note-card/context.md
      extensions: [.md]
      min_bytes: 10
      example: user_inputs/pdf-note-card/context.md
  outputs:
    - id: note_index
      label: PDF 笔记卡索引
      path: literature/skill_pdf_note_cards/index.md
      description: 指向本次动态命名的笔记卡、源 PDF、阅读覆盖、证据等级和下一步建议。
    - id: note_record
      label: PDF 笔记卡结构记录
      path: literature/skill_pdf_note_cards/index.json
      description: 机器可读的笔记路径、来源、section 覆盖、引用边界和未解决问题。
---

# PDF Note Card

Read the intake packet and the uploaded PDF first. Call `process_seed_paper` with
`source="pdf_path"` and `value="user_inputs/pdf-note-card/paper.pdf"` so the original
source is registered under `user_seeds/` without overwriting another paper. Then call
`extract_paper_sections` for the available core sections. Use `extract_pdf_text` only
when a required fact is absent from the bounded section extraction, and preserve the
tool's page-coverage/truncation information.

Create a dynamically named Markdown note beneath `literature/skill_pdf_note_cards/`
and update both declared index files. The card must separately record: source path and
resolved metadata; pages/sections actually read; `FULL-TEXT`, `PARTIAL-TEXT`, or
`UNREADABLE` evidence status; problem; method; data/evaluation; reported results;
limitations; mechanism/design rationale when present; exact section anchors; allowed
citation use; and unresolved facts. A source PDF does not justify an invented DOI,
BibTeX entry, numerical result, or causal claim.

This is a standalone note-card lane, not a substitute for the canonical T3 queue. Do
not write `literature/paper_notes/` or claim a T3 deep-read note is complete. If the
PDF cannot support the requested extraction, write the index with the concrete failure
and use `ask_human` for a readable PDF, DOI, or clarification. Finish by naming the
dynamic note-card path and both declared index paths.
