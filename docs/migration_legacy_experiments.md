# Legacy T5/T6/T7 迁移说明

旧设计中：

```text
T5 pilot -> T6 novelty after pilot -> T7 full internal experiment
```

ResearchOS 会在自身 runtime 中实现和运行实验。新版设计废弃这个默认语义。

## 新主链

```text
T5-HANDOFF
 -> T5-EXECUTOR-GATE
 -> T5-DRY-RUN 或 T5-EXTERNAL-WAIT
 -> T7-INGEST
 -> T7-AUDIT
 -> T7-POST-NOVELTY
 -> T7-CLAIMS
```

ResearchOS 负责协议、证据契约、执行器选择、摄取、审计和 result-to-claim。Codex CLI、Claude Code、人工或 mock 执行器负责实验执行。

## 单任务入口变化

为了避免误入旧内部长实验，普通 CLI 调试入口不再静默映射：

- `researchos run-task T5` 会报 retired，并提示使用 `T5-HANDOFF`
- `researchos run-task T6` 会报 retired，并提示使用 `T7-POST-NOVELTY`
- `researchos run-task T7` 会报 retired，并提示使用 `T5-HANDOFF`

如确实需要旧内部实验调试，使用：

- `researchos run-task LEGACY-T5-PILOT --allow-legacy`
- `researchos run-task LEGACY-T6-NOVELTY --allow-legacy`
- `researchos run-task LEGACY-T7-FULL --allow-legacy`

## 旧 workspace 恢复

旧 `evaluation_decision.md` 中的：

```text
next_task: T7
```

会被状态机映射到 `T5-HANDOFF`，避免 resume 后回到内部完整实验。这种映射只用于旧 workspace 的状态机恢复，不用于普通 `run-task T7`。旧 `next_task: T8` / `T8-WRITE` 会映射到 `T8-STYLE-GATE`，除非已有合法 `drafts/writing_style.json`。

## 为什么这样改

真实实验通常需要隔离环境、代码执行、外部 repo、数据集下载和长时间运行。让 ResearchOS runtime 自己执行容易造成预算浪费、resume 混乱和证据不清。新版协议把实验执行移到外部执行器，但所有 claim 必须经过 ResearchOS 的 ingest、integrity audit、post novelty 和 result-to-claim 才能进入写作链。
