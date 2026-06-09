# EEI Codex 需求确认文档 v0.1

本文档基于 `external_executor_invocation_proposal.md`、`external_executor_protocol.md`、`Experiment_dev_overview.md`、`agent_pipeline.md` 和 `experiment_module_redesign.md`，整理 T5-T7 阶段中仅针对 `codex_cli` 的 external executor invocation（EEI）需求。

本文档用于形成后续 EEI Codex 开发文档前的需求确认，不包含实现代码，不重新设计 T7 ingest/audit/post-novelty/claims，也不覆盖 `mock_dry_run`、`claude_code_window`、`manual` 的完整行为。

## 1. 目标和边界

EEI Codex 的目标是在 `T5-EXECUTOR-GATE` 选择 `codex_cli` 后，定义 ResearchOS 如何准备、可选启动、记录、等待和校验 Codex CLI 外部执行过程，并保持现有 `external_executor/` artifact-first 契约不被削弱。

核心边界：

- ResearchOS 负责编译实验协议、记录执行器选择、可选调用 Codex CLI、等待写回文件、做进入 T7 前的结构校验。
- Codex CLI 负责在允许路径内实现和运行真实实验，并写回结构化结果、状态、manifest、raw results、configs 和 logs。
- ResearchOS 不接受只有自然语言总结的实验结果。
- Codex CLI 可以声明执行完成，但不能声明证据被 ResearchOS 接受；`executor_status.json.accepted` 必须保持 `false`。
- 无论 Codex CLI 是否由 ResearchOS 主动启动，真实结果进入 T7 前都必须经过 `T5-EXTERNAL-WAIT`。[待修改]

## 2. 推荐 MVP 决策

### 2.1 调用方式

最建议方式：MVP 采用“默认 handoff/wait，可选 auto-launch”的 Codex 调用策略。[待修改]

- 选择 `codex_cli` 后，ResearchOS 必须继续要求用户二次确认。
- 默认不静默启动 Codex CLI；`auto_launch=false` 时只写入调用说明和必要 artifact，然后进入 `T5-EXTERNAL-WAIT`。
- 当 `auto_launch=true` 且用户已确认时，ResearchOS 可以启动 Codex CLI，并写入完整 invocation artifact。
- 任何真实 Codex invocation 前，CLI 必须展示 command、cwd、prompt file、timeout、network/dependency policy、allowed paths mode 和 log paths。

理由：这符合当前主链“ResearchOS 不默认长时间跑实验”的原则，同时为后续真实托管调用保留清晰扩展点。

### 2.2 状态机集成

最建议方式：MVP 不强制新增状态；若实现 ResearchOS 主动启动 Codex CLI，则新增 `T5-EXTERNAL-INVOKE`。[待修改]

推荐状态语义：

```text
T5-HANDOFF
 -> T5-EXECUTOR-GATE
 -> T5-EXTERNAL-INVOKE?   // 仅 auto-launch=true 时需要
 -> T5-EXTERNAL-WAIT
 -> T7-INGEST
 -> T7-AUDIT
 -> T7-POST-NOVELTY
 -> T7-CLAIMS
```

若本阶段只实现 `auto_launch=false`，可以不新增状态，只需在 gate 后写清楚 handoff instructions 并进入 wait。若实现 managed launch，建议加入独立 invoke 状态，避免把“选择执行器”“启动进程”“等待结果”混在同一个节点里。

### 2.3 allowed paths enforcement

最建议方式：MVP 至少实现 post-run audit，后续再引入 pre-run sandbox。

- Codex 必须只在 `allowed_paths.txt` 允许的路径内读写。
- MVP 若没有强 sandbox，CLI 必须明确提示当前 enforcement 是 audit-based。
- `T5-EXTERNAL-WAIT` 必须校验 result pack、run manifest 和被引用 artifact 的路径没有越界。
- 路径校验必须使用规范化后的 workspace-relative path，拒绝 `..` 越界和 workspace 外绝对路径。
- `no` rule 应覆盖 `rw` / `ro` rule。

理由：post-run audit 与现有 artifact validator 最容易一致落地；pre-run sandbox 可作为后续增强，不阻断当前 EEI Codex MVP。

### 2.4 partial results

最建议方式：默认不允许 `PARTIAL_RESULTS_READY` 进入 T7。

- `T5-EXTERNAL-WAIT` 默认只接受 `done` / `COMPLETED` 等完成状态。
- `PARTIAL_RESULTS_READY` 默认写 rejection report 并暂停。
- 只有显式配置 `allow_partial_results=true` 时才允许进入 T7，并且 T7 claims 必须降级或 block 相关 claim。

理由：部分结果容易导致半成品 claim，默认阻断更符合证据链安全。

### 2.5 invocation log

最建议方式：invocation log 是长期可审计 artifact。

新增或确认以下文件：

- `external_executor/executor_invocation.json`
- `external_executor/executor_invocation_log.jsonl`

`executor_invocation.json` 记录当前 invocation summary；`executor_invocation_log.jsonl` 记录 append-only events。它们可以与现有 `executor_events.jsonl` 保持分离，后续再决定是否统一。

## 3. EEI Codex 输入契约

Codex CLI 调用必须依赖 `external_executor/` 中已有 handoff artifact。

必需输入：

| 文件 | 用途 |
| --- | --- |
| `external_executor/handoff_pack.json` | 主实验契约，包含实验计划、metrics、baselines、allowed outputs 和 executor mode |
| `external_executor/executor_selection.json` | 记录 `selected_executor=codex_cli`、二次确认、next state 和 fallback 信息 |
| `external_executor/input_manifest.json` | handoff 输入源和 required executor outputs 清单 |
| `external_executor/expected_outputs_schema.json` | Codex 必须写回的 result/status/manifest/artifact schema |
| `external_executor/allowed_paths.txt` | `rw` / `ro` / `no` 路径策略 |
| `external_executor/AGENTS.md` | Codex/generic coding agent 的执行规则 |
| `external_executor/codex_prompt.md` | Codex 专用主 prompt |
| `external_executor/job_state.json` | 外部执行器生命周期状态 scaffold |
| `external_executor/README.md` | 人类可读 workspace 协议说明 |

推荐 fallback：

- `codex_prompt.md` 是 Codex 主 prompt。
- 只有在 `codex_prompt.md` 缺失或显式禁用时，才 fallback 到 `executor_prompt.md`。

## 4. EEI Codex 输出契约

Codex CLI 必须写回：

| 文件或目录 | 要求 |
| --- | --- |
| `external_executor/result_pack.json` | 必须包含 `semantics=external_executor_result_pack`、metrics、artifacts、run_manifest 引用 |
| `external_executor/executor_status.json` | 必须声明完成状态；`accepted=false` |
| `external_executor/run_manifest.json` | 必须记录 runs、raw/config/log artifact、sha256、provenance |
| `external_executor/raw_results/*` | 机器可读 raw evidence；metric 必须能指向 source artifact |
| `external_executor/configs/*` | 实验配置、seed、环境和可复现性信息 |
| `external_executor/logs/*` | 执行日志 |

如果 ResearchOS 主动启动 Codex CLI，还必须写入：[待修改]

| 文件 | 要求 |
| --- | --- |
| `external_executor/executor_invocation.json` | 当前 invocation summary |
| `external_executor/executor_invocation_log.jsonl` | append-only invocation events |
| `external_executor/logs/codex_cli_stdout.log` | Codex stdout，或在 invocation artifact 中记录等价路径 |
| `external_executor/logs/codex_cli_stderr.log` | Codex stderr，或在 invocation artifact 中记录等价路径 |

## 5. Codex Adapter 职责

建议抽象 `CodexCliExecutorAdapter`，但名称不要求成为最终 API。

职责：[待修改]

- 解析 `executor_selection.json`，确认当前 executor 是 `codex_cli`。
- 检查用户二次确认和 `auto_launch` 配置。
- 在 preview/no-op mode 下打印即将执行的 command、cwd、prompt、timeout、policy 和 log paths。
- 在 auto-launch 模式下检查 Codex CLI command path 是否存在且可执行。
- 确定 Codex CLI 工作目录，并写入 invocation artifact。
- 注入 `codex_prompt.md`。
- 捕获 stdout/stderr。
- 记录 started_at、ended_at、exit_code、signal、timeout、pid。
- 记录脱敏后的 command、argv 和环境变量 allowlist。
- 记录 network、dependency installation、allowed paths enforcement、timeout 等有效策略。
- 调用结束后不直接接受证据，只汇总 result/status/manifest 是否存在。
- 将结果交给 `T5-EXTERNAL-WAIT` 做必需 artifact 校验。

非职责：

- 不判断科学结论是否可信。
- 不把 executor summary 转成 paper claim。
- 不替代 T7 audit。
- 不在未确认时静默启动真实长实验。

## 6. Codex 运行环境建议

### 6.1 工作目录

最建议方式：默认使用 workspace 根目录作为 Codex CLI cwd。

理由：

- Codex 更容易读取 `external_executor/AGENTS.md`、`codex_prompt.md` 和 workspace-relative artifact。
- 现有协议中的路径多为 workspace-relative。
- audit 和 resume 更容易重建上下文。

可选方式：

- 如果后续引入强 sandbox，可以将 cwd 设为 `external_executor/workdir/`。
- 若 cwd 不是 workspace 根目录，adapter 必须确保 Codex 仍能稳定读取 `external_executor/codex_prompt.md`、`handoff_pack.json`、`expected_outputs_schema.json` 和 `allowed_paths.txt`。

### 6.2 prompt 注入

最建议方式：MVP 先把 prompt 注入方式作为 adapter 配置项记录，不在需求文档中绑定具体 CLI flag。

候选方式：

- stdin
- command argument
- prompt file flag

无论采用哪种方式，都必须记录到 `executor_invocation.json`。

### 6.3 安装与认证检查

最建议方式：MVP 必须检查 command 是否存在且可执行；认证状态只做 best-effort smoke check。

- command 缺失或不可执行时，写 recoverable error，不进入不可恢复失败。
- 如果 auth 无法确认，必须给出清晰错误或提示用户手动确认。
- 不得把 credentials 写入 prompt、argv、stdout/stderr 摘要或 invocation log。

## 7. Wait 和 Validation 需求

`T5-EXTERNAL-WAIT` 是 Codex 真实结果进入 T7 前的必需边界。

进入 T7 前必须校验：

- `result_pack.json` 存在。
- `executor_status.json` 存在。
- `run_manifest.json` 存在。
- raw result、config、log artifact 存在。
- `executor_status.json.accepted=false`。
- status/current_state 是允许的完成状态；默认不接受 `PARTIAL_RESULTS_READY`。
- `result_pack.json.semantics=external_executor_result_pack`。
- 每个 metric 都有 `source_artifact`。
- 每个 source artifact 都存在于允许路径内。
- result pack 和 run manifest 中记录的 sha256 与磁盘文件匹配。
- 真实运行必须有非空 run/raw result 记录。
- 不允许只有自然语言总结。
- mock/dry-run 标记不得被误用。
- baseline coverage 必须被表示，并交给 T7 audit 进一步处理。

校验失败时：

- 写 `external_executor/wait_rejection_report.md`。
- 保持可恢复暂停。
- 用户或 Codex 修复 artifact 后，通过 `researchos resume` 重新检查。

校验通过时：

- 写 `external_executor/wait_acceptance_report.json`。
- 进入 `T7-INGEST`。

## 8. Timeout、Heartbeat 和恢复

### 8.1 Timeout

最建议方式：timeout 必须是显式配置项，MVP 提供保守默认值并允许覆盖。

建议默认：

- interactive/local MVP：`timeout_seconds=0` 表示不由 ResearchOS 强杀，仅记录启动与等待。
- managed launch MVP：默认 `timeout_seconds=14400`，即 4 小时。

若发生 timeout：

- adapter 必须记录 timeout 状态。
- 若管理进程，必须按文档化策略 terminate 或 detach。
- 不自动进入 T7。
- 交给 `T5-EXTERNAL-WAIT` 检查是否已有完整 artifacts。

### 8.2 Heartbeat

最建议方式：heartbeat 对 managed launch 作为 SHOULD，对 handoff/wait 模式不强制。 [待修改]

若启用 heartbeat：

- 写 `external_executor/heartbeat.json`。
- stale 默认规则可采用 `last_heartbeat_at` 早于 `max(3 * heartbeat_interval, configured_minimum)`。
- stale 只表示执行状态可疑，不等于证据失败；证据仍由 wait/audit 判定。

### 8.3 Resume

resume 必须基于落盘 artifact，而不是进程内记忆。

resume 时应检查：

- `executor_selection.json`
- `executor_invocation.json`
- `result_pack.json`
- `executor_status.json`
- `run_manifest.json`
- `wait_acceptance_report.json`
- `wait_rejection_report.md`

推荐行为：

- invocation 未发生且 `auto_launch=false`：继续显示 handoff/wait instructions。
- invocation 失败但 artifacts 完整：进入 wait validation。
- invocation 成功但 artifacts 缺失：停留在 wait，并报告缺失文件。
- wait rejection 已存在：重新校验当前 artifact，若修复则通过。

## 9. 安全和隐私

Codex EEI 必须遵守最小权限原则。

安全需求：

- 不在 prompt 中嵌入 secrets，除非用户显式允许。
- 不在 invocation log 中记录 token、API key、cookie、credential path 等敏感信息。
- stdout/stderr 写入前或展示前必须经过脱敏。
- command argv 和 environment allowlist 必须脱敏记录。
- network access 和 dependency installation 必须作为显式 policy 记录。
- 若无法强 sandbox，CLI 必须提示当前为 audit-based enforcement。
- 越权路径、hash mismatch、缺失 source artifact 必须阻断进入 T7。

最建议默认：

- `network_policy=require_confirmation`
- `dependency_install_policy=require_confirmation`
- `allowed_paths_enforcement=audit`
- `require_user_confirmation=true`
- `auto_launch=false`

## 10. CLI/UX 需求

选择 `codex_cli` 后，CLI 应展示：

- selected executor
- workspace path
- handoff directory
- `codex_prompt.md` path
- `allowed_paths.txt` path
- `expected_outputs_schema.json` path
- 是否 auto-launch
- 若 auto-launch：command、cwd、timeout、network/dependency policy、log paths
- 下一步 resume command

UX 必须明确区分：

- Codex process exited
- Codex declared done
- ResearchOS wait accepted
- ResearchOS audit accepted evidence
- claim 是否可用

## 11. 测试和验收标准

MVP 必测：

- 选择 `codex_cli` 但未二次确认时，不启动真实 Codex，按现有策略降级或暂停。
- `auto_launch=false` 时，只生成 instructions 并进入 `T5-EXTERNAL-WAIT`。
- `auto_launch=true` 且 command 不存在时，写清晰可恢复错误。
- preview/no-op mode 能展示 command、cwd、prompt、timeout、policy、log paths。
- managed launch 能写 `executor_invocation.json` 和 `executor_invocation_log.jsonl`。
- stdout/stderr 被捕获到 logs 或等价路径。
- 缺失 `result_pack.json` 时 wait reject/pause。
- `executor_status.json.accepted=true` 时 wait reject。
- 缺失 raw/config/log artifact 时 wait reject。
- hash mismatch 时 wait reject。
- artifact 路径越出 `allowed_paths.txt` 时 wait reject。
- 只有自然语言总结时 wait reject。
- 有效 result/status/manifest/raw/config/log 写回后进入 `T7-INGEST`。
- `PARTIAL_RESULTS_READY` 默认不通过。

Future tests：

- disposable workspace 中的真实 Codex CLI smoke test。
- timeout and resume test。
- heartbeat stale test。
- sandbox escape attempt test。
- log redaction test。
- dependency install policy test。
- network policy test。
- large raw/log artifact truncate/archive/hash test。

## 12. 待确认需求

以下问题是在形成最终 EEI Codex 开发文档前仍建议确认的事项。已由现有文档或本次推荐策略可收敛的问题不再单独沿用原提案第 15 节编号。

| ID | 待确认需求 | 最建议方式 | 影响 |
| --- | --- | --- | --- |
| C-01 | 本阶段是否实现 `auto_launch=true` 的真实 Codex CLI managed launch，还是只实现 handoff/wait + preview？ | 先实现 handoff/wait + preview；managed launch 作为同一 adapter 的可选能力 | 决定是否需要立即新增 `T5-EXTERNAL-INVOKE` |
| C-02 | 若实现 managed launch，是否正式新增 `T5-EXTERNAL-INVOKE` 状态？ | 新增 | 让 gate、invoke、wait 可审计分离 |
| C-03 | Codex CLI 的实际 command/path 配置从哪里读取？ | 支持显式 config override，默认查找 `codex` | 影响 CLI 配置和错误提示 |
| C-04 | Codex prompt 的最终注入方式是什么？ | 先配置化，记录 stdin/argv/prompt-file 之一 | 影响 process runner 实现 |
| C-05 | auth smoke check 采用什么命令？ | 只做 best-effort；失败时提示用户手动登录，不记录 credential | 避免阻塞 MVP 和泄露凭据 |
| C-06 | managed launch 的默认 timeout 是否采用 4 小时？ | 默认 4 小时，可配置；handoff/wait 模式不强杀 | 影响长实验稳定性 |
| C-07 | heartbeat 是否进入 MVP？ | managed launch 中 SHOULD，非 managed 模式不要求 | 影响 monitor 复杂度 |
| C-08 | network access 默认策略是否 require confirmation？ | 是 | 影响真实实验依赖下载和复现性 |
| C-09 | dependency installation 默认策略是否 require confirmation？ | 是 | 影响安全与可复现性 |
| C-10 | allowed paths 是否只做 audit，还是本阶段要实现强 sandbox？ | MVP 只做 audit，并明确警告；强 sandbox 后续开发 | 影响安全边界和开发量 |
| C-11 | large raw/log 文件的大小上限、截断和归档规则是什么？ | MVP 记录 sha256 和 bytes；stdout/stderr 可设 max log size 并 truncate with notice | 影响日志可靠性 |
| C-12 | 用户重新选择 executor 时，旧 Codex artifacts 如何处理？ | archive 到带时间戳目录或写 supersession event，避免覆盖无记录 | 影响 resume 和审计 |
| C-13 | 是否统一 `executor_events.jsonl` 与 `executor_invocation_log.jsonl`？ | MVP 分离；后续若事件模型稳定再统一 | 降低兼容风险 |
| C-14 | schema version strategy 是否在本阶段落地？ | 至少给 result/status/manifest/invocation 增加 `schema_version` | 影响长期兼容 |

## 13. 建议开发拆分

第一阶段：[待修改]

- 确认 `codex_cli` selection 的 gate 行为。
- 实现/补齐 `auto_launch=false` handoff instructions。
- 实现 preview/no-op mode。
- 增加 invocation artifact schema 草案。
- 强化 `T5-EXTERNAL-WAIT` 对 Codex result/status/manifest/raw/config/log 的校验。

第二阶段：

- 实现 `CodexCliExecutorAdapter` managed launch。
- 捕获 stdout/stderr。
- 写 `executor_invocation.json` 和 `executor_invocation_log.jsonl`。
- 实现 command/path 检测、timeout、脱敏日志。
- 按需新增 `T5-EXTERNAL-INVOKE`。

第三阶段：

- heartbeat/stale monitor。
- 更强的 allowed paths enforcement。
- network/dependency policy enforcement。
- large artifact/log archive 策略。
- 真实 Codex CLI smoke test。

## 14. Traceability

| Requirement | 来源 |
| --- | --- |
| artifact-first external executor 契约 | `external_executor_protocol.md`、`agent_pipeline.md` |
| `codex_cli` 是推荐真实执行器且需要二次确认 | `external_executor_protocol.md`、`agent_pipeline.md` |
| 真实路径进入 `T5-EXTERNAL-WAIT` | `Experiment_dev_overview.md`、`agent_pipeline.md` |
| 必须写回 result/status/run_manifest/raw/config/log | `external_executor_protocol.md` |
| `accepted=false` | `external_executor_protocol.md` |
| 不接受自然语言总结作为证据 | `external_executor_protocol.md`、`external_executor_invocation_proposal.md` |
| partial results 默认不通过 | `agent_pipeline.md` |
| allowed paths 校验 | `external_executor_protocol.md`、`agent_pipeline.md` |
| invocation adapter、logs、timeout、heartbeat 等 | `external_executor_invocation_proposal.md` 推导需求 |

## 15. 需求确认


| ID | 确认需求 |
| --- | --- |
| C-01 | 选择 `codex_cli` 后，不需要再做任何其他确认，自动启动 codex cli，删除 handoff/wait 和 auto-launch 的区分以及 auto-launch 的选项，新增 `T5-EXTERNAL-INVOKE` 状态|
| C-02 | 参考c-01，正式新增 `T5-EXTERNAL-INVOKE` 状态 |
| C-03 | 支持显式 config override，默认查找 `codex` |
| C-04 | 先配置化，记录 stdin/argv/prompt-file 之一 |
| C-05 | 只做 best-effort；失败时提示用户手动登录，不记录 credential |
| C-06 | 默认 4 小时，可配置 |
| C-07 | heartbeat 进入 MVP |
| C-08 | 是 |
| C-09 | 是 |
| C-10 | MVP 只做 audit，并明确警告；强 sandbox 后续开发 |
| C-11 | mvp 记录 sha256 和 bytes；stdout/stderr 可设 max log size 并 truncate with notice |
| C-12 | archive 到带时间戳目录或写 supersession event，避免覆盖无记录 |
| C-13 | mvp 分离；后续若事件模型稳定再统一 |
| C-14 | schema version strategy 在本阶段落地 |