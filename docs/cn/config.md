# 配置说明

> [中文](../cn/config.md) | [English](../en/config.md)

ResearchOS 只保留一个面向用户的 LLM 配置入口。所有 Agent 和 Skill 使用同一个 provider 与 model；日常使用不再需要理解 heavy/medium/light、per-Agent model routing、token budget 或 fallback model chain。

## 配置 Model

在仓库根目录执行一次：

```bash
python -m researchos.cli configure-llm
```

交互式流程会询问：

| 字段 | 含义 |
| --- | --- |
| `provider` | 使用下方的 provider preset；其他 OpenAI-compatible relay 使用 `openai_compatible`。 |
| `api_base` | 已知 provider 的可选 URL 覆盖；只有 `openai_compatible` 必须填写。留空时已知 provider 使用官方默认地址。 |
| `api_key` | provider credential。 |
| `model` | 全部 workflow 共用的一个 model。 |
| `fallback` | 同一 provider/model 临时失败后的 retry 策略，不是自动换模型的路由链。 |
| `context_window_fallback` | 仅当 provider 无法报告 model 真实容量时使用的总上下文容量（token）；provider metadata 优先。 |
| `truncation` | 达到有效总容量前的历史压缩阈值；通常保留默认值。 |

命令会写入真实生效文件 `config/model_settings.yaml`，让用户选择把 key 存在该本地文件或 `.env`，并立刻发送一个最小请求检查连接。该文件已被 Git 忽略；系统支持时会设置为仅当前用户可读。`context_window_fallback` 和 `truncation` 也会在同一个文件中写入或保留，因此模型连接和上下文设置不需要查看第二个配置文件。

非交互环境也可以显式配置：

```bash
python -m researchos.cli configure-llm \
  --provider deepseek \
  --api-base https://api.deepseek.com \
  --api-key "$DEEPSEEK_API_KEY" \
  --model your-model-name \
  --key-storage env
```

## Provider Preset

`configure-llm` 支持下列公开 provider 名称。`gemini`、`grok`、`kimi`、`dashscope`、`lmstudio`、`nvidia` 等别名会自动映射到对应 preset。model 名称仍由 provider 定义，ResearchOS 不会擅自改写。

| 类别 | Provider preset | 连接方式 |
| --- | --- | --- |
| 主流 API | `openai`、`anthropic`、`openrouter` | 使用官方 endpoint preset；`anthropic` 使用原生 adapter。 |
| 全球 OpenAI-compatible API | `deepseek`、`siliconflow`、`google`、`groq`、`together`、`fireworks`、`mistral`、`cohere`、`xai`、`perplexity`、`cerebras`、`nvidia_nim` | 使用官方 OpenAI-compatible URL preset 与对应环境变量名。 |
| 中国托管 API | `moonshot`、`zhipu`、`qwen`、`minimax` | 使用官方 compatible URL preset 与对应环境变量名。 |
| 本地 runtime | `ollama`、`lm_studio`、`vllm` | 使用本地 URL preset；通常无需 API key。 |
| 其他 gateway | `openai_compatible` | 必须明确填写 `api_base`；未直接填写 `api_key` 时使用 `RESEARCHOS_API_KEY`。 |

## 缺少配置时的交互

`run`、`resume`、`run-task` 与 `run-skill` 在创建 Agent 前发现连接未配置时，会直接停在 Rich 配置卡。若真实 `config/model_settings.yaml` 已存在且字段完整，系统会跳过该向导，不会要求重复填写。若只缺少一个字段，例如已有 `provider`、API URL 和 API key 但未填写 `model`，选择“现在配置”后只会询问 `model`；已有 API key 的环境变量引用会原样保留，不会重新显示、输入或写入配置文件。若主动切换 provider，系统会要求补充该 provider 的 API key 和 model，不会沿用旧 provider 的凭据或 model。文件缺失、模板文件未复制，或 `provider`、`api_key`、`model` 等必要字段缺失时，才会展示以下选择：

1. 现在配置：在终端输入字段并立即检查连接。
2. 自己编辑 `config/model_settings.yaml`：ResearchOS 会展示真实生效路径、模板路径、必填字段和可直接复制的校验命令；修改后让它重新读取并检查。
3. 退出：不改动 workspace。

因此不会等到 T2、T3 或 T4 执行到一半才因为 API 配置失败。连接检查失败时，已经保存的配置会保留，用户只需修正 URL、key、provider 或 model 后再次检查。API key 在输入时不会回显，这既避免 shell history 和录屏泄露，也不表示输入丢失：每一步确认会显示掩码、字符数和末尾校验位，保存后还会显示 key 的保存位置。

## `model_settings.yaml`

真实输出文件是 `config/model_settings.yaml`。同目录的 `config/model_settings.example.yaml` 只是模板，runtime 不会读取它；可先从模板创建：

```bash
cp config/model_settings.example.yaml config/model_settings.yaml
```

```yaml
provider: deepseek
api_base: https://api.deepseek.com
api_key: "${DEEPSEEK_API_KEY}"
model: your-model-name
context_window_fallback: 262144
truncation:
  trigger_ratio: 0.90
  target_ratio: 0.72
fallback:
  max_attempts: 3
  initial_wait_seconds: 3
  max_wait_seconds: 20
  retry_after_timeout: true
```

`api_key` 可以直接填写，也可以使用环境变量占位符。即使留空，runtime 仍会查找 provider 的惯例变量，例如 `DEEPSEEK_API_KEY`。`.env` 会从仓库或当前 project 加载，但不会覆盖 shell 或 Docker 已经传入的环境变量。`openai_compatible` 必须填写准确的 `api_base`；已知 provider 在该字段留空时使用官方 endpoint。`model_settings.example.yaml` 只是示例，runtime 不会读取它；只有同目录的 `model_settings.yaml` 会生效。使用自定义位置时，同样在保存后执行 `python -m researchos.cli selftest --model-settings /absolute/path/model_settings.yaml`。

`fallback` 只针对同一条连接。认证错误、URL 错误和 model 不存在不能靠重试解决，因此会立即提示修正配置；timeout、临时过载等情况会等待后重试。重试耗尽时，workspace 保持可恢复状态，runtime 会走正常的 retry / wait / pause 交互，不会悄悄切换到其他模型。

## 上下文容量兜底

`context_window_fallback: 262144` 与 provider、URL、key、model 一起位于真实生效的 `config/model_settings.yaml`。仅当当前 provider/model 没有通过模型 metadata 报告可核验的真实 context window 时，才会使用该值；provider 报告的、与当前 model 匹配的真实容量优先。

这个数值表示 token 计的**总上下文容量估计**，由 system prompt、研究材料、对话历史、Tool 调用及其结果，以及为模型回复预留的空间共同使用。因此它不是用户单次输入上限，不是固定文件读取大小，也不表示 provider 对外承诺的 API 极限。runtime 会依据有效容量自动计算文件分页、上下文压缩与摘要批处理；同一文件中的 `truncation` 控制何时压缩已保存历史。研究者日常通常保留两者的默认值；只有维护一个无法报告容量、且其总上下文容量已知的 provider/gateway 时，才应修改该兜底值。

## MCP Tool

`config/mcp.yaml` 是唯一的可选 MCP 配置文件。配置好的 stdio server 会在 ResearchOS 启动时自动启动，系统会发现其 Tool schema，并在当前 run 中保持连接。普通 stdio server 不需要 `--mcp-connector`。只有需要限制 server 使用范围时才填写 `allowed_agents`；否则其已发现 Tool 对所有 Agent 和 Skill 可用。

```yaml
servers:
  - name: github
    enabled: true
    transport: stdio
    command: npx
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_TOKEN}"
    allowed_agents: ["scout", "experimenter", "reviewer"]
```

MCP server 的 credential 放在 `.env` 或 shell 中。GitHub preset 会把通用的 `GITHUB_TOKEN` 传给其要求的 `GITHUB_PERSONAL_ACCESS_TOKEN`。`enabled: false` 的 preset 不会读取其环境变量；启用后如果 `${ENV_VAR}` 不存在，系统会在启动 server 前指出缺少的变量。内置 `arxiv_search`、`openalex_search`、`semantic_scholar_search` 和 `fetch_paper_pdf` 已覆盖论文检索与 PDF 获取，不需要另外安装 arXiv MCP server。`--mcp-connector` 仅为非 stdio 的自定义 transport 保留。

## 配置归属

| 路径 | 谁会修改 | 用途 |
| --- | --- | --- |
| `config/model_settings.yaml` | 研究者 | 唯一的 provider/model、retry、上下文容量与压缩配置，本地文件且被 Git 忽略。 |
| `config/mcp.yaml` | 研究者，可选 | 额外 MCP stdio server，启动时自动发现。内置论文检索与 PDF Tool 不依赖它。 |
| `config/system_config/runtime.yaml` | Runtime | workspace、logging、UI、web fetch、LaTeX/Docker 默认值。 |
| `config/system_config/agent_params.yaml` | Runtime | Agent capability、Tool permission、prompt 与机械阅读行为。 |
| `config/system_config/state_machine.yaml` 及相关文件 | Runtime | workflow topology、gate、schema 与 writing profile。 |

`system_config/` 是版本化的系统契约，不是第二个让用户配置模型、修改普通运行限制的入口。`venue_writing_profiles.yaml` 同时保存 venue alias、内部 writing style 建议和模板建议；不再有单独的 `venue_style_map.yaml`，避免两个文件的别名漂移。

## 检查与 Docker

```bash
python -m researchos.cli selftest
python -m researchos.cli validate-config
python -m researchos.cli doctor
```

`selftest` 检查当前 LLM connection 和本地依赖；`validate-config` 只检查配置结构，不输出 secret。Docker 会以只读方式挂载 `config/`，所以运行 `deploy/researchos.sh` 或 `researchos.ps1` 前，应先在 host 上执行 `configure-llm`。

原有 `user_settings.yaml` 与 endpoint/profile routing 在迁移期仍可读取，以免旧部署立即失效；新的配置始终写入 `model_settings.yaml`，不会再生成旧文件。
