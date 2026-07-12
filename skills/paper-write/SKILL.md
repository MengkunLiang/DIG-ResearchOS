---
name: paper-write
description: Create an evidence-bounded academic LaTeX manuscript from a research brief or an existing ResearchOS outline. Use for Chinese or English paper drafting when the user needs section-level prose, provenance-aware citations, mechanical manuscript audits, and a resumable workspace workflow.
tools:
  - read_file
  - write_file
  - list_files
  - build_manuscript_resource_index
  - plan_manuscript_sections
  - plan_manuscript_evidence
  - build_manuscript_registries
  - build_alignment_matrix
  - initialize_manuscript_state
  - build_section_evidence_supplement
  - update_manuscript_section_state
  - assemble_manuscript
  - audit_manuscript_claims
  - audit_writing_craft
  - audit_paper_claims
  - finish_task
strict_tools: true
model_tier: heavy
temperature: 0.2
allowed_read_prefixes:
  - user_inputs/paper-write/
  - drafts/
  - literature/
  - ideation/
  - experiments/
  - figures/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - drafts/
outputs_expected:
  manuscript: drafts/paper.tex
  writing_storyline: drafts/writing_storyline.md
  manuscript_audit: drafts/manuscript_audit.md
  craft_audit: drafts/craft_audit.md
  summary: drafts/paper_write_summary.md
interaction:
  mode: guided
  language: zh-CN
  summary: 从研究简介或已有大纲生成可审计的论文初稿；不会编造实验结果、参考文献或作者信息。
  request_required: true
  request_prompt: 请说明论文类型、目标语言/期刊会议、希望完成的范围，以及是否只起草某些章节。
  example_request: 请以英文起草一篇 systems research article，目标为 NeurIPS 风格，先完成完整初稿。
  required_inputs:
    - id: research_brief
      label: 研究简介或已有论文大纲
      description: 必须说明问题、方法设想、可用证据、目标读者及目标语言。已有完整流水线可直接使用 drafts/outline.md。
      paths:
        - user_inputs/paper-write/brief.md
        - drafts/outline.md
      extensions: [.md]
      min_bytes: 80
      example: user_inputs/paper-write/brief.md
  optional_inputs:
    - id: bibliography
      label: 已核验的参考文献库
      description: 可选；只允许使用其中存在且可追溯的 BibTeX key。未提供时不得虚构引用。
      paths:
        - user_inputs/paper-write/references.bib
        - literature/related_work.bib
      extensions: [.bib]
      min_bytes: 40
      example: literature/related_work.bib
    - id: evidence_pack
      label: 实验或论证证据包
      description: 可选；提供后才可写带具体数值的结果和强结论。没有证据时必须保留为研究计划或限制。
      paths:
        - user_inputs/paper-write/evidence.md
        - drafts/experiment_evidence_pack.json
      extensions: [.md, .json]
      min_bytes: 40
      example: drafts/experiment_evidence_pack.json
    - id: writing_contract
      label: 写作风格与论证主线
      description: 可选；项目 workspace 会优先读取已确认的 venue profile 和 storyline。独立运行时若缺失，Skill 会生成明确的补充请求后再继续。
      paths:
        - drafts/writing_style.json
        - drafts/writing_storyline.md
        - user_inputs/paper-write/writing_style.json
      extensions: [.json, .md]
      min_bytes: 20
      example: drafts/writing_style.json
  outputs:
    - id: manuscript
      label: 论文 LaTeX 初稿
      path: drafts/paper.tex
      description: 由各章节草稿机械组装的完整论文；不包含虚构的结果或文献。
    - id: writing_storyline
      label: Venue-aware 论证主线
      path: drafts/writing_storyline.md
      description: 问题、rationale/技术根因、洞见、设计、证据、替代解释与边界的可追溯写作合同。
    - id: manuscript_audit
      label: 稿件声明审计
      path: drafts/manuscript_audit.md
      description: 引用、数值、图表和章节完整性的机械检查结果。
    - id: craft_audit
      label: 写作质量审计
      path: drafts/craft_audit.md
      description: 文风、限制、证据与论证对齐问题的检查结果。
    - id: summary
      label: 写作会话总结
      path: drafts/paper_write_summary.md
      description: 已使用输入、完成章节、未解决证据缺口和下一步。
---

# Evidence-Bounded Paper Drafting

Use the verified session inputs and `user_inputs/paper-write/_intake.md` before writing. The user request chooses scope and language; it never authorizes invented evidence. In a project workspace, inspect selected artifacts semantically before trusting them. If the target venue/language, a key rationale, an evidence source, a citation, or a result is insufficient, write `user_inputs/paper-write/_followup_request.md` with the exact gap, why it affects the paper, and the answer/file requested; then call `ask_human` and wait. Do not draft a final deliverable around a guessed fact.

1. Resolve `drafts/writing_style.json` and `drafts/writing_storyline.md` before prose. If a standalone brief does not contain an explicit venue/language decision, use the focused follow-up protocol. The saved profile is an internal drafting target, not an official venue rule. For UTD/IS/INFORMS, the storyline must show phenomenon/problem -> theory/rationale -> mechanism -> design principle -> evidence -> theoretical/practical implication and boundary. For NeurIPS/ICML/ICLR/KDD, it must show technical/data bottleneck -> root technical reason -> core insight -> method module -> main result/ablation/analysis/failure evidence.
2. Build `drafts/manuscript_resource_index.json`, `drafts/section_plan.json`, `drafts/evidence_plan.json`, and `drafts/figure_table_plan.json` with the corresponding mechanical tools. Build the registries, alignment matrix, and `drafts/paper_state.json` before prose.
3. Read the research brief/outline, every selected evidence input, the resource index, and any existing bibliography. Make an explicit section plan under `drafts/section_outlines/` before drafting.
4. Draft one section at a time under `drafts/sections/`. Call `update_manuscript_section_state` after each completed section. Write the abstract and conclusion only after the main argument is stable.
5. Use quantitative language only when a selected evidence artifact supports it. Use only existing BibTeX keys and literature-note provenance. When a section lacks support, call `build_section_evidence_supplement`; if support remains insufficient, weaken/remove the claim and state the limitation instead of searching from memory or fabricating a citation.
6. Assemble with `assemble_manuscript` to `drafts/paper.tex`. Run `audit_manuscript_claims` and `audit_writing_craft` with `target_venue`, `writing_style_path`, and `storyline_path`; treat profile budget or storyline warnings as diagnostics, not official venue-limit checks. When both `drafts/experiment_evidence_pack.json` and `drafts/result_to_claim.json` exist, also run `audit_paper_claims`.
7. Correct every actionable hard failure. Do not conceal audit failures or overwrite source evidence. Write `drafts/paper_write_summary.md` with inputs used, files created, profile, completed sections, audit status, unsupported claims removed/weakened, and precise next actions.
8. Finish with a concise completion summary that names the output paths. Do not compile here; use `paper-compile` after the manuscript is accepted by the user.

Never generate author identities, affiliations, fabricated BibTeX, fabricated measurements, fake figures, or unverified numerical comparisons.
