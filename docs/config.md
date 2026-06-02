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

- [config/runtime.yaml](../config/runtime.yaml)
- [config/model_routing.yaml](../config/model_routing.yaml)
- [config/agent_params.yaml](../config/agent_params.yaml)
- [config/state_machine.yaml](../config/state_machine.yaml)
- [config/gates.yaml](../config/gates.yaml)
- [config/mcp.example.yaml](../config/mcp.example.yaml)
- [config/mcp.yaml](../config/mcp.yaml)
- [.env.example](../.env.example)

一句话理解：

- `runtime.yaml`：runtime 全局行为
- `model_routing.yaml`：模型/endpoint/fallback
- `agent_params.yaml`：agent 默认参数
- `state_machine.yaml`：任务图
- `gates.yaml`：human gate 展示与分支
- `mcp*.yaml`：MCP server 描述
- `.env`：密钥和少量环境变量

---

## 2. 参数覆盖优先级

理解这套系统的关键是先搞清楚优先级。

### 2.1 LLM 相关优先级

从高到低大致是：

1. `state_machine.yaml` 中当前 task 的 `llm.*` 覆盖
2. `ExecutionContext.llm_override`（例如 CLI `--profile`）
3. `agent_params.yaml` 中该 agent 的默认 `llm`
4. `model_routing.yaml` 的 `profile + tier`
5. `default_profile`

### 2.2 Budget / 工具 / 路径相关优先级

从高到低：

1. `state_machine.yaml` 中当前节点的 `budget` / `tools`
2. `agent_params.yaml` 中 agent 默认值
3. `runtime.yaml` 中 runtime 共享默认值

### 2.3 环境变量优先级

通常是：

1. 当前 shell 已导出的环境变量
2. `.env`
3. `model_routing.yaml` 中直接写的 `api_keys`
4. 缺省值

注意：

- `.env` 适合放密钥
- `runtime.yaml` 才是运行参数主入口

---

## 3. `config/runtime.yaml`

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

## 4. `config/model_routing.yaml`

文件： [config/model_routing.yaml](../config/model_routing.yaml)

这是模型路由总表。

它不关心任务逻辑，只回答：

- 用哪个 profile
- profile 下的 heavy / medium / light 用哪个模型
- 这些模型走哪个 endpoint
- endpoint 用哪个 provider / key / base URL

### 4.1 结构概览

主要块：

- `api_keys`
- `default_profile`
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

### 4.3 `default_profile`

```yaml
default_profile: default
```

如果 task 和 agent 都没显式指定 profile，就走这里。

### 4.4 `endpoints`

当前典型 endpoint：

- `siliconflow`
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
- endpoint 名字只是 runtime 内部逻辑名

### 4.5 `profiles`

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
        - model: "Pro/MiniMaxAI/MiniMax-M2.5"
          endpoint: siliconflow
          max_context: 128000
```

### 4.6 `truncation`

控制上下文逼近模型上限时的裁剪策略：

- `trigger_ratio`
- `target_ratio`
- `keep_system`
- `keep_recent_turns`

### 4.7 当前使用建议

如果你要：

- 给某一类任务加 fallback：改 `profiles`
- 切 provider：改 `endpoints` 或 profile 中的 `endpoint`
- 切全局默认：改 `default_profile`
- 只切某一任务：优先改 `state_machine.yaml`

---

## 5. `config/agent_params.yaml`

文件： [config/agent_params.yaml](../config/agent_params.yaml)

这是 agent 默认参数的总表，也是日常最常改的文件之一。

### 5.1 它放什么

按 agent 存放：

- LLM 默认值
- 运行预算
- 模型运行参数
- 工具列表
- workspace 读写权限
- `prompt_template`
- `modes` 下的分阶段覆盖
- 各 agent 自定义参数

推荐阅读时把每个 agent 切成 5 个 part：

```yaml
agents:
  writer:
    # Part 1: 模型路由与模型运行参数
    llm:
      profile: siliconflow_only
      tier: heavy
      max_context: 1280000
      temperature: 0.7

    # Part 2: Agent 级默认预算
    max_steps: 1500
    max_tokens_total: 10000000
    max_wall_seconds: 240000
    max_validation_retries: 3

    # Part 3: 工具能力与 workspace 权限
    tool_names: [...]
    allowed_read_prefixes: [...]
    allowed_write_prefixes: [...]

    # Part 4: prompt / schema / task-specific knobs
    prompt_template: writer.j2
    structured_outputs: {...}
    expected_outputs: {...}

    # Part 5: mode 级覆盖
    modes:
      section_draft:
        description: 单章节草稿
        max_steps: 80
        max_tokens_total: 50000000
```

当前 runtime 仍使用扁平字段读取预算和权限，这是为了保持向后兼容。不要把 `max_steps`
移动到 `budget.max_steps` 这类新结构，除非同时修改 `researchos/runtime/agent_params.py`
的加载逻辑和相关测试。最清晰、最安全的做法是：用注释和字段顺序分区，保持字段名稳定。

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

### 5.3 `llm` 子段

一个 agent 的 `llm` 常见字段：

- `profile`
- `tier`
- `model`
- `endpoint`
- `max_context`
- `temperature`

推荐理解方式：

- 想固定 agent 默认走哪条 provider 链：配 `profile`
- 想固定具体模型和 endpoint：配 `model + endpoint`
- 想保留 profile 体系，只声明负载级别：配 `tier`

### 5.4 行为与预算字段

每个 agent 最常用的运行上限：

- `max_steps`
- `max_tokens_total`
- `max_wall_seconds`
- `max_validation_retries`

### 5.5 工具与权限字段

- `tool_names`
- `allowed_read_prefixes`
- `allowed_write_prefixes`

这些会直接进入 `AgentSpec`，并最终决定：

- tool registry 构建哪些工具
- workspace policy 允许读写什么路径

### 5.6 重要的全局块

除了 per-agent 配置，还包含：

- `global_budget`
- `global_timeout`
- `retry_policy`
- `docker`
- `budget_escalation`

其中当前真实接线最重要的是：

- `global_timeout`
- `retry_policy.llm_retries`
- `retry_policy.llm_retry_delay`
- `budget_escalation`

`global_timeout.max_tool_call` 只作为普通工具的小上限。长任务工具会使用专用上限：

- `docker_exec` 使用 `global_timeout.docker_operation`
- `latex_compile` 使用 `global_timeout.latex_compile`

这样 T7 长实验和 T9 TeX 编译不会再被 180 秒的普通工具上限误杀。

### 5.7 当前值得注意的字段

#### `submission.enforce_anonymization_precheck`

当前默认是：

```yaml
enforce_anonymization_precheck: false
```

作用：

- 是否在 T9 开始前就用 pre-hook 拦匿名化问题

#### `submission.max_compile_attempts`

当前用于：

- T9 编译失败后的“诊断-修复-重试”轮数上限

#### `budget_escalation.max_extensions_per_run`

当前设为 `null` 时表示：

- 不限制扩限次数

预算扩限 gate 覆盖 `steps`、`tokens` 和 `wall_seconds`。当 `max_steps`、token 或 wall time 触顶时，runtime 会先询问是否扩限；如果用户选择停止或当前无法继续输入，本轮 run 会保存为 `PAUSED`，后续可以用 `researchos resume --workspace ...` 从已落盘 artifact 继续。

`modes` 中的字段会覆盖 agent 级默认值。例如 `writer.section_draft.max_steps`
只影响单章节写作，不影响 `writer.self_check`。这比复制多个 agent 配置更清晰，也能让
T3.6、T8、T9 这种长流程按阶段设置不同预算。

---

## 6. `config/state_machine.yaml`

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
- `tools.*`

---

## 7. `config/gates.yaml`

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

## 8. `config/mcp.example.yaml` 与 `config/mcp.yaml`

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

## 9. `.env.example` 与 `.env`

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

## 10. Requirements 与安装层配置

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

## 11. 当前哪些字段已经接线，哪些只是部分接线

这是非常重要的一节。

### 11.1 已明确接线

- `runtime.yaml` 中前面列出的共享字段
- `model_routing.yaml` 的 endpoint/profile/fallback/truncation
- `agent_params.yaml` 中 agent 的工具、预算、温度、路径、prompt
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

## 12. 常见修改场景

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
        - model: Pro/MiniMaxAI/MiniMax-M2.5
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

- `agent_params.yaml` 中对应 agent 的 `allowed_*_prefixes`

例子：

```yaml
agents:
  reviewer:
    allowed_read_prefixes:
      - drafts/
      - experiments/
    allowed_write_prefixes:
      - drafts/review_rounds/
```

### 12.6 想让 T9 更严格

改：

- `submission` 的 agent params
- `submission.py` validator
- `submission.j2`

例子：

```yaml
agents:
  submission:
    max_steps: 300
    max_validation_retries: 10
    enforce_anonymization_precheck: true
    max_compile_attempts: 4
```

---

## 13. 推荐配置实践

### 13.1 使用者

推荐只动：

- `.env`
- `runtime.yaml`
- 必要时 `model_routing.yaml`

### 13.2 开发者

推荐按层修改：

- 改 task 行为：`state_machine.yaml`
- 改 agent 默认值：`agent_params.yaml`
- 改 provider/fallback：`model_routing.yaml`
- 改 runtime 公共行为：`runtime.yaml`

---

## 14. 推荐联读

- [docs/runtime.md](./runtime.md)
- [docs/agent_pipeline.md](./agent_pipeline.md)
- [docs/docker.md](./docker.md)
- [docs/dev.md](./dev.md)
- [README.md](../README.md)
- [README.zh-CN.md](../README.zh-CN.md)
