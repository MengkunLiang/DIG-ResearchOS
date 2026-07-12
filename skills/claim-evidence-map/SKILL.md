---
name: claim-evidence-map
description: Map a batch of proposed manuscript or research claims to exact ResearchOS note sections, evidence levels, permitted wording, and evidence gaps. Use when a researcher needs a claim-by-claim source map before drafting, review, revision, or submission.
tools:
  - read_file
  - list_files
  - grep_search
  - write_file
  - finish_task
strict_tools: true
model_tier: medium
temperature: 0.1
allowed_read_prefixes:
  - user_inputs/claim-evidence-map/
  - literature/
  - ideation/
  - drafts/
  - experiments/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - drafts/
outputs_expected:
  claim_map: drafts/skill_claim_evidence_map.md
  claim_record: drafts/skill_claim_evidence_map.json
interaction:
  mode: guided
  language: zh-CN
  summary: 批量将待写 claim 映射到精确笔记 section、实验/综合证据、允许措辞和需要补检的缺口。
  request_required: true
  request_prompt: 请说明这些 claim 将用于论文、综述、idea 还是审稿回复，以及是否有优先章节或禁止的措辞。
  example_request: 为 Introduction 和 Related Work 的 8 条 claim 建立证据映射；没有 FULL-TEXT 支持的条目只能建议背景措辞。
  required_inputs:
    - id: claims
      label: 待映射的 claim 清单
      description: 一行一个 claim，建议标注目标章节和所需强度；不要把希望成立的结论伪装成已有证据。
      paths:
        - user_inputs/claim-evidence-map/claims.md
      extensions: [.md]
      min_bytes: 20
      example: user_inputs/claim-evidence-map/claims.md
  optional_inputs:
    - id: source_scope
      label: 可用来源范围
      description: 可选；列出优先读取的 note 路径、synthesis、实验包或排除的来源。未提供时只扫描项目中相关的现有证据。
      paths:
        - user_inputs/claim-evidence-map/source_scope.md
      extensions: [.md]
      min_bytes: 10
      example: user_inputs/claim-evidence-map/source_scope.md
  outputs:
    - id: claim_map
      label: Claim-证据映射报告
      path: drafts/skill_claim_evidence_map.md
      description: 每条 claim 的状态、精确来源/section、证据等级、允许措辞、风险和下一步补检建议。
    - id: claim_record
      label: Claim-证据映射结构记录
      path: drafts/skill_claim_evidence_map.json
      description: 机器可读的 claim、证据路径、section anchor、支持状态和修复动作。
---

# Claim Evidence Mapping

Treat each submitted claim as unproven until an actual workspace artifact supports it.
Use `grep_search` to locate candidate note sections, then `read_file` to inspect the
specific section before assigning support. Record whether the evidence is direct,
background-only, partial, abstract-only, experiment-backed, or absent. A citation key
or paper title alone is not a sufficient anchor.

For every claim, write: source path(s), exact section/field, evidence level, what the
source actually supports, allowed wording, disallowed overstatement, alternative
explanation/boundary, and a concrete follow-up action. Never create citations or make
an unsupported claim look supported merely because it fits the intended research story.
