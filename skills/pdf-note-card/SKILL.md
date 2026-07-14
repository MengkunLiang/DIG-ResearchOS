---
name: pdf-note-card
description: Turn one uploaded or explicitly identified research paper into a source-traceable standalone paper reading note with clear locations in the paper. DOI, arXiv ID, OpenAlex ID, or a direct URL can be resolved into the declared input PDF path before reading.
tools:
  - read_file
  - write_file
  - process_seed_paper
  - extract_paper_sections
  - extract_pdf_text
  - fetch_paper_pdf
  - fetch_paper_metadata
  - openalex_search
  - crossref_search
  - semantic_scholar_search
  - arxiv_search
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.15
allowed_read_prefixes:
  - user_inputs/pdf-note-card/
  - user_seeds/
  - literature/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - user_seeds/
  - literature/
intake_tools:
  - fetch_paper_pdf
  - fetch_paper_metadata
  - openalex_search
  - crossref_search
  - semantic_scholar_search
  - arxiv_search
outputs_expected:
  note_index: literature/skill_pdf_note_cards/index.md
  note_record: literature/skill_pdf_note_cards/index.json
interaction:
  mode: guided
  language: zh-CN
  summary: 上传一篇 PDF，或提供 DOI、arXiv/OpenAlex ID、URL 或精确标题；系统会尝试获取 PDF，再生成带论文位置、阅读范围、方法、结果、局限和引用边界的论文阅读笔记。
  request_required: false
  request_prompt: 可选：说明这篇论文的用途，例如用于 Related Work、方法比较、某个 claim，或 idea 机制分析。
  example_request: 为这篇 PDF 建立中文论文阅读笔记，重点提取方法、实验、局限和可用于当前研究问题机制分析的相关内容。
  required_inputs:
    - id: paper_pdf
      label: 单篇研究论文 PDF
      description: 上传真实可读 PDF，或在材料准备时明确提供 DOI、arXiv/OpenAlex ID、直接 URL 或精确标题以尝试下载到此路径。Skill 会读取论文内容；扫描件、损坏文件、访问受限链接或只含封面会被明确标为无法充分阅读。
      paths:
        - user_inputs/pdf-note-card/paper.pdf
      extensions: [.pdf]
      min_bytes: 1024
      example: user_inputs/pdf-note-card/paper.pdf
  optional_inputs:
    - id: paper_context
      label: 阅读目的或已知标识符
      description: 可选；粘贴 DOI、arXiv/OpenAlex ID、直接 URL、已知标题、研究问题或希望优先核验的论文内容。标识可触发受控下载尝试，但元数据或检索命中不会替代可读 PDF。
      paths:
        - user_inputs/pdf-note-card/context.md
      extensions: [.md]
      min_bytes: 10
      example: user_inputs/pdf-note-card/context.md
  outputs:
    - id: note_index
      label: PDF 阅读笔记索引
      path: literature/skill_pdf_note_cards/index.md
      description: 指向本次动态命名的阅读笔记、源 PDF、阅读覆盖范围、证据等级和下一步建议。
    - id: note_record
      label: PDF 阅读笔记结构记录
      path: literature/skill_pdf_note_cards/index.json
      description: 机器可读的笔记路径、来源、阅读覆盖范围、引用边界和未解决问题。
---

# PDF Note Card

Read the intake packet and the resolved or uploaded PDF first. When the intake record
lists a DOI/arXiv/OpenAlex ID or URL, preserve that identifier and its download outcome
in the note provenance. Call `process_seed_paper` with
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
not write `literature/deep_read_notes/` or claim a T3 deep-read note is complete. If the
PDF cannot support the requested extraction, write the index with the concrete failure
and use `ask_human` for a readable PDF, DOI, or clarification. Finish by naming the
dynamic note-card path and both declared index paths.
