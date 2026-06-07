# 外部实验执行器协议

ResearchOS 当前不再默认自己执行长实验。`T5-HANDOFF -> T5-EXECUTOR-GATE -> T5-DRY-RUN/T5-EXTERNAL-WAIT -> T7-*` 是新的实验主链。

## 运行链路

```text
T5-HANDOFF
 -> T5-EXECUTOR-GATE
    -> mock_dry_run: T5-DRY-RUN
    -> codex_cli / claude_code_window / manual: T5-EXTERNAL-WAIT
 -> T7-INGEST
 -> T7-AUDIT
 -> T7-POST-NOVELTY
 -> T7-CLAIMS
```

## T5-HANDOFF 写什么

`build_experiment_handoff_pack` 会生成：

- `external_executor/handoff_pack.json`
- `external_executor/input_manifest.json`
- `external_executor/expected_outputs_schema.json`
- `external_executor/allowed_paths.txt`
- `external_executor/AGENTS.md`
- `external_executor/CLAUDE.md`
- `external_executor/README.md`
- `external_executor/job_state.json`
- `external_executor/codex_prompt.md`
- `external_executor/claude_code_prompt.md`
- `external_executor/manual_instructions.md`

`handoff_pack.json` 包含 metrics、seeds、source artifact hash、required baseline、allowed paths 和 executor output contract。它不是实验结果。

## 执行器选择

`T5-EXECUTOR-GATE` 是状态机级 gate，不启动 LLM。用户选择：

- `mock_dry_run`：只测试协议，不能作为论文实验证据。
- `claude_code_window`：复制 `claude_code_prompt.md` 到 Claude Code，完成后 resume。
- `codex_cli`：外部 Codex 在 `external_executor/workdir` 中执行真实实验。
- `manual`：人工或其它执行器按协议写回结果。

选择后 runtime 写 `external_executor/executor_selection.json`，并把 AGENTS/CLAUDE/prompt 中的 `UNSET` mode 占位 patch 成真实值。

交互式 CLI 中，`T5-EXECUTOR-GATE` 直接回车会选择默认 `mock_dry_run`。选择 `codex_cli` 后必须再次输入 `yes` 才允许真实实验；其它输入会降级为 `claude_code_window`，并在 `executor_selection.json.notes` 记录降级原因。非交互 stdin 不可用时，gate 会暂停等待用户输入，不会默认推进。

## 外部等待与恢复

`T5-EXTERNAL-WAIT` 不调用 LLM。它检查：

- `external_executor/result_pack.json`
- `external_executor/executor_status.json`
- `external_executor/run_manifest.json`
- result pack 引用的 raw/config/log 文件和 sha256
- 引用路径是否落在 `allowed_paths.txt` 的 `rw` 规则内

缺失或 schema 不合格时项目进入可恢复暂停，并写 `external_executor/wait_rejection_report.md`。外部执行器修复或写回结果后运行：

```bash
researchos resume --workspace ./workspace/<project>
```

runtime 会重新检查；通过后写 `external_executor/wait_acceptance_report.json` 并进入 `T7-INGEST`。

`PARTIAL_RESULTS_READY` 默认不能通过外部等待。`check_external_executor_wait`
的默认 `allow_partial_results=false`；如果外部执行器只写回部分结果，runtime 会写
`external_executor/wait_rejection_report.md` 并保持可恢复暂停。只有显式允许 partial 的调试路径
才可放行，并且后续 result-to-claim 必须把相关 claim 降级或标记为不能强 claim。

## 证据规则

每个 metric 必须能追溯到 raw file、config、run id、log 和 sha256。mock/dry-run 必须带：

```json
{
  "dry_run": true,
  "mock_only": true,
  "evidence_grade": "mock_only"
}
```

T7-AUDIT 会检查 required baseline coverage。缺 baseline 时，T7-CLAIMS 会写入 `drafts/must_not_claim.md` 并降低 claim strength。
