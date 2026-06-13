# EEI Codex 需求确认文档 v0.2

本文档基于 `EEI_codex_proposal_v01.md`、其末尾“15. 需求确认”、用户补充确认，以及 `external_executor_invocation_proposal.md`，整理 T5-T7 阶段中 `codex_cli` 外部执行器调用（external executor invocation, EEI）的 v0.2 需求。

v0.2 的核心流程是：在 `T5-EXECUTOR-GATE` 选择 `codex_cli` 即表示系统必须自动拉起 Codex CLI，并新增 `T5-EXTERNAL-INVOKE` 状态承载该调用过程。

本文档只覆盖 `codex_cli`。`mock_dry_run`、`claude_code_window`、`manual` 仅作为状态机相邻分支被提及，不展开其完整需求。

## 1. 已确认的总体语义

EEI Codex 的目标是在 `T5-EXECUTOR-GATE` 选择 `codex_cli` 后，由 ResearchOS 主动启动 Codex CLI，让 Codex 在当前 workspace 运行目录内读取 `external_executor/` 契约、执行真实实验、写回结构化 artifacts，然后由 ResearchOS 在 `T5-EXTERNAL-WAIT` 做进入 T7 前的严格校验。

已确认原则：

- `codex_cli` 是 ResearchOS 托管启动的真实执行器路径。
- `T5-EXECUTOR-GATE` 选择 `codex_cli` 即进入 Codex CLI 托管调用流程。
- 必须新增 `T5-EXTERNAL-INVOKE`。
- 所有 Codex 真实执行结果进入 T7 前仍必须经过 `T5-EXTERNAL-WAIT`。
- Codex CLI 只能声明执行完成，不能声明证据被 ResearchOS 接受。
- `executor_status.json.accepted` 必须保持 `false`。
- ResearchOS 不接受只有自然语言总结、没有 raw/config/log/hash/source artifact 链接的结果。

## 2. 状态机需求

### 2.1 Codex 分支主链

`codex_cli` 分支的目标流程固定为：

```text
T5-HANDOFF
 -> T5-EXECUTOR-GATE
    -> codex_cli
 -> T5-EXTERNAL-INVOKE
 -> T5-EXTERNAL-WAIT
 -> T7-INGEST
 -> T7-AUDIT
 -> T7-POST-NOVELTY
 -> T7-CLAIMS
```

`T5-EXECUTOR-GATE` 负责选择 executor 并写入 `external_executor/executor_selection.json`。当选择值为 `codex_cli` 时，状态机必须进入 `T5-EXTERNAL-INVOKE`，不得直接进入 `T5-EXTERNAL-WAIT`。

`T5-EXTERNAL-INVOKE` 负责启动、监控和记录 Codex CLI 调用。它不负责接受科学证据，也不负责把实验结果转成 claims。

`T5-EXTERNAL-WAIT` 负责检查 Codex 是否已经写回完整、结构合法、可审计的 result/status/manifest/raw/config/log artifacts。只有 wait 通过后才能进入 `T7-INGEST`。

### 2.2 Invoke 失败后的状态

`T5-EXTERNAL-INVOKE` 中的失败应默认是可恢复的，除非 handoff artifact 被破坏。

典型失败包括：

- Codex command 不存在或不可执行。
- auth smoke check 失败或无法确认。
- prompt file 缺失。
- `allowed_paths.txt` 缺失或格式不可解析。
- Codex process spawn 失败。
- Codex process timeout。
- Codex process 非零退出。
- Codex 退出后未写回必需 artifacts。

这些失败必须写入 invocation artifact 和可读错误信息。只要 workspace 契约文件仍完整，项目应保持可恢复暂停，用户修复环境或 artifacts 后可继续。

## 3. Artifact 契约

### 3.1 T5-HANDOFF 输入给 Codex 的文件

Codex 调用必须依赖 workspace 运行目录下的 `external_executor/` 文件契约。

必需输入：

| 文件 | 需求 |
| --- | --- |
| `external_executor/handoff_pack.json` | 主实验契约，包含实验计划、metrics、baselines、allowed outputs 和 executor mode |
| `external_executor/executor_selection.json` | 必须记录 `selected_executor=codex_cli`、选择来源、选择时间、next state |
| `external_executor/input_manifest.json` | handoff 输入源、hash、required executor outputs 清单 |
| `external_executor/expected_outputs_schema.json` | Codex 必须写回的 result/status/manifest/artifact schema |
| `external_executor/allowed_paths.txt` | `rw` / `ro` / `no` 路径策略 |
| `external_executor/AGENTS.md` | Codex/generic coding agent 执行规则 |
| `external_executor/codex_prompt.md` | Codex 专用主 prompt |
| `external_executor/job_state.json` | executor 生命周期状态 scaffold |
| `external_executor/README.md` | 人类可读 workspace 协议说明 |

`codex_prompt.md` 是主 prompt。只有在它缺失且实现明确允许 fallback 时，才可以使用 `executor_prompt.md`。

### 3.2 Codex 必须写回的结果文件

Codex CLI 必须写回：

| 文件或目录 | 要求 |
| --- | --- |
| `external_executor/result_pack.json` | `semantics=external_executor_result_pack`，包含 metrics、artifacts、baseline coverage、run manifest 引用 |
| `external_executor/executor_status.json` | 声明执行完成状态；`accepted=false` |
| `external_executor/run_manifest.json` | 记录 run records、raw/config/log artifacts、sha256、provenance |
| `external_executor/raw_results/*` | 机器可读 raw evidence；metric 必须能指向 source artifact |
| `external_executor/configs/*` | 实验配置、seed、环境、依赖和可复现性信息 |
| `external_executor/logs/*` | Codex 和实验执行日志 |

只写自然语言总结不满足协议。自然语言说明可以作为日志或 notes 的补充，但不能是唯一证据源。

### 3.3 ResearchOS 必须写入的 invocation 文件

因为 `codex_cli` 现在固定为 ResearchOS 主动启动，所以以下文件是必需输出：

| 文件 | 要求 |
| --- | --- |
| `external_executor/executor_invocation.json` | 当前 invocation summary，长期可审计 |
| `external_executor/executor_invocation_log.jsonl` | append-only invocation events |
| `external_executor/logs/codex_cli_stdout.log` | Codex CLI stdout，或在 invocation summary 中记录等价路径 |
| `external_executor/logs/codex_cli_stderr.log` | Codex CLI stderr，或在 invocation summary 中记录等价路径 |
| `external_executor/heartbeat.json` | managed Codex invocation 的 heartbeat 和 liveness 信息 |

`executor_invocation_log.jsonl` 与现有 `executor_events.jsonl` 在 MVP 中保持分离。后续如事件模型稳定，再考虑统一。

### 3.4 Schema version and field contract

v0.2 调整：MVP 不实现完整 schema version management。当前阶段优先落地 artifact 字段契约和字段级 validator。

MVP 必须明确并校验以下内容：

- 每个 EEI artifact 的字段清单。
- required / optional 规则。
- 枚举值。
- path、sha256、source artifact、status、accepted、dry/mock flags 等关键语义。
- rejection error code 和 remediation hint。

MVP 可以在新写出的 ResearchOS artifacts 中写入固定占位字段：

```json
{
  "schema_version": "1.0.0"
}
```

但 MVP validator 不需要实现 schema registry、SemVer 兼容矩阵、migration、legacy workspace 自动识别、newer minor warning 或 unknown major 分派。`schema_version` 在 MVP 中作为信息字段和未来扩展钩子；是否 reject 应主要由字段契约、`semantics`、required fields、枚举、hash、allowed paths 和 evidence integrity 决定。

Codex 写回文件如果缺少 `schema_version`，MVP 可以 warning；如果字段结构不符合 artifact contract，则必须 reject。

## 4. Codex CLI 调用需求

### 4.1 Command/path 解析

Codex command 解析规则：

- 支持显式 config override。
- 默认查找 `codex`。
- 启动前必须检查 command 是否存在且可执行。
- command 缺失或不可执行时，写 recoverable invocation failure，不进入不可恢复失败。

`executor_invocation.json` 必须记录：

- resolved command path
- 脱敏后的 argv
- prompt injection mode
- cwd
- timeout
- policy snapshot
- log paths

### 4.2 工作目录

Codex CLI 的 cwd 必须是本次运行的 workspace 实例目录。

例如完整工程根目录下有 `workspace/`，本次运行初始化目录为 `workspace/test/`，则 Codex CLI cwd 应为：

```text
workspace/test/
```

原因：

- 本次运行的所有文件都在该目录下。
- `external_executor/` 也在该目录下。
- `external_executor/AGENTS.md`、`codex_prompt.md`、`handoff_pack.json`、`allowed_paths.txt` 都可用 workspace-relative path 访问。
- wait/audit/resume 都可以基于同一运行目录中的落盘 artifact 恢复上下文。

不建议 MVP 将 cwd 设为 `external_executor/workdir/`。后续如引入强 sandbox，可以再将 `external_executor/workdir/` 作为隔离执行目录，但必须保持 Codex 能稳定读取 `external_executor/` 契约文件。

### 4.3 Prompt 注入

v0.2 确认：prompt 注入方式在需求层保持配置化，具体实现层再决定。

候选方式：

- stdin
- command argument
- prompt file flag

无论采用哪种方式，必须记录到 `executor_invocation.json`，并在 `executor_invocation_log.jsonl` 中记录启动事件。

### 4.4 Auth 检查

认证检查采用 best-effort 策略：

- 可以在可用时执行 smoke command。
- 如果 auth 无法确认，必须给出清晰错误或提示用户手动登录。
- 不得记录 credential、token、cookie、API key。
- auth failure 是 recoverable invocation failure。

### 4.5 stdout/stderr 和日志

Codex stdout/stderr 必须被捕获。

推荐默认路径：

- `external_executor/logs/codex_cli_stdout.log`
- `external_executor/logs/codex_cli_stderr.log`

若实现选择等价路径，必须在 `executor_invocation.json` 中记录。

stdout/stderr 展示或摘要前必须脱敏。MVP 允许设置 `max_log_size`，超过上限时 truncate with notice，并记录原始 bytes 或截断状态。

## 5. Adapter 职责

建议抽象 `CodexCliExecutorAdapter`，但名称不要求成为最终 API。

职责：

- 读取并验证 `executor_selection.json` 中 `selected_executor=codex_cli`。
- 生成 invocation id。
- 解析 command path，支持 config override，默认查找 `codex`。
- 检查 command 可执行性。
- 执行 best-effort auth smoke check。
- 校验 `codex_prompt.md`、`handoff_pack.json`、`expected_outputs_schema.json`、`allowed_paths.txt` 等必需输入存在。
- 设置 cwd 为本次运行的 workspace 实例目录。
- 按配置注入 `codex_prompt.md`。
- 启动 Codex CLI。
- 捕获 stdout/stderr。
- 写 `executor_invocation.json`。
- 追加写 `executor_invocation_log.jsonl`。
- 写/更新 `heartbeat.json`。
- 记录 started_at、ended_at、exit_code、signal、timeout、pid。
- 记录脱敏后的 argv 和环境变量 allowlist。
- 记录 network、dependency installation、allowed paths enforcement、timeout、log redaction 等 policy snapshot。
- 进程结束后汇总 result/status/manifest/raw/config/log 是否存在。
- 将后续判断交给 `T5-EXTERNAL-WAIT`。

非职责：

- 不接受科学证据。
- 不把 executor summary 作为 evidence。
- 不生成 paper claims。
- 不替代 `T7-AUDIT`。
- 不绕过 `T5-EXTERNAL-WAIT`。

## 6. 配置需求

MVP 配置项：

| 配置项 | v0.2 决策 |
| --- | --- |
| `executor_mode` | `codex_cli` 由 gate 选择 |
| `codex_command` | 支持显式 override；默认 `codex` |
| `cwd` | 本次运行 workspace 实例目录，例如 `workspace/test/` |
| `prompt_file` | 默认 `external_executor/codex_prompt.md` |
| `prompt_injection_mode` | 配置化，候选为 stdin / argv / prompt-file |
| `timeout_seconds` | 默认 14400，可配置 |
| `heartbeat_interval_seconds` | 必需配置或使用默认值 |
| `heartbeat_stale_rule` | `last_heartbeat_at` 早于 `max(3 * heartbeat_interval, configured_minimum)` 视为 stale |
| `max_log_size` | 必需配置或使用默认值；超限 truncate with notice |
| `network_policy` | `require_confirmation` |
| `dependency_install_policy` | `require_confirmation` |
| `allowed_paths_enforcement` | MVP 为 `audit`，后续增强为 sandbox |
| `log_redaction_policy` | 必需，至少脱敏 tokens/secrets/credential-like values |
| `schema_version` | MVP 可写固定 `"1.0.0"` 作为未来扩展钩子；不实现完整版本管理 |

说明：`network_policy=require_confirmation` 与 `dependency_install_policy=require_confirmation` 是 policy 记录需求。具体实现可以通过项目配置、全局配置或 gate 配置确定这些策略；`T5-EXTERNAL-INVOKE` 按已记录策略启动 Codex CLI。

## 7. Timeout、Heartbeat 和进程状态

### 7.1 Timeout

v0.2 确认：managed Codex launch 默认 timeout 为 4 小时，即：

```text
timeout_seconds=14400
```

timeout 必须可配置。

发生 timeout 时：

- adapter 必须记录 timeout。
- 按文档化策略 terminate 或 detach Codex process。
- 写 invocation failure event。
- 不自动进入 T7。
- 如果已有 result/status/manifest，仍交给 `T5-EXTERNAL-WAIT` 判断是否完整有效。

### 7.2 Heartbeat

v0.2 确认：由于 `codex_cli` 必定由 ResearchOS 自动拉起，heartbeat 是必需项。

需求：

- `T5-EXTERNAL-INVOKE` 必须写 `external_executor/heartbeat.json`。
- heartbeat 至少包含 invocation id、executor、state、pid（如可用）、started_at、last_heartbeat_at、timeout_seconds。
- heartbeat interval 必须有默认值并可配置。
- stale 默认规则为 `last_heartbeat_at` 早于 `max(3 * heartbeat_interval, configured_minimum)`。
- stale 只表示执行状态可疑，不等于证据失败。
- 证据是否可进入 T7 仍由 `T5-EXTERNAL-WAIT` 和 T7 audit 判定。

建议 heartbeat state：

- `starting`
- `running`
- `stale`
- `exited`
- `timeout`
- `failed`
- `completed`

### 7.3 Exit code

非零 exit code 不直接等于 T7 evidence failure。

规则：

- 非零 exit code 必须写入 invocation summary。
- 若必需 result artifacts 缺失，wait 必须 reject/pause。
- 若 Codex 非零退出但 artifacts 完整，仍由 `T5-EXTERNAL-WAIT` 校验 shape、hash、路径和 status。
- T7 audit 不信任 Codex 自评。

## 8. MVP Resume 策略

完整 resume 的难度较高。v0.2 建议 MVP 采用“artifact-first、单次 invocation 可恢复、避免自动重拉起”的简化策略，以降低实现难度，同时保留后续扩展空间。

### 8.1 MVP Resume 目标

MVP 不要求恢复已经中断的 Codex 进程控制权，也不要求自动判断是否应重新启动 Codex。MVP 只要求基于已落盘 artifact 做确定性恢复。

MVP resume 需要支持：

- 识别是否已经发生过 Codex invocation。
- 识别 invocation 是 running/exited/timeout/failed/completed。
- 识别必需 result artifacts 是否已经存在。
- 对已存在 artifacts 重新运行 `T5-EXTERNAL-WAIT` validation。
- 对 artifacts 缺失或不完整的情况给出清晰暂停和修复提示。
- 保留足够 invocation metadata，便于未来实现完整 relaunch/attach/retry。

### 8.2 MVP Resume 行为

resume 时检查：

- `external_executor/executor_selection.json`
- `external_executor/executor_invocation.json`
- `external_executor/executor_invocation_log.jsonl`
- `external_executor/heartbeat.json`
- `external_executor/result_pack.json`
- `external_executor/executor_status.json`
- `external_executor/run_manifest.json`
- `external_executor/wait_acceptance_report.json`
- `external_executor/wait_rejection_report.md`

推荐状态表：

| 情况 | MVP 行为 |
| --- | --- |
| 没有 `executor_invocation.json`，但 selection 是 `codex_cli` | 进入 `T5-EXTERNAL-INVOKE`，启动一次 Codex |
| 有 invocation，状态为 `running`，heartbeat 未 stale | 暂停并提示 Codex 可能仍在运行；不重复启动 |
| 有 invocation，状态为 `running`，heartbeat stale | 暂停，提示检查外部进程或手动修复 artifacts；MVP 不自动重拉起 |
| invocation 为 `timeout` / `failed` / 非零退出，且 result artifacts 缺失 | 暂停，报告 invocation failure 和缺失文件；MVP 不自动重拉起 |
| invocation 为 `timeout` / `failed` / 非零退出，但 result artifacts 完整 | 进入 `T5-EXTERNAL-WAIT` 重新校验 |
| invocation completed/exited，result artifacts 缺失 | 进入 wait rejection/pause，列出缺失文件 |
| wait rejection 已存在 | 重新运行 wait validation；通过则写 acceptance |
| wait acceptance 已存在 | 可进入或继续 `T7-INGEST` |

### 8.3 为什么 MVP 不自动重拉起

不自动重拉起可以显著降低复杂度：

- 避免重复运行长实验。
- 避免覆盖已有 partial artifacts。
- 避免处理同一 workspace 多个 Codex 进程并发写文件。
- 避免在无法判断外部进程状态时误杀或误重启。

为了保留扩展能力，MVP 必须把以下信息落盘：

- invocation id
- command path and argv
- cwd
- prompt file and injection mode
- policy snapshot
- started_at / ended_at
- pid
- timeout
- exit code / signal
- heartbeat timestamps
- stdout/stderr log paths
- result presence summary

后续完整 resume 可以在这些字段基础上增加：

- explicit rerun command
- attach/detach process tracking
- invocation supersession
- retry policy
- archive previous artifacts before rerun
- multi-invocation history

### 8.4 Rerun 和旧 artifacts

v0.2 确认：用户重新选择 executor 或重新运行 Codex 时，旧 Codex artifacts 必须可审计地保留。

最建议方式：

- 将旧 artifacts archive 到带时间戳目录；或
- 写 supersession event，记录哪些文件被新 invocation 替代。

MVP 可以先实现 supersession event，不强制实现完整 archive；但不得无记录覆盖旧 invocation summary。

## 9. Wait 和 Validation 需求

`T5-EXTERNAL-WAIT` 是 Codex 真实结果进入 T7 前的必需边界。

进入 T7 前必须校验：

- `result_pack.json` 存在。
- `executor_status.json` 存在。
- `run_manifest.json` 存在。
- raw result、config、log artifact 存在。
- `executor_status.json.accepted=false`。
- status/current_state 是允许的完成状态。
- 默认不接受 `PARTIAL_RESULTS_READY`。
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
- 报告 error code、related file、expected value、observed value、remediation hint。

校验通过时：

- 写 `external_executor/wait_acceptance_report.json`。
- 进入 `T7-INGEST`。

## 10. allowed paths enforcement

v0.2 确认：MVP 至少实现 post-run audit，后续再引入 pre-run sandbox。

需求：

- Codex 必须只在 `allowed_paths.txt` 允许的路径内工作。
- MVP 若没有强 sandbox，CLI 必须明确提示当前 enforcement 是 audit-based。
- `T5-EXTERNAL-WAIT` 必须校验 result pack、run manifest 和被引用 artifact 的路径没有越界。
- 路径校验必须使用规范化后的 workspace-relative path。
- 必须拒绝 `..` 越界和 workspace 外 absolute path。
- `no` rule 覆盖 `rw` / `ro` rule。
- 越权路径必须阻断进入 T7。

MVP 不要求阻止 Codex 在运行中尝试越权，但必须能在 wait/audit 阶段发现协议中引用的越权 artifact。后续 sandbox 版本再扩展为执行前强隔离和 changed-file audit。

## 11. Partial Results

v0.2 确认：默认不允许 `PARTIAL_RESULTS_READY` 进入 T7。

规则：

- 默认允许完成状态为 `done` / `COMPLETED` 等明确完成状态。
- `PARTIAL_RESULTS_READY` 默认写 wait rejection 并暂停。
- 只有显式配置 `allow_partial_results=true` 时才允许进入 T7。
- 若允许 partial results，T7 claims 必须降级或 block 相关 claim。

MVP 建议不实现 `allow_partial_results=true` 的通过路径，只保留配置和错误信息扩展点。

## 12. 安全和隐私

安全需求：

- 不在 prompt 中嵌入 secrets，除非用户显式允许。
- 不在 invocation log 中记录 token、API key、cookie、credential path 等敏感信息。
- stdout/stderr 写入摘要或展示前必须脱敏。
- command argv 和 environment allowlist 必须脱敏记录。
- network access 和 dependency installation 必须作为 policy 记录。
- `network_policy=require_confirmation`。
- `dependency_install_policy=require_confirmation`。
- `allowed_paths_enforcement=audit`。
- 如果无法强 sandbox，必须提示当前为 audit-based enforcement。
- 越权路径、hash mismatch、缺失 source artifact 必须阻断进入 T7。

说明：`require_confirmation` 在这里表示策略必须通过项目配置、全局配置或 gate 配置形成可审计记录。

## 13. CLI/UX 需求

选择 `codex_cli` 后，CLI/UX 应展示：

- selected executor
- workspace instance path，例如 `workspace/test/`
- handoff directory
- `codex_prompt.md` path
- `allowed_paths.txt` path
- `expected_outputs_schema.json` path
- Codex command
- cwd
- prompt injection mode
- timeout
- heartbeat interval
- network/dependency policy
- allowed paths enforcement mode
- stdout/stderr log paths
- 当前 allowed paths enforcement mode

UX 必须明确区分：

- Codex process started
- Codex process exited
- Codex declared done
- ResearchOS wait accepted
- ResearchOS audit accepted evidence
- claim 是否可用

## 14. MVP 测试和验收标准

MVP 必测：

- `T5-EXECUTOR-GATE` 选择 `codex_cli` 后直接进入 `T5-EXTERNAL-INVOKE`。
- `T5-EXTERNAL-INVOKE` 会启动 Codex CLI 并写入 invocation artifacts。
- Codex command 缺失时，写 recoverable invocation failure。
- Codex command 使用显式 config override 时优先于默认 `codex`。
- cwd 是本次运行 workspace 实例目录，例如 `workspace/test/`。
- invocation 写入 `executor_invocation.json` 和 `executor_invocation_log.jsonl`。
- invocation 写入 `heartbeat.json`。
- stdout/stderr 被捕获到 logs 或等价路径。
- timeout 默认 14400 秒且可配置。
- auth smoke check failure 不泄露 credential。
- `network_policy` 和 `dependency_install_policy` 被记录。
- `allowed_paths_enforcement=audit` 被记录，并有 audit-based warning。
- 缺失 `result_pack.json` 时 wait reject/pause。
- `executor_status.json.accepted=true` 时 wait reject。
- 缺失 raw/config/log artifact 时 wait reject。
- hash mismatch 时 wait reject。
- artifact 路径越出 `allowed_paths.txt` 时 wait reject。
- 只有自然语言总结时 wait reject。
- `PARTIAL_RESULTS_READY` 默认不通过。
- 有效 result/status/manifest/raw/config/log 写回后进入 `T7-INGEST`。
- resume 遇到已有 running invocation 时不重复启动 Codex。
- resume 遇到 invocation failed 但 artifacts 完整时进入 wait validation。
- rerun 或重新选择 executor 时不无记录覆盖旧 invocation。

Future tests：

- disposable workspace 中的真实 Codex CLI smoke test。
- process timeout terminate/detach test。
- heartbeat stale test。
- sandbox escape attempt test。
- log redaction test。
- dependency install policy enforcement test。
- network policy enforcement test。
- large raw/log artifact truncate/archive/hash test。
- multi-invocation archive/supersession test。

## 15. 建议开发拆分

### 第一阶段：状态和契约

- 修改 `codex_cli` gate 路由：选择后进入 `T5-EXTERNAL-INVOKE`。
- 定义 `T5-EXTERNAL-INVOKE` 对 Codex CLI 的托管启动语义。
- 定义 `executor_invocation.json` schema。
- 定义 `executor_invocation_log.jsonl` event schema。
- 定义 `heartbeat.json` managed Codex 字段。
- 定义 result/status/manifest/invocation 的字段契约、required/optional 和枚举。
- 保留固定 `schema_version="1.0.0"` 信息字段，但不实现版本分派、migration 或兼容矩阵。

### 第二阶段：Codex adapter MVP

- 实现 `CodexCliExecutorAdapter`。
- 实现 command/path resolution。
- 设置 cwd 为 workspace 实例目录。
- 实现 prompt injection 配置入口。
- 实现 best-effort auth smoke check。
- 启动 Codex CLI。
- 捕获 stdout/stderr。
- 写 invocation summary、event log、heartbeat。
- 实现 timeout。
- 实现日志脱敏和 max log size。

### 第三阶段：wait validation 强化

- 强化 required result/status/manifest/raw/config/log presence validation。
- 强化 `accepted=false` validation。
- 强化 metric source artifact validation。
- 强化 sha256 validation。
- 强化 allowed paths audit。
- 默认 reject `PARTIAL_RESULTS_READY`。
- 完善 wait rejection report。

### 第四阶段：MVP resume

- 实现 artifact-first resume decision table。
- resume 不自动重复启动已有 invocation。
- invocation failed 但 artifacts 完整时进入 wait validation。
- invocation 缺失且 selection 为 `codex_cli` 时进入 invoke。
- 记录 supersession event，避免无记录覆盖。

### 第五阶段：后续增强

- 强 sandbox / pre-run enforcement。
- 完整 rerun/archive。
- attach/detach process tracking。
- network/dependency policy enforcement。
- large artifact archive。
- 真实 Codex CLI integration test。

## 16. Traceability

| 需求 | 来源 / 确认 |
| --- | --- |
| 选择 `codex_cli` 后自动启动 Codex CLI | v0.1 “15. 需求确认” C-01，用户补充确认 |
| 新增 `T5-EXTERNAL-INVOKE` | C-01 / C-02，用户补充确认 |
| command 支持 config override，默认 `codex` | C-03 |
| prompt 注入方式配置化，保留三种候选 | C-04，用户补充确认 |
| auth best-effort，不记录 credential | C-05 |
| timeout 默认 4 小时，可配置 | C-06，用户补充确认 |
| heartbeat 是必须项 | 用户补充确认 8.2 |
| cwd 是本次运行 workspace 实例目录 | 用户补充确认 6.1 |
| allowed paths MVP post-run audit | 用户补充确认 2.3 / C-10 |
| partial results 默认不进入 T7 | 用户补充确认 2.4 |
| invocation log 长期可审计，MVP 与 executor_events 分离 | 用户补充确认 2.5 / C-13 |
| large raw/log MVP 记录 sha256/bytes，stdout/stderr 可截断并声明 | C-11 |
| 重新选择 executor 或 rerun 时保留旧 artifacts 或 supersession event | C-12 |
| MVP 暂不实现完整 schema version management，优先实现 artifact 字段契约和字段级 validator | Prompt7 需求调整 |
