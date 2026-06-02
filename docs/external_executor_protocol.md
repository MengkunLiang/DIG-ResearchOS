# External Executor Protocol

ResearchOS 与 Codex CLI、Claude Code、manual executor 或 mock dry-run 的交互只通过 workspace artifact 文件完成。

## 执行器选择

可选执行器：

- `mock_dry_run`：默认测试执行器，只生成协议文件，不跑真实实验。
- `codex_cli`：推荐正式执行器，由外部 Codex 在隔离路径实现和运行实验。
- `claude_code_window`：适合把 `claude_code_prompt.md` 粘贴到 Claude Code 窗口执行。
- `manual_external`：人工或其它工具按协议写 result pack。

## Handoff 文件

ResearchOS 在 `T5-HANDOFF` 写：

- `external_executor/handoff_pack.json`
- `external_executor/expected_outputs_schema.json`
- `external_executor/allowed_paths.txt`
- `external_executor/codex_prompt.md`
- `external_executor/claude_code_prompt.md`
- `external_executor/manual_instructions.md`

外部执行器必须只在 allowed paths 内工作。推荐路径：

- `external_executor/`
- `experiments/external_runs/`
- `experiments/raw_results/`
- `experiments/figures/`
- `experiments/tables/`

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

- handoff 已存在时，从 `T5-DRY-RUN` 或真实外部执行后从 `T7-INGEST` 继续。
- result pack 已存在时，不回 T5 重写协议，直接摄取。
- audit 已存在且通过时，`T7-CLAIMS` 可只补 claim/evidence pack。

