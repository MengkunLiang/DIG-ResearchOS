# 外部执行器调用需求提案

## 1. 目的

本文档用于定义在 ResearchOS T5-T7 实验链路中补充真实外部执行器调用层的需求。

**已知需求：** ResearchOS 已经把新的实验链路定义为 artifact-first 的 handoff、wait、ingest、audit、post-novelty 和 claims 工作流。

**推导需求：** 为了安全调用真实外部执行器，ResearchOS 需要明确 `codex_cli` 的调用需求，明确 `claude_code_window` 的 handoff 行为，并定义一个不会削弱现有 `external_executor/` artifact 契约的小型 adapter 边界。

本文档是设计前需求说明。它不规定实现代码，不把文中模块名视为最终 API，也不展开进程运行器的实现细节。

## 2. 来自现有总览的背景

**已知需求：** `Experiment_dev_overview.md`、`agent_pipeline.md`、`experiment_module_redesign.md` 和 `external_executor_protocol.md` 已将 T5-T7 主链定义为：

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

**已知需求：** ResearchOS 不再默认在 T5-T7 内部实现并长时间运行实验。ResearchOS 负责编译协议、记录执行器选择、等待外部 artifact、摄取结果、审计 provenance 与 integrity、执行实验后 novelty 检查，并把审计后的结果映射为 claims。

**已知需求：** 主要接口是 `external_executor/` 下的 workspace artifact 契约，而不是网络 API。

**已知需求：** `mock_dry_run` 只用于协议联调，并且输出 MUST 标记 `dry_run=true` 和 `mock_only=true`。

**已知需求：** `codex_cli`、`claude_code_window` 和 `manual` 是真实或人工控制的执行路径，并进入 `T5-EXTERNAL-WAIT`。

**已知需求：** ResearchOS MUST NOT 接受只有自然语言总结的内容作为实验证据。ResearchOS 只消费结构化 result/status/manifest artifact、raw results、configs、logs、hash 和 metric source 链接。

**已知需求：** 外部执行器可以声明完成，但不能接受证据。`executor_status.json.accepted` MUST 保持 `false`；证据是否可用由 ResearchOS ingest/audit/claim 阶段决定。

**待决策：** 现有文档说明 `codex_cli` 是推荐的正式真实执行器，但尚未定义 ResearchOS 是否主动启动 Codex 进程，还是只准备 artifact 和说明。

## 3. 范围

### 范围内

- **推导需求：** `codex_cli` 调用需求。
- **已知需求 / 推导需求：** `claude_code_window` handoff 需求，并将任何托管 CLI adapter 明确标注为 optional/future。
- **推导需求：** executor adapter layer 职责。
- **推导需求：** 进程启动、人工启动和 wait 边界需求。
- **已知需求：** `T5-EXECUTOR-GATE` 执行器选择，以及进入 `T5-DRY-RUN` 或 `T5-EXTERNAL-WAIT` 的衔接。
- **已知需求：** `external_executor/` 下输入/输出 artifact 闭环行为。
- **推导需求：** 状态、调用日志、heartbeat、timeout、失败恢复和 resume 行为。
- **已知需求 / 推导需求：** `allowed_paths.txt` enforcement 与 validation。
- **推导需求：** 安全、隐私和权限边界。
- **推导需求：** 最小测试和验收标准。

### 范围外

- **已知需求：** 不重新设计完整的 T7 ingest、audit、post-novelty 或 claim 逻辑。
- 不实现真实科学实验算法。
- 不把 dry-run 输出当作论文证据。
- 不在本文档中编写实现代码。
- 不替代后续详细技术设计文档。
- 除非后续设计明确改变 artifact-first 协议，否则不把网络 API 执行作为主契约。

## 4. 当前已知 Artifact 契约

### 4.1 T5-HANDOFF 生成的文件

| 文件 | 作用 | 消费者 | 是否必须 | 待确认字段 / 决策 |
| --- | --- | --- | --- | --- |
| `external_executor/handoff_pack.json` | 主实验契约，包含输入、metrics、baselines、允许输出和 semantics | 外部执行器、`T5-EXTERNAL-WAIT`、`T7-INGEST`、audit context | 是 | 稳定 schema / version 兼容策略 |
| `external_executor/executor_selection.json` | gate 前占位，gate 后记录选中执行器和 next state | runtime、executor、`T5-EXTERNAL-WAIT` | 是 | invocation config 放在此文件还是单独文件 |
| `external_executor/input_manifest.json` | handoff 输入源 artifact 清单 | 外部执行器、audit | 是 | hash 和路径规范化规则 |
| `external_executor/expected_outputs_schema.json` | 必需输出 schema / shape | 外部执行器、wait validator | 是 | 完整 JSON schema 和版本策略 |
| `external_executor/allowed_paths.txt` | `rw` / `ro` / `no` 路径策略 | 外部执行器、validators | 是 | 执行前强隔离还是执行后审计 |
| `external_executor/AGENTS.md` | agent 风格执行指南 | Codex / agent executors | 是 | 如果进程 cwd 变化，文件应放置在哪里 |
| `external_executor/CLAUDE.md` | Claude Code 执行指南 | Claude Code window / adapter | 是 | window-only 还是 managed CLI 行为 |
| `external_executor/README.md` | 外部 workspace 人类可读说明 | 用户和 manual executors | 是 | 无主要待决策项 |
| `external_executor/job_state.json` | job state scaffold 和 allowed states | executor、monitor、wait validator | 是 | invocation 状态更新此文件还是新文件 |
| `external_executor/executor_prompt.md` | 通用 executor prompt | 任意 executor | 是 | specialized prompt 是否总是覆盖它 |
| `external_executor/codex_prompt.md` | Codex 专用 prompt | Codex CLI 或手动 Codex session | 是 | prompt 注入方式 |
| `external_executor/claude_code_prompt.md` | Claude Code prompt | 用户 / Claude Code | 是 | 未来 CLI adapter 是否直接消费 |
| `external_executor/manual_instructions.md` | manual executor 指令 | 人工或第三方 executor | 是 | 最小 manual 完成清单 |
| `external_executor/_DIR_GUIDE.md` | 目录协议说明 | 用户和工具 | 当前代码声明 | 是否纳入正式协议 |
| `external_executor/executor_events.jsonl` | 初始事件日志 scaffold | 用户、monitor | 当前代码可能创建 | 与未来 invocation log 的关系 |

### 4.2 外部执行器写回的文件

| 文件 | 生产者 | 作用 | ResearchOS 如何使用 | 缺失 / 无效处理 |
| --- | --- | --- | --- | --- |
| `external_executor/result_pack.json` | 外部执行器 | 主结构化结果包 | wait 校验；T7 摄取 metrics 和 artifact 引用 | MUST 在 T7 前暂停 / reject |
| `external_executor/executor_status.json` | 外部执行器 | 完成 / 状态声明 | wait 检查 semantics、state、`accepted=false`、dry/mock flags | 缺失、无效或自我接受时 MUST reject |
| `external_executor/run_manifest.json` | 外部执行器 | run records、raw/config/log 路径、provenance | wait 和 audit 校验 artifact 与 hash | 真实运行必需且缺失 / 无效时 MUST reject |
| `external_executor/raw_results/*` | 外部执行器 | 机器可读 raw evidence | metrics MUST 指向这里或其他允许 raw path 的 source artifact | 缺失时 reject 或 downgrade |
| `external_executor/configs/*` | 外部执行器 | 实验配置和可复现性信息 | ingest/audit 将 configs 关联到 runs | reject 或报告 provenance 不完整 |
| `external_executor/logs/*` | 外部执行器 | 执行日志 | audit/debug evidence | reject 或报告 provenance 不完整 |
| `external_executor/heartbeat.json` | 当前 mock tool 写入；真实 executor 可写入 | liveness signal | 未来 monitor 可检测 stale runs | 真实 adapter 待决策 |
| `external_executor/patches/*` | 如果追踪代码修改，由外部执行器写入 | patch / provenance logs | T7-POST-NOVELTY 可能消费 `patch_log.jsonl` | required vs optional 待决策 |
| `external_executor/repo_snapshot.json` | 外部执行器或未来 adapter | 外部代码状态快照 | T7-POST-NOVELTY 可能消费 | 待决策 |

### 4.3 T5-EXTERNAL-WAIT / T7-INGEST / T7-AUDIT 消费的文件

**已知需求：** `T5-EXTERNAL-WAIT` 消费 `executor_selection.json`、`handoff_pack.json`、`expected_outputs_schema.json`、`allowed_paths.txt`、`result_pack.json` 和 `executor_status.json`。成功时写入 `external_executor/wait_acceptance_report.json`；失败或当前未就绪时写入 `external_executor/wait_rejection_report.md`。

**已知需求：** `T7-INGEST` 消费外部 result pack/status/handoff，并生成 `experiments/results_summary.json`、`experiments/run_records.jsonl`、`experiments/evidence_index.json` 和 `experiments/ingest_report.json`。

**已知需求：** `T7-AUDIT` 消费已 ingest 的 results/evidence，并校验 provenance、metric source artifacts、hash、mock flags 和 baseline coverage。它不信任 executor 自评。

## 5. Executor Modes

### 5.1 `codex_cli`

**已知需求：** `codex_cli` 被列为推荐的正式真实执行器路径，在当前 CLI gate 中选择它需要明确的人类二次确认。如果确认不是 `yes`，当前行为会降级到 `claude_code_window`。

**待决策：** ResearchOS 尚未定义是否 MUST 主动启动 Codex CLI。本文档建议 MVP 采用可选 adapter：当 `auto_launch=true` 且已确认时，ResearchOS MAY 启动 Codex CLI；否则 ResearchOS MUST 生成 handoff 指令并进入 `T5-EXTERNAL-WAIT`。

需求：

- **推导需求：** adapter MUST 是可选的，并且除非已配置或已确认，否则保持禁用。仅选择 `codex_cli` MUST NOT 静默启动长时间真实实验。
- **推导需求：** 如果 Codex 需要读取 `AGENTS.md` 约定和相对 artifact 路径，进程工作目录 SHOULD 是 workspace 根目录；如果存在强 sandbox，则 MAY 使用 `external_executor/workdir/`。MVP MUST 在 invocation artifact 中明确记录选定 cwd。
- **推导需求：** 如果 cwd 是 `external_executor/workdir/`，adapter MUST 确保 executor 仍能通过稳定的相对或绝对路径读取 `external_executor/codex_prompt.md`、`handoff_pack.json`、`expected_outputs_schema.json` 和 `allowed_paths.txt`。
- **推导需求：** Codex CLI 的主 prompt SHOULD 使用 `external_executor/codex_prompt.md`。只有在专用文件缺失或被明确禁用时，`external_executor/executor_prompt.md` MAY 作为通用 fallback。
- **推导需求：** `AGENTS.md` SHOULD 出现在 Codex 的有效上下文中。如果 cwd 不是 `external_executor/`，adapter MUST 记录 Codex 如何接收这些指令。
- **待决策：** 精确 prompt 注入方式待定。候选方式包括 stdin、命令参数，或已安装 CLI 支持的 prompt file flag。选定方式 MUST 被记录。
- **推导需求：** adapter MUST 将 stdout 和 stderr 捕获到 `external_executor/logs/codex_cli_stdout.log` 和 `external_executor/logs/codex_cli_stderr.log`，或捕获到 invocation log 中记录的等价路径。
- **推导需求：** adapter MUST 写入启动命令、脱敏后的 argv、cwd、环境变量 allowlist、开始 / 结束时间、exit code、timeout 状态，以及可用时的 process id。
- **推导需求：** 启动前，ResearchOS MUST 检测 Codex CLI command path 是否存在且可执行。
- **待决策：** 认证 / 登录状态检测待定。MVP MAY 只在可用时执行 smoke command，但如果无法确认 auth，MUST 给出清晰错误。
- **推导需求：** 非零 exit code MUST NOT 自动导致 T7 evidence 失败。它 MUST 被记录为 invocation failure，随后由 `T5-EXTERNAL-WAIT` 决定必需 artifacts 是否存在且有效。
- **已知需求 / 推导需求：** 如果 Codex 退出但未写入必需文件，ResearchOS MUST 停留或返回 `T5-EXTERNAL-WAIT`，并生成 rejection / not-ready report。
- **待决策：** network access、dependency installation、test execution 和 dataset download 策略尚未确定。adapter MUST 将这些作为显式配置项，并在 invocation log 中记录有效策略。
- **推导需求：** adapter MUST 在启动前校验 `allowed_paths.txt`，并在启动后审计引用输出路径。如果未实现强执行前 enforcement，这一限制 MUST 对用户可见。
- **推导需求：** adapter SHOULD 支持 preview / no-op mode，用于打印 command、cwd、prompt file、allowed paths、timeout 和 log paths，而不启动进程。
- **推导需求：** 任何真实 `codex_cli` invocation 默认 SHOULD 要求用户确认。

### 5.2 `claude_code_window`

**已知需求：** 现有协议将 `claude_code_window` 描述为适合把 `external_executor/claude_code_prompt.md` 复制到 Claude Code 窗口执行。ResearchOS 等待文件写回。

#### Human-in-the-loop window mode

**已知需求：** 在此模式下，ResearchOS 不控制 Claude Code 进程。它写入 handoff 文件，记录 `selected_executor=claude_code_window`，告知用户应复制哪个 prompt，然后进入 `T5-EXTERNAL-WAIT`。

需求：

- **已知需求：** 用户或外部 Claude Code session MUST 写入 `result_pack.json`、`executor_status.json`、`run_manifest.json`、raw results、configs 和 logs。
- **已知需求：** ResearchOS MUST 在进入 T7 前校验这些 artifacts。
- **推导需求：** CLI/UX SHOULD 展示 prompt path、workspace path、allowed paths file 和 resume command。
- **推导需求：** 在此模式下，ResearchOS SHOULD NOT 声称自己启动或监控了 Claude Code。

#### Managed Claude Code CLI adapter

**待决策：** Managed Claude Code CLI adapter 是 optional/future，当前文档并未将其确立为事实。

如果后续批准，它 MUST 满足与 `CodexCliExecutorAdapter` 类似的需求：

- 安装和可执行检测；
- 认证检测；
- 明确 cwd；
- 从 `claude_code_prompt.md` 注入 prompt；
- stdout/stderr 捕获；
- timeout 和 heartbeat 行为；
- exit code 记录；
- `allowed_paths.txt` enforcement 或 post-run audit；
- 必需 artifact 校验；
- 明确区分 executor completion 和 ResearchOS evidence acceptance。

## 6. 建议的 Executor Adapter Layer

本节命名需求级模块。名称仅用于说明，不是最终实现承诺。

| 模块 | 职责 | 输入 | 输出 | 失败模式 |
| --- | --- | --- | --- | --- |
| `ExecutorSelectionResolver` | 从 gate 输出解析 selected executor、mode、confirmation 和 next state | `executor_selection.json`、CLI config | resolved execution plan | selection 缺失 / 无效、真实运行未确认 |
| `ExternalExecutorAdapter` | 外部执行器模式的通用接口 | resolved execution plan、workspace path | invocation result 或 wait instructions | executor 不支持、config 无效 |
| `CodexCliExecutorAdapter` | 可选真实 Codex 进程 adapter | `codex_prompt.md`、cwd、command path、allowed paths、policy | invocation artifacts 和 process logs | 未安装、auth failure、timeout、非零 exit、outputs 缺失 |
| `ClaudeCodeWindowAdapter` | human-in-the-loop Claude handoff | `claude_code_prompt.md`、workspace path | 用户指令和 wait transition | prompt 缺失、非交互 UX 限制 |
| `ManualExecutorAdapter` | manual / third-party handoff | `manual_instructions.md`、contract files | 用户指令和 wait transition | instructions 缺失 |
| `ExternalProcessRunner` | 通用 process launch 和 monitoring | command、cwd、env allowlist、timeout | stdout/stderr logs、exit code、timing | spawn failure、timeout、crash |
| `ExecutorStatusMonitor` | 读取 heartbeat/status 并判断 stale/running/not-ready | `job_state.json`、`heartbeat.json`、`executor_status.json`、timestamps | monitor status | stale heartbeat、status malformed |
| `AllowedPathsValidator` | 执行前后校验 path policy | `allowed_paths.txt`、referenced artifacts、可选 changed-file list | pass/fail 和 violations | policy 缺失、path traversal、disallowed write |
| `ExternalResultPresenceValidator` | T7 前校验必需 outputs | result/status/manifest/raw/config/log paths | acceptance/rejection report | files 缺失、semantics 无效、hash mismatch |
| `ExecutorInvocationLogWriter` | 写入持久 invocation audit trail | resolved config、process events、redaction policy | `executor_invocation.json`、`executor_invocation_log.jsonl` | log write failure、max-size overflow |

**推导需求：** Adapter modules MUST NOT 接受科学证据。它们只能启动、移交、监控和校验存在性 / 形状。证据接受仍由下游 ResearchOS 负责。

## 7. State Machine 集成需求

候选流程：

```text
T5-HANDOFF
 -> T5-EXECUTOR-GATE
 -> T5-EXTERNAL-INVOKE?    // optional new node
 -> T5-EXTERNAL-WAIT
 -> T7-INGEST
 -> T7-AUDIT
 -> T7-POST-NOVELTY
 -> T7-CLAIMS
```

**待决策：** 是否新增 `T5-EXTERNAL-INVOKE`。

建议：

- **推导需求：** 如果 ResearchOS 主动启动任何外部进程，则应新增 `T5-EXTERNAL-INVOKE`。这样可以把 gate selection、process invocation 和 result waiting 分离，并保持可审计。
- **推导需求：** 如果不新增节点，invocation logic SHOULD 在 `T5-EXECUTOR-GATE` 之后、`T5-EXTERNAL-WAIT` 之前运行，但仍 MUST 写 invocation artifacts，以便 resume 重建已发生的动作。
- **已知需求：** `T5-EXTERNAL-WAIT` 仍是所有真实 executors 进入 T7 前的必需边界。
- **推导需求：** 非交互环境 MUST 能在 gate 或 invoke failure 后暂停，并从 artifacts resume，而不是依赖内存。
- **推导需求：** 如果用户重新选择 executor，ResearchOS SHOULD 在 patch `executor_selection.json` 前，将旧 artifacts 保存在带时间戳的 archive 中，或记录 supersession event。
- **推导需求：** invocation failure SHOULD 进入 recoverable pause，除非它破坏了必需 handoff files。
- **推导需求：** invocation 成功但结果缺失时 MUST 仍由 `T5-EXTERNAL-WAIT` 处理，而不是直接让 T7 失败。

## 8. 配置需求

| 配置项 | MVP | 推荐 | Optional/future |
| --- | --- | --- | --- |
| executor mode | 必需：`mock_dry_run`、`codex_cli`、`claude_code_window`、`manual` | 同 MVP | 其他 executors |
| command path | auto-launched `codex_cli` 必需 | 允许显式 path override | 通过 tool registry discovery |
| working directory | launch 必需 | 记录到 invocation artifact | per-experiment workdir templates |
| prompt file | 必需 | 每个 executor 默认 specialized prompt | prompt composition strategy |
| environment variables allowlist | launch 时必需 | redact secrets | per-executor env profiles |
| timeout seconds | launch 时必需 | 按 executor 可配置 | adaptive timeout |
| heartbeat interval | MVP 中如存在 process monitor 则待定 | long runs 必需 | executor-written heartbeat protocol |
| max log size | 捕获 logs 时必需 | rotate/truncate with notice | log archive compression |
| network policy | 必需，作为显式记录值 | 默认 deny 或 require confirmation | sandbox-enforced network controls |
| dependency install policy | 必需，作为显式记录值 | require confirmation | package cache policy |
| allowed paths enforcement mode | 必需：至少 audit | strong pre-run isolation | container/sandbox backend |
| auto-launch enabled | 必需 | 默认 disabled | per-workspace default |
| require user confirmation | 必需 | 真实 runs 默认 true | policy-based skip in CI |
| dry-run/no-op mode | preview 必需 | 用于 tests | full command planner |
| log redaction policy | 必需 | redact env、tokens、absolute private paths | pluggable redactors |

## 9. 安全和权限需求

- **已知需求：** 外部 executors MUST 只在 `allowed_paths.txt` 内工作。
- **推导需求：** ResearchOS MUST 在 validation 前规范化路径：移除开头的 `./`，解析 `..`，拒绝 workspace 外部 absolute paths，并与 canonical workspace-relative paths 比较。
- **推导需求：** 当规则重叠时，`no` rules MUST 覆盖 `rw`/`ro`，或者必须明确记录并测试确切优先级。
- **已知需求：** 未授权修改 ResearchOS repo paths、`drafts/`、`submission/`、`_runtime/` 或其他 denied paths 是不可接受的。
- **待决策：** allowed paths 是在执行前通过 sandbox enforcement，还是在执行后 audit，或两者都有。
- **推导需求：** 如果无法强隔离，ResearchOS MUST 提供 post-run validator 和 rejection report，用于识别 disallowed paths。
- **待决策：** 外部 executors 是否可以读取 seed PDFs、API keys、环境变量、私有 notes 或用户材料。默认应采用最小权限。
- **推导需求：** invocation logs MUST 尽可能从环境变量、命令参数、prompts 和 stderr/stdout 中脱敏 secrets。
- **推导需求：** prompt generation SHOULD 避免嵌入 secrets 或 private materials，除非明确允许。
- **待决策：** network access 和 dependency installation policies 必须在真实 invocation 前确定。MVP SHOULD 对两者都要求显式用户确认。
- **待决策：** `codex_cli` MVP 是否必须 sandboxing。如果不是必须，CLI MUST 警告 enforcement 是 audit-based。

## 10. 状态、Heartbeat、Timeout 和恢复

**已知需求：** `job_state.json` 已存在，并包含 allowed state scaffold。当前 mock path 也写入 `heartbeat.json`，wait 可以写 acceptance/rejection reports。

**推导需求：** 新增 `external_executor/executor_invocation.json`，用于记录当前一次 invocation summary：

- invocation id；
- executor mode；
- selected executor；
- auto-launch true/false；
- 脱敏后的 command path 和 argv；
- cwd；
- prompt file；
- policy snapshot；
- started_at / ended_at；
- exit_code / signal / timeout；
- log paths；
- result presence summary。

**推导需求：** 新增 `external_executor/executor_invocation_log.jsonl`，用于 append-only events。除非有意合并，否则它 SHOULD 与现有 `executor_events.jsonl` 区分开。

**待决策：** heartbeat stale 规则。MVP SHOULD 定义默认规则，例如当 running states 的 `last_heartbeat_at` 早于 `max(3 * heartbeat_interval, configured_minimum)` 时视为 stale。

**推导需求：** timeout MUST 按文档化策略 terminate 或 detach managed process，并写入 recoverable failure record。

**推导需求：** Resume MUST 检查 invocation summary、result/status/manifest 是否存在，以及 wait acceptance/rejection state，以决定 relaunch、wait 或询问用户。

**推导需求：** result pack 缺失时 MUST 给出清晰信息，列明 required files 和 selected executor。

**推导需求：** process crash MUST 记录 exit code/signal、经脱敏的最后日志片段，以及是否存在 partial artifacts。

**待决策：** partial results policy 尚未解决。如果接受 `PARTIAL_RESULTS_READY`，T7 行为 MUST 明确 downgrade 或限制 claims。

**推导需求：** rejection reports SHOULD 包含：error code、severity、related file、expected value、observed value、remediation hint、是否 recoverable、受影响 downstream node。

## 11. Validation 需求

进入 T7 前，ResearchOS MUST 校验：

- **已知需求：** 必需文件存在：result pack、executor status、run manifest、raw result(s)、config(s) 和 log(s)。
- **已知需求：** `executor_status.json.accepted` MUST 是 `false`；`accepted=true` MUST 作为协议违规被 reject。
- **已知需求：** `current_state` 或 `status` MUST 是可接受状态，目前文档 / 实现围绕 `done`、`COMPLETED` 或 `PARTIAL_RESULTS_READY`。
- **已知需求：** `result_pack.json` MUST 具有 `semantics=external_executor_result_pack`。
- **已知需求：** 每个 metric MUST 有 `source_artifact`。
- **已知需求：** 被引用的 raw/config/log artifacts MUST 存在，并且被 `allowed_paths.txt` 允许。
- **已知需求：** Artifact hashes MUST 与磁盘文件匹配。
- **已知需求：** 路径 MUST NOT 违反 `allowed_paths.txt`。
- **已知需求：** Mock/dry-run markers MUST NOT 被误用。Mock-only results MUST NOT 成为 claim evidence。
- **已知需求：** Baseline coverage MUST 被表示并被 audit。
- **推导需求：** validation failure MUST 阻断 T7，或者只有在有文档化策略允许时，才可带明确 downgrade 进入 T7。
- **推导需求：** Natural-language summaries MAY 出现在 logs 或 notes 中，但 MUST NOT 是唯一 result source。

## 12. UX / CLI 需求

**已知需求：** 当前 gate 允许用户选择 `mock_dry_run`、`claude_code_window`、`codex_cli` 或 `manual`；`codex_cli` 需要二次确认。

CLI/UX 需求：

- **推导需求：** ResearchOS SHOULD 展示 selected executor、handoff directory、prompt path、allowed paths path 和 next resume command。
- **推导需求：** 对于启用 auto-launch 的 `codex_cli`，ResearchOS MUST 在确认前展示 command、cwd、prompt file、timeout、network/dependency policy 和 log paths。
- **推导需求：** 对于禁用 auto-launch 的 `codex_cli`，ResearchOS MUST 写入 instructions 并进入 wait，不得声称自己启动了进程。
- **推导需求：** 对于 `claude_code_window`，ResearchOS MUST 展示 `external_executor/claude_code_prompt.md`，并提示用户在 required outputs 存在后 resume。
- **推导需求：** CLI MAY 提供类似 `researchos executor status`、`researchos executor invoke`、`researchos executor preview`、`researchos executor rerun` 或 `researchos executor reject-report` 的命令，但本文档不假设这些命令已经存在。
- **推导需求：** UX MUST 区分 executor `done` 和 ResearchOS evidence accepted。
- **推导需求：** UX MUST 说明 dry-run/mock-only outputs 不能支持 paper claims。

## 13. 测试和验收标准

### MVP tests

- **已知需求：** Mock dry-run 仍能通过现有协议链。
- **推导需求：** 选择 `codex_cli` 但 command 未安装时，给出清晰、可恢复的错误。
- **推导需求：** 选择 `codex_cli` 且 `auto_launch=false` 时，生成 instructions 并进入 wait，不启动进程。
- **已知需求 / 推导需求：** 选择 `claude_code_window` 时，生成复制 prompt 的 instructions 并进入 wait。
- **已知需求：** 缺失 `result_pack.json` 时，在 T7 前 reject / pause。
- **已知需求：** `executor_status.json.accepted=true` 时 reject。
- **已知需求：** 缺失 raw/config/log artifacts 时 reject，或生成文档化 blocking rejection。
- **已知需求：** Hash mismatch 时 reject。
- **已知需求：** 路径位于 `allowed_paths.txt` 外时 reject。
- **已知需求：** 成功写回有效 artifacts 后进入 `T7-INGEST`。
- **待决策：** Partial results 行为 MUST 记录为 pending，或根据文档化 downgrade/block 规则测试。

### Future integration tests

- disposable workspace 中的真实 Codex CLI smoke test。
- 如果获批，执行 managed Claude Code CLI adapter smoke test。
- long-running heartbeat / stale heartbeat test。
- timeout and resume test。
- large raw result handling 和 log rotation test。
- sandbox escape attempt test。
- log redaction test。
- dependency install policy test。
- network disabled/enabled policy test。

## 14. 推荐 MVP

保守 MVP SHOULD：

1. 保持 artifact-first 协议不变。
2. 增加可配置的 `codex_cli` invocation adapter，但默认要求用户确认且 `auto_launch=false`。
3. 将 `claude_code_window` 作为 human-in-the-loop window mode 处理。
4. 所有真实 executor 路径都经过 `T5-EXTERNAL-WAIT`。
5. 对任何 managed launch attempt 强制写 invocation log。
6. 在 T7 前强制校验 `allowed_paths.txt` 和 required result artifacts。
7. Reject 只有自然语言总结的外部实验结果。
8. 保持 `executor_status.json.accepted` 必须为 `false` 的规则。
9. 保持 mock/dry-run output 不能支持 paper claims 的规则。

## 15. 待确认问题

按优先级排序：

1. ResearchOS 是否应新增独立状态 `T5-EXTERNAL-INVOKE`？
2. ResearchOS 是否默认主动启动 Codex CLI，还是默认 handoff/wait、可选 launch？
3. Claude Code 在 MVP 中是否 window-only，还是现在就规划 managed CLI adapter？
4. allowed paths 是通过 pre-run sandboxing、post-run audit，还是两者共同 enforcement？
5. 真实 executors 是否允许 network access 和 dependency installation？
6. 如何检查 Codex/Claude authentication status 且不泄露 credentials？
7. 真实 executor runs 的默认 timeout 是多少？
8. 应使用什么 heartbeat interval 和 stale rule？
9. `PARTIAL_RESULTS_READY` 是否可以进入 T7？对应 claim downgrade/block 规则是什么？
10. audit failure 后 workflow 是暂停、返回 T5、路由到 T7.5/human gate，还是继续但 block claims？
11. result/status/schema/handoff files 使用什么 schema version strategy？
12. invocation log 是否是长期可审计 artifact？
13. large raw files 和 large logs 如何 archive、truncate 或 hash？
14. 用户重新选择 executors 时，旧 executor artifacts 如何处理？
15. 是否统一 `executor_events.jsonl` 和未来的 `executor_invocation_log.jsonl`？

## 16. Traceability Matrix

| Requirement ID | Requirement | Source from current overview | Related artifact | Related state/node | Priority | Open question / resolved |
| --- | --- | --- | --- | --- | --- | --- |
| EEI-001 | ResearchOS 使用 artifact-first 外部 executor 协议，而不是 network API | overview/protocol 已知 | `external_executor/` | T5-T7 | High | Resolved |
| EEI-002 | 真实路径在 T7 前进入 `T5-EXTERNAL-WAIT` | overview 已知 | `executor_selection.json` | `T5-EXECUTOR-GATE`, `T5-EXTERNAL-WAIT` | High | Resolved |
| EEI-003 | `mock_dry_run` 仅用于协议验证，不能作为 paper evidence | overview/redesign 已知 | `result_pack.json` | `T5-DRY-RUN`, T7 | High | Resolved |
| EEI-004 | `executor_status.json.accepted` 保持 `false` | overview/protocol 已知 | `executor_status.json` | `T5-EXTERNAL-WAIT`, `T7-AUDIT` | High | Resolved |
| EEI-005 | Reject 只有自然语言总结的 external results | overview/protocol 已知 | `raw_results/*`, `configs/*`, `logs/*` | `T5-EXTERNAL-WAIT` | High | Resolved |
| EEI-006 | `codex_cli` launch 在 MVP 中必须 optional/configurable | 从 invocation design 缺口推导 | `codex_prompt.md`, invocation logs | Optional `T5-EXTERNAL-INVOKE` | High | Open decision |
| EEI-007 | Codex launch 需要记录 command/cwd/prompt/log/exit | 从 process invocation 缺口推导 | `executor_invocation.json` | Optional invoke node | High | Open decision |
| EEI-008 | Codex install/auth checks 应给出清晰可恢复错误 | 从 invocation 缺口推导 | `executor_invocation_log.jsonl` | Optional invoke node | High | Open decision |
| EEI-009 | Claude Code MVP 是 human-in-the-loop prompt copy | protocol 已知 | `claude_code_prompt.md` | `T5-EXTERNAL-WAIT` | High | Resolved for MVP recommendation |
| EEI-010 | Managed Claude CLI adapter 是 future/pending | overview 指出 automation unclear | `claude_code_prompt.md` | Optional invoke node | Medium | Open decision |
| EEI-011 | 如果实现 managed launch，应新增 `T5-EXTERNAL-INVOKE` | 从 state separation 推导 | invocation artifacts | Between gate and wait | High | Open decision |
| EEI-012 | T7 前校验 required result/status/manifest/raw/config/log | overview/protocol 已知 | result/status/manifest dirs | `T5-EXTERNAL-WAIT` | High | Resolved |
| EEI-013 | 校验 artifact hashes 和 metric source artifacts | overview/protocol 已知 | `result_pack.json`, raw files | `T5-EXTERNAL-WAIT`, `T7-AUDIT` | High | Resolved |
| EEI-014 | 执行前后校验 `allowed_paths.txt` | 已知需求加 enforcement 推导 | `allowed_paths.txt` | Invoke/wait/audit | High | Partially open |
| EEI-015 | network 和 dependency install policy 必须显式 | 从 security gap 推导 | invocation config/log | Optional invoke node | High | Open decision |
| EEI-016 | timeout、heartbeat 和 stale rules 必须配置 | overview 列为缺口 | `job_state.json`, `heartbeat.json` | Invoke/wait | High | Open decision |
| EEI-017 | invocation logs 必须脱敏 | security requirement 推导 | logs and invocation files | Optional invoke node | High | Open decision |
| EEI-018 | process exit 后 result pack 缺失仍是 wait rejection | known wait behavior 加 process behavior 推导 | `wait_rejection_report.md` | `T5-EXTERNAL-WAIT` | High | Resolved |
| EEI-019 | partial results policy 必须在 claim use 前定义 | overview 列为缺口 | `executor_status.json`, `result_pack.json` | Wait/T7 | High | Open decision |
| EEI-020 | CLI UX 必须区分 executor done 和 ResearchOS accepted | protocol 已知 | `executor_status.json`, audit outputs | Gate/wait/T7 | High | Resolved |
| EEI-021 | 重新选择 executor 时必须可审计地 preserve 或 supersede old artifacts | resume/recovery requirement 推导 | `executor_selection.json`, invocation logs | Gate/invoke/wait | Medium | Open decision |
| EEI-022 | stable adapters 需要 schema version strategy | overview 列为缺口 | schema/result/status files | T5-T7 | High | Open decision |

## 后续设计可能涉及的代码位置

以下是后续实现设计可能相关的当前仓库位置。本文档不修改这些代码：

- `config/system_config/state_machine.yaml`：T5/T7 state definitions，以及潜在 `T5-EXTERNAL-INVOKE` 插入点。
- `researchos/tools/external_experiment.py`：handoff、selection、mock dry-run、wait validation、ingest、audit、claim mapping tools。
- `researchos/tools/human_gate.py`：CLI gate 行为和 `codex_cli` 二次确认。
- `researchos/agents/experimenter.py`：experimenter output validation。
- `researchos/cli.py` 和 `researchos/cli_runners/`：可能的 CLI/runner 集成点。
- `tests/unit/test_external_experiment_tools.py` 和 `tests/unit/test_human_gate.py`：可扩展的现有 protocol 和 gate tests。
