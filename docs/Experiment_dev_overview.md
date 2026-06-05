# T5-T7 实验模块项目开发总览

> 本文件用于后续形成更细粒度的开发文档。它只基于现有 3 份参考文档整理已知事实、缺口和待确认事项，不作为最终实现方案。

## 1. 项目概述

T5-T7 阶段是 ResearchOS 当前实验链路的核心开发对象。根据现有文档，当前主链已经废弃“ResearchOS 自己在 T5-T7 内部长时间实现并运行实验”的默认语义，改为由 ResearchOS 负责编译实验协议、选择外部执行器、生成 handoff artifact、等待外部结果、摄取结果、审计证据、做实验后 novelty/collision 复核，并把审计后的 result-to-claim 与 evidence pack 交给后续 T7.5/T8 使用。

T5-T7 的核心目标可以概括为：

- 把 T4/T4.5 产出的假设、实验计划、新颖性审计和资源线索编译成外部执行器可执行的文件契约。
- 通过 `T5-EXECUTOR-GATE` 让用户显式选择 `mock_dry_run`、`codex_cli`、`claude_code_window` 或 `manual`。
- 在真实执行器路径中暂停于 `T5-EXTERNAL-WAIT`，等待外部执行器按协议写回 result pack、status、manifest、raw result、config 和 log。
- 在 mock 路径中用 `T5-DRY-RUN` 验证文件协议，但明确标记 `dry_run=true` / `mock_only=true`，不得作为论文实验证据。
- 将外部结果规范化为 ResearchOS 下游 artifact，完成 provenance/hash/metric source/required baseline 覆盖审计。
- 在 T7 阶段把实验结果转成保守 claim mapping，并输出 T8 可消费的 evidence pack、must-not-claim、claim support matrix 等文件。

已明确包含的主链节点包括：

```text
T5-HANDOFF
 -> T5-EXECUTOR-GATE
    -> mock_dry_run: T5-DRY-RUN
    -> codex_cli / claude_code_window / manual: T5-EXTERNAL-WAIT
 -> T7-INGEST
 -> T7-AUDIT
 -> T7-POST-NOVELTY
 -> T7-CLAIMS
 -> T7.5
```

其中 T7.5 不属于本文件重点开发范围的主体，但它是 T5-T7 证据链的直接消费者，用于判断审计后的证据是否足以进入写作链。

接口设计目前主要体现为 workspace artifact 文件契约，而不是网络 API。ResearchOS 与外部执行器之间只通过 `external_executor/` 下的文件交互。具体字段、schema 细节已有部分示例，但完整 schema、错误码、状态枚举和实现级接口仍待确认。

## 2. 当前已知信息

### 2.1 工作流与状态

| 已知事项 | 来源 | 可信度 |
| --- | --- | --- |
| T5-T7 新主链从 `T5-HANDOFF` 开始，不再默认进入旧内部实验。 | `agent_pipeline.md`、`experiment_module_redesign.md` | 高 |
| `T5-EXECUTOR-GATE` 是真实实验前的人工控制点，属于状态机 immediate gate，完整 pipeline 中通常不启动 LLM。 | `agent_pipeline.md` | 高 |
| `mock_dry_run` 进入 `T5-DRY-RUN`，真实/人工执行路径进入 `T5-EXTERNAL-WAIT`。 | 三份文档 | 高 |
| `T5-EXTERNAL-WAIT` 检查外部执行器写回的 result pack/status/manifest，不合格时写 rejection report 并可恢复暂停。 | `agent_pipeline.md`、`external_executor_protocol.md` | 高 |
| T7 被拆分为 `T7-INGEST`、`T7-AUDIT`、`T7-POST-NOVELTY`、`T7-CLAIMS`。 | `agent_pipeline.md` | 高 |
| `experiment_module_redesign.md` 的最小联调命令列出 `T5-HANDOFF -> T5-EXECUTOR-GATE -> T5-DRY-RUN -> T7-INGEST -> T7-AUDIT -> T7-CLAIMS`，但未包含 `T7-POST-NOVELTY`。 | `experiment_module_redesign.md` | 中 |

### 2.2 输入与输出 artifact

T5-HANDOFF 明确读取：

- `project.yaml`
- `ideation/hypotheses.md`
- `ideation/exp_plan.yaml`
- `ideation/risks.md`
- `ideation/novelty_audit.md`
- `ideation/idea_scorecard.yaml`
- `literature/synthesis.md`
- `literature/comparison_table.csv`
- `resources/` 与 `literature/baseline_map.json` 中已有资源线索（`agent_pipeline.md` 提到）

T5-HANDOFF 明确写出：

- `external_executor/handoff_pack.json`
- `external_executor/executor_selection.json`
- `external_executor/input_manifest.json`
- `external_executor/expected_outputs_schema.json`
- `external_executor/allowed_paths.txt`
- `external_executor/AGENTS.md`
- `external_executor/CLAUDE.md`
- `external_executor/README.md`
- `external_executor/job_state.json`
- `external_executor/executor_prompt.md`
- `external_executor/codex_prompt.md`
- `external_executor/claude_code_prompt.md`
- `external_executor/manual_instructions.md`

外部执行器必须写出：

- `external_executor/result_pack.json`
- `external_executor/executor_status.json`
- `external_executor/run_manifest.json`
- `external_executor/raw_results/*`
- `external_executor/configs/*`
- `external_executor/logs/*`

T7-INGEST 明确写出：

- `experiments/results_summary.json`
- `experiments/run_records.jsonl`
- `experiments/evidence_index.json`
- `experiments/ingest_report.json`

T7-AUDIT 明确写出：

- `experiments/integrity_audit.json`
- `experiments/experiment_fairness_review.md`

T7-POST-NOVELTY 明确写出：

- `novelty/post_experiment_novelty_check.json`
- `novelty/post_experiment_collision_cases.md`

T7-CLAIMS 明确写出：

- `experiments/experimental_claims.json`
- `drafts/result_to_claim.json`
- `drafts/experiment_evidence_pack.json`
- `drafts/must_not_claim.md`
- `drafts/claim_support_matrix.csv`
- `drafts/limitations_from_experiments.md`
- `drafts/figure_table_evidence_map.json`
- `experiments/iteration_log.md`

### 2.3 工具与职责

| 节点 | Agent/mode | 明确工具 | 职责 |
| --- | --- | --- | --- |
| `T5-HANDOFF` | `ExperimenterAgent` / `handoff` | `build_experiment_handoff_pack` | 编译外部实验契约、schema、allowed paths 和执行器 prompt。 |
| `T5-DRY-RUN` | `ExperimenterAgent` / `dry_run` | `mock_external_dry_run` | 生成 schema-compatible mock result pack，验证协议可通。 |
| `T7-INGEST` | `ExperimenterAgent` / `result_ingest` | `ingest_external_results` | 摄取外部 result pack 并规范化。 |
| `T7-AUDIT` | `ExperimenterAgent` / `integrity_audit` | `audit_experiment_integrity` | 审计 provenance、hash、metric source、mock 标记和 baseline 覆盖。 |
| `T7-POST-NOVELTY` | `ExperimenterAgent` / `post_novelty` | `build_post_experiment_novelty_check` | 做实验后 novelty/collision 证据状态复核。 |
| `T7-CLAIMS` | `ExperimenterAgent` / `result_to_claim` | `map_results_to_claims`、`build_experiment_evidence_pack` | 生成 result-to-claim、evidence pack 和禁止措辞。 |

### 2.4 明确约束

- ResearchOS 不接受只有自然语言总结的外部实验结果。
- 外部执行器必须只在 `allowed_paths.txt` 允许的路径内工作。
- `executor_status.json` 中 `accepted` 必须保持 `false`；执行器只能声明完成，不能替 ResearchOS 接收证据。
- result pack 中每个 metric 必须能追踪到 source artifact。
- dry-run 必须显式 `mock_only=true`，不能作为真实论文 claim evidence。
- `results_summary.json` 必须标明 `source=external_executor`。
- audit 必须能追踪 artifact 到磁盘文件和 sha256。
- result-to-claim 必须生成 support status 和 allowed/forbidden wording。

## 3. 初步功能范围

### 3.1 已明确的功能

| 功能 | 说明 | 依据 |
| --- | --- | --- |
| 外部实验 handoff pack 生成 | 从项目、假设、实验计划、风险、新颖性审计、综述等上游文件编译实验契约。 | 三份文档 |
| 执行器选择 gate | 用户选择 mock、Codex CLI、Claude Code 窗口或 manual，并写 `executor_selection.json`。 | `agent_pipeline.md`、`external_executor_protocol.md` |
| 执行器 prompt/指南生成与 patch | 生成 AGENTS/CLAUDE/README/prompt，gate 后替换 `UNSET` 执行模式。 | `agent_pipeline.md` |
| allowed paths 权限约束 | 使用 `rw/ro/no` 前缀约束外部执行器可读写路径。 | `external_executor_protocol.md` |
| mock dry-run | 生成 mock result/status/manifest/raw/config/log/heartbeat，用于协议联调。 | 三份文档 |
| 外部等待与恢复 | 等待 result pack/status/manifest，不合格写 rejection report，修复后 resume。 | `agent_pipeline.md`、`external_executor_protocol.md` |
| 结果摄取 | 把 result pack 规范化为 `results_summary`、`run_records`、`evidence_index` 和 `ingest_report`。 | 三份文档 |
| 实验诚信审计 | 校验 metric source artifact、hash、run manifest、mock 标记、required baseline 覆盖。 | 三份文档 |
| 实验后 novelty/collision 复核 | 基于 mock-only、integrity fail、baseline missing 等状态生成 claim downgrade 信号。 | `agent_pipeline.md` |
| result-to-claim | 生成 claim mapping、allowed/forbidden wording、must-not-claim、claim support matrix 和 evidence pack。 | `agent_pipeline.md`、`experiment_module_redesign.md` |
| legacy 入口退休/兼容 | 普通 `run-task T5/T6/T7` 报 retired；显式 legacy 节点需 `--allow-legacy`。 | `agent_pipeline.md` |

### 3.2 推测可能需要的功能

以下内容不是文档直接给出的完整设计，而是为了形成后续开发文档可能需要补齐的功能范围。

| 推测功能 | 推测依据 | 待确认点 |
| --- | --- | --- |
| schema 版本管理 | 文档多次提到 `expected_outputs_schema.json`、semantics、schema-compatible，但未说明版本字段策略。 | 是否需要 `schema_version`、兼容策略、迁移规则。 |
| 外部执行器状态枚举规范 | 文档提到 `done` / `COMPLETED` / `PARTIAL_RESULTS_READY` 可接受，但未完整列出状态机。 | 状态枚举、大小写兼容、失败/取消/部分结果语义。 |
| artifact hash 生成与校验规范 | 文档要求 sha256，但未说明相对路径、换行、二进制文件和目录 hash 规则。 | hash 计算范围与跨平台一致性。 |
| baseline 覆盖判定规则 | 文档说从 `Required Baselines` 抽取并生成 coverage，缺失会 fail 或 block claim，但规则未展开。 | baseline required/completed/missing 的判定标准。 |
| partial results 处理 | `PARTIAL_RESULTS_READY` 被列为可接受状态，但 result-to-claim 如何处理部分结果未详细说明。 | 是否允许进入 T7-CLAIMS，claim strength 如何降级。 |
| 外部执行器 sandbox/隔离实现 | 文档强调 allowed paths 和隔离路径，但未给出真实执行进程的启动/监控方案。 | 是否由 ResearchOS 启动 Codex CLI，还是只生成 prompt。 |
| 审计失败后的修复循环 | 文档说明 fail/block/降级，但未明确自动回到哪个节点或用户如何选择补实验。 | fail 后默认路由、人工 gate、重跑 handoff 还是复用 contract。 |

### 3.3 暂时无法确定的功能

| 功能 | 无法确定原因 |
| --- | --- |
| 真实 `codex_cli` 的进程调用方式 | 文档说推荐正式执行器，但主要描述文件协议和人工确认，没有完整进程管理接口。 |
| `claude_code_window` 的自动化程度 | 文档说用户复制 prompt 到 Claude Code 窗口，未说明 ResearchOS 是否需要检测窗口或只等文件。 |
| 第三方 manual executor 的最低兼容测试 | 文档定义了必须写出的文件，但未说明是否提供校验工具或模板化验收命令。 |
| 安全策略与权限 enforcement 的实现边界 | 文档定义了 `allowed_paths.txt`，但未说明外部执行器违规时是预防、检测还是两者都有。 |
| 运行成本、超时、GPU/资源预算 | `agent_pipeline.md` 提到预算扩限 gate 对 T5/T7 长任务重要，但 T5-T7 新链的具体预算策略未在三份文档中完整展开。 |

## 4. 初步模块划分

### 4.1 明确提到的模块

| 模块 | 归属节点 | 职责 | 边界说明 |
| --- | --- | --- | --- |
| Handoff Pack Builder | `T5-HANDOFF` | 读取上游研究 artifact，生成外部实验契约、schema、allowed paths、prompt 和指南。 | 明确不写实验代码、不运行实验、不发明 baseline。 |
| Executor Gate | `T5-EXECUTOR-GATE` | 展示 handoff 信息，获取用户执行器选择，写 `executor_selection.json`，patch 执行模式。 | 是状态机 gate；正常完整 pipeline 不启动 LLM。 |
| External Wait Boundary | `T5-EXTERNAL-WAIT` | 检查外部执行器是否写回合格 result/status/manifest；不合格时可恢复暂停。 | 文档称优先不调用 LLM，主要由 runtime pre-finalizer 处理。 |
| Mock Dry Run Executor | `T5-DRY-RUN` | 生成 mock 协议结果，验证协议端到端可通。 | 只证明协议，不产生真实证据。 |
| Result Ingest | `T7-INGEST` | 把外部 result pack 规范化为下游兼容结果。 | 不判断科学可信度。 |
| Integrity Audit | `T7-AUDIT` | 审计 provenance、hash、metric source、baseline 覆盖和 mock 状态。 | 不接受执行器自评作为证据真相。 |
| Post Experiment Novelty Check | `T7-POST-NOVELTY` | 基于实验状态生成 novelty/collision 复核和 claim downgrade 信号。 | 当前只做确定性证据状态复核，不替代 LLM/人工判断。 |
| Result-to-Claim Mapper | `T7-CLAIMS` | 将审计后的 metric 转为保守 claim mapping，并生成 T8 evidence pack。 | 机械 mapping 不是最终科学判断。 |
| Legacy Compatibility Layer | `LEGACY-T5/T6/T7` | 保留旧 workspace 迁移和显式 legacy 调试入口。 | 普通主链和普通 `run-task T5/T6/T7` 不进入旧内部实验。 |

### 4.2 为后续开发文档临时归纳的模块

| 模块 | 归纳目的 | 待确认边界 |
| --- | --- | --- |
| Artifact Contract Layer | 统一描述 handoff/result/status/manifest/evidence/claim 文件契约。 | 是否拆成 schema 文档、I/O contract 文档和 examples。 |
| Resume & Rejection Layer | 统一描述 wait rejection、acceptance report、recoverable pause、resume 规则。 | 哪些校验属于 wait，哪些属于 ingest/audit。 |
| Executor Adapter Layer | 统一描述 mock、Codex CLI、Claude Code、manual 的差异。 | 是否存在真实 adapter 代码，还是仅以 prompt 和文件协议完成。 |
| Security & Path Policy Layer | 统一描述 `allowed_paths.txt`、`rw/ro/no`、越界检测和隐私约束。 | 当前是检测型还是强隔离型，需要确认。 |
| Evidence Quality Policy Layer | 统一描述 audit status、baseline coverage、mock-only、partial results 和 claim strength 的关系。 | 需要负责人确认各类失败如何路由和降级。 |

## 5. 待确认问题清单

### 5.1 目标与边界

| 问题 | 为什么需要确认 | 优先级 |
| --- | --- | --- |
| T5-T7 详细开发文档是否只覆盖新版外部实验链，还是也要覆盖 legacy 节点实现细节？ | legacy 节点仍存在，但文档强调主链不再使用；范围不清会导致开发文档膨胀。 | 高 |
| T7.5 是否纳入“实验模块开发文档”的范围？ | T7.5 是 T5-T7 证据链消费者，但严格来说不属于 T5-T7；会影响文档边界。 | 高 |
| ResearchOS 是否需要主动启动 `codex_cli`，还是仅生成 prompt 并等待用户/外部进程写回文件？ | 决定是否需要进程管理、日志采集、超时和权限控制设计。 | 高 |

### 5.2 核心流程

| 问题 | 为什么需要确认 | 优先级 |
| --- | --- | --- |
| `T7-POST-NOVELTY` 是否应加入最小联调路径？ | `agent_pipeline.md` 已列为主链节点，但 `experiment_module_redesign.md` 的最小联调命令未包含它。 | 高 |
| `PARTIAL_RESULTS_READY` 是否允许进入 `T7-INGEST` 和 `T7-CLAIMS`？ | 文档列为可接受状态，但 claim 降级和后续路由未明确。 | 高 |
| audit `fail` 后默认应暂停、回 `T5-HANDOFF`、回 `T5-EXTERNAL-WAIT`，还是交给 T7.5/人工 gate？ | 影响状态机、错误处理和用户体验。 | 高 |
| required baseline missing 时，真实结果是整体 fail 还是仅 block strong claim？ | `agent_pipeline.md` 同时提到 fail 或 claim block，需要细化规则。 | 高 |

### 5.3 接口设计

| 问题 | 为什么需要确认 | 优先级 |
| --- | --- | --- |
| `handoff_pack.json`、`result_pack.json`、`executor_status.json`、`run_manifest.json` 是否需要稳定 schema version？ | 后续测试、兼容和迁移都依赖 schema 版本策略。 | 高 |
| `executor_status.json.current_state` 的完整枚举是什么？ | wait 阶段需要准确判断哪些状态可接受、可恢复或失败。 | 高 |
| result pack 中 `evidence_grade` 的允许值和语义是什么？ | 示例给出 `audited_external`，但 dry-run、partial、failed audit 的等级未完整定义。 | 中 |
| `allowed_paths.txt` 的路径匹配规则是前缀匹配、规范化相对路径匹配，还是支持 glob？ | 越界检测、安全策略和跨平台路径处理都依赖该规则。 | 高 |

### 5.4 状态流转

| 问题 | 为什么需要确认 | 优先级 |
| --- | --- | --- |
| 非交互环境在 `T5-EXECUTOR-GATE` 暂停后，resume 时如何携带用户选择？ | 文档说会暂停等待 resume，但未说明预置选择文件或 CLI 输入格式。 | 中 |
| 重新选择执行器时是否允许复用旧 handoff pack？ | 文档建议回到 gate 重新选择，但需要确认 result/status/manifest 是否清理或保留。 | 中 |
| 旧 workspace 的 `next_task: T7` 映射到 `T5-HANDOFF` 是否需要写迁移日志？ | 影响可审计性和调试。 | 低 |

### 5.5 错误处理

| 问题 | 为什么需要确认 | 优先级 |
| --- | --- | --- |
| wait 阶段 rejection report 需要包含哪些结构化字段？ | 目前只明确文件名，未明确机器可读结构；影响自动修复和测试。 | 中 |
| hash 不符、路径越界、缺 raw/config/log、metric 为空是否都作为同一级 recoverable pause？ | 不同错误严重程度可能需要不同用户提示和修复路径。 | 中 |
| 外部执行器写入 `accepted=true` 时是直接 reject、自动 patch 为 false，还是视为协议违规并暂停？ | 文档明确不允许，但处理策略需确认。 | 高 |

### 5.6 第三方服务

| 问题 | 为什么需要确认 | 优先级 |
| --- | --- | --- |
| `codex_cli` 正式执行器需要支持哪些运行环境和登录状态？ | 决定开发文档是否需要安装、认证、权限和失败恢复章节。 | 中 |
| `claude_code_window` 是否完全人工复制 prompt，还是后续计划接入自动化桥接？ | 影响接口边界和安全设计。 | 低 |

### 5.7 部署与运维

| 问题 | 为什么需要确认 | 优先级 |
| --- | --- | --- |
| 真实外部实验的日志、raw results 和 workdir 是否需要清理/归档策略？ | 外部实验可能产生大文件，影响 workspace 管理。 | 中 |
| 长实验的 timeout、heartbeat stale 判定和用户提醒机制是什么？ | 文档提到 heartbeat，但未定义 stale/timeout 处理。 | 中 |

### 5.8 安全与隐私

| 问题 | 为什么需要确认 | 优先级 |
| --- | --- | --- |
| `allowed_paths.txt` 是执行前强制隔离，还是执行后审计检测？ | 决定是否需要 sandbox 或只需要 validator。 | 高 |
| 外部执行器是否允许联网、安装依赖、访问用户 seed PDFs 或 API key？ | 涉及安全、隐私和复现实验边界。 | 高 |
| `AGENTS.md` / `CLAUDE.md` 中是否需要写入敏感信息过滤规则？ | 防止外部执行器 prompt 泄露环境变量、凭据或未授权路径。 | 中 |

### 5.9 测试与验收标准

| 问题 | 为什么需要确认 | 优先级 |
| --- | --- | --- |
| 最小验收链路是否以 mock dry-run 为主，还是必须包含 manual/codex_cli 样例？ | 决定 CI 和 smoke test 范围。 | 高 |
| 对每个节点 validator 的 hard fail 与 warn 标准是什么？ | 文档列出关注点，但未形成完整验收表。 | 高 |
| 是否需要构造恶意/错误 result pack 测试路径越界、hash 不符、missing artifact、accepted=true？ | 这些是核心协议风险，建议纳入验收，但需确认范围。 | 中 |

## 6. 待设计内容清单

| 待设计内容 | 设计目标 | 依赖的待确认问题 |
| --- | --- | --- |
| T5-T7 状态机详细设计 | 明确每个节点的进入条件、退出条件、分支、暂停和 resume 行为。 | 核心流程、状态流转、错误处理相关问题。 |
| Artifact schema 设计 | 为 handoff pack、executor selection、result pack、status、manifest、ingest/audit/claim 输出建立稳定字段说明。 | 接口设计问题，尤其 schema version 和状态枚举。 |
| Executor adapter 设计 | 区分 mock、Codex CLI、Claude Code、manual 的职责、启动方式、用户操作和验收。 | 目标边界、第三方服务问题。 |
| allowed paths 与安全策略设计 | 定义路径规范化、rw/ro/no 语义、越界处理和外部执行器权限边界。 | 安全与隐私、接口设计问题。 |
| Wait/rejection/resume 设计 | 明确缺文件、状态不合格、hash 不符、路径越界、partial results 的可恢复处理。 | 错误处理、状态流转问题。 |
| Integrity audit 规则设计 | 明确 audit status、baseline coverage、mock-only、partial results 和 fail 的判定规则。 | baseline、partial results、validator 验收问题。 |
| Result-to-claim 策略设计 | 明确 support status、claim strength、allowed/forbidden wording 和 must-not-claim 的生成规则。 | Evidence quality 与 baseline 判定问题。 |
| 最小联调与测试设计 | 定义 mock dry-run、错误 result pack、resume、legacy retired 提示等测试场景。 | 测试与验收标准问题。 |
| 运维与大文件管理设计 | 约定 raw results、logs、configs、workdir 的大小、归档、清理和可复现记录。 | 部署与运维、第三方服务问题。 |

## 7. 后续开发文档规划

### 7.1 T5-T7 技术架构文档

用途：描述 T5-T7 在 ResearchOS 主状态机中的位置、模块职责、artifact-first 原则、runtime/Agent/tool/validator 分工。

依赖已知信息：`agent_pipeline.md` 的真实阶段图、节点速览、T5-T7 详细章节。

需先确认：T7.5 是否纳入范围、`T7-POST-NOVELTY` 是否进入最小联调、legacy 细节覆盖范围。

### 7.2 Artifact 与 Schema 接口文档

用途：详细定义 `handoff_pack.json`、`expected_outputs_schema.json`、`allowed_paths.txt`、`executor_selection.json`、`result_pack.json`、`executor_status.json`、`run_manifest.json`、`evidence_index.json`、`integrity_audit.json`、`result_to_claim.json` 等文件。

依赖已知信息：三份文档中列出的输入输出文件、Result Pack 最小语义示例、validator 关注点。

需先确认：schema version、状态枚举、hash 规则、路径匹配规则、evidence grade 枚举。

### 7.3 外部执行器协议文档

用途：分别说明 `mock_dry_run`、`codex_cli`、`claude_code_window`、`manual` 的执行方式、用户操作、允许路径、必须写回的文件和失败恢复。

依赖已知信息：`external_executor_protocol.md` 的执行器选择、handoff 文件、必写文件、不接受内容、resume 规则。

需先确认：ResearchOS 是否主动启动 Codex CLI、Claude Code 是否自动化、manual executor 的最小验收方式。

### 7.4 状态流转与恢复文档

用途：定义 T5-T7 每个节点的进入/退出条件、暂停条件、resume 行为、rejection/acceptance report、旧节点 retired/legacy 映射。

依赖已知信息：`agent_pipeline.md` 的状态图、恢复机制、T5-EXTERNAL-WAIT 说明。

需先确认：audit fail 后路由、partial results 路由、重新选择执行器时的 artifact 保留策略。

### 7.5 安全与权限设计文档

用途：定义外部执行器只能在 allowed paths 内工作的执行约束、越界检测、敏感信息处理、网络/依赖/凭据访问策略。

依赖已知信息：`allowed_paths.txt` 的 `rw/ro/no` 示例、不接受未授权路径修改的规则。

需先确认：allowed paths 是执行前强制还是执行后审计、是否允许联网和安装依赖、是否允许访问用户私有材料。

### 7.6 测试与验收文档

用途：定义每个节点的最小测试、mock dry-run smoke、错误 result pack 测试、resume 测试、claim 降级测试和 legacy retired 行为测试。

依赖已知信息：`experiment_module_redesign.md` 的最小联调命令、三份文档中的 validator 关注点和不接受内容。

需先确认：最小验收是否必须包含 `T7-POST-NOVELTY`、是否需要真实外部执行器样例、hard fail/warn 标准。

### 7.7 部署与运维文档

用途：说明真实外部实验的 workspace 管理、日志与 raw result 留存、heartbeat、长任务恢复、大文件归档和资源预算。

依赖已知信息：`agent_pipeline.md` 的 workspace 事实源、resume、预算扩限 gate；`external_executor_protocol.md` 的 heartbeat/result/log/config 约定。

需先确认：heartbeat stale 判定、workdir 清理策略、真实 Codex CLI/Claude Code 运行环境要求。

## 8. 信息来源与可信度说明

### 8.1 实际读取的参考文档

用户提供的第 1 个路径为：

```text
./DIG-ResearchOS/docs/agent_pipline.md
```

项目中实际存在的是：

```text
./DIG-ResearchOS/docs/agent_pipeline.md
```

本文件按实际存在的 `agent_pipeline.md` 读取和引用。其余两个参考文档按用户路径存在：

```text
./DIG-ResearchOS/docs/experiment_module_redesign.md
./DIG-ResearchOS/docs/external_executor_protocol.md
```

### 8.2 关键结论可信度

| 关键结论 | 可信度 | 原因 |
| --- | --- | --- |
| T5-T7 新主链采用外部执行器协议，不再默认内部长实验。 | 高 | 三份文档均直接描述，`agent_pipeline.md` 明确说明旧 `T5/T6/T7` retired。 |
| T5-T7 的主要接口是 workspace artifact 文件，不是网络 API。 | 高 | `external_executor_protocol.md` 明确写 ResearchOS 与执行器只通过 workspace artifact 文件交互。 |
| `mock_dry_run` 只验证协议，不能作为论文实验证据。 | 高 | 三份文档均强调 mock/dry-run 标记和 claim 限制。 |
| `codex_cli` 是推荐正式执行器。 | 高 | `external_executor_protocol.md` 明确说明推荐正式执行器，且需要二次确认。 |
| ResearchOS 是否主动启动 `codex_cli`。 | 低 | 文档强调推荐正式执行器和隔离路径，但没有完整说明进程启动、监控、认证和日志采集机制。 |
| `T7-POST-NOVELTY` 是主链节点。 | 高 | `agent_pipeline.md` 明确列入当前真实阶段图和 T7 详细章节。 |
| `T7-POST-NOVELTY` 是否属于最小联调必须步骤。 | 中 | `agent_pipeline.md` 包含该节点，但 `experiment_module_redesign.md` 的最小联调命令未列出。 |
| required baseline missing 的处理规则。 | 中 | 文档明确会影响 audit/claim，但“fail 或 claim block”的精确分界仍不完整。 |
| `allowed_paths.txt` 可强制限制执行器写入范围。 | 中 | 文档明确路径白名单和越界 reject，但未说明是执行前 sandbox 强制还是执行后审计检测。 |
| partial results 的处理策略。 | 低 | 文档提到 `PARTIAL_RESULTS_READY` 可被 wait 接受，但未展开后续 claim/audit 路由。 |

### 8.3 低可信度内容的原因

低可信度内容主要来自以下情况：

- 文档给出了目标或约束，但未给出实现级流程。
- 不同文档粒度不同，短设计文档未覆盖 `agent_pipeline.md` 中后来增加的节点。
- 示例字段不足以推导完整 schema 或状态枚举。
- 外部执行器涉及第三方 CLI/人工操作/窗口复制，现有文档主要定义文件协议，没有完整运维与安全方案。

