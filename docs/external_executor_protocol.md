# External Executor Protocol

ResearchOS 与 Codex CLI、Claude Code、manual executor 或 mock dry-run 的交互只通过 workspace artifact 文件完成。

## 执行器选择

可选执行器：

- `mock_dry_run`：默认测试执行器，只生成协议文件，不跑真实实验。
- `codex_cli`：推荐正式执行器，由外部 Codex 在隔离路径实现和运行实验。
- `claude_code_window`：适合把 `claude_code_prompt.md` 粘贴到 Claude Code 窗口执行。
- `manual`：人工或其它工具按协议写 result pack。

`codex_cli` 是唯一默认允许真实长实验的分支，CLI 会要求二次确认：只有输入 `yes` 才保持 `codex_cli`，否则降级为 `claude_code_window` 做协议联调。非交互 stdin 不可用时 gate 会暂停，等待用户之后 `resume`。

## Handoff 文件

ResearchOS 在 `T5-HANDOFF` 写：

- `external_executor/handoff_pack.json`
- `external_executor/expected_outputs_schema.json`
- `external_executor/allowed_paths.txt`
- `external_executor/AGENTS.md`
- `external_executor/CLAUDE.md`
- `external_executor/README.md`
- `external_executor/job_state.json`
- `external_executor/codex_prompt.md`
- `external_executor/claude_code_prompt.md`
- `external_executor/manual_instructions.md`

`T5-EXECUTOR-GATE` 随后写 `external_executor/executor_selection.json` 并 patch 执行模式。外部执行器必须只在 allowed paths 内工作。当前 `allowed_paths.txt` 使用 `rw/ro/no` 前缀，例如：

```text
rw  external_executor/workdir/
rw  external_executor/raw_results/
rw  external_executor/configs/
rw  external_executor/logs/
ro  external_executor/handoff_pack.json
ro  novelty/required_baselines.json
no  researchos/
no  drafts/
```

## 外部执行器必须写出的文件

```text
external_executor/result_pack.json
external_executor/executor_status.json
external_executor/run_manifest.json
external_executor/raw_results/*
external_executor/configs/*
external_executor/logs/*
```

`executor_status.json` 的 `accepted` 必须保持 `false`。执行器只能声明 `done`；ResearchOS 的 ingest/audit/result-to-claim 决定证据是否可用于论文。

## Result Pack 最小语义

```json
{
  "semantics": "external_executor_result_pack",
  "run_id": "run_x",
  "executor": "codex_cli",
  "dry_run": false,
  "mock_only": false,
  "evidence_grade": "audited_external",
  "baseline_coverage": {
    "status": "complete",
    "required": [],
    "completed": [],
    "missing_baselines": []
  },
  "metrics": [
    {
      "metric_id": "m1",
      "experiment_id": "exp_main",
      "name": "Recall@20",
      "value": 0.213,
      "dataset": "dataset_name",
      "seed": 42,
      "source_artifact": "external_executor/raw_results/main.json"
    }
  ],
  "artifacts": [
    {
      "path": "external_executor/raw_results/main.json",
      "kind": "raw_results",
      "role": "main_result",
      "sha256": "..."
    }
  ],
  "run_manifest": "external_executor/run_manifest.json"
}
```

## 不接受的内容

- 只有自然语言总结，没有 raw result/config/log/hash。
- 修改 ResearchOS 主 repo 中未授权路径。
- dry-run 不标 `mock_only=true`。
- 把 executor 自评写成 `accepted=true`。
- result pack 中 metric 没有 source artifact。

## Resume

如果流程中断：

- handoff 已存在时，从 `T5-EXECUTOR-GATE` 选择执行器。
- 真实外部执行器未写回 result pack 时，`T5-EXTERNAL-WAIT` 会暂停。
- result pack/status 已存在且 schema 合格时，`resume` 会从 `T5-EXTERNAL-WAIT` 自动进入 `T7-INGEST`。
- result pack 存在但引用文件缺失、hash 不符、路径越界、status 不可接收或真实运行没有 run/raw result 记录时，`T5-EXTERNAL-WAIT` 会写 `external_executor/wait_rejection_report.md` 并保持可恢复暂停；修复 artifact 后再次 `resume`。
- audit 已存在且通过时，`T7-CLAIMS` 可只补 claim/evidence pack。
