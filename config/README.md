# ResearchOS 配置系统

本目录按“单一参数所有权”组织。普通使用只需要改 `user_settings.yaml` 和 `.env`；其他 YAML 是能力、路由或拓扑表，不要把同一个参数在多张表里重复配置。

## 文件职责

| 文件 | 职责 | 日常是否修改 |
| --- | --- | --- |
| `user_settings.yaml` | `llm.*` 模型参数、`budget.*` 预算参数、`runtime.*` timeout/retry/budget escalation | 是，日常参数唯一入口 |
| `runtime.yaml` | workspace、日志、UI、human interface、web_fetch allowlist、Docker 镜像与执行环境基础设置 | 偶尔 |
| `model_routing.yaml` | endpoint、profile、primary/fallback 候选链、上下文截断策略 | 只在新增 provider/model/fallback 时改 |
| `agent_params.yaml` | agent capability registry：工具、读写权限、prompt/schema、behavior、mode 说明 | 开发 agent/tool 时改 |
| `state_machine.yaml` | 状态机拓扑、输入输出、gate、分支、节点 extra | 改流程时改 |
| `gates.yaml` | human gate 展示、选项和附加上下文 | 改 gate 时改 |
| `mcp.yaml` / `mcp.example.yaml` | MCP server 描述 | 需要 MCP 时改 |

## 快速开始

1. 配置密钥：

```bash
cp ../.env.example ../.env
nano ../.env
```

常用变量包括 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`SILICONFLOW_API_KEY`、`SILICONFLOW_BASE_URL`、`OPENROUTER_API_KEY`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`ANTHROPIC_API_KEY`、`S2_API_KEY`、`ELSEVIER_API_KEY`、`RESEARCHER_EMAIL`。

2. 日常切模型、预算或超时：

```bash
nano config/user_settings.yaml
```

3. 验证配置：

```bash
python -m researchos.cli validate-config
```

## 参数所有权

| 参数类型 | 唯一日常入口 | 说明 |
| --- | --- | --- |
| 默认 profile | `user_settings.yaml: llm.default_profile` | 例如 `deepseek`、`siliconflow_only`、`mixed` |
| Agent 模型档位 | `user_settings.yaml: llm.agents.<agent>.tier` | `heavy` / `medium` / `light` |
| Agent 直接模型绑定 | `user_settings.yaml: llm.agents.<agent>.model + endpoint` | 会绕过 profile fallback，只在确实需要固定模型时用 |
| Agent temperature/max_context | `user_settings.yaml: llm.agents.<agent>.*` | mode 内可单独覆盖 |
| Agent step/token/wall budget | `user_settings.yaml: budget.agents.<agent>.*` | 支持 `unlimited_budget: true` |
| LLM timeout/retry/cooldown | `user_settings.yaml: runtime.timeouts` 和 `runtime.retry_policy` | 包含 provider 连续超时后的 cooldown |
| budget escalation | `user_settings.yaml: runtime.budget_escalation` | 触顶后是否 ask_human 扩限 |
| CLI quiet/verbose 默认值 | `runtime.yaml: ui.quiet/ui.verbose` | 只影响终端展示，不影响 `researchos.log` / trace |
| 工具列表和读写权限 | `agent_params.yaml` | 能力声明，不是预算表 |
| endpoint/API base/API key env | `model_routing.yaml` + `.env` | 路由候选定义与密钥分离 |
| 状态机输入输出和分支 | `state_machine.yaml` | 默认不要在这里写 LLM/budget 参数 |

## `user_settings.yaml`

这是普通用户最该看的文件。默认配置采用分表写法，避免同一个 agent 块里混入模型和预算：

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
    llm_retry_delay: 1
    llm_timeout_cooldown_seconds: 60
    llm_timeout_pause_after_cooldowns: 0
  budget_escalation:
    enabled: true
    max_extensions_per_run: null
```

兼容层仍能读取旧的 `agents.<agent>` 混合简写，但 checked-in 默认配置不再使用。日常使用请按 `llm.*` 和 `budget.*` 分开写；`max_tokens` 会被归一化为 `max_tokens_total`，`tags: [unlimited_budget]` 与 `unlimited_budget: true` 等价。

## `model_routing.yaml`

这里只定义可选的模型路由候选：

```yaml
endpoints:
  deepseek:
    provider: openai
    api_key_env: DEEPSEEK_API_KEY
    api_base_env: DEEPSEEK_BASE_URL

profiles:
  deepseek:
    heavy:
      primary:
        model: deepseek-v4-pro
        endpoint: deepseek
        max_context: 128000
      fallback:
        - model: deepseek-v4-flash
          endpoint: deepseek
          max_context: 128000
```

切全局默认不要改这里，改 `user_settings.yaml: llm.default_profile`。新增 provider、fallback 或上下文窗口候选时才改这里。

## `agent_params.yaml`

这是能力注册表，主要放：

- `tools.tool_names`
- `tools.allowed_read_prefixes`
- `tools.allowed_write_prefixes`
- `prompt.prompt_template`
- `prompt.structured_outputs`
- `prompt.expected_outputs`
- `behavior.*`
- `modes.<mode>.description/prompt/behavior/tools`

兼容层仍能读取旧的 `llm` / `budget` 字段，但 checked-in 默认配置不再把它们放这里。不要把日常模型和预算参数写回 `agent_params.yaml`，否则又会出现多表参数冲突。

当前 Reader 的 `modes.read.behavior.abstract_sweep` 默认用于覆盖 T3 deep read 后尚未读完的 verified 论文：

- `expected_notes_ratio: 1.0` 是无 queue 旧 workspace 的 fallback 比例，表示输入池默认必须 100% 有笔记；新主流程仍优先用 `deep_read_queue` 区分 active deep-read 和 shallow/backlog。
- `lite_paper_num: null` 表示不设固定 40 篇上限，尽量覆盖所有剩余候选。
- `min_relevance: 0.0` 表示不靠 metadata priority hint 丢弃候选。
- `include_metadata_only: true` 表示缺摘要但有标题的论文也会生成 metadata-only 轻量 note。
- `exclude_semantic_excluded: false` 表示 `shared_keyword_only/unrelated` 也会保留为排除线索，而不是静默消失。

这组参数只控制机械覆盖行为；论文是否能作为学术证据仍由 Reader/Writer 的 LLM 判断和 evidence level 控制。

### 日志与控制台

- `runtime.yaml: ui.quiet=true`：控制台只显示状态跳转、暂停、错误和最终结果
- `runtime.yaml: ui.verbose=true`：控制台额外显示 Agent 文本输出和 step token 摘要
- `workspace/<name>/_runtime/logs/researchos.log`：统一人类时间线，由 runtime 写入，不受 quiet 影响
- `workspace/<name>/_runtime/logs/researchos-debug.log`：底层 Python/structlog 调试日志
- `workspace/<name>/_runtime/traces/*.jsonl`：机器级完整 trace

LiteLLM INFO 默认被 runtime 压到 WARNING；正常情况下不会刷屏，也不会进入 `researchos.log`。

## `state_machine.yaml`

这里定义 workflow，不是参数表。默认主链不应写 `llm` / `budget`。只有临时调试或确实需要固定某个 task 时，才使用 task 级强覆盖：

```yaml
states:
  HELLO:
    agent: hello
    llm:
      profile: hello_fast
```

如果 `user_settings.yaml` 改了 profile 但运行不生效，优先检查 `validate-config` 输出里的 `state_machine_llm_overrides`。

## 验证与排查

```bash
python -m researchos.cli validate-config
python -m researchos.cli selftest --profile deepseek
```

常见问题：

- 模型没有切过去：检查 `state_machine_llm_overrides` 是否有 task 级强覆盖。
- DeepSeek OpenAI-compatible 调用失败：确认 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 和 profile 中的模型名匹配。
- 预算仍然触顶：检查 `user_settings.yaml: budget.defaults` 或 `budget.agents.<agent>` 是否设置 `unlimited_budget: true`，以及 `runtime.budget_escalation.enabled` 是否开启。
- T9/Docker 超时：检查 `runtime.timeouts.docker_operation` 与 `runtime.timeouts.latex_compile`，普通 `max_tool_call` 不应承担长实验或 TeX 编译上限。

更多细节见 [docs/config.md](../docs/config.md)。
