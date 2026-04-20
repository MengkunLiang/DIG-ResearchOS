# ResearchOS 配置系统

本目录包含 ResearchOS 的所有配置文件。配置系统采用分层设计，将不同关注点分离到独立的配置文件中。

## 配置文件概览

| 文件 | 用途 | 必需 | 说明 |
|------|------|------|------|
| `runtime.yaml` | Runtime 核心配置 | ✅ | 工作空间、日志、Agent 行为、Docker 执行模式 |
| `model_routing.yaml` | LLM 模型路由 | ✅ | API 端点、模型选择、上下文截断策略 |
| `state_machine.yaml` | 状态机定义 | ✅ | Agent 工作流、状态转换、输入输出映射 |
| `mcp.yaml` | MCP 工具配置 | ❌ | 外部工具和数据源（arXiv, Semantic Scholar 等）|
| `gates.yaml` | Gate 配置 | ❌ | 状态转换的条件检查和验证 |

## 快速开始

### 1. 环境变量配置

首先配置环境变量（API Key 等敏感信息）：

```bash
# 复制环境变量模板
cp ../.env.example ../.env

# 编辑并填入你的 API Key
nano ../.env
```

最少需要配置：
- `OPENAI_API_KEY`: OpenAI API Key（必需）
- `OPENAI_BASE_URL`: API 端点 URL（可选，默认为 OpenAI 官方）

### 2. 基础配置

默认配置已经可以直接使用，适合快速开始：

```bash
# 使用默认配置运行
python -m researchos.cli run
```

### 3. 自定义配置

根据需要修改配置文件：

```bash
# 修改 Runtime 配置
nano config/runtime.yaml

# 修改模型路由配置
nano config/model_routing.yaml

# 修改工作流配置
nano config/state_machine.yaml
```

## 配置文件详解

### runtime.yaml - Runtime 核心配置

控制 ResearchOS 运行时的核心行为。

**主要配置项：**

- **workspace**: 工作空间配置
  - `default_root`: 工作空间根目录（默认：`./workspace`）
  - `runtime_dir`: Runtime 私有目录（默认：`_runtime`）

- **logging**: 日志配置
  - `level`: 日志级别（`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`）
  - `json`: 是否使用 JSON 格式（`true`/`false`）

- **agent_behavior**: Agent 行为控制
  - `max_empty_reply`: 最大空回复次数（默认：2）
  - `max_nudge_finish`: 最大推动完成次数（默认：2）
  - `max_validation_retries`: 验证重试次数（默认：3）

- **execution**: Docker 执行模式
  - `mode`: 执行模式（`auto`, `docker`, `container-native`）
  - `detect_container`: 是否自动检测容器环境（`true`/`false`）

**使用场景：**

```yaml
# 开发环境：详细日志 + 文本格式
logging:
  level: "DEBUG"
  json: false

# 生产环境：INFO 日志 + JSON 格式
logging:
  level: "INFO"
  json: true
```

### model_routing.yaml - LLM 模型路由

定义 LLM 服务端点和模型选择策略。

**主要配置项：**

- **endpoints**: API 端点定义
  - `provider`: 服务提供商（当前支持 `openai`）
  - `api_key_env`: API Key 环境变量名
  - `api_base_env`: Base URL 环境变量名

- **profiles**: 模型配置文件
  - `heavy`: 重负载任务（文献综合、深度分析）
  - `medium`: 中等负载任务（文献检索、信息提取）
  - `light`: 轻负载任务（简单查询、格式转换）

- **truncation**: 上下文截断策略
  - `trigger_ratio`: 触发截断的阈值（默认：0.8）
  - `target_ratio`: 截断后保留比例（默认：0.6）
  - `keep_system`: 是否保留系统消息（默认：`true`）
  - `keep_recent_turns`: 保留最近轮数（默认：10）

**使用场景：**

```yaml
# 成本优化：大部分任务使用 gpt-3.5-turbo
profiles:
  default:
    heavy:
      primary:
        model: "gpt-4"
        max_context: 32000
    medium:
      primary:
        model: "gpt-3.5-turbo"
        max_context: 16000
    light:
      primary:
        model: "gpt-3.5-turbo"
        max_context: 4000

# 性能优先：所有任务使用 gpt-4
profiles:
  performance:
    heavy:
      primary:
        model: "gpt-4"
    medium:
      primary:
        model: "gpt-4"
    light:
      primary:
        model: "gpt-4"
```

### state_machine.yaml - 状态机定义

定义 Agent 工作流的状态转换和数据流。

**主要配置项：**

- **initial_state**: 初始状态（工作流入口）
  - `HELLO`: 调试用的简单 workflow
  - `T1`: 完整研究流程的起点

- **states**: 状态定义
  - `agent`: 使用的 Agent 名称
  - `mode`: Agent 模式（可选）
  - `inputs`: 输入文件映射
  - `outputs`: 输出文件映射
  - `next_on_success`: 成功后的下一状态
  - `next_on_failure`: 失败后的下一状态
  - `terminal`: 是否为终止状态

**工作流说明：**

1. **HELLO Workflow**（调试用）
   ```
   HELLO → done/failed
   ```

2. **T1-T4 完整研究 Workflow**
   ```
   T1 (项目初始化)
   → T2 (文献检索)
   → T3 (深度阅读)
   → T3.5 (文献综合)
   → T4 (假设生成)
   → T4.5 (新颖性审计)
   → T6 (实验执行)
   → done/failed
   ```

**使用场景：**

```yaml
# 调试：只运行 HELLO
initial_state: HELLO

# 完整流程：从 T1 开始
initial_state: T1

# 从中间状态开始（需要准备好输入文件）
initial_state: T3
```

### mcp.yaml - MCP 工具配置

配置外部工具和数据源（Model Context Protocol）。

**支持的 MCP 服务器：**

1. **arxiv** - arXiv 论文搜索和下载
   - 依赖：Node.js, npx
   - 无需 API Key
   - 推荐启用

2. **semantic_scholar** - Semantic Scholar 学术搜索
   - 依赖：Python, `semantic_scholar_mcp` 包
   - 需要 API Key（`S2_API_KEY`）
   - 可选

3. **github** - GitHub 仓库和代码搜索
   - 依赖：Node.js, npx
   - 需要 Personal Access Token（`GITHUB_TOKEN`）
   - 可选

**使用场景：**

```yaml
# 最小配置：只启用 arxiv
servers:
  - name: arxiv
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-arxiv"]

# 完整配置：启用所有服务器
servers:
  - name: arxiv
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-arxiv"]
  
  - name: semantic_scholar
    command: "python"
    args: ["-m", "semantic_scholar_mcp"]
    env:
      S2_API_KEY: "${S2_API_KEY}"
  
  - name: github
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"
```

### gates.yaml - Gate 配置

定义状态转换时的条件检查和验证规则。

**当前状态：** 空配置（无 Gate 限制）

**未来支持的 Gate 类型：**

- `budget_check`: 预算检查（成本控制）
- `quality_check`: 输出质量验证
- `time_limit`: 时间限制
- `approval_required`: 需要人工审批

**使用场景：**

```yaml
# 预算控制
gates:
  budget_gate:
    type: budget_check
    config:
      max_cost: 10.0         # 最大成本（美元）
      warn_threshold: 0.8    # 警告阈值（80%）

# 输出验证
gates:
  quality_gate:
    type: output_validation
    config:
      min_length: 100        # 最小输出长度
      required_fields:       # 必需字段
        - title
        - summary
```

## 配置优先级

ResearchOS 按以下优先级读取配置：

1. **环境变量**（最高优先级）
   ```bash
   export LOG_LEVEL=DEBUG
   python -m researchos.cli run
   ```

2. **`.env` 文件**（中等优先级）
   ```bash
   # .env
   LOG_LEVEL=DEBUG
   ```

3. **配置文件**（最低优先级）
   ```yaml
   # runtime.yaml
   logging:
     level: "INFO"
   ```

## 配置验证

ResearchOS 提供配置验证工具：

```bash
# 验证所有配置文件
python -m researchos.cli validate-config

# 查看当前生效的配置
python -m researchos.cli show-config

# 查看特定配置文件
python -m researchos.cli show-config --file runtime.yaml
```

## 常见配置场景

### 场景 1：本地开发

```yaml
# runtime.yaml
logging:
  level: "DEBUG"
  json: false

debug:
  enable_trace: true

execution:
  mode: "auto"
```

```bash
# .env
OPENAI_API_KEY=sk-xxxxx
OPENAI_BASE_URL=https://api.openai.com/v1
```

### 场景 2：生产部署

```yaml
# runtime.yaml
logging:
  level: "INFO"
  json: true

debug:
  enable_trace: false

execution:
  mode: "docker"
```

```bash
# 使用系统环境变量
export OPENAI_API_KEY=sk-xxxxx
export OPENAI_BASE_URL=https://api.openai.com/v1
```

### 场景 3：成本优化

```yaml
# model_routing.yaml
profiles:
  cost_optimized:
    heavy:
      primary:
        model: "gpt-4"
        max_context: 32000    # 限制上下文
    medium:
      primary:
        model: "gpt-3.5-turbo"
    light:
      primary:
        model: "gpt-3.5-turbo"

truncation:
  trigger_ratio: 0.7          # 更早触发截断
  target_ratio: 0.5           # 更激进的截断
```

### 场景 4：使用第三方 API

```bash
# .env
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://your-service.com/v1
```

```yaml
# model_routing.yaml
endpoints:
  relay:
    provider: openai
    api_key_env: OPENAI_API_KEY
    api_base_env: OPENAI_BASE_URL
```

## 故障排查

### 问题 1：配置不生效

**症状：** 修改配置后没有变化

**排查步骤：**
1. 检查配置文件路径是否正确
2. 验证 YAML 语法（使用 `yamllint` 或在线工具）
3. 确认环境变量是否覆盖了配置文件
4. 查看日志中的配置加载信息

```bash
# 验证 YAML 语法
python -c "import yaml; yaml.safe_load(open('config/runtime.yaml'))"

# 查看生效的配置
python -m researchos.cli show-config
```

### 问题 2：API 调用失败

**症状：** LLM 调用返回错误

**排查步骤：**
1. 验证 API Key 是否正确
2. 检查 Base URL 是否可访问
3. 确认模型名称是否正确
4. 查看 API 服务商的状态页面

```bash
# 测试 API 连接
curl -H "Authorization: Bearer $OPENAI_API_KEY" \
     $OPENAI_BASE_URL/models

# 查看详细错误日志
LOG_LEVEL=DEBUG python -m researchos.cli run
```

### 问题 3：MCP 工具不可用

**症状：** Agent 无法使用外部工具

**排查步骤：**
1. 检查 MCP 服务器是否正确配置
2. 验证依赖是否已安装（Node.js, Python 包）
3. 确认环境变量是否设置（API Key, Token）
4. 查看 MCP 服务器日志

```bash
# 测试 arxiv MCP 服务器
npx -y @modelcontextprotocol/server-arxiv

# 测试 Semantic Scholar MCP 服务器
python -m semantic_scholar_mcp
```

## 最佳实践

### 1. 环境隔离

为不同环境使用不同的配置：

```bash
# 开发环境
cp .env.example .env.dev
ln -sf .env.dev .env

# 生产环境
cp .env.example .env.prod
# 编辑 .env.prod
```

### 2. 版本控制

- ✅ 提交：`*.yaml` 配置文件、`.env.example`
- ❌ 不提交：`.env` 文件（包含敏感信息）

```bash
# .gitignore
.env
.env.local
*.env.local
```

### 3. 配置文档化

在配置文件中添加详细注释：

```yaml
# 好的实践：说明配置项的作用和可选值
logging:
  level: "INFO"    # DEBUG, INFO, WARNING, ERROR, CRITICAL

# 不好的实践：没有注释
logging:
  level: "INFO"
```

### 4. 配置验证

在部署前验证配置：

```bash
# CI/CD 流程中添加配置验证
python -m researchos.cli validate-config
if [ $? -ne 0 ]; then
  echo "配置验证失败"
  exit 1
fi
```

### 5. 敏感信息管理

- 开发环境：使用 `.env` 文件
- 生产环境：使用密钥管理服务（AWS Secrets Manager, HashiCorp Vault 等）
- CI/CD：使用 CI 平台的密钥管理功能

## 进一步阅读

- [配置管理详细指南](../docs/configuration.md)
- [Docker 使用指南](../docs/docker-usage.md)
- [Agent 开发指南](../docs/AGENT_DEVELOPMENT_GUIDE.md)
- [README 中文版](../README.zh-CN.md)

## 获取帮助

如果遇到配置问题：

1. 查看本文档的"故障排查"部分
2. 查看详细的配置文档：`docs/configuration.md`
3. 查看日志文件：`workspace/_runtime/logs/`
4. 提交 Issue：https://github.com/MengkunLiang/DIG-ResearchOS/issues
