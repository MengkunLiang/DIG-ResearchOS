# ResearchOS Configuration

普通用户只需要认识两个文件：`model_settings.yaml` 用于模型连接、重试与上下文容量，`mcp.yaml` 用于可选 MCP Tool server。`model_settings.yaml` 在首次配置前不存在是正常的，因为它可能保存 API key 并被 Git 忽略；它不是被删除，也不是修改 `model_settings.example.yaml` 才会生效。

首次配置执行：

```bash
python -m researchos.cli configure-llm
```

它会创建 **`config/model_settings.yaml`**。这是日常唯一需要查看的模型文件，包含 `provider`、`api_base`、`api_key`、`model`、`fallback`、`context_window_fallback` 和 `truncation`。也可将 `model_settings.example.yaml` 复制为这个准确文件名；不要把真实 key 写进受版本控制的 example 文件。

手动配置时，真实生效路径是 **`config/model_settings.yaml`**，模板路径是 **`config/model_settings.example.yaml`**。模板本身不会被读取；可执行 `cp config/model_settings.example.yaml config/model_settings.yaml` 创建真实文件，再填写 `provider`、`api_key`、`model`（`openai_compatible` 还需要 `api_base`）。保存后执行 `python -m researchos.cli selftest` 检查连接；使用自定义路径时，附加 `--model-settings /absolute/path/model_settings.yaml`。

同一文件中的 `context_window_fallback: 262144` 表示 provider 无法报告真实容量时采用的**总上下文容量兜底**，覆盖 system prompt、研究材料、历史、Tool 输入/结果和回复预留空间，不是单个输入框的上限。provider 报告真实 `context window` 时会优先使用真实值；`truncation` 是到达容量前的历史压缩阈值。两者通常保留模板默认值，不需要再查看另一个 LLM runtime 配置文件。

顶层保持精简是为了区分用户设置与系统契约：`system_config/` 中的内容没有丢失，它们仍由 runtime 加载，只是不应成为日常模型配置入口。

| 路径 | 修改者 | 用途 |
| --- | --- | --- |
| `model_settings.yaml` | 研究者 | 实际生效的 provider、API URL、API key、model、同模型 retry 与 context-capacity/compaction 设置；由 `configure-llm` 创建，Git 忽略。 |
| `model_settings.example.yaml` | 参考模板 | 安全的本地模型配置模板。 |
| `mcp.yaml` | 研究者，可选 | 可选 MCP server；包含默认关闭、可直接启用的 preset。 |
| `system_config/` | 系统维护者 | Runtime 默认值、Agent capability、状态机、Gate、schema 和写作 profile。 |

## 首次配置

```bash
python -m researchos.cli configure-llm
```

交互式配置会询问 `provider`、`api_base`、`api_key` 和 `model`，然后让你选择将 key 存在本地 `model_settings.yaml`，或存入 `.env` 并在配置中写入 `${PROVIDER_API_KEY}`。已知 provider 有官方 API URL preset；`openai_compatible` 必须填写 URL。`ollama`、`lm_studio`、`vllm` 等本地 preset 通常不需要 key。保存后会立刻进行一次最小连接检查。

所有 Agent 和 Skill 共享这一对 provider/model。`model_settings.yaml` 中的 `fallback` 仅控制 provider 临时故障后的同连接 retry；不会暗中引入 heavy/medium/light profile 或第二条模型链。

## MCP Tool

`config/mcp.yaml` 仍然生效，也是唯一的 MCP 配置文件；原先的 example 已合并为默认关闭的 preset。把某个条目的 `enabled` 改为 `true` 后，ResearchOS 会启动其中的 stdio server、发现 Tool schema、在整个 run 内保持连接，并默认向所有 Agent/Skill 提供这些 Tool。通过 `allowed_agents` 可缩小范围。`--mcp-connector` 只用于非 stdio 的自定义 transport。论文检索、OpenAlex/Semantic Scholar 查询和 PDF 获取是内置能力，不依赖 MCP。

## 配置结构

```text
config/
├── model_settings.example.yaml
├── mcp.yaml
└── system_config/
    ├── runtime.yaml
    ├── agent_params.yaml
    ├── state_machine.yaml
    ├── gates.yaml
    ├── cdr_schema.yaml
    └── venue_writing_profiles.yaml
```

`model_settings.yaml` appears beside the example only after setup. It is intentionally excluded from version control because it can hold an API key. The `.env` file is also supported and is loaded without overriding keys already present in the shell or Docker environment.

## 检查

```bash
python -m researchos.cli configure-llm
python -m researchos.cli selftest
python -m researchos.cli validate-config
python -m researchos.cli doctor
```

`selftest` 检查已配置的 provider connection 和本地依赖；`validate-config` 只验证配置结构，不显示 secret。Docker 会以只读方式挂载同一份 `config/`，因此应在 host 上执行 `configure-llm`，再启动 Docker Mode。
