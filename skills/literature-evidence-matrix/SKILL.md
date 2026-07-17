---
name: literature-evidence-matrix
description: Build a compact, source-traceable comparison matrix from a bounded set of ResearchOS paper notes, PDF note cards, or identifier records. Use when a researcher needs a review-ready evidence table before synthesis, survey writing, or idea selection.
tools:
  - read_file
  - list_files
  - grep_search
  - write_file
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.1
allowed_read_prefixes:
  - user_inputs/literature-evidence-matrix/
  - literature/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - literature/
outputs_expected:
  matrix_csv: literature/skill_evidence_matrix.csv
  matrix_report: literature/skill_evidence_matrix.md
  matrix_record: literature/skill_evidence_matrix.json
interaction:
  mode: guided
  language: zh-CN
  summary: 将一组已有论文阅读笔记或记录整理为带可回查来源位置、证据等级和空白项的比较矩阵，供综述与 idea 使用。
  request_required: true
  request_prompt: 请说明矩阵要服务的主题、比较字段，以及是否优先方法 taxonomy、实验设计、机制或局限。
  example_request: 为当前研究主题的综述生成方法/数据/任务/指标/机制/局限矩阵，并标注哪些行只允许作为背景。
  required_inputs:
    - id: source_list
      label: 文献来源路径清单
      description: 一行一个 workspace 相对路径，指向结构化 paper note、PDF note card 或 identifier record；建议 3-30 项。
      paths:
        - user_inputs/literature-evidence-matrix/sources.md
      extensions: [.md]
      min_bytes: 20
      example: user_inputs/literature-evidence-matrix/sources.md
  optional_inputs:
    - id: matrix_schema
      label: 自定义比较字段
      description: 可选；指定列、术语、目标读者和哪些字段必须保留为空而不是猜测。
      paths:
        - user_inputs/literature-evidence-matrix/schema.md
      extensions: [.md]
      min_bytes: 10
      example: user_inputs/literature-evidence-matrix/schema.md
  outputs:
    - id: matrix_csv
      label: 文献证据矩阵 CSV
      path: literature/skill_evidence_matrix.csv
      description: 一行一篇来源，包含可追溯字段、证据等级和来源路径；未知值保留为空或明确 unknown。
    - id: matrix_report
      label: 矩阵覆盖报告
      path: literature/skill_evidence_matrix.md
      description: 说明输入覆盖、字段缺失、弱证据比例、可用于综述的边界和建议补检方向。
    - id: matrix_record
      label: 矩阵结构记录
      path: literature/skill_evidence_matrix.json
      description: 机器可读的矩阵 schema、行来源、空白字段、证据状态和生成限制。
---

# Literature Evidence Matrix

Validate each requested source path before extracting fields. Use source-native labels where possible and record the path and note section/field that supplied every nontrivial cell. A missing field must remain blank or `unknown`; never infer a dataset, metric, causal mechanism, result, or citation from a title alone.

Write a CSV that is valid RFC-style CSV: quote cells containing commas, quotes, or new lines. Keep the table compact enough to scan, while the Markdown report carries longer explanations, weak-evidence warnings, coverage counts, and proposed section-specific follow-up searches. Clearly distinguish full-text/partial-text/abstract-only/metadata records and prevent the latter two from becoming strong evidence rows.
