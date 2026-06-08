# ResearchOS Configuration Guide

本文件现在是 **唯一的配置总览主文档**。

它已经整合并取代了旧的拆分配置文档。

如果你之前看过旧版本配置说明，现在请以本文件为准。

本文档详细说明 ResearchOS 当前所有关键配置入口：

- `config/*.yaml`
- `.env.example` / `.env`
- provider / endpoint / profile / tier 的覆盖关系
- agent 参数、budget、gate、workspace 路径、MCP、skills roots 等与配置相关的行为

这份文档强调一件事：

**有些字段已经真实接线，有些字段只是声明或部分接线。**

因此本文档不仅解释“字段是什么”，也解释“当前 runtime 是否真正消费了它”。

---

## 1. 配置总览

当前最重要的配置文件：

- [config/user_settings.yaml](../config/user_settings.yaml)
- [config/runtime.yaml](../config/runtime.yaml)
- [config/model_routing.yaml](../config/model_routing.yaml)
- [config/agent_params.yaml](../config/agent_params.yaml)
- [config/state_machine.yaml](../config/state_machine.yaml)
- [config/gates.yaml](../config/gates.yaml)
- [config/mcp.example.yaml](../config/mcp.example.yaml)
- [config/mcp.yaml](../config/mcp.yaml)
- [.env.example](../.env.example)

一句话理解：

- `user_settings.yaml`：唯一的日常参数入口，`llm.*` 管模型，`budget.*` 管预算，`runtime.*` 管 timeout/retry/budget escalation
- `runtime.yaml`：workspace、日志、UI、human interface、web_fetch、Docker 镜像等 runtime 基础行为
- `model_routing.yaml`：endpoint/profile/fallback 候选定义
- `agent_params.yaml`：agent capability registry，包含工具、权限、prompt/schema、behavior、mode 说明
- `state_machine.yaml`：任务图
- `gates.yaml`：human gate 展示与分支
- `mcp*.yaml`：MCP server 描述
- `.env`：密钥和少量环境变量

---

## 2. 参数覆盖优先级

理解这套系统的关键是先搞清楚优先级。

### 2.1 LLM 相关优先级

从高到低大致是：

1. CLI / 上层 runner 写入的 `ExecutionContext.llm_override`（例如 `run-task --profile`）
2. `state_machine.yaml` 中当前 task 的少数 `llm.*` 强覆盖（默认主链不写）
3. `config/user_settings.yaml` 中 `llm.agents.<agent>` 或 `llm.defaults` 的 LLM 设置
4. `config/user_settings.yaml` 中 `llm.default_profile`
5. `model_routing.yaml` 的 `profiles.<profile>.<tier>` 候选链
6. Python fallback（完全禁用 user settings 时兜底为 profile `default`）

重要：`state_machine.yaml` 的 `states.<task>.llm.profile/model/endpoint` 是 task 级强覆盖。它会压过 `user_settings.yaml`，因此除非这个 task 确实需要临时固定模型，否则不要在状态机里写 LLM 参数。日常切模型只改 `user_settings.yaml`。

### 2.2 Budget / 工具 / 路径相关优先级

从高到低：

1. CLI / 上层 runner 写入的临时 budget/tool override
2. `state_machine.yaml` 中当前节点的少数强覆盖（默认主链不写）
3. `config/user_settings.yaml` 中 `budget.agents.<agent>` / `budget.agents.<agent>.modes.<mode>` 的 budget
4. `agent_params.yaml` 中 agent 工具能力和路径权限
5. `runtime.yaml` 中 workspace/UI/logging 等基础运行行为

### 2.3 环境变量优先级

通常是：

1. 当前 shell 已导出的环境变量
2. `.env`
3. `model_routing.yaml` 中直接写的 `api_keys`
4. 缺省值

注意：

- `.env` 适合放密钥
- `user_settings.yaml` 才是运行参数主入口

---

## 3. `config/user_settings.yaml`

文件： [config/user_settings.yaml](../config/user_settings.yaml)

这是普通用户的唯一日常参数入口。它会 overlay 到默认 `agent_params.yaml` 与 `model_routing.yaml`，但不改变状态机拓扑。

主要结构：

- `llm.default_profile`：全局默认 profile
- `llm.endpoints` / `llm.profiles`：少量临时覆盖或扩展路由定义
- `llm.defaults`：所有 agent 的默认 profile、tier、temperature、max_context
- `llm.agents.<agent>`：某个 agent 的 profile、tier、temperature、max_context、model/endpoint
- `llm.agents.<agent>.modes.<mode>`：某个 mode 的 LLM 局部覆盖
- `budget.defaults`：所有 agent 的默认预算设置，例如 `unlimited_budget`
- `budget.agents.<agent>`：某个 agent 的 step/token/wall/validation 预算
- `budget.agents.<agent>.modes.<mode>`：某个 mode 的预算局部覆盖
- `runtime.global_budget`
- `runtime.timeouts`
- `runtime.retry_policy`
- `runtime.budget_escalation`

示例：

```yaml
llm:
  default_profile: deepseek
  defaults:
    profile: deepseek
    tier: medium
  agents:
    scout:
      tier: medium
      temperature: 0.5
      max_context: 1280000
    writer:
      tier: heavy

budget:
  defaults:
    unlimited_budget: true
  agents:
    scout:
      max_steps: 300
      max_tokens_total: 50000000
      max_wall_seconds: 36000
    writer:
      max_steps: 1500
      modes:
        section_draft:
          max_steps: 80
          max_tokens_total: 50000000

runtime:
  timeouts:
    llm_call: 90
    max_tool_call: 180
    docker_operation: 7200
    latex_compile: 1800
  retry_policy:
    llm_retries: 10
    llm_timeout_cooldown_seconds: 60
  budget_escalation:
    enabled: true
    max_extensions_per_run: null
```

兼容说明：

- 旧版 `agents.<agent>` 混合简写仍可读取，但不推荐继续使用
- 日常请按 `llm.*` 和 `budget.*` 分开写，避免同一个参数出现在多个表里
- `max_tokens` 会归一化为 `max_tokens_total`
- `tags: [unlimited_budget]` 与 `unlimited_budget: true` 等价

---

## 4. `config/runtime.yaml`

文件： [config/runtime.yaml](../config/runtime.yaml)

作用：

- 定义 runtime 共享设置

当前已真实接线的字段：

- `workspace.default_root`
- `workspace.runtime_dir`
- `logging.level`
- `logging.json`
- `human_interface.backend`
- `agent_behavior.max_empty_reply`
- `agent_behavior.max_nudge_finish`
- `agent_behavior.max_validation_retries`
- `debug.enable_trace`
- `ui.no_banner`
- `web_fetch.allowed_schemes`
- `web_fetch.allowed_hosts`

### 3.1 `workspace`

```yaml
workspace:
  default_root: "./workspace"
  runtime_dir: "_runtime"
```

含义：

- `default_root`：默认 workspace 根目录
- `runtime_dir`：runtime 私有目录名

影响：

- trace/log/resume 文件都写进 `workspace/<runtime_dir>/`

### 3.2 `logging`

```yaml
logging:
  level: "INFO"
  json: true
```

建议：

- 本地调试：`DEBUG` + `json: false`
- 长期运行：`INFO` + `json: true`

### 3.3 `human_interface`

当前只有：

```yaml
human_interface:
  backend: "cli"
```

含义：

- human gate 当前通过 CLI 呈现

### 3.4 `agent_behavior`

```yaml
agent_behavior:
  max_empty_reply: 2
  max_nudge_finish: 2
  max_validation_retries: 3
```

含义：

- 连续空回复上限
- 只说话不调用工具时的 nudging 上限
- 输出校验失败时的引导重试上限

### 3.5 `debug`

```yaml
debug:
  enable_trace: true
```

作用：

- 是否写 `_runtime/traces/<run_id>.jsonl`

### 3.6 `ui`

```yaml
ui:
  no_banner: false
```

作用：

- 控制启动时是否显示 ASCII banner

### 3.7 `web_fetch`

```yaml
web_fetch:
  allowed_schemes:
    - "http"
    - "https"
  allowed_hosts: []
```

作用：

- 控制 `web_fetch` 工具的 allowlist

---

## 5. `config/model_routing.yaml`

文件： [config/model_routing.yaml](../config/model_routing.yaml)

这是模型路由候选表，不是日常参数入口。

它不关心任务逻辑，只回答：

- 每个 profile 下的 heavy / medium / light 用哪个模型
- 这些模型走哪个 endpoint
- endpoint 用哪个 provider / key / base URL
- primary 失败后按什么 fallback 顺序尝试

### 4.1 结构概览

主要块：

- `api_keys`
- `endpoints`
- `profiles`
- `truncation`

### 4.2 `api_keys`

可以直接写：

```yaml
api_keys:
  SILICONFLOW_API_KEY: ""
  OPENROUTER_API_KEY: ""
```

但更推荐：

- 在 `.env` 里写真实密钥
- 这里保留空值或示例

### 4.3 `endpoints`

当前典型 endpoint：

- `siliconflow`
- `deepseek`
- `openrouter_main`
- `openai_official`
- `anthropic_main`

例如：

```yaml
endpoints:
  siliconflow:
    provider: openai
    api_key_env: SILICONFLOW_API_KEY
    api_base_env: SILICONFLOW_BASE_URL
```

注意：

- SiliconFlow 是 OpenAI-compatible，所以 provider 仍可能是 `openai`
- DeepSeek 官方 OpenAI-compatible API 也使用 `provider: openai`，但应使用 `DEEPSEEK_API_KEY` 和 `DEEPSEEK_BASE_URL`
- endpoint 名字只是 runtime 内部逻辑名
- runtime 不会再把 DeepSeek endpoint 自动回退到 SiliconFlow/OpenAI 的 key/base URL；如果 key 或 base 缺失，错误信息会显示 `endpoint`、`provider`、`model`、`api_base` 和 `api_key` 是否存在

DeepSeek 官方接口推荐写法：

```yaml
api_keys:
  DEEPSEEK_API_KEY: ""
  DEEPSEEK_BASE_URL: "https://api.deepseek.com"

endpoints:
  deepseek:
    provider: openai
    api_key_env: DEEPSEEK_API_KEY
    api_base_env: DEEPSEEK_BASE_URL
```

profile 中模型名写官方 OpenAI-compatible model id 即可，例如：

```yaml
profiles:
  deepseek:
    heavy:
      primary:
        model: "deepseek-v4-pro"
        endpoint: deepseek
        max_context: 128000
      fallback:
        - model: "deepseek-v4-flash"
          endpoint: deepseek
          max_context: 128000
```

ResearchOS 调用 LiteLLM 时会自动补成 `openai/deepseek-v4-pro`。runtime 默认会抑制
LiteLLM 的 INFO 噪音；正常情况下，你应该在 `researchos.log` 中看到的是 `LLM_CALL` /
`LLM_RESULT` 摘要，而不是 `LiteLLM completion() ...`。如果 provider 真正失败，应优先看
ResearchOS 最终错误里打印的 endpoint debug hint，确认 base URL、key、模型名三者匹配。
如果终端仍大量出现 LiteLLM INFO，通常是外部脚本或环境变量重新打开了 LiteLLM debug。

### 4.4 `profiles`

profile 是一套路由策略。

每个 profile 下又按：

- `heavy`
- `medium`
- `light`

拆分。

每档包括：

- `primary`
- `fallback`
- `max_context`

例如：

```yaml
profiles:
  default:
    medium:
      primary:
        model: "deepseek-ai/DeepSeek-V4-Flash"
        endpoint: siliconflow
        max_context: 128000
      fallback:
        - model: "deepseek-ai/DeepSeek-V4-Pro"
          endpoint: siliconflow
          max_context: 128000
```

### 4.5 `truncation`

控制上下文逼近模型上限时的裁剪策略：

- `trigger_ratio`
- `target_ratio`
- `keep_system`
- `keep_recent_turns`

### 4.6 当前使用建议

如果你要：

- 给某一类任务加 fallback：改 `profiles`
- 切 provider：改 `endpoints` 或 profile 中的 `endpoint`
- 切全局默认：改 `config/user_settings.yaml` 的 `llm.default_profile`
- 只切某一任务：优先用 CLI/run-task 临时参数；确需长期固定才写 state_machine 强覆盖

---

## 6. `config/agent_params.yaml`

文件： [config/agent_params.yaml](../config/agent_params.yaml)

这是 agent capability registry，不是日常参数入口。日常的 LLM、budget、timeout、retry、budget escalation 统一写在 [config/user_settings.yaml](../config/user_settings.yaml)。

### 5.1 它放什么

按 agent 存放：

- 工具列表
- workspace 读写权限
- `prompt_template`
- structured outputs / expected outputs
- `modes` 下的阶段说明、prompt、behavior、tools
- 各 agent 的机械行为开关

当前 checked-in 文件主要使用这些分区：

```yaml
agents:
  writer:
    tools:
      tool_names: [...]
      allowed_read_prefixes: [...]
      allowed_write_prefixes: [...]

    prompt:
      prompt_template: writer.j2
      structured_outputs: {...}
      expected_outputs: {...}

    behavior:
      latex_required: true

    modes:
      section_draft:
        description: 单章节草稿
        prompt:
          expected_outputs: {...}
```

这不是只靠注释做“视觉分区”。当前 runtime 已经真实支持 `llm`、`budget`、`tools`、
`prompt`、`behavior` 和 `modes` 分区，并在读取时通过
`researchos/runtime/agent_params.py` 规范化为旧的扁平字段。因此现有代码中
`params.get("max_steps")`、`params.get("tool_names")`、`params.get("prompt_template")`
仍然有效；旧扁平配置也仍可读取，但不再是推荐写法。

注意：兼容读取 `llm`/`budget` 不代表推荐在这里改它们。checked-in 默认配置已把 LLM 与 budget 移到 `user_settings.yaml`，避免同一参数散落在多张表里。

### 5.2 当前主要 agent 段

典型条目：

- `hello`
- `pi`
- `scout`
- `reader`
- `ideation`
- `novelty_auditor`
- `novelty`
- `experimenter`
- `writer`
- `reviewer`
- `submission`

### 5.3 `tools` 分区

- `tool_names`
- `allowed_read_prefixes`
- `allowed_write_prefixes`

这些会直接进入 `AgentSpec`，并最终决定：

- tool registry 构建哪些工具
- workspace policy 允许读写什么路径

### 5.4 `prompt` 与 `behavior` 分区

`prompt` 放 prompt 和输出契约：

- `prompt_template`
- `structured_outputs`
- `expected_outputs`
- `expected_sections`
- `expected_length_min`

`behavior` 放当前 agent/task 的机械行为开关或 validator/preflight 参数：

- `scout.behavior.t2_finalize`
- `scout.behavior.progress`
- `reader.modes.read.behavior.abstract_sweep`
- `experimenter.modes.full.behavior.docker_required`
- `experimenter.modes.full.behavior.gpu_required`
- `submission.behavior.max_compile_attempts`
- `submission.behavior.enforce_anonymization_precheck`

原则是：不依赖学术知识、可机械执行或可验证的运行参数放 `behavior`；需要知识判断、写作策略、审稿标准、idea 推理的内容仍放 prompt/skill guidance，让 LLM 发挥能力。

### 5.4.1 T2/T3 文献流程参数

这些参数已收敛到 `config/agent_params.yaml`，runtime、validator、resume 和工具默认值都从同一处读取。不要在 `state_machine.yaml`、prompt 或代码里再维护第二份相同阈值。
代码中函数签名保留的数值只作配置缺失/损坏时的 fallback；正常 CLI 进程会从本表读取。修改 YAML 后重新运行命令即可生效。

| 参数 | 默认值 | 位置 | 作用 |
| --- | --- | --- | --- |
| `finish_finalize_min_raw` | `30` | `agents.scout.behavior.t2_finalize` | Scout 调用 `finish_task` 后，runtime 至少看到多少 raw 才 deterministic finalize；下限为 10 |
| `active_pool_max` | `120` | `agents.scout.behavior.t2_finalize` | `papers_dedup.jsonl` / `papers_verified.jsonl` active pool 上限 |
| `screened_active_pool_cap` | `60` | `agents.scout.behavior.t2_finalize` | semantic-screened deep-read 候选在 active pool 中的优先保留上限 |
| `bridge_active_pool_cap_per_bridge` | `15` | `agents.scout.behavior.t2_finalize` | 每个 confirmed bridge 在 active pool 中的召回保留上限 |
| `snowball_active_pool_cap` | `12` | `agents.scout.behavior.t2_finalize` | citation snowball 候选在 active pool 中的优先保留上限 |
| `dedup_title_threshold` | `0.95` | `agents.scout.behavior.t2_finalize` | raw、snowball 和最终 active pool 去重时的标题相似度阈值 |
| `access_audit_top_n` | `50` | `agents.scout.behavior.t2_finalize` | `access_audit.md` 展示的 top 论文数量 |
| `metadata_backfill_max_concurrency` | `6` | `agents.scout.behavior.t2_finalize` | OpenAlex/Crossref/title metadata 回填并发上限 |
| `abstract_backfill_title_match_threshold` | `0.88` | `agents.scout.behavior.t2_finalize` | 多源摘要回填的标题匹配阈值 |
| `abstract_backfill_max_concurrency` | `6` | `agents.scout.behavior.t2_finalize` | 多源摘要回填并发上限 |
| `snowball_max_sources` | `12` | `agents.scout.behavior.t2_finalize` | citation snowball 最多从多少个高置信来源扩展 |
| `snowball_refs_per_source` | `8` | `agents.scout.behavior.t2_finalize` | 每个 snowball 来源最多解析多少条引用/相关工作 |
| `snowball_max_candidates` | `40` | `agents.scout.behavior.t2_finalize` | 每轮 OpenAlex/Crossref snowball 最多尝试解析多少个候选 |
| `snowball_max_concurrency` | `6` | `agents.scout.behavior.t2_finalize` | snowball metadata 解析并发上限 |
| `snowball_title_match_threshold` | `0.90` | `agents.scout.behavior.t2_finalize` | Crossref 引用标题转 OpenAlex 候选时的最低标题相似度 |
| `progress.enabled` | `true` | `agents.scout.behavior.progress` | 是否写 `literature/temp/scout_progress.md` |
| `progress.update_on_tool_results` | `true` | `agents.scout.behavior.progress` | 搜索工具自动落盘 raw 后是否同步写 progress |
| `progress.update_on_finalize` | `true` | `agents.scout.behavior.progress` | deterministic finalize 是否同步写开始、active/backlog 切分、完成/失败 |
| `deep_read_min` | `35` | `agents.reader.modes.read.behavior` | T3 validator 最少完成的结构合格 deep-read note 数 |
| `deep_read_target` | `35` | `agents.reader.modes.read.behavior` | T3 目标精读数 |
| `deep_read_max` | `45` | `agents.reader.modes.read.behavior` | active deep-read target 上限 |
| `probe_pool` | `45` | `agents.reader.modes.read.behavior` | T3 优先 probe 的候选池大小 |
| `mainline_screened_cap` | `90` | `agents.reader.modes.read.behavior` | 主线 shallow/screened backlog 保留上限 |
| `bridge_deep_floor` | `3` | `agents.reader.modes.read.behavior` | 每个 must_explore bridge 通过 screen 后的 active deep-read 保底 |
| `bridge_screened_cap` | `7` | `agents.reader.modes.read.behavior` | 每个 bridge 的 shallow/screened backlog 保留上限 |
| `bridge_pool_cap` | `15` | `agents.reader.modes.read.behavior` | 每个 bridge 在 deep-read queue 中保留的候选总上限 |
| `citation_hub_slots` | `3` | `agents.reader.modes.read.behavior` | citation graph 枢纽节点保护槽，仍需 Reader 复核 |

`reader.modes.read.behavior.abstract_sweep` 当前默认是有上限的轻量补读取向：

- `expected_notes_ratio: 1.0` 表示无 `deep_read_queue` 的旧 workspace fallback 也按输入池 100% 校验，不再按 80% 放行。
- `lite_paper_num: 120` 表示每轮最多补 120 篇 abstract-only / metadata-only note，避免 backlog 爆量拖垮 T3。
- `sources: [papers_verified, papers_dedup, papers_backlog]` 表示优先覆盖 active verified/dedup，再从 backlog 中补读尚未覆盖的候选。
- `min_relevance: 0.0` 表示不靠 metadata priority hint 丢弃剩余候选。
- `include_metadata_only: true` 表示缺摘要但有标题的论文也会生成 metadata-only 轻量 note；这类记录不调用 Reader LLM，只走确定性 fallback，并保持 `ABSTRACT_ONLY / abstract_claim_hint` 弱证据标记。
- `exclude_semantic_excluded: true` 表示 LLM screen 为 `shared_keyword_only/unrelated` 或 `can_enter_deep_read=false` 的论文默认不写入 abstract sweep note/BibTeX/comparison table，避免被后续 synthesis/writer 当作可用证据；需要排除线索复核时可显式设为 `false`。

这和 T2 的 active pool/backlog 分层配套：active deep-read 由 T3 精读，active shallow 和一部分 backlog 由 abstract sweep 生成弱证据提示。

### 5.5 当前值得注意的字段

#### `submission.behavior.enforce_anonymization_precheck`

当前默认是：

```yaml
agents:
  submission:
    behavior:
      enforce_anonymization_precheck: false
```

作用：

- 是否在 T9 开始前就用 pre-hook 拦匿名化问题

#### `submission.behavior.max_compile_attempts`

当前用于：

- T9 编译失败后的“诊断-修复-重试”上限
- 该上限按当前 TeX + dependency fingerprint 计数；同一 fingerprint 的源级失败不会无限重编译，必须先修改 TeX 或依赖
- Docker/latexmk 不可用等环境失败不作为 source-level attempt 上限

预算现在见 `user_settings.yaml` 的 `budget.*` 段；timeout、retry 和扩限策略见 `runtime.*` 段。

---

## 7. `config/state_machine.yaml`

文件： [config/state_machine.yaml](../config/state_machine.yaml)

它定义的是 workflow 图，而不是 prompt。

### 6.1 核心字段

顶层：

- `initial_state`
- `states`

每个 state 常见字段：

- `agent` 或 `skill`
- `mode`
- `inputs`
- `outputs`
- `next_on_success`
- `next_on_failure`
- `llm`
- `budget`
- `tools`
- `gate`
- `branches`
- `terminal`

### 6.2 当前最重要的真实节点

- `T1` 到 `T9`
- `T7.5`
- `T8-RESOURCE`
- `T8-WRITE`
- `T8-SECTION-PLAN`
- `T8-SEC-METHOD`
- `T8-SEC-EXPERIMENTS`
- `T8-SEC-RELATED`
- `T8-SEC-ANALYSIS`
- `T8-SEC-INTRO`
- `T8-SEC-CONCLUSION`
- `T8-SEC-ABSTRACT`
- `T8-DRAFT`
- `T8-SELF-CHECK`
- `T8-REVIEW-1`
- `T8-REVISE-1`
- `T8-REVIEW-2`
- `T8-REVISE-2`

### 6.3 当前关键设计

- `T7.5` 已正式进入主链
- `T8` 已拆成多节点
- `T9` 是投稿打包节点

### 6.4 task 级覆盖

如果你只想改某个任务，不想改整个 agent 默认值，优先在这里改：

- `llm.profile`
- `llm.tier`
- `llm.model`
- `llm.endpoint`
- `budget.*`
- `tags`
- `tools.*`

task 节点也支持显式无限预算：

```yaml
states:
  T8-SEC-METHOD:
    agent: writer
    mode: section_draft
    tags: [unlimited_budget]
    budget:
      max_steps: 1
```

上例会保留 `max_steps: 1` 作为可见配置，但当前节点运行时不会因为 step 上限暂停。
也可以写在 `budget` 内：

```yaml
budget:
  unlimited_budget: true
```

task 级 `budget.unlimited_budget: false` 可以覆盖 agent/mode 默认的无限预算。

---

## 8. `config/gates.yaml`

文件： [config/gates.yaml](../config/gates.yaml)

用途：

- 定义 gate 展示内容、选项和附加上下文

### 7.1 当前真实生效的场景

主要用于：

- `T7.5` human review gate
- 其他配置化 gate 展示

### 7.2 需要注意

`gates.yaml` 不是一个通用策略引擎。

也就是说：

- 写了 `type: budget_gate`
- 不等于 runtime 自动理解并执行这个 type 的语义

当前主要还是：

- 展示
- option branch
- 从文件提取字段

---

## 9. `config/mcp.example.yaml` 与 `config/mcp.yaml`

### 8.1 `mcp.example.yaml`

用途：

- 提供 MCP server 配置模板

当前示例包括：

- `arxiv`
- `semantic_scholar`
- `github`

### 8.2 `mcp.yaml`

用途：

- 当前仓库实例化的 MCP 配置

当前已列出：

- `arxiv`
- `semantic_scholar`

### 8.3 重要提醒

MCP 不是“写了 yaml 就自动生效”。

要真正注册为 runtime tool，还需要：

- CLI 启动时提供 connector
- connector 实际创建 client
- `register_mcp_servers()` 完成注册

---

## 10. `.env.example` 与 `.env`

文件： [`.env.example`](../.env.example)

### 9.1 适合放什么

建议只放：

- API keys / tokens
- 研究者邮箱
- 少数必须通过环境变量传递给第三方进程的值

典型字段：

- `SILICONFLOW_API_KEY`
- `SILICONFLOW_BASE_URL`
- `OPENROUTER_API_KEY`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `ANTHROPIC_API_KEY`
- `S2_API_KEY`
- `RESEARCHER_EMAIL`
- `GITHUB_TOKEN`

### 9.2 不建议放什么

不建议把这些主运行参数长期塞在 `.env`：

- `LOG_LEVEL`
- `ENABLE_TRACE`
- `NO_BANNER`
- `web_fetch allowlist`

这些应优先放在 `runtime.yaml`。

### 9.3 推荐流程

```bash
cp .env.example .env
```

然后编辑 `.env`，只填你真实需要的服务。

---

## 11. Requirements 与安装层配置

虽然这些不在 `config/` 目录下，但和配置使用方式高度相关。

### 10.1 `requirements.txt`

核心依赖：

- `httpx`
- `jinja2`
- `jsonschema`
- `pydantic`
- `pyyaml`
- `structlog`
- `pdfplumber`
- `bibtexparser`
- `python-dotenv`

### 10.2 `requirements-llm.txt`

在核心依赖上额外加：

- `litellm`

### 10.3 `requirements-dev.txt`

在核心依赖上额外加：

- `pytest`
- `pytest-asyncio`

### 10.4 `requirements-optional-pdf.txt`

额外加：

- `PyMuPDF`

适合 richer PDF 处理场景。

---

## 12. 当前哪些字段已经接线，哪些只是部分接线

这是非常重要的一节。

### 11.1 已明确接线

- `runtime.yaml` 中前面列出的共享字段
- `model_routing.yaml` 的 endpoint/profile/fallback/truncation
- `agent_params.yaml` 中 agent 的 `llm/budget/tools/prompt/behavior/modes` 分区
- `state_machine.yaml` 的节点跳转、gate、inputs/outputs
- `.env` 中的 provider / API keys

### 11.2 部分接线或保留字段

典型包括：

- `global_budget.stage_allocation`
- `retry_policy.tool_retries`
- 一些 `gates.yaml` 的 type/config 阈值语义
- 不是所有 `agent_params` 附加字段都已有统一执行器

所以：

- 文档里不能把“字段存在”直接等同于“运行时一定使用”
- 最稳的是同时看代码和 `validate-config` / config audit 输出

---

## 13. 常见修改场景

### 12.1 想让某个任务换模型

优先改：

- `state_machine.yaml` 中该任务的 `llm`

例子：

```yaml
T6:
  llm:
    profile: siliconflow_only
    tier: heavy
```

### 12.2 想让整个 agent 默认换模型

优先改：

- `agent_params.yaml`

例子：

```yaml
agents:
  novelty:
    llm:
      profile: siliconflow_only
      tier: medium
      temperature: 0.3
```

### 12.3 想让一整类任务都有 fallback

优先改：

- `model_routing.yaml` 的 profile

例子：

```yaml
profiles:
  siliconflow_only:
    medium:
      primary:
        model: deepseek-ai/DeepSeek-V4-Flash
        endpoint: siliconflow
      fallback:
        - model: deepseek-ai/DeepSeek-V4-Pro
          endpoint: siliconflow
```

### 12.4 想关掉横幅或开关 trace

改：

- `runtime.yaml`

例子：

```yaml
ui:
  no_banner: true

debug:
  enable_trace: true
```

### 12.5 想限制 tool 读写范围

改：

- `agent_params.yaml` 中对应 agent 的 `tools.allowed_*_prefixes`

例子：

```yaml
agents:
  reviewer:
    tools:
      allowed_read_prefixes:
        - drafts/
        - experiments/
      allowed_write_prefixes:
        - drafts/review_rounds/
```

### 12.6 想让 T9 更严格

日常先改 `user_settings.yaml` 中 submission 的预算；如果要改校验规则或机械行为，再改 agent/validator/prompt：

- `config/user_settings.yaml: budget.agents.submission`
- `submission.py` validator
- `submission.j2`
- 必要时 `agent_params.yaml` 的 `submission.behavior`

例子：

```yaml
budget:
  agents:
    submission:
      max_steps: 300
      max_validation_retries: 10
      max_tokens_total: 500000000
```

---

## 14. 推荐配置实践

### 13.1 使用者

推荐只动：

- `.env`
- `config/user_settings.yaml`
- 偶尔 `runtime.yaml`（workspace、日志、UI、web_fetch、Docker 镜像等基础行为）

### 13.2 开发者

推荐按层修改：

- 改 task 行为：`state_machine.yaml`
- 改日常模型和预算：`user_settings.yaml` 的 `llm.*` 与 `budget.*`
- 改 agent 工具、权限、prompt/schema、behavior：`agent_params.yaml`
- 改 provider/fallback：`model_routing.yaml`
- 改 runtime 公共行为：`runtime.yaml`

---

## 15. 推荐联读

- [docs/runtime.md](./runtime.md)
- [docs/agent_pipeline.md](./agent_pipeline.md)
- [docs/docker.md](./docker.md)
- [docs/dev.md](./dev.md)
- [README.md](../README.md)
- [README.zh-CN.md](../README.zh-CN.md)
