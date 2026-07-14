# ResearchOS Configuration

普通用户只需要认识两个文件：`model_settings.yaml` 用于模型连接，`mcp.yaml` 用于可选 MCP Tool server。`model_settings.yaml` 在首次配置前不存在是正常的，因为它可能保存 API key 并被 Git 忽略；它不是被删除，也不是修改 `model_settings.example.yaml` 才会生效。

首次配置执行：

```bash
python -m researchos.cli configure-llm
```

它会创建 **`config/model_settings.yaml`**。这是日常唯一需要修改的模型文件，包含 `provider`、`api_base`、`api_key`、`model` 和 `fallback`。也可将 `model_settings.example.yaml` 复制为这个准确文件名；不要把真实 key 写进受版本控制的 example 文件。

顶层保持精简是为了区分用户设置与系统契约：`system_config/` 中的内容没有丢失，它们仍由 runtime 加载，只是不应成为日常模型配置入口。

| 路径 | 修改者 | 用途 |
| --- | --- | --- |
| `model_settings.yaml` | 研究者 | 实际生效的 provider、API URL、API key、model 和同模型 retry 策略；由 `configure-llm` 创建，Git 忽略。 |
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
    ├── llm_runtime.yaml
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
