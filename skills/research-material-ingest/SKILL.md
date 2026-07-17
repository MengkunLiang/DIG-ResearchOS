---
name: research-material-ingest
description: Register user-provided research PDFs, datasets, code, URLs, and context notes into a bounded, source-traceable ResearchOS material manifest. Use before literature work, experiment design, paper writing, or a standalone analysis when a researcher needs to upload and organize their own project materials.
tools:
  - read_file
  - list_files
  - upload_seed_pdf
  - upload_seed_data
  - upload_seed_code
  - ask_human
  - write_file
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.1
allowed_read_prefixes:
  - user_inputs/research-material-ingest/
  - user_seeds/
  - resources/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - user_seeds/
outputs_expected:
  material_manifest: user_seeds/skill_material_manifest.json
  material_summary: user_seeds/skill_material_summary.md
interaction:
  mode: guided
  language: zh-CN
  summary: 导入并盘点研究者自己的 PDF、数据、代码和背景说明，建立可追溯材料清单与后续可用性边界。
  request_required: false
  request_prompt: 可选：说明这些材料要服务的主题、任务、论文、实验或写作目标。
  example_request: 导入我的已发表论文、实验数据和代码；区分可公开使用、仅供复现和暂时缺失的材料。
  required_inputs:
    - id: material_inventory
      label: 材料说明与路径清单
      description: 说明每份材料的角色、来源、使用限制和对应文件名；实际文件放在同一输入目录或已存在的 workspace 相对路径中。
      paths:
        - user_inputs/research-material-ingest/materials.md
      extensions: [.md]
      min_bytes: 20
      example: user_inputs/research-material-ingest/materials.md
  optional_inputs:
    - id: research_context
      label: 研究目标与材料使用边界
      description: 可选；说明研究主题、待验证问题、版权/保密限制和不能上传的材料。
      paths:
        - user_inputs/research-material-ingest/context.md
      extensions: [.md]
      min_bytes: 10
      example: user_inputs/research-material-ingest/context.md
  outputs:
    - id: material_manifest
      label: 研究材料结构清单
      path: user_seeds/skill_material_manifest.json
      description: 每项材料的路径、类别、来源、可读性、注册状态、使用限制和未解决项。
    - id: material_summary
      label: 研究材料导入报告
      path: user_seeds/skill_material_summary.md
      description: 面向人工的材料覆盖、成功导入、无法读取/缺失材料和下一步建议。
---

# Research Material Ingest

Read `materials.md` first and inspect only the listed workspace-relative files or files under this Skill's input directory. Classify each item as PDF, data, code, URL/context, or unsupported. For eligible local files, use the matching
`upload_seed_pdf`, `upload_seed_data`, or `upload_seed_code` tool so ResearchOS keeps the normal user-seed provenance. Do not copy arbitrary directory trees manually.

Write both declared manifest files even when part of the input cannot be imported. For each entry record the original path, stated owner/source, intended role, import result, machine-readable limitation, and whether it is safe to use as evidence, a reproduction input, or only background. Do not inspect secrets, silently publish private material, or infer licence/permission from a filename.

When a listed path is absent or its intended use is ambiguous, write a focused follow-up request and use `ask_human` if available. Continue to register usable materials; the output must make the unresolved items visible rather than blocking or inventing replacements. Finish by naming both outputs and the exact materials that need human action.
