---
name: t4-evolution
description: Start, resume, inspect, or safely pause the native T4 Evolutionary Idea workflow without editing its population, scoring, lineage, or selection files by hand.
tools:
  - read_file
  - write_file
  - list_files
  - grep_search
  - ask_human
  - update_skill_workflow
  - finish_task
strict_tools: true
temperature: 0.2
allowed_read_prefixes:
  - project.yaml
  - user_seeds/
  - literature/
  - ideation/
  - _runtime/
  - user_inputs/t4-evolution/
allowed_write_prefixes:
  - user_inputs/t4-evolution/
  - _runtime/skill_sessions/
outputs_expected:
  launch_note: user_inputs/t4-evolution/launch_note.md
  workflow_manifest: user_inputs/t4-evolution/t4_evolution_manifest.json
interaction:
  mode: guided
  language: zh-CN
  summary: 检查 T4 的材料与当前进度，说明应启动、恢复、查看 Gate1，还是先补充论文阅读笔记；原生 T4 仍由 ResearchOS pipeline 执行。
  request_required: true
  request_prompt: 请说明你要启动新的 T4、恢复未完成运行、查看当前候选，还是在 Gate1 继续操作。
  example_request: 恢复当前 T4；先确认会复用哪些候选和评分，再告诉我下一条安全的 ResearchOS 命令。
  required_inputs: []
  optional_inputs:
    - id: researcher_intent
      label: 本次意图或限制
      description: 可选；例如希望聚焦某一类机制、保留并行候选、调整 Publication Orientation，或只查看证据与谱系。
      paths:
        - user_inputs/t4-evolution/request.md
      extensions: [.md]
      min_bytes: 8
      example: user_inputs/t4-evolution/request.md
  outputs:
    - id: launch_note
      label: T4 启动或恢复说明
      path: user_inputs/t4-evolution/launch_note.md
      description: 记录当前可复用的 T4 状态、不会被覆盖的版本、需要补充的材料，以及下一条应执行的 ResearchOS 命令。
    - id: workflow_manifest
      label: 会话恢复记录
      path: user_inputs/t4-evolution/t4_evolution_manifest.json
      description: 记录已检查的状态、用户意图和完成阶段，供中断后恢复该 Skill 会话使用。
workflow:
  kind: integrated
  summary: 先检查原生 T4 的 Evidence、预运行确认和 Population 状态，再给出唯一安全的启动或恢复路径；不手工编辑原生 T4 产物。
  phases:
    - id: state_check
      label: T4 状态检查
      objective: 判断是首次运行、可恢复运行、等待 Gate1，还是需要先补充上游材料。
      operations: [inspect project and T4 state, inspect evidence summary, identify resume-safe path]
    - id: researcher_choice
      label: 研究者意图确认
      objective: 确认启动、恢复、查看、继续演化、聚焦、组合或回滚的意图；复杂组合只能转交原生 Gate1。
      operations: [explain current state, ask human when intent is ambiguous, preserve parallel candidates]
      human_gate: true
    - id: handoff
      label: 原生 T4 交接
      objective: 写入可读说明并给出唯一安全的 ResearchOS pipeline 命令，不直接改写 T4 的内部状态。
      operations: [write launch note, finish task]
---

# Native T4 Evolution

Start by reading only the declared T4 material and state files: `project.yaml`, `literature/deep_read_notes/`, `literature/shallow_read_notes/`, `literature/bridge_notes/` for real Bridge reading notes, `literature/cross_domain_catalogs/` for B1–B# retrieval context, `ideation/evidence/evidence_index_summary.json`, `ideation/evolution/pre_run_confirmation.json`, `ideation/evolution/state.json`, `ideation/portfolio.json`, `ideation/selected/`, and the current `_runtime` resume record when it exists. Catalogs may expand analogies and validation questions but are never direct claim evidence. Do not inspect unrelated conventional filenames.

Explain the state in researcher language. Say whether T4 will create a new P0, resume an unfinished Route or score batch, reuse P0/P1, wait for Gate1, or continue into T4.5 after a confirmed selection. State which existing Candidate versions remain preserved, whether rollback is available, and whether an input fingerprint makes existing work stale. Do not expose raw JSON in normal interaction.

Use the native pipeline for every state-changing operation. The safe commands are `python -m researchos.cli run --workspace <workspace> --from-task T4` for a fresh T4 entry and `python -m researchos.cli resume --workspace <workspace>` for an interrupted or waiting T4 run. When the workspace is already at `T4-GATE1`, resume so ResearchOS can present the Gate1 choices. Never tell a researcher to run a second concurrent `run` or `resume` process for the same workspace.

This Skill does not create, edit, delete, or select native T4 artifacts. In particular, never write `ideation/evidence/`, `ideation/populations/`, `ideation/evolution/`, `ideation/scoring/`, `ideation/portfolio.json`, `ideation/final_cards/`, `ideation/hypotheses.md`, or `ideation/exp_plan.yaml`. T4 owns Evidence Routing, P0, Independent Scoring, Mutation Child, Crossover Child, Survival Selection, Portfolio, Gate1, and the handoff to T4.5. A request to combine components from different Candidates must be passed to Gate1, where ResearchOS performs Compatibility Check, Gene Donor Map, Independent Scoring, and a second confirmation.

When explaining a native result, distinguish integrity blocks from recoverable work. Native T4 uses `valid`, `repairable`, `degraded`, and `blocked`: only provenance/evidence-permission violations, fabricated or untraceable citations, lineage/ID conflicts, fingerprint or workspace corruption, and Legacy overwrite risk are Hard Invariant blocks. Format envelopes, aliases, enrichable-field gaps, a failed Route, an unscored Candidate, an underfilled Population, a documented `no_improvement` Plan deferral, and an incompatible Crossover are not proof of a failed T4 run. They are diagnostics, bounded repair/retry targets, or degraded states. A Seed may be explicitly conjectural and verification-required; never describe it as verified evidence. Its `CreativeContext` can retain the model's conceptual leap or competing explanation, and a high-`scientific_upside` Wildcard is a comparison option rather than a certification or T4.5 selection. An unscored Candidate remains visible for review/retry and has no synthetic ranking score.

Write `user_inputs/t4-evolution/launch_note.md` as a compact researcher-facing record with: current state, materials actually found, evidence limitations, what the suggested command will do, what it preserves, and the exact next command. Update `user_inputs/t4-evolution/t4_evolution_manifest.json` with the checked state, selected intent, and completed workflow phases so the Skill session is recoverable. If the requested action cannot be inferred safely, write a focused follow-up request and call `ask_human`. Finish only after the launch note reflects the current workspace state.
