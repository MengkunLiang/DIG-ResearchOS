---
name: draft-evidence-repair
description: Audit a manuscript or section for unsupported claims, missing citations, stale evidence mappings, and overly strong wording, then prepare a traceable repair package without changing project facts. Use when a researcher needs to repair evidence before review, revision, or submission.
tools:
  - read_file
  - write_file
  - write_structured_file
  - list_files
  - grep_search
  - audit_manuscript_claims
  - audit_paper_claims
  - build_manuscript_resource_index
  - plan_manuscript_evidence
  - ask_human
  - update_skill_workflow
  - finish_task
strict_tools: true
model_tier: heavy
temperature: 0.1
allowed_read_prefixes:
  - user_inputs/draft-evidence-repair/
  - literature/
  - ideation/
  - drafts/
  - submission/
  - _runtime/skill_sessions/
allowed_write_prefixes:
  - user_inputs/draft-evidence-repair/
  - drafts/
outputs_expected:
  repair_report: drafts/draft_evidence_repair.md
  repair_record: drafts/draft_evidence_repair.json
  patch_plan: drafts/draft_evidence_patch_plan.md
  claim_boundary: drafts/draft_evidence_claim_boundary.md
  workflow_manifest: drafts/draft_evidence_repair_manifest.json
interaction:
  mode: guided
  language: zh-CN
  summary: 审计草稿中没有证据、没有引用、来源过弱或表述过强的内容，给出可追溯修复计划；不会虚构文献、实验结果或项目协议。
  request_required: true
  request_prompt: 请说明要修复的稿件/章节、目标 venue 或审阅问题、可用文献和实验证据，以及是否允许直接生成补丁建议。
  example_request: 审计 drafts/paper.tex 的所有强主张与引用；把 abstract-only 证据降级，并给出最小修复方案。
  required_inputs:
    - id: manuscript_target
      label: 稿件路径与审计目标
      description: 指定 workspace 相对稿件路径、需要修复的主张类型和目标审阅/投稿约束。
      paths:
        - user_inputs/draft-evidence-repair/request.md
      extensions: [.md]
      min_bytes: 40
      example: user_inputs/draft-evidence-repair/request.md
  optional_inputs:
    - id: evidence_context
      label: 可用证据包
      description: 可选；列出 literature cards、实验 evidence pack、BibTeX 或相关审计。未提供时会先建立资源索引并请求缺失项目。
      paths:
        - user_inputs/draft-evidence-repair/evidence.md
        - drafts/experiment_evidence_pack.json
      extensions: [.md, .json]
      min_bytes: 10
      example: user_inputs/draft-evidence-repair/evidence.md
  outputs:
    - id: repair_report
      label: 草稿证据修复报告
      path: drafts/draft_evidence_repair.md
      description: 按严重度列出 claim、证据、引用、措辞、版本和可行动修复建议。
    - id: repair_record
      label: 草稿证据修复结构记录
      path: drafts/draft_evidence_repair.json
      description: 每条问题的来源、严重度、允许措辞、建议动作和验证状态。
    - id: patch_plan
      label: 草稿修复计划
      path: drafts/draft_evidence_patch_plan.md
      description: 章节级最小改动、需要补检/补实验的项和不得自动修改的事实。
    - id: claim_boundary
      label: Claim 边界表
      path: drafts/draft_evidence_claim_boundary.md
      description: 可保留、需弱化、需补证据、必须删除的主张及理由。
    - id: workflow_manifest
      label: 草稿修复工作流清单
      path: drafts/draft_evidence_repair_manifest.json
      description: 稿件版本、证据输入、审计工具、风险、补料 Gate 和恢复信息。
workflow:
  kind: integrated
  summary: 从当前稿件与证据包出发审计、绑定和修复主张；所有无法验证的事实保持待补料而非自动补写。
  phases:
    - id: manuscript_contract
      label: 稿件与审计范围
      objective: 解析稿件路径、版本、目标审阅问题和允许修改范围。
      operations: [read request, verify manuscript, identify audit scope]
    - id: evidence_inventory
      label: 证据与引用盘点
      objective: 建立可用资源索引，明确缺失或失效的 evidence mapping。
      operations: [resource index, claim audit, evidence mapping]
    - id: repair_gate
      label: 缺口补料与修复范围确认
      objective: 对不可修复的事实询问用户，是补文献/实验、降级措辞、删除还是暂停。
      operations: [claim boundary, ask human, repair decision]
      human_gate: true
    - id: repair_package
      label: 修复报告与补丁计划
      objective: 输出最小可审计修复计划，不把建议当作已执行的研究结论。
      operations: [repair report, patch plan, artifact manifest]
---

# Draft Evidence Repair

Read only the declared manuscript and authorized evidence. Separate literature claims,
experimental claims, and rhetorical positioning. For each issue state the current text,
evidence status, source/artifact, allowed wording, and the smallest action that would
resolve it. Never create a citation, experiment result, metric, dataset, or concrete
protocol because the draft appears to require one.
