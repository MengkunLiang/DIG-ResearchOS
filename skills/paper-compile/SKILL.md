---
name: paper-compile
description: Prepare a ResearchOS manuscript as a submission bundle and compile it with the configured native or Docker LaTeX backend. Use when the user needs a real PDF, compile diagnostics, and an auditable summary rather than shell-only LaTeX instructions.
tools:
  - read_file
  - write_file
  - list_files
  - prepare_submission_bundle
  - latex_compile
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.1
allowed_read_prefixes:
  - user_inputs/paper-compile/
  - drafts/
  - literature/
  - submission/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - drafts/
  - literature/
  - submission/
outputs_expected:
  pdf: submission/bundle/main.pdf
  compile_report: submission/compile_report.json
  summary: submission/compile_summary.md
interaction:
  mode: guided
  language: zh-CN
  summary: 将已写好的 ResearchOS 稿件打包并用真实 LaTeX 后端编译；会保留报告和失败原因，不伪造 PDF。
  request_required: false
  request_prompt: 可选：说明需要的引擎（pdflatex/xelatex）或目标会议限制。
  example_request: 使用 xelatex 编译并保留所有警告，目标是中文论文。
  required_inputs:
    - id: manuscript
      label: 已组装的论文 LaTeX
      description: 由 paper-write 或 T8 生成的完整 manuscript，不是单独章节文件。
      paths:
        - user_inputs/paper-compile/paper.tex
        - drafts/paper.tex
      extensions: [.tex]
      min_bytes: 120
      example: drafts/paper.tex
    - id: bibliography
      label: 已核验的参考文献库
      description: 打包阶段会将它复制为 submission/bundle/references.bib。
      paths:
        - user_inputs/paper-compile/references.bib
        - literature/related_work.bib
      extensions: [.bib]
      min_bytes: 40
      example: literature/related_work.bib
  optional_inputs:
    - id: figures
      label: 图表资源
      description: 可选；存在 drafts/figures/ 或 figures/ 时，打包工具会复制被稿件引用的资源。
      paths:
        - drafts/figures/manifest.json
      extensions: [.json]
      min_bytes: 2
      example: drafts/figures/manifest.json
  outputs:
    - id: pdf
      label: 编译后的 PDF
      path: submission/bundle/main.pdf
      description: 只有 LaTeX 后端实际成功时才会存在。
    - id: compile_report
      label: 编译报告
      path: submission/compile_report.json
      description: 记录后端、引擎、日志摘要和成功/失败状态。
    - id: summary
      label: 编译总结
      path: submission/compile_summary.md
      description: 说明输入、PDF 路径、警告和需要人工处理的问题。
---

# Real LaTeX Compilation

Read `user_inputs/paper-compile/_intake.md` first. In an existing project workspace, inspect the selected source and bibliography semantically before compiling. If a file is missing, its figure paths are unclear, or the requested engine conflicts with the manuscript, write `user_inputs/paper-compile/_followup_request.md` and call `ask_human`; do not claim a predicted PDF exists.

1. For a standalone upload, copy the exact contents of `user_inputs/paper-compile/paper.tex` to `drafts/paper.tex` and `user_inputs/paper-compile/references.bib` to `literature/related_work.bib` before calling the packaging tool; record those staging paths in the summary. In a project workspace, retain the existing standard files. Use `prepare_submission_bundle` with `paper_path="drafts/paper.tex"`, `bib_path="literature/related_work.bib"`, and `bundle_dir="submission/bundle"`.
2. Select `xelatex` only for CJK or font requirements explicitly present in the manuscript/request; otherwise use `pdflatex`. Call `latex_compile` on `submission/bundle/main.tex` with `backend="auto"` and allow the configured Docker fallback.
3. Read the returned compile report. Never claim success without `submission/bundle/main.pdf` and a successful report. Do not use `export_only` as a successful result.
4. Write `submission/compile_summary.md` with the selected backend/engine, PDF path, `submission/compile_report.json`, warning/error summary, and exact next repair action. Do not rewrite scientific prose or bibliography metadata in this Skill.
5. Finish only when all declared outputs exist. On a real compilation failure, preserve the report and finish with a failure reason so the session can be resumed after repair.
