---
name: paper-section-evidence
description: Answer a bounded research question from exact sections of one uploaded paper PDF, preserving page and section anchors and evidence boundaries. Use when a researcher needs a precise method, result, limitation, dataset, or claim check without a full literature note card.
tools:
  - read_file
  - write_file
  - process_seed_paper
  - extract_paper_sections
  - extract_pdf_text
  - finish_task
strict_tools: true
model_tier: medium
temperature: 0.1
allowed_read_prefixes:
  - user_inputs/paper-section-evidence/
  - user_seeds/
  - literature/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - user_seeds/
  - literature/
outputs_expected:
  evidence_report: literature/skill_section_evidence.md
  evidence_record: literature/skill_section_evidence.json
interaction:
  mode: guided
  language: zh-CN
  summary: 上传一篇 PDF 并提出具体问题，从相关 section 提取带锚点的证据、允许措辞和不确定项，而非泛泛总结整篇论文。
  request_required: true
  request_prompt: 请明确要核验的问题，例如方法机制、数据集、实验设置、报告数字、局限，或某个待写 claim 是否得到支持。
  example_request: 从该论文的 Method、Experiments 和 Limitations 核验其异质 treatment 机制、实验数据和可用于 Related Work 的保守表述。
  required_inputs:
    - id: paper_pdf
      label: 单篇研究论文 PDF
      description: 上传真实可读 PDF；Skill 只对实际可提取 section 作出结论。
      paths:
        - user_inputs/paper-section-evidence/paper.pdf
      extensions: [.pdf]
      min_bytes: 1024
      example: user_inputs/paper-section-evidence/paper.pdf
  optional_inputs:
    - id: known_identifier
      label: 已知 DOI、arXiv ID 或题名
      description: 可选；用于标注来源，不会替代对 PDF section 的实际读取。
      paths:
        - user_inputs/paper-section-evidence/context.md
      extensions: [.md]
      min_bytes: 10
      example: user_inputs/paper-section-evidence/context.md
  outputs:
    - id: evidence_report
      label: Section 证据报告
      path: literature/skill_section_evidence.md
      description: 对每个问题列出查阅 section、直接证据、允许表述、禁止延伸和需要补读的位置。
    - id: evidence_record
      label: Section 证据结构记录
      path: literature/skill_section_evidence.json
      description: 机器可读的问题、读取范围、section/page 锚点、证据等级、结论和不确定性。
---

# Paper Section Evidence

Read the request and register the uploaded PDF through `process_seed_paper` with
`source="pdf_path"` and the declared input path. Translate the request into a small
set of target sections, then call `extract_paper_sections` before reading broader text.
Use `extract_pdf_text` only to resolve a concrete missing fact, and record its
page-coverage or truncation status.

Answer only from text actually extracted from the PDF. For every substantive answer,
retain the section heading and page or locator returned by the tool. Separate direct
source facts, cautious interpretation, and unsupported requests. A method description
does not prove a reported result; an abstract-only extraction does not justify a
mechanism or causal claim. Do not manufacture quotations, numeric values, a DOI, or a
BibTeX entry.

Write both declared outputs even when the requested evidence is unavailable. In that
case name the exact unreadable or missing section and formulate the smallest useful
human follow-up. This Skill is intentionally narrower than `pdf-note-card`: it must
not claim to create a complete T3 note card.
