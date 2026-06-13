# EEI Artifact Field Contract

本文档基于 `EEI_codex_proposal_v02.md`，规定 EEI 阶段会从 `external_executor/` 读取和写回的 artifact 字段设计策略。

本文档采用 ResearchOS 轻量字段契约表达方式：列出文件语义、生产者、消费者、required/optional 字段、枚举、路径规则和 validation 规则。
MVP 不实现完整 schema version management。

## 1. 通用规则

### 1.1 语义字段

除纯文本说明文件和二进制/任意格式 raw artifact 外，EEI JSON/JSONL 文件应包含 `semantics` 字段。MVP validator 不基于 `schema_version` 做分派、迁移或兼容矩阵判断。

通用字段：

| 字段 | 类型 | Required | 规则 |
| --- | --- | --- | --- |
| `semantics` | string | yes | 必须匹配文件类型 |

MVP 不因缺少 `schema_version` 单独 reject；
字段结构、`semantics`、required fields、枚举、hash、allowed paths 和 evidence integrity 是 reject 依据。

### 1.2 时间、路径和 hash

| 项 | 规则 |
| --- | --- |
| timestamp | ISO-8601 字符串，建议 UTC，字段名通常为 `created_at`、`updated_at`、`started_at`、`ended_at`、`last_heartbeat_at` |
| path | workspace-relative path，禁止 workspace 外 absolute path |
| path normalization | 移除开头 `./`，解析 `..`，规范化后再和 `allowed_paths.txt` 比较 |
| sha256 | lowercase hex string |
| bytes | integer，非负 |

### 1.3 通用枚举

| 枚举 | 允许值 |
| --- | --- |
| `executor` | `mock_dry_run`, `codex_cli`, `claude_code_window`, `manual` |
| `path_permission` | `rw`, `ro`, `no` |
| `allowed_paths_enforcement` | `audit`, future `sandbox` |
| `policy` | `require_confirmation`, `allow`, `deny` |
| `prompt_injection_mode` | `stdin`, `argv`, `prompt_file` |
| `artifact_kind` | `raw_results`, `config`, `log`, `stdout_log`, `stderr_log`, `invocation`, `manifest`, `result_pack`, `status`, `heartbeat`, `other` |
| `executor_state` | `starting`, `running`, `COMPLETED`, `done`, `failed`, `timeout`, `cancelled`, `stale` |
| `partial_state` | `PARTIAL_RESULTS_READY` |

`PARTIAL_RESULTS_READY` 默认不允许进入 T7。

### 1.4 Error object

结构化错误对象建议统一为：

| 字段 | 类型 | Required | 说明 |
| --- | --- | --- | --- |
| `code` | string | yes | 稳定错误码 |
| `severity` | string | yes | `error`, `warning`, `info` |
| `file` | string | no | 相关 workspace-relative file |
| `field` | string | no | 相关字段路径 |
| `expected` | any | no | 期望值 |
| `observed` | any | no | 实际值 |
| `remediation` | string | no | 修复建议 |
| `recoverable` | boolean | yes | 是否可通过修复 artifact 后 resume |

建议错误码：

- `SCHEMA_SEMANTICS_MISMATCH`
- `REQUIRED_FIELD_MISSING`
- `INVALID_ENUM_VALUE`
- `INVALID_PATH`
- `PATH_NOT_ALLOWED`
- `HASH_MISMATCH`
- `ARTIFACT_MISSING`
- `STATUS_ACCEPTED_TRUE`
- `PARTIAL_RESULTS_NOT_ALLOWED`
- `NATURAL_LANGUAGE_ONLY_RESULT`

## 2. EEI 读取文件

### 2.1 `external_executor/handoff_pack.json`

生产者：ResearchOS `T5-HANDOFF`

消费者：selected executor、`T5-EXTERNAL-INVOKE`、`T5-EXTERNAL-WAIT`、T7 context

语义：主实验契约。

Required fields：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `semantics` | string | 推荐 `external_executor_handoff_pack` |
| `executor` 或 `selected_executor` | string | 如果已 patch，应属于 executor 枚举 |
| `execution_mode` | string | 应表示真实外部执行 |
| `metrics` | array | 需要执行和回填的 metric 需求 |
| `expected_outputs` | array/object | 必需输出说明 |
| `allowed_paths` 或 `allowed_paths_file` | array/string | 必须能追踪到 `allowed_paths.txt` |
| `required_baselines` | array | 可为空，但字段应存在 |
| `source_artifacts` | array | 上游输入 artifact 引用 |

Optional fields：

- `experiment_plan`
- `seeds`
- `datasets`
- `resource_hints`
- `notes`

Validation：

- 文件存在且 JSON 可解析。
- executor mode 与 `executor_selection.json` 不得冲突。
- required outputs 必须覆盖 result/status/manifest/raw/config/log。

### 2.2 `external_executor/executor_selection.json`

生产者：ResearchOS `T5-EXECUTOR-GATE`

消费者：`T5-EXTERNAL-INVOKE`、resume、`T5-EXTERNAL-WAIT`

Required fields：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `semantics` | string | `external_executor_selection` |
| `selected_executor` | string | enum: executor |
| `next_state` | string | `T5-EXTERNAL-INVOKE` |
| `selected_by` | string | `human`, `cli`, `config`, `runtime` 之一 |
| `selected_at` | string | timestamp |

Optional fields：

- `notes`
- `fallback`
- `policy_snapshot`

Validation：

- `selected_executor` 必须属于 executor 枚举才进入 EEI。
- `next_state` 必须是 `T5-EXTERNAL-INVOKE`。

### 2.3 `external_executor/input_manifest.json`

生产者：ResearchOS `T5-HANDOFF`

消费者：selected executor、audit context

Required fields：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `semantics` | string | `external_executor_input_manifest` |
| `inputs` | array | 输入 artifact 列表 |
| `required_executor_outputs` | array | selected executor 必须写出的文件或目录 |

`inputs[]` item：

| 字段 | 类型 | Required |
| --- | --- | --- |
| `path` | string | yes |
| `kind` | string | yes |
| `sha256` | string | recommended |
| `bytes` | integer | recommended |
| `role` | string | optional |

Validation：

- path 必须 workspace-relative。
- 如果提供 sha256，必须可校验或在 report 中说明无法校验。
- `required_executor_outputs` 必须至少包含 result/status/manifest/raw/config/log。

### 2.4 `external_executor/expected_outputs_schema.json`

生产者：ResearchOS `T5-HANDOFF`

消费者：selected executor、`T5-EXTERNAL-WAIT`

Required fields：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `semantics` | string | `external_executor_expected_outputs_schema` |
| `required_outputs` | array | 必需输出声明 |
| `artifact_contracts` | object | result/status/manifest 字段契约摘要 |

`artifact_contracts` recommended keys：

- `result_pack`
- `executor_status`
- `run_manifest`

`required_outputs[]` item：

| 字段 | 类型 | Required | 说明 |
| --- | --- | --- | --- |
| `path` | string | yes | 文件或目录 |
| `kind` | string | yes | artifact kind |
| `required` | boolean | yes | 是否必需 |
| `min_count` | integer | no | 目录类输出最小数量 |

Validation：

- 必须声明 `result_pack.json`、`executor_status.json`、`run_manifest.json`。
- 必须声明 `raw_results/`、`configs/`、`logs/`。

### 2.5 `external_executor/allowed_paths.txt`

生产者：ResearchOS `T5-HANDOFF`

消费者：selected executor、`T5-EXTERNAL-INVOKE`、`T5-EXTERNAL-WAIT`

格式：纯文本，每行一条规则。

Line grammar：

```text
<permission> <workspace_relative_path>
```

Required permissions：

- `rw`
- `ro`
- `no`

Rules：

- 空行允许。
- 以 `#` 开头的注释行允许。
- path 必须是 workspace-relative path。
- `no` 覆盖 `rw` / `ro`。

Validation：

- 文件必须存在。
- 至少应允许写入 `external_executor/raw_results/`、`external_executor/configs/`、`external_executor/logs/`。
- 必须拒绝 workspace 外 absolute path。
- 必须拒绝 path traversal。

### 2.6 `external_executor/AGENTS.md`

生产者：ResearchOS `T5-HANDOFF`

消费者：selected executor

格式：Markdown。

Required content：

- 指明 selected executor。
- 指明必须遵守 `allowed_paths.txt`。
- 指明必须写出 result/status/manifest/raw/config/log。
- 指明 `executor_status.json.accepted=false`。
- 指明不得只写自然语言总结。

Validation：

- MVP 只要求文件存在且非空。
- 可选检查关键短语或生成时模板完整性。

### 2.7 `external_executor/codex_prompt.md`

生产者：ResearchOS `T5-HANDOFF`

消费者：selected executor

格式：Markdown。

Required content：

- 指向 `handoff_pack.json`。
- 指向 `expected_outputs_schema.json`。
- 指向 `allowed_paths.txt`。
- 指明输出文件路径。
- 指明字段契约、required fields 和枚举要求。
- 指明 hash/source artifact 要求。

Validation：

- 文件存在且非空。
- `T5-EXTERNAL-INVOKE` 必须记录 prompt injection mode 和 prompt path。

### 2.8 `external_executor/job_state.json`

生产者：ResearchOS `T5-HANDOFF`，可由 ResearchOS 后续 patch

消费者：selected executor、monitor、resume

Required fields：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `semantics` | string | `external_executor_job_state` |
| `current_state` | string | lifecycle state |
| `allowed_states` | array | 状态枚举 |
| `updated_at` | string | timestamp |

Recommended states：

- `created`
- `selected`
- `invoking`
- `running`
- `waiting_results`
- `wait_rejected`
- `wait_accepted`
- `ingested`

Validation：

- `current_state` 必须属于 `allowed_states`。

### 2.9 `external_executor/README.md`

生产者：ResearchOS `T5-HANDOFF`

消费者：用户、selected executor

格式：Markdown。

Validation：

- MVP 只要求存在且非空。

## 3. ResearchOS 写入文件

### 3.1 `external_executor/executor_invocation.json`

生产者：ResearchOS `T5-EXTERNAL-INVOKE`

消费者：resume、wait、audit/debug

Required fields：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `semantics` | string | `external_executor_invocation` |
| `invocation_id` | string | 稳定唯一 id |
| `executor` | string | enum: executor |
| `cwd` | string | 本次运行 workspace 实例目录 |
| `command` | object | command summary |
| `prompt` | object | prompt summary |
| `policy` | object | policy snapshot |
| `timing` | object | started/ended/timeout |
| `process` | object | pid/exit/signal |
| `logs` | object | stdout/stderr paths |
| `result_presence` | object | 进程结束后的结果存在性摘要 |

`command` fields：

| 字段 | 类型 | Required |
| --- | --- | --- |
| `resolved_path` | string | yes |
| `argv_redacted` | array | yes |
| `env_allowlist` | array | yes |

`prompt` fields：

| 字段 | 类型 | Required |
| --- | --- | --- |
| `path` | string | yes |
| `injection_mode` | string | yes |

`policy` fields：

| 字段 | 类型 | Required | 枚举 |
| --- | --- | --- | --- |
| `network_policy` | string | yes | `require_confirmation`, `allow`, `deny` |
| `dependency_install_policy` | string | yes | `require_confirmation`, `allow`, `deny` |
| `allowed_paths_enforcement` | string | yes | `audit`, future `sandbox` |
| `log_redaction_policy` | string/object | yes | implementation-defined |

`timing` fields：

| 字段 | 类型 | Required |
| --- | --- | --- |
| `started_at` | string | yes |
| `ended_at` | string/null | yes |
| `timeout_seconds` | integer | yes |
| `timed_out` | boolean | yes |

`process` fields：

| 字段 | 类型 | Required |
| --- | --- | --- |
| `pid` | integer/null | yes |
| `exit_code` | integer/null | yes |
| `signal` | string/null | yes |
| `state` | string | yes |

`logs` fields：

| 字段 | 类型 | Required |
| --- | --- | --- |
| `stdout_path` | string | yes |
| `stderr_path` | string | yes |
| `stdout_truncated` | boolean | yes |
| `stderr_truncated` | boolean | yes |

Validation：

- cwd 必须是 workspace 实例目录。
- `prompt.injection_mode` 必须属于枚举。
- log paths 必须位于允许路径内。
- command/env 必须脱敏。

### 3.2 `external_executor/executor_invocation_log.jsonl`

生产者：ResearchOS `T5-EXTERNAL-INVOKE`

消费者：resume、debug、audit trail

每行 event required fields：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `event_contract` | string | optional | event 字段契约标识 |
| `event_id` | string | stable id |
| `invocation_id` | string | 对应 invocation |
| `event_type` | string | event enum |
| `created_at` | string | timestamp |
| `payload` | object | event-specific |

Event type enum：

- `invocation_planned`
- `input_validated`
- `process_started`
- `heartbeat_written`
- `stdout_log_opened`
- `stderr_log_opened`
- `process_exited`
- `process_timeout`
- `invocation_failed`
- `result_presence_checked`
- `superseded`

Validation：

- 每行必须是合法 JSON。
- `invocation_id` 必须与 `executor_invocation.json` 一致。
- unknown event type 在 MVP 中 warning；关键 process events 缺失时 warning 或 reject，按实现策略确定。

### 3.3 `external_executor/heartbeat.json`

生产者：ResearchOS `T5-EXTERNAL-INVOKE`

消费者：monitor、resume

Required fields：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `semantics` | string | `external_executor_heartbeat` |
| `invocation_id` | string | 对应 invocation |
| `executor` | string | enum: executor |
| `state` | string | heartbeat state enum |
| `pid` | integer/null | 可用时写 pid |
| `started_at` | string | timestamp |
| `last_heartbeat_at` | string | timestamp |
| `timeout_seconds` | integer | 默认 14400，可配置 |

Heartbeat state enum：

- `starting`
- `running`
- `stale`
- `exited`
- `timeout`
- `failed`
- `completed`

Validation：

- `invocation_id` 必须与 invocation 一致。
- stale rule 使用 `last_heartbeat_at`。

### 3.4 `external_executor/wait_acceptance_report.json`

生产者：ResearchOS `T5-EXTERNAL-WAIT`

消费者：resume、T7 transition、audit/debug

Required fields：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `semantics` | string | `external_executor_wait_acceptance_report` |
| `accepted_at` | string | timestamp |
| `executor` | string | enum: executor |
| `invocation_id` | string/null | 若存在 invocation 则写入 |
| `validated_files` | array | 已校验文件 |
| `artifact_contracts_checked` | object | 已校验字段契约摘要 |
| `warnings` | array | warning objects |
| `next_state` | string | `T7-INGEST` |

Validation：

- 只有 wait validation 全部通过才可写入。

### 3.5 `external_executor/wait_rejection_report.md`

生产者：ResearchOS `T5-EXTERNAL-WAIT`

消费者：用户、resume/debug

格式：Markdown。

Required content：

- rejection summary。
- selected executor。
- invocation id，如存在。
- blocking errors。
- related file。
- expected / observed。
- remediation hint。
- recoverable 状态。

建议同时写结构化 sidecar：

```text
external_executor/wait_rejection_report.json
```

如果实现 sidecar，字段契约见下一节。

### 3.6 `external_executor/wait_rejection_report.json`

生产者：ResearchOS `T5-EXTERNAL-WAIT`

消费者：resume、CLI status、tests

Optional in MVP，recommended。

Required fields：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `semantics` | string | `external_executor_wait_rejection_report` |
| `rejected_at` | string | timestamp |
| `executor` | string | enum: executor |
| `invocation_id` | string/null | 若存在 invocation 则写入 |
| `recoverable` | boolean | 通常为 true |
| `errors` | array | error objects |
| `warnings` | array | warning objects |
| `required_files_missing` | array | 缺失文件列表 |
| `next_action_hint` | string | 修复建议 |

## 4. Executor 写回文件

### 4.1 `external_executor/result_pack.json`

生产者：selected executor

消费者：`T5-EXTERNAL-WAIT`、`T7-INGEST`、`T7-AUDIT`

Required fields：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `semantics` | string | `external_executor_result_pack` |
| `run_id` | string | stable id |
| `executor` | string | enum: executor |
| `dry_run` | boolean | `mock_dry_run` 可为 `true`；其他 executor 必须为 `false` |
| `mock_only` | boolean | `mock_dry_run` 可为 `true`；其他 executor 必须为 `false` |
| `evidence_grade` | string | recommended `audited_external` or `external_pending_audit` |
| `baseline_coverage` | object | required |
| `metrics` | array | non-empty |
| `artifacts` | array | non-empty |
| `run_manifest` | string | path to `external_executor/run_manifest.json` |

`baseline_coverage` fields：

| 字段 | 类型 | Required |
| --- | --- | --- |
| `status` | string | yes |
| `required` | array | yes |
| `completed` | array | yes |
| `missing_baselines` | array | yes |

`metrics[]` item：

| 字段 | 类型 | Required |
| --- | --- | --- |
| `metric_id` | string | yes |
| `experiment_id` | string | yes |
| `name` | string | yes |
| `value` | number/string/boolean/object | yes |
| `dataset` | string | recommended |
| `seed` | integer/string/null | recommended |
| `source_artifact` | string | yes |

`artifacts[]` item：

| 字段 | 类型 | Required |
| --- | --- | --- |
| `path` | string | yes |
| `kind` | string | yes |
| `role` | string | yes |
| `sha256` | string | yes |
| `bytes` | integer | recommended |

Validation：

- metrics must be non-empty.
- 每个 metric 必须有 `source_artifact`。
- 每个 `source_artifact` 必须存在于 `artifacts` 或 run manifest artifacts。
- artifact path 必须允许。
- sha256 必须匹配。
- `dry_run=true` 或 `mock_only=true` 仅允许 `mock_dry_run`，其他 executor 必须 reject。
- 只有自然语言 summary 必须 reject。

### 4.2 `external_executor/executor_status.json`

生产者：selected executor

消费者：`T5-EXTERNAL-WAIT`

Required fields：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `semantics` | string | `external_executor_status` |
| `executor` | string | enum: executor |
| `current_state` | string | allowed completion state |
| `accepted` | boolean | must be `false` |
| `dry_run` | boolean | `mock_dry_run` 可为 `true`；其他 executor 必须为 `false` |
| `updated_at` | string | timestamp |

Optional fields：

- `message`
- `error_summary`
- `run_id`
- `invocation_id`

Allowed states:

- `done`
- `COMPLETED`

Rejected by default:

- `PARTIAL_RESULTS_READY`
- `failed`
- `timeout`
- `running`
- `starting`
- `cancelled`

Validation：

- `accepted=true` must reject.
- `PARTIAL_RESULTS_READY` must reject unless explicit future config enables partial results.
- `executor` must belong to executor enum.

### 4.3 `external_executor/run_manifest.json`

生产者：selected executor

消费者：`T5-EXTERNAL-WAIT`、`T7-AUDIT`

Required fields：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `semantics` | string | `external_executor_run_manifest` |
| `executor` | string | enum: executor |
| `run_id` | string | stable id |
| `runs` | array | non-empty |
| `artifacts` | array | non-empty |
| `created_at` | string | timestamp |

`runs[]` item：

| 字段 | 类型 | Required |
| --- | --- | --- |
| `run_id` | string | yes |
| `experiment_id` | string | yes |
| `started_at` | string | recommended |
| `ended_at` | string | recommended |
| `status` | string | yes |
| `config_artifact` | string | yes |
| `raw_result_artifact` | string | yes |
| `log_artifact` | string | yes |

`artifacts[]` item：

| 字段 | 类型 | Required |
| --- | --- | --- |
| `path` | string | yes |
| `kind` | string | yes |
| `role` | string | yes |
| `sha256` | string | yes |
| `bytes` | integer | yes |

Validation：

- runs must be non-empty.
- artifacts must include at least one `raw_results`, one `config`, and one `log`.
- all paths must exist and be allowed.
- all sha256 values must match.

### 4.4 `external_executor/raw_results/*`

生产者：selected executor

消费者：`T5-EXTERNAL-WAIT`、T7 ingest/audit

格式：建议 JSON/JSONL/CSV；MVP 必须 machine-readable。

Contract：

- 文件必须存在。
- 必须被 `result_pack.metrics[].source_artifact` 或 `run_manifest.artifacts[]` 引用。
- 必须登记 sha256。
- 路径必须在 allowed paths 内。

如果是 JSON，建议顶层字段：

| 字段 | 类型 | Required |
| --- | --- | --- |
| `semantics` | string | recommended |
| `run_id` | string | recommended |
| `records` | array/object | yes |

### 4.5 `external_executor/configs/*`

生产者：selected executor

消费者：wait/audit

格式：JSON/YAML/TOML/text allowed；建议 JSON/YAML。

Contract：

- 至少一个 config artifact 必须存在。
- 必须被 `run_manifest.artifacts[]` 引用。
- 必须登记 sha256 和 bytes。
- 应记录 seed、dataset、dependency、command/config 参数。

### 4.6 `external_executor/logs/*`

生产者：selected executor 和 ResearchOS invocation adapter

消费者：wait/audit/debug

Contract：

- 至少一个实验执行 log 必须存在。
- `<executor>_stdout.log` 和 `<executor>_stderr.log` 由 ResearchOS 写入或记录等价路径。
- logs 必须被 run manifest 或 invocation summary 引用。
- logs 可截断，但必须记录 truncation notice。
- logs 不能作为唯一 evidence source。

## 5. Resume 相关读取规则

resume 至少读取：

- `executor_selection.json`
- `executor_invocation.json`
- `executor_invocation_log.jsonl`
- `heartbeat.json`
- `result_pack.json`
- `executor_status.json`
- `run_manifest.json`
- `wait_acceptance_report.json`
- `wait_rejection_report.md`
- optional `wait_rejection_report.json`

Resume validation：

- selection 属于 executor 枚举且 invocation 缺失时，进入 `T5-EXTERNAL-INVOKE`。
- invocation 存在且 heartbeat running 时，不重复启动。
- invocation failed/timeout 但 result/status/manifest 完整时，进入 wait validation。
- wait acceptance 存在时，可进入或继续 `T7-INGEST`。
- wait rejection 存在时，重新运行 validation。

## 6. 最小实现优先级

P0 必须严格实现：

- `expected_outputs_schema.json`
- `executor_selection.json`
- `executor_invocation.json`
- `executor_invocation_log.jsonl`
- `heartbeat.json`
- `result_pack.json`
- `executor_status.json`
- `run_manifest.json`
- `allowed_paths.txt`
- `wait_acceptance_report.json`
- `wait_rejection_report.md`

P1 建议结构化：

- `input_manifest.json`
- `handoff_pack.json`
- `job_state.json`
- `wait_rejection_report.json`
- raw result JSON field recommendation

P2 只做存在性/模板检查：

- `AGENTS.md`
- `codex_prompt.md`
- `README.md`
- arbitrary logs/configs text formats

## 7. 测试清单

Field contract tests:

- 每个 P0 JSON 文件 `semantics` 不匹配时 reject。
- `executor_selection.selected_executor` 不属于 executor 枚举时不进入 EEI。
- `executor_selection.next_state != T5-EXTERNAL-INVOKE` 时 reject。
- `allowed_paths.txt` 中 invalid permission reject。
- `allowed_paths.txt` 中 path traversal reject。
- `executor_invocation.cwd` 不是 workspace 实例目录时 reject。
- `executor_invocation.prompt.injection_mode` 不在枚举时 reject。
- `heartbeat.state` 不在枚举时 reject。
- JSONL event 缺 required fields 时 reject or warning，按实现策略。
- `result_pack.metrics[]` 缺 `source_artifact` 时 reject。
- `executor_status.accepted=true` 时 reject。
- `executor_status.current_state=PARTIAL_RESULTS_READY` 默认 reject。
- `run_manifest.artifacts[]` hash mismatch reject。
- raw/config/log 缺失 reject。
- only natural-language logs without raw result reject。
- wait acceptance 只在 validation 全通过后写出。

## 8. 待确认项

| ID | 待确认项 | 建议 |
| --- | --- | --- |
| A-04 | `wait_rejection_report.json` 是否作为 MVP 必需 sidecar | 建议 P1，便于测试和 CLI |
| A-05 | raw result JSON 是否强制统一字段结构 | MVP 不强制，只要求 machine-readable 和 manifest 引用 |
| A-06 | config 文件是否强制 JSON/YAML | MVP 不强制 |
| A-07 | `status` 是否兼容旧字段 | 新写使用 `current_state`，旧读兼容 `status` |
| A-08 | JSONL unknown event type 是 warning 还是 reject | MVP warning，关键事件缺失再 reject |
