# T5 Context Re-boost 实现说明

本文说明当前 re-boost 模块的实现方式。现在 re-boost 由 ResearchOS 在 `T5-REBOOST-GATE` 中直接调用 LLM API 完成，不再暂停等待用户手动拉起 Codex CLI，也不再复制或执行 `context-re-boosting/SKILL.md`。

## 功能定位

re-boost 是外部执行器链路进入 T5 后的第一步。它的职责是把 Pre-T5 已经产生的研究材料重排为外部实验执行器真正需要的执行语境，而不是运行实验、实现方法、选择执行器或写论文。

当前主链为：

```text
T5-REBOOST-GATE
  -> T5-HANDOFF
  -> T5-SKILL-CUSTOMIZATION-GATE
  -> T5-EXPR-MATERIAL-GATE
  -> T5-EXECUTOR-GATE
  -> T5-DRY-RUN / T5-EXTERNAL-WAIT
  -> T7-INGEST ...
```

`T5-REBOOST-GATE` 现在是普通 LLM agent 节点，负责：

- 读取固定 Pre-T5 文件和必要补充上下文。
- 按 `docs/ResearchOS_external_executor_design.md` 中“Context Re-boosting 怎么做？”的要求重排执行语境。
- 写入 `external_executor/handoff_pack.json#context_reboost`。
- 写入 `external_executor/reboost_report.json`。
- 校验通过后自动进入 `T5-HANDOFF`。

后续 `T5-HANDOFF` 会复制 13 个外部执行器模板 skill 到 `external_executor/skills/`，`T5-SKILL-CUSTOMIZATION-GATE` 会继续直接调用 LLM API，读取 `external_executor/skills/skills_customization/SKILL.md` 和 `template_manifest.json`，把这些副本改写为项目专属 skills，并写出 `external_executor/skills/customization_report.json`。

## 运行方式

完整主链中不需要单独操作：

```bash
python -m researchos.cli run --workspace ./workspaces/local-test2
python -m researchos.cli resume --workspace ./workspaces/local-test2
```

单阶段调试 re-boost：

```bash
python -m researchos.cli run-task T5-REBOOST-GATE --workspace ./workspaces/local-test2
```

这条命令会直接调用当前 ResearchOS 配置的 LLM provider。成功后应产生：

- `external_executor/handoff_pack.json`
- `external_executor/reboost_report.json`

不需要在另一个终端启动 Codex CLI，也不需要提交 `执行 external_executor/skills/context-re-boosting/SKILL.md`。

## 输入材料

re-boost agent 必须优先读取这些固定 Pre-T5 文件：

- `project.yaml`
- `literature/synthesis.md`
- `literature/synthesis_workbench.json`
- `literature/domain_map.json`
- `literature/comparison_table.csv`
- `ideation/hypotheses.md`
- `ideation/exp_plan.yaml`
- `ideation/idea_scorecard.yaml`
- `ideation/risks.md`
- `ideation/novelty_audit.md`
- `novelty/novelty_audit.md`

如信息不足，可进一步读取：

- `literature/paper_notes/`
- `literature/paper_notes_abstract/`
- `resources/`
- `user_seeds/seed_external_resources.jsonl`
- `user_seeds/bridge_domains.yaml`
- 已存在的 `external_executor/handoff_pack.json`

## 输出材料

re-boost 的核心输出是：

- `external_executor/handoff_pack.json`

该文件至少应包含：

- `schema_version: external_executor_handoff.v1`
- `semantics: external_experiment_handoff_contract`
- `status: context_reboost_completed`
- `context_reboost`
- 顶层 `baseline_matrix`
- 顶层 `claim_evidence_matrix`

`context_reboost` 必须覆盖设计文档要求的关键问题：

- 当前研究目标是什么。
- central hypothesis 是什么。
- 方法机制不能偏离什么。
- 哪些方法模块是核心贡献意图，哪些只是候选。
- novelty audit 中有哪些 required baselines。
- 哪些 baseline 必须跑，哪些可以替代。
- 哪些实验构成最低闭环。
- 哪些结果可支持强 claim。
- 哪些结果只能支持弱 claim。
- 哪些 claim 现在不能说。
- 实验结果如何反向精炼 method / idea。
- 外部执行器完成后必须交给 Writer 什么。

推荐结构：

```json
{
  "schema_version": "external_executor_handoff.v1",
  "semantics": "external_experiment_handoff_contract",
  "status": "context_reboost_completed",
  "context_reboost": {
    "project_goal": "",
    "central_hypothesis": "",
    "method_mechanism": {
      "core_mechanism": "",
      "must_preserve_components": [],
      "candidate_components": [],
      "allowed_refinements": [],
      "forbidden_scope_changes": []
    },
    "required_baselines": [],
    "baseline_matrix": [],
    "claim_evidence_matrix": [],
    "minimum_experiment_loop": [],
    "iteration_budget": {
      "max_rounds": 3,
      "stop_conditions": [
        "budget_exhausted",
        "improvement_plateau",
        "required_baseline_unavailable",
        "audited_target_reached",
        "implementation_blocked",
        "claim_must_be_narrowed"
      ]
    },
    "claim_boundaries": [],
    "writer_handoff_contract": [],
    "source_files_used": [],
    "known_context_mismatches": []
  },
  "baseline_matrix": [],
  "claim_evidence_matrix": []
}
```

同时会写：

- `external_executor/reboost_report.json`

该报告记录 re-boost 的 source files、缺失的可选来源、已发现的上下文冲突以及 handoff pack 路径。

## T5-HANDOFF 如何使用 re-boost 输出

`T5-HANDOFF` 仍调用 `build_experiment_handoff_pack`，并优先读取已有的：

- `external_executor/handoff_pack.json#context_reboost`

如果该字段完整，工具会保留 re-boost 结果，并在此基础上补全：

- `method_intent`
- `experiment_contract`
- executor prompts
- `expected_outputs_schema.json`
- `allowed_paths.txt`
- `AGENTS.md`
- `CLAUDE.md`
- `job_state.json`
- `external_executor/skills/template_manifest.json`
- `external_executor/expr/`

如果直接调试 `BuildExperimentHandoffPackTool` 且没有 re-boost 输出，工具仍保留确定性 fallback，用于生成一个最小可用的 `context_reboost`。完整主链会先经过 `T5-REBOOST-GATE`，因此正常不会依赖这个 fallback。

## 相关代码位置

状态机：

- `config/system_config/state_machine.yaml`
  - `T5-REBOOST-GATE`
  - `T5-HANDOFF`

re-boost agent：

- `researchos/agents/experimenter.py`
  - `mode == "reboost"`
  - `validate_outputs(mode="reboost")`

handoff 工具消费 re-boost 输出：

- `researchos/tools/external_experiment.py`
  - `validate_context_reboost_handoff`
  - `_existing_context_reboost_for_handoff`
  - `BuildExperimentHandoffPackTool`

任务 IO：

- `researchos/orchestration/task_io_contract.py`

测试：

- `tests/unit/test_state_machine_runtime_features.py`
- `tests/unit/test_external_experiment_tools.py`

## 验证点

当前实现应满足：

- 进入 T5 时先执行 `T5-REBOOST-GATE`，不是直接进入 `T5-HANDOFF`。
- `T5-REBOOST-GATE` 会直接调用 LLM API，而不是进入 human gate。
- `T5-REBOOST-GATE` 不复制 `context-re-boosting` skill。
- `T5-REBOOST-GATE` 成功后写入 `external_executor/handoff_pack.json` 和 `external_executor/reboost_report.json`。
- `handoff_pack.json#context_reboost` 结构不完整时，re-boost 阶段校验失败并可通过 `resume` 修复。
- re-boost 成功后自动进入 `T5-HANDOFF`。
- `T5-HANDOFF` 会保留 re-boost 写入的 `context_reboost`，并继续补全外部执行协议。
