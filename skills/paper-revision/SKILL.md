---
name: paper-revision
description: Revise an academic LaTeX manuscript from reviewer comments with an evidence-aware response letter and traceable change log. Use when the user has a draft plus reviews and needs revisions that distinguish accepted edits, rebuttals, and requests requiring new evidence.
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
  - user_inputs/paper-revision/
  - drafts/
  - literature/
  - experiments/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - drafts/
outputs_expected:
  revised_manuscript: drafts/revised_paper.tex
  response_letter: drafts/revision_response.md
  change_log: drafts/revision_change_log.md
  manuscript_audit: drafts/revised_manuscript_audit.md
interaction:
  mode: guided
  language: zh-CN
  summary: 根据审稿意见修订论文并生成逐点回应；无法由现有证据支持的要求会明确列为待补实验或保留异议。
  request_required: true
  request_prompt: 请说明当前轮次、目标语言、是否允许改变论文结构，以及哪些审稿意见必须优先处理。
  example_request: 处理 R1-R3 的主要问题，英文回复；可重写 Method 和 Limitations，但不要杜撰补充实验。
  required_inputs:
    - id: manuscript
      label: 当前论文 LaTeX
      description: 原稿保持不变，修订后的独立版本写入 drafts/revised_paper.tex。
      paths:
        - user_inputs/paper-revision/manuscript.tex
        - drafts/paper.tex
      extensions: [.tex]
      min_bytes: 120
      example: drafts/paper.tex
    - id: reviews
      label: 审稿意见或编辑决定
      description: 保留 reviewer 编号和原始问题，便于逐条回应并生成 change log。
      paths:
        - user_inputs/paper-revision/reviews.md
        - drafts/reviews.md
      extensions: [.md]
      min_bytes: 40
      example: user_inputs/paper-revision/reviews.md
  optional_inputs:
    - id: evidence_pack
      label: 新旧证据包
      description: 可选；有标准证据包时可检查修订是否把弱证据升级成过强措辞。
      paths:
        - drafts/experiment_evidence_pack.json
        - user_inputs/paper-revision/evidence.md
      extensions: [.json, .md]
      min_bytes: 40
      example: drafts/experiment_evidence_pack.json
    - id: writing_contract
      label: 写作风格与论证主线
      description: 可选；帮助回应审稿人时保持既定 venue 的叙事主线、贡献粒度和证据边界。
      paths:
        - drafts/writing_style.json
        - drafts/writing_storyline.md
        - user_inputs/paper-revision/writing_storyline.md
      extensions: [.json, .md]
      min_bytes: 20
      example: drafts/writing_storyline.md
  outputs:
    - id: revised_manuscript
      label: 修订后的论文
      path: drafts/revised_paper.tex
      description: 保留原稿不变的可提交修订副本。
    - id: response_letter
      label: 逐点审稿回复
      path: drafts/revision_response.md
      description: 每条意见的处理、修改位置、证据边界和未处理原因。
    - id: change_log
      label: 变更记录
      path: drafts/revision_change_log.md
      description: 按文件/章节记录实际变化、未变化和原因。
    - id: manuscript_audit
      label: 修订稿审计
      path: drafts/revised_manuscript_audit.md
      description: 修订后引用、数值、图表和章节问题的机械检查。
---

# Evidence-Aware Reviewer Revision

Read the intake packet, selected manuscript, review input, and any existing writing contract first. If reviewer scope, user priority, missing evidence, or target-venue constraint needs clarification, write `user_inputs/paper-revision/_followup_request.md`, call `ask_human`, and record the answer before revising. Create a response matrix for every reviewer comment: `accept`, `clarify`, `rebut_with_existing_evidence`, or `needs_new_evidence`. Do not use a requested result as proof that the result exists.

Write a separate revised manuscript. Preserve the existing `writing_storyline.md` logic where it is evidence-backed: UTD/IS revisions should protect the rationale/mechanism/design/implication chain and boundary conditions; CCF-A revisions should protect the technical bottleneck/insight/method/evidence mapping and concise contribution granularity. In the response letter, quote or accurately identify each comment, say exactly where the manuscript changed, and state when an experiment, citation, or analysis remains unavailable. A respectful rebuttal must be based on the submitted material, not invented support.

Preserve clean publication prose while revising. Ordinary body prose should not rely on dashes, colons, or label sentences such as `Problem:` and `Gap:`. Do not replace a coherent argument with fragmented parallel short sentences to make a change look concise. Maintain natural paragraph-to-paragraph transitions, compact sectioning, and clear first-use explanations of technical and theoretical terms. Use a real or conditional `such as` example only when it improves comprehension without claiming new evidence. Any citation retained or added in a revision must be real and semantically matched to the exact claim, as checked against the bibliography and available source record. Prefer genuinely relevant UTD/FT50/CCF-A, leading disciplinary, foundational, highly cited, influential, and important recent work, while treating quality as secondary to claim-level fit. These are writing preferences rather than a mechanical gate; evidence truthfulness and the target-venue template prevail.

Write the change log, run `audit_manuscript_claims` on the revision, and run `audit_paper_claims` only when both standard evidence inputs exist. Keep unresolved hard audit failures visible in the response letter and final summary.
