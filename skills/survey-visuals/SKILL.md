---
name: survey-visuals
description: Generate the one permitted deterministic taxonomy-overview PDF for a ResearchOS survey. It uses only explicit taxonomy labels and direct paper-ID links from the survey plan, and skips rather than inventing a visual.
tools:
  - build_survey_figures
  - read_file
  - finish_task
strict_tools: true
model_tier: medium
temperature: 0.0
allowed_read_prefixes:
  - user_inputs/survey-visuals/
  - literature/
  - drafts/survey/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - literature/
  - drafts/survey/
outputs_expected:
  visual_manifest: drafts/survey/figures/survey_visual_manifest.json
interaction:
  mode: guided
  language: zh-CN
  summary: 从 survey taxonomy 生成最多一张 150 DPI、英文学术标签、可复现的结构图；不生成性能、相对提升、筛选分数、热图或装饰图。
  request_required: false
  request_prompt: 可选：说明 taxonomy 图希望放在哪个 Taxonomy 小节，或只生成 manifest。
  example_request: 为英文 survey 生成唯一允许的 taxonomy overview；不要绘制性能、baseline、相对提升或比较图。
  required_inputs:
    - id: survey_plan
      label: Survey taxonomy 计划
      description: 需要至少两个显式顶层 taxonomy class；每个计划中直接写明的 paper ID 都必须解析到本地结构化 note card，否则生成 skipped manifest。图只显示类标签和已解析的直接 paper-ID 链接。
      paths:
        - user_inputs/survey-visuals/survey_plan.json
        - drafts/survey/survey_plan.json
      extensions: [.json]
      min_bytes: 80
      example: drafts/survey/survey_plan.json
  outputs:
    - id: visual_manifest
      label: 综述图 manifest
      path: drafts/survey/figures/survey_visual_manifest.json
      description: taxonomy-only 政策、图路径、survey-plan 来源、字体、DPI 与跳过原因的可审计记录。
---

# Deterministic Survey Visuals

Read the intake packet before work. For a standalone upload, stage its exact plan at `drafts/survey/survey_plan.json` and record that staging action; in a project workspace, inspect the existing taxonomy tree first. If the plan lacks two explicit top-level classes, write `user_inputs/survey-visuals/_followup_request.md` only when the user can realistically provide or correct the taxonomy; otherwise produce the explicit skipped manifest.

Call `build_survey_figures` once with the declared survey plan. Read the manifest and report the generated or skipped result. The manifest's `source.paper_link_audit` is mandatory evidence for every displayed direct ID: resolution to a local note card is a source-link check, not an empirical-quality or evidence-strength verdict. Do not write LaTeX sections or claim that a figure demonstrates more than its explicit class and paper-ID fields support. A figure may be inserted later only when the manifest lists `fig_taxonomy_overview.pdf` and the surrounding Taxonomy text explains the structure and source. Do not add performance, baseline, relative-gain, screening-score, safety, risk, or decorative graphics.
