---
name: paper-polish
description: Polish a Chinese or English academic LaTeX manuscript while preserving verified claims, citations, numbers, and author-controlled scope. Use after a draft exists when the user needs clearer prose, stronger structure, and a traceable change report rather than a silent rewrite.
tools:
  - read_file
  - write_file
  - audit_manuscript_claims
  - audit_paper_claims
  - finish_task
strict_tools: true
model_tier: standard
temperature: 0.15
allowed_read_prefixes:
  - user_inputs/paper-polish/
  - drafts/
  - literature/
  - experiments/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - drafts/
outputs_expected:
  polished_manuscript: drafts/polished_paper.tex
  polish_report: drafts/polish_report.md
  manuscript_audit: drafts/polished_manuscript_audit.md
interaction:
  mode: guided
  language: zh-CN
  summary: 在不篡改证据、数值和引用的前提下润色论文，并保留原稿、变更理由和审计报告。
  request_required: true
  request_prompt: 请说明使用语言、目标会议/期刊、希望优先优化的方面，以及是否允许重组段落。
  example_request: 以英文 NeurIPS 风格润色，允许重组 Introduction 和 Related Work，但不要改变任何实验数字。
  required_inputs:
    - id: manuscript
      label: 待润色的论文 LaTeX
      description: 原稿会保持不变，润色版写入 drafts/polished_paper.tex。
      paths:
        - user_inputs/paper-polish/manuscript.tex
        - drafts/paper.tex
      extensions: [.tex]
      min_bytes: 120
      example: drafts/paper.tex
  optional_inputs:
    - id: bibliography
      label: 已核验的 BibTeX
      description: 可选；用于确认润色期间没有产生不存在的 citation key。
      paths:
        - literature/related_work.bib
        - user_inputs/paper-polish/references.bib
      extensions: [.bib]
      min_bytes: 40
      example: literature/related_work.bib
    - id: evidence_pack
      label: 实验证据材料
      description: 可选；存在标准 evidence pack 和 result mapping 时会额外做强断言审计。
      paths:
        - drafts/experiment_evidence_pack.json
        - user_inputs/paper-polish/evidence.md
      extensions: [.json, .md]
      min_bytes: 40
      example: drafts/experiment_evidence_pack.json
    - id: writing_contract
      label: 写作风格与论证主线
      description: 可选；用于在润色时保留 venue-specific rationale、technical contribution emphasis 和证据边界。
      paths:
        - drafts/writing_style.json
        - drafts/writing_storyline.md
        - user_inputs/paper-polish/writing_storyline.md
      extensions: [.json, .md]
      min_bytes: 20
      example: drafts/writing_storyline.md
  outputs:
    - id: polished_manuscript
      label: 润色后的论文
      path: drafts/polished_paper.tex
      description: 原稿的独立润色副本，保留可核验的内容边界。
    - id: polish_report
      label: 润色变更报告
      path: drafts/polish_report.md
      description: 按章节记录结构、表达和限制的变更，以及故意未改的证据问题。
    - id: manuscript_audit
      label: 润色稿审计
      path: drafts/polished_manuscript_audit.md
      description: 对润色稿运行的引用、数值、图表和章节机械审计。
---

# Traceable Paper Polishing

Read the selected manuscript and preserve its original file. If it comes from `user_inputs`, write the polished version directly to `drafts/polished_paper.tex`; otherwise never overwrite `drafts/paper.tex`.

Read the intake packet and inspect project artifacts semantically. If the chosen venue, a scope constraint, a missing source, or a reviewer-facing rationale is unclear, write `user_inputs/paper-polish/_followup_request.md` and call `ask_human` before changing the manuscript.

When `drafts/writing_style.json` or `drafts/writing_storyline.md` exists, preserve and strengthen its venue-aware argument chain rather than merely shortening prose. UTD/IS/INFORMS drafts need a clear rationale -> mechanism -> design -> evidence -> implication story with bounded claims. NeurIPS/ICML/ICLR/KDD drafts need an early technical bottleneck, concise contribution statements, and direct method -> ablation/analysis/failure evidence mapping. Internal profile budgets are diagnostics, not official venue rules.

Polish toward clean UTD/FT50/CCF-A prose without turning the manuscript into a sequence of short, parallel statements. In ordinary body prose, replace AI-like dash, colon, and label constructions such as `Problem:` or `Insight:` with complete sentences that make the causal or logical relation explicit. Preserve a natural bridge between paragraphs, and do not introduce a new term, citation, result, or implication without showing why the prior discussion leads to it. Keep the heading hierarchy compact rather than adding small subsections or `\paragraph{}` fragments. Explain terminology where it first becomes necessary; a real or conditional `such as` example may clarify an abstract mechanism but cannot create evidence. Keep only citations that are real and semantically matched to the sentence they support. Prefer genuinely relevant, high-quality, foundational, influential, or important recent work, but never use venue prestige as a substitute for claim-level support. These are prose preferences, not a mechanical validator, and verified facts and the selected venue template take priority.

Improve argument ordering, topic sentences, terminology consistency, grammar, concision, transitions, and limitations. Never add a number, citation key, factual comparison, result, dataset, author information, or venue requirement absent from verified inputs. Do not remove a limitation merely to make the manuscript sound stronger.

Write the change report by section, including unchanged high-risk claims. Run `audit_manuscript_claims` on the polished output. Only when the standard evidence pack and result mapping are both present, run `audit_paper_claims` as well. Report hard audit failures plainly; do not silently repair evidence by inventing support.
