# EEI Schema Version Strategy

本文档基于 `EEI_codex_proposal_v02.md`，汇总 EEI Codex 阶段需要落地的 schema version strategy。目标是在当前阶段就明确哪些 artifact 必须带版本、版本如何命名、validator 如何兼容旧文件、后续 schema 变更如何演进，从而服务后续最终开发文档和实现拆分。

## 1. 目标

Schema version strategy 需要解决以下问题：

- 新写出的 EEI Codex artifacts 必须能被长期识别和校验。
- `T5-EXTERNAL-INVOKE`、`T5-EXTERNAL-WAIT`、`T7-INGEST`、`T7-AUDIT` 能根据版本做确定性 validation。
- 旧 workspace 中暂时没有 `schema_version` 的文件可以有明确兼容策略。
- schema 变更时能区分兼容变更、破坏性变更和需要 migration 的变更。
- rejection report 能明确指出 schema 缺失、版本不支持或字段不合法。

## 2. 适用范围

v0.2 已确认至少以下文件必须加入或保留 `schema_version`：

- `external_executor/result_pack.json`
- `external_executor/executor_status.json`
- `external_executor/run_manifest.json`
- `external_executor/executor_invocation.json`

本阶段建议同步纳入版本策略的文件：

- `external_executor/expected_outputs_schema.json`
- `external_executor/executor_selection.json`
- `external_executor/heartbeat.json`
- `external_executor/wait_acceptance_report.json`
- `external_executor/executor_invocation_log.jsonl`

说明：`executor_invocation_log.jsonl` 是 JSONL event stream，建议每条 event 都带 `schema_version` 和 `event_schema_version`，避免只依赖文件级版本。

## 3. 推荐版本命名

最建议方式：使用独立 artifact schema 版本，初始均为 `"1.0.0"`。

字段名统一为：

```json
{
  "schema_version": "1.0.0"
}
```

推荐语义：

| 版本段 | 含义 | 示例 |
| --- | --- | --- |
| major | 破坏性 schema 变更；旧 validator 不应静默接受 | `1.x.x` -> `2.0.0` |
| minor | 向后兼容新增字段或宽松枚举 | `1.0.0` -> `1.1.0` |
| patch | 文档、错误码说明、非结构性修正 | `1.0.0` -> `1.0.1` |

建议不要把 ResearchOS package version 当作 artifact schema version。代码版本和 artifact 契约版本应分离。

## 4. 每类 Artifact 的版本字段要求

### 4.1 `result_pack.json`

必需顶层字段：

```json
{
  "schema_version": "1.0.0",
  "semantics": "external_executor_result_pack",
  "run_id": "run_x",
  "executor": "codex_cli",
  "dry_run": false,
  "mock_only": false,
  "metrics": [],
  "artifacts": [],
  "run_manifest": "external_executor/run_manifest.json"
}
```

必须校验：

- `schema_version` 存在且为 supported version。
- `semantics=external_executor_result_pack`。
- `executor=codex_cli`。
- `dry_run=false`。
- `mock_only=false`。
- 每个 metric 必须有 `source_artifact`。
- 每个 artifact 必须有 path、kind、role、sha256。
- `run_manifest` 必须指向 `external_executor/run_manifest.json` 或允许路径内的等价 manifest。

### 4.2 `executor_status.json`

必需顶层字段：

```json
{
  "schema_version": "1.0.0",
  "semantics": "external_executor_status",
  "executor": "codex_cli",
  "current_state": "COMPLETED",
  "accepted": false,
  "dry_run": false,
  "mock_only": false,
  "updated_at": "..."
}
```

必须校验：

- `schema_version` 存在且为 supported version。
- `accepted=false`。
- `executor=codex_cli`。
- 完成状态为明确完成状态，例如 `done` / `COMPLETED`。
- 默认不接受 `PARTIAL_RESULTS_READY`。
- `dry_run=false` 且 `mock_only=false`。

待确认：状态字段统一使用 `current_state`、`status`，还是二者兼容。建议新写文件使用 `current_state`，validator 在读取旧文件时兼容 `status`。

### 4.3 `run_manifest.json`

必需顶层字段：

```json
{
  "schema_version": "1.0.0",
  "semantics": "external_executor_run_manifest",
  "executor": "codex_cli",
  "runs": [],
  "artifacts": [],
  "created_at": "..."
}
```

必须校验：

- `schema_version` 存在且为 supported version。
- `runs` 非空。
- `artifacts` 至少覆盖 raw result、config、log。
- 每个 artifact 必须记录 path、kind、sha256、bytes。
- path 必须是规范化 workspace-relative path。
- sha256 必须与磁盘文件匹配。
- artifact path 必须满足 `allowed_paths.txt`。

建议 artifact kind 至少包含：

- `raw_results`
- `config`
- `log`
- `stdout_log`
- `stderr_log`
- `invocation`

### 4.4 `executor_invocation.json`

必需顶层字段：

```json
{
  "schema_version": "1.0.0",
  "semantics": "external_executor_invocation",
  "invocation_id": "...",
  "executor": "codex_cli",
  "command": {},
  "cwd": "workspace/test",
  "prompt": {},
  "policy": {},
  "timing": {},
  "process": {},
  "logs": {},
  "result_presence": {}
}
```

必须校验：

- `schema_version` 存在且为 supported version。
- `semantics=external_executor_invocation`。
- `executor=codex_cli`。
- `invocation_id` 存在且稳定。
- cwd 是本次运行 workspace 实例目录。
- command path 和 argv 必须是脱敏后的记录。
- prompt injection mode 必须记录。
- timeout、network policy、dependency policy、allowed paths enforcement、log redaction policy 必须记录。
- stdout/stderr log path 必须记录。
- exit code、signal、timeout 状态、started_at、ended_at 在进程结束后必须可追踪。

### 4.5 `expected_outputs_schema.json`

建议新增或确认字段：

```json
{
  "schema_version": "1.0.0",
  "semantics": "external_executor_expected_outputs_schema",
  "required_outputs": [],
  "artifact_schema_versions": {
    "result_pack": "1.0.0",
    "executor_status": "1.0.0",
    "run_manifest": "1.0.0"
  }
}
```

作用：

- 告诉 Codex 需要写出哪些 artifact。
- 告诉 wait validator 应按哪些 schema 版本校验。
- 作为 T5-HANDOFF 和 T5-EXTERNAL-WAIT 的共同契约。

待确认：`expected_outputs_schema.json` 是采用完整 JSON Schema draft，还是 ResearchOS 自定义轻量 schema。建议 MVP 先采用 ResearchOS 自定义轻量 schema，后续可映射到 JSON Schema draft。

### 4.6 `heartbeat.json`

建议字段：

```json
{
  "schema_version": "1.0.0",
  "semantics": "external_executor_heartbeat",
  "invocation_id": "...",
  "executor": "codex_cli",
  "state": "running",
  "pid": 12345,
  "started_at": "...",
  "last_heartbeat_at": "...",
  "timeout_seconds": 14400
}
```

必须校验：

- `schema_version` 存在且为 supported version。
- `invocation_id` 与 `executor_invocation.json` 一致。
- state 属于允许枚举。
- stale rule 可根据 `last_heartbeat_at` 计算。

### 4.7 `executor_invocation_log.jsonl`

建议每行 event 结构：

```json
{
  "schema_version": "1.0.0",
  "event_schema_version": "1.0.0",
  "event_id": "...",
  "invocation_id": "...",
  "event_type": "process_started",
  "created_at": "...",
  "payload": {}
}
```

建议 event type：

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

MVP 中 `executor_invocation_log.jsonl` 与 `executor_events.jsonl` 分离。

## 5. 兼容策略

### 5.1 新写文件

新写出的 EEI Codex artifacts 必须带 `schema_version`。

适用文件：

- `result_pack.json`
- `executor_status.json`
- `run_manifest.json`
- `executor_invocation.json`
- `heartbeat.json`
- `wait_acceptance_report.json`
- `executor_invocation_log.jsonl` 每行 event

### 5.2 旧文件读取

MVP validator 可以兼容读取没有 `schema_version` 的旧文件，但必须标记为 legacy。

建议行为：

| 情况 | 行为 |
| --- | --- |
| 新写文件缺失 `schema_version` | reject |
| 旧 workspace 文件缺失 `schema_version`，但结构符合旧契约 | 允许 legacy read，并在 report 中标记 |
| 文件有 unknown major version | reject |
| 文件有 supported major 且较新 minor | warn 或 soft reject，取决于字段是否可安全忽略 |
| 文件有 supported exact version | 正常校验 |
| `semantics` 与文件类型不匹配 | reject |

待确认：如何识别“旧 workspace”。建议依据 workspace metadata、run history 或文件创建阶段；如果无法识别，则 Codex 分支新生成结果缺版本应 reject。

### 5.3 版本升级

建议兼容规则：

- 新增 optional 字段：minor version。
- 新增 required 字段：major version，除非 validator 能提供默认值且不会改变语义。
- 改变字段含义：major version。
- 改变枚举含义：major version。
- 增加枚举值：minor version，但 validator 对未知枚举应有明确策略。
- 修正文档或错误码说明：patch version。

## 6. Validator 落地工作

### 6.1 通用 SchemaVersionValidator

需要新增或抽象一个通用 validator，职责：

- 读取 `schema_version`。
- 校验版本字符串格式。
- 判断是否 supported。
- 判断是否 legacy。
- 将版本校验结果返回给调用方。
- 为 rejection report 提供 error code 和 remediation hint。

建议错误码：

| 错误码 | 含义 |
| --- | --- |
| `SCHEMA_VERSION_MISSING` | 新写文件缺少 `schema_version` |
| `SCHEMA_VERSION_INVALID` | 版本格式非法 |
| `SCHEMA_VERSION_UNSUPPORTED` | major version 不支持 |
| `SCHEMA_VERSION_NEWER_MINOR` | minor version 高于当前 validator |
| `SCHEMA_SEMANTICS_MISMATCH` | `semantics` 与文件类型不匹配 |
| `SCHEMA_LEGACY_COMPAT_READ` | legacy 文件兼容读取 |

### 6.2 Artifact-specific validators

需要为以下文件补齐 schema-aware validation：

- `result_pack.json`
- `executor_status.json`
- `run_manifest.json`
- `executor_invocation.json`
- `heartbeat.json`
- `expected_outputs_schema.json`

每个 validator 至少输出：

- `file`
- `schema_version`
- `semantics`
- `status`
- `errors`
- `warnings`
- `legacy_mode`

### 6.3 Wait validation 集成

`T5-EXTERNAL-WAIT` 在进入内容校验前，应先做 schema version 校验。

顺序建议：

1. 文件存在性。
2. JSON parse。
3. `schema_version` / `semantics` 校验。
4. artifact-specific required fields。
5. allowed paths。
6. sha256。
7. metric source artifacts。
8. executor status / accepted / partial results。

### 6.4 Rejection report 集成

`wait_rejection_report.md` 应包含 schema 相关信息：

- 相关文件。
- expected schema version。
- observed schema version。
- 是否 legacy read。
- error code。
- remediation hint。

示例 remediation：

- “Regenerate `result_pack.json` with `schema_version=1.0.0`.”
- “Use supported major version 1.x.”
- “Set `semantics=external_executor_result_pack`.”

## 7. Handoff 和 Prompt 落地工作

Codex prompt 和 handoff 文件必须明确 schema 要求。

需要更新：

- `external_executor/expected_outputs_schema.json`
- `external_executor/codex_prompt.md`
- `external_executor/AGENTS.md`
- `external_executor/README.md`

这些文件应告诉 Codex：

- 必须写出 `schema_version`。
- 初始版本为 `1.0.0`。
- 每个必需 artifact 的 `semantics` 值。
- `executor_status.json.accepted=false`。
- metrics 必须有 `source_artifact`。
- artifacts 必须有 sha256 和 bytes。
- 路径必须是 workspace-relative path。

## 8. 测试工作

MVP 必测：

- 新写 `result_pack.json` 缺少 `schema_version` 时 reject。
- 新写 `executor_status.json` 缺少 `schema_version` 时 reject。
- 新写 `run_manifest.json` 缺少 `schema_version` 时 reject。
- 新写 `executor_invocation.json` 缺少 `schema_version` 时 reject。
- unknown major version reject。
- invalid version string reject。
- `semantics` mismatch reject。
- exact supported version pass。
- legacy fixture 无版本但结构合法时按 legacy 策略读取并 warning。
- `expected_outputs_schema.json` 声明版本与实际 result/status/manifest 不一致时 reject 或明确 warning。
- `executor_invocation_log.jsonl` 中单行 event 缺少 event schema version 时 warning 或 reject，按最终策略测试。
- `wait_rejection_report.md` 包含 schema error code 和 remediation hint。

建议 future tests：

- newer minor version compatibility test。
- schema migration test。
- old workspace resume compatibility test。
- malformed JSON and schema error ordering test。
- mixed-version artifact set test。

## 9. 待确认内容

形成最终开发文档前，需要确认：

| ID | 待确认内容 | 建议 |
| --- | --- | --- |
| S-01 | 初始版本是否统一为 `1.0.0` | 是 |
| S-02 | 版本字段是否统一命名为 `schema_version` | 是 |
| S-03 | 是否采用 SemVer 语义 | 是 |
| S-04 | `expected_outputs_schema.json` 是否也必须带 `schema_version` | 是 |
| S-05 | `executor_selection.json` 是否纳入版本策略 | 建议纳入，但可低于 result/status/manifest 优先级 |
| S-06 | `heartbeat.json` 是否必须带 `schema_version` | 是 |
| S-07 | `executor_invocation_log.jsonl` 是文件级版本还是 event 级版本 | 建议每行 event 带版本 |
| S-08 | 旧无版本文件的兼容边界如何识别 | 建议只对 legacy workspace 或旧 fixture 开启兼容读取 |
| S-09 | newer minor version 是 warning 还是 reject | MVP 建议 warning，但若 required field 不可理解则 reject |
| S-10 | 是否使用完整 JSON Schema draft | MVP 建议先用 ResearchOS 轻量 schema，后续可映射 |
| S-11 | `executor_status.json` 状态字段统一为 `current_state` 还是兼容 `status` | 新写使用 `current_state`，旧读兼容 `status` |
| S-12 | schema version mismatch 的报告格式 | 建议纳入 `wait_rejection_report.md` 和 structured rejection payload |

## 10. 建议开发拆分

第一阶段：版本常量和 schema registry

- 定义 artifact type。
- 定义 supported schema versions。
- 定义 semantics 常量。
- 定义 schema version parser。
- 定义 schema compatibility policy。

第二阶段：写出端更新

- 更新 handoff generator，使 `expected_outputs_schema.json` 带版本。
- 更新 Codex prompt/AGENTS/README，明确 result/status/manifest schema version。
- 更新 invocation writer，使 `executor_invocation.json` 和 JSONL events 带版本。
- 更新 heartbeat writer，使 `heartbeat.json` 带版本。

第三阶段：读取端 validator

- 实现通用 `SchemaVersionValidator`。
- 实现 result/status/manifest/invocation/heartbeat validators。
- 将 schema validation 接入 `T5-EXTERNAL-WAIT`。
- 将 schema error 写入 rejection report。

第四阶段：测试

- 增加 schema version unit tests。
- 增加 wait validation schema failure tests。
- 增加 legacy compatibility fixture tests。
- 增加 invocation schema tests。

## 11. Traceability

| 需求 | 来源 |
| --- | --- |
| result/status/manifest/invocation 必须带 schema version | `EEI_codex_proposal_v02.md` 3.4 / 6 / 15 / 16 |
| `expected_outputs_schema.json` 是 Codex 必须写回 schema 的输入契约 | `EEI_codex_proposal_v02.md` 3.1 |
| wait 前必须做结构合法性校验 | `EEI_codex_proposal_v02.md` 2.1 / 9 |
| `result_pack.json.semantics=external_executor_result_pack` | `EEI_codex_proposal_v02.md` 3.2 / 9 |
| `executor_status.json.accepted=false` | `EEI_codex_proposal_v02.md` 1 / 9 |
| sha256、source artifact、allowed paths 必须校验 | `EEI_codex_proposal_v02.md` 9 / 10 |
| invocation log 长期可审计 | `EEI_codex_proposal_v02.md` 3.3 / 16 |
