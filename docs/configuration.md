# ResearchOS 配置管理指南

本文档说明 ResearchOS 的配置系统，包括配置文件位置、作用、示例和最佳实践。

## 目录

- [配置文件概览](#配置文件概览)
- [环境变量配置](#环境变量配置)
- [Runtime 配置](#runtime-配置)
- [模型路由配置](#模型路由配置)
- [状态机配置](#状态机配置)
- [MCP 工具配置](#mcp-工具配置)
- [Gate 配置](#gate-配置)
- [配置最佳实践](#配置最佳实践)
- [配置验证](#配置验证)
- [故障排查](#故障排查)

## 配置文件概览

ResearchOS 使用多个配置文件来管理不同方面的行为：

| 配置文件 | 位置 | 作用 | 必需 |
|---------|------|------|------|
| `.env` | 项目根目录 | 环境变量（API密钥等敏感信息） | 是 |
| `runtime.yaml` | `config/` | Runtime 核心行为配置 | 是 |
| `model_routing.yaml` | `config/` | LLM 模型路由和负载配置 | 是 |
| `state_machine.yaml` | `config/` | Agent 状态机定义 | 是 |
| `mcp.yaml` | `config/` | MCP 工具配置 | 否 |
| `gates.yaml` | `config/` | Gate 配置 | 否 |

**配置文件关系图：**

```
.env (环境变量)
  ↓
config/
  ├── runtime.yaml (核心配置)
  ├── model_routing.yaml (模型路由) ← 读取 .env 中的 API Key
  ├── state_machine.yaml (工作流定义)
  ├── mcp.yaml (外部工具) ← 读取 .env 中的 API Key
  └── gates.yaml (条件检查)
```

## 环境变量配置

### `.env` 文件

存储敏感信息和环境特定配置。**此文件不应提交到版本控制系统。**

#### 示例

```bash
# OpenAI API 配置
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxx
OPENAI_BASE_URL=https://api.openai.com/v1
```

#### 使用第三方 OpenAI 兼容服务

```bash
# 示例：使用其他 OpenAI 兼容服务
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://your-service.com/v1
```

#### 创建 `.env` 文件

```bash
# 从模板复制
cp .env.example .env

# 编辑并填入你的 API Key
nano .env
```

### 环境变量优先级

ResearchOS 按以下优先级读取配置：

1. **系统环境变量**（最高优先级）
   ```bash
   export OPENAI_API_KEY=sk-xxxxx
   python -m researchos.cli run
   ```

2. **`.env` 文件**（中等优先级）
   - 项目根目录的 `.env` 文件

3. **配置文件默认值**（最低优先级）
   - `config/*.yaml` 中定义的默认值

**推荐做法**：
- 开发环境：使用 `.env` 文件
- 生产环境：使用系统环境变量或密钥管理服务
- CI/CD：使用 CI 平台的密钥管理功能

## Runtime 配置

### `config/runtime.yaml`

定义 ResearchOS 运行时的核心行为。

#### 完整示例

```yaml
# Workspace 工作空间配置
workspace:
  default_root: "./workspace"  # 工作空间根目录
  runtime_dir: "_runtime"      # Runtime 私有目录

# 日志配置
logging:
  level: "INFO"    # DEBUG, INFO, WARNING, ERROR, CRITICAL
  json: true       # true: JSON格式, false: 文本格式

# 人机接口
human_interface:
  backend: "cli"   # 当前仅支持 cli

# Agent 行为
agent_behavior:
  max_empty_reply: 2           # 最大空回复次数
  max_nudge_finish: 2          # 最大推动完成次数
  max_validation_retries: 3    # 输出验证重试次数

# 调试
debug:
  enable_trace: true  # 启用详细执行追踪

# Docker 执行模式
execution:
  mode: "auto"              # auto, docker, container-native
  detect_container: true    # 自动检测容器环境

# Docker 镜像
docker:
  default_image: "researchos/system:latest"
  build_context: "infra/docker"
```

#### 配置项说明

##### Workspace 配置

- **`default_root`**: 工作空间根目录
  - 所有研究项目数据存储位置
  - 可以是相对路径或绝对路径
  - 默认：`./workspace`

- **`runtime_dir`**: Runtime 私有目录名
  - 存储日志、trace、状态等运行时数据
  - 与用户数据分离，便于清理
  - 默认：`_runtime`

##### Logging 配置

- **`level`**: 日志级别
  - 可选值：`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
  - 开发环境推荐：`DEBUG`
  - 生产环境推荐：`INFO`

- **`json`**: 日志格式
  - `true`: 结构化 JSON 格式，便于日志聚合和分析
  - `false`: 人类可读的文本格式

##### Agent Behavior 配置

- **`max_empty_reply`**: 最大空回复次数
  - Agent 连续返回空内容的容忍次数
  - 防止无限循环
  - 推荐值：2-3

- **`max_nudge_finish`**: 最大推动完成次数
  - Agent 未主动结束时的推动次数
  - 推荐值：2

- **`max_validation_retries`**: 验证重试次数
  - 输出格式验证失败时的重试上限
  - 推荐值：3

##### Execution 配置

- **`mode`**: 执行模式
  - `auto`: 自动检测（推荐）
  - `docker`: 强制使用 Docker 隔离执行
  - `container-native`: 强制直接执行

- **`detect_container`**: 容器检测
  - `true`: 自动检测是否在容器内运行
  - `false`: 不检测，使用 `mode` 指定的模式

## 模型路由配置

### `config/model_routing.yaml`

定义 LLM 服务端点和模型选择策略。

#### 完整示例

```yaml
# 默认配置文件
default_profile: default

# API 端点配置
endpoints:
  relay:
    provider: openai              # 服务提供商类型
    api_key_env: OPENAI_API_KEY   # API Key 环境变量名
    api_base_env: OPENAI_BASE_URL # Base URL 环境变量名

# 配置文件
profiles:
  default:
    # 重负载任务（文献综合、深度分析）
    heavy:
      primary:
        model: "gpt-4"
        endpoint: relay
        max_context: 128000

    # 中等负载任务（文献检索、信息提取）
    medium:
      primary:
        model: "gpt-3.5-turbo"
        endpoint: relay
        max_context: 16000

    # 轻负载任务（简单查询、格式转换）
    light:
      primary:
        model: "gpt-3.5-turbo"
        endpoint: relay
        max_context: 4000

# 上下文截断配置
truncation:
  trigger_ratio: 0.8        # 触发截断的阈值（80%）
  target_ratio: 0.6         # 截断后保留比例（60%）
  keep_system: true         # 保留系统提示词
  keep_recent_turns: 10     # 保留最近对话轮数
```

#### 配置项说明

##### Endpoints 配置

定义可用的 LLM 服务端点：

- **`provider`**: 服务提供商
  - 当前支持：`openai`
  - 兼容 OpenAI API 的服务均可使用

- **`api_key_env`**: API Key 环境变量名
  - 从环境变量读取 API Key
  - 避免硬编码敏感信息

- **`api_base_env`**: Base URL 环境变量名
  - 支持自定义 API 端点
  - 用于第三方 OpenAI 兼容服务

##### Profiles 配置

定义不同负载级别的模型选择：

- **`heavy`**: 重负载任务
  - 文献综合、深度分析
  - 需要强推理能力和大上下文
  - 推荐：GPT-4, Claude Opus

- **`medium`**: 中等负载任务
  - 文献检索、信息提取
  - 平衡性能和成本
  - 推荐：GPT-3.5-turbo, Claude Sonnet

- **`light`**: 轻负载任务
  - 简单查询、格式转换
  - 优先速度和成本
  - 推荐：GPT-3.5-turbo, Claude Haiku

##### Truncation 配置

上下文自动截断策略：

- **`trigger_ratio`**: 触发阈值
  - 使用量达到此比例时触发截断
  - 推荐：0.8（80%）

- **`target_ratio`**: 目标比例
  - 截断后保留的上下文比例
  - 推荐：0.6（60%）

- **`keep_system`**: 保留系统消息
  - `true`: 始终保留系统提示词
  - 确保 Agent 行为一致性

- **`keep_recent_turns`**: 保留最近轮数
  - 保留最新的 N 轮对话
  - 确保上下文连贯性
  - 推荐：10

## 配置最佳实践

### 1. 环境隔离

为不同环境使用不同的配置：

```bash
# 开发环境
cp .env.example .env.dev
ln -sf .env.dev .env

# 生产环境
cp .env.example .env.prod
# 编辑 .env.prod，使用生产 API Key
```

### 2. 成本优化

根据任务类型选择合适的模型：

```yaml
profiles:
  cost_optimized:
    heavy:
      primary:
        model: "gpt-4"           # 仅重要任务使用
        max_context: 32000       # 限制上下文降低成本
    medium:
      primary:
        model: "gpt-3.5-turbo"   # 大部分任务
    light:
      primary:
        model: "gpt-3.5-turbo"   # 简单任务
```

### 3. 多端点配置

配置多个 API 端点实现负载均衡或故障转移：

```yaml
endpoints:
  primary:
    provider: openai
    api_key_env: OPENAI_API_KEY
    api_base_env: OPENAI_BASE_URL

  backup:
    provider: openai
    api_key_env: BACKUP_API_KEY
    api_base_env: BACKUP_BASE_URL

profiles:
  default:
    heavy:
      primary:
        endpoint: primary
      fallback:
        endpoint: backup
```

### 4. 调试配置

开发时启用详细日志：

```yaml
logging:
  level: "DEBUG"
  json: false  # 文本格式更易读

debug:
  enable_trace: true
```

### 5. 生产配置

生产环境优化：

```yaml
logging:
  level: "INFO"
  json: true   # 结构化日志便于分析

debug:
  enable_trace: false  # 减少 I/O 开销

agent_behavior:
  max_empty_reply: 1   # 更严格的错误检测
  max_validation_retries: 2
```

## 配置验证

ResearchOS 在启动时会自动验证配置：

```bash
# 验证配置
python -m researchos.cli validate-config

# 查看当前配置
python -m researchos.cli show-config
```

### 常见配置错误

1. **缺少 API Key**
   ```
   错误：环境变量 OPENAI_API_KEY 未设置
   解决：在 .env 文件中设置 OPENAI_API_KEY
   ```

2. **无效的日志级别**
   ```
   错误：logging.level 必须是 DEBUG, INFO, WARNING, ERROR, CRITICAL 之一
   解决：检查 runtime.yaml 中的 logging.level 配置
   ```

3. **模型路由配置缺失**
   ```
   错误：model_routing.yaml 缺少 'endpoints' 部分
   解决：确保 model_routing.yaml 包含完整的 endpoints 配置
   ```

4. **YAML 语法错误**
   ```
   错误：yaml.scanner.ScannerError: mapping values are not allowed here
   解决：检查 YAML 文件的缩进和语法，确保使用空格而非制表符
   ```

5. **环境变量未展开**
   ```
   错误：API Key 显示为 "${OPENAI_API_KEY}" 而非实际值
   解决：确保 .env 文件存在且格式正确，不要在值两边加引号
   ```

## 配置文件模板

### 最小配置

适用于快速开始：

```yaml
# runtime.yaml
workspace:
  default_root: "./workspace"

logging:
  level: "INFO"

# model_routing.yaml
default_profile: default

endpoints:
  relay:
    provider: openai
    api_key_env: OPENAI_API_KEY
    api_base_env: OPENAI_BASE_URL

profiles:
  default:
    heavy:
      primary:
        model: "gpt-3.5-turbo"
        endpoint: relay
```

### 完整配置

适用于生产环境，参见本文档前面的完整示例。

## 故障排查

### 配置不生效

1. 检查配置文件路径是否正确
2. 验证 YAML 语法是否正确
3. 确认环境变量已正确设置
4. 查看日志中的配置加载信息

### API 调用失败

1. 验证 API Key 是否正确
2. 检查 Base URL 是否可访问
3. 确认模型名称是否正确
4. 查看 API 服务商的状态页面

### 性能问题

1. 调整 `max_context` 降低上下文长度
2. 使用更快的模型（如 gpt-3.5-turbo）
3. 启用上下文截断
4. 检查网络延迟

## 进一步阅读

- [配置系统概览](../config/README.md) - 配置文件快速参考
- [Agent 开发指南](AGENT_DEVELOPMENT_GUIDE.md) - Agent 开发和配置
- [Docker 使用指南](docker-usage.md) - Docker 部署配置
- [README 中文版](../README.zh-CN.md) - 项目总览

## 附录

### A. 完整配置示例

#### 开发环境配置

```bash
# .env
OPENAI_API_KEY=sk-xxxxx
OPENAI_BASE_URL=https://api.openai.com/v1
LOG_LEVEL=DEBUG
ENABLE_TRACE=true
```

```yaml
# config/runtime.yaml
workspace:
  default_root: "./workspace"
  runtime_dir: "_runtime"

logging:
  level: "DEBUG"
  json: false

human_interface:
  backend: "cli"

agent_behavior:
  max_empty_reply: 2
  max_nudge_finish: 2
  max_validation_retries: 3

debug:
  enable_trace: true

execution:
  mode: "auto"
  detect_container: true

docker:
  default_image: "researchos/system:latest"
  build_context: "infra/docker"
```

```yaml
# config/model_routing.yaml
default_profile: default

endpoints:
  relay:
    provider: openai
    api_key_env: OPENAI_API_KEY
    api_base_env: OPENAI_BASE_URL

profiles:
  default:
    heavy:
      primary:
        model: "gpt-4"
        endpoint: relay
        max_context: 128000
    medium:
      primary:
        model: "gpt-3.5-turbo"
        endpoint: relay
        max_context: 16000
    light:
      primary:
        model: "gpt-3.5-turbo"
        endpoint: relay
        max_context: 4000

truncation:
  trigger_ratio: 0.8
  target_ratio: 0.6
  keep_system: true
  keep_recent_turns: 10
```

#### 生产环境配置

```bash
# 使用系统环境变量（不使用 .env 文件）
export OPENAI_API_KEY=sk-xxxxx
export OPENAI_BASE_URL=https://api.openai.com/v1
export LOG_LEVEL=INFO
export ENABLE_TRACE=false
export SENTRY_DSN=https://xxxxx@sentry.io/xxxxx
```

```yaml
# config/runtime.yaml
workspace:
  default_root: "/data/researchos/workspace"
  runtime_dir: "_runtime"

logging:
  level: "INFO"
  json: true

human_interface:
  backend: "cli"

agent_behavior:
  max_empty_reply: 1
  max_nudge_finish: 2
  max_validation_retries: 2

debug:
  enable_trace: false

execution:
  mode: "docker"
  detect_container: true

docker:
  default_image: "researchos/system:v1.0.0"
  build_context: "infra/docker"
```

### B. 配置参数完整列表

#### runtime.yaml 参数

| 参数路径 | 类型 | 默认值 | 说明 |
|---------|------|--------|------|
| `workspace.default_root` | string | `"./workspace"` | 工作空间根目录 |
| `workspace.runtime_dir` | string | `"_runtime"` | Runtime 私有目录名 |
| `logging.level` | string | `"INFO"` | 日志级别 |
| `logging.json` | boolean | `true` | 是否使用 JSON 格式 |
| `human_interface.backend` | string | `"cli"` | 人机接口类型 |
| `agent_behavior.max_empty_reply` | integer | `2` | 最大空回复次数 |
| `agent_behavior.max_nudge_finish` | integer | `2` | 最大推动完成次数 |
| `agent_behavior.max_validation_retries` | integer | `3` | 验证重试次数 |
| `debug.enable_trace` | boolean | `true` | 是否启用 trace |
| `execution.mode` | string | `"auto"` | 执行模式 |
| `execution.detect_container` | boolean | `true` | 是否检测容器环境 |
| `docker.default_image` | string | `"researchos/system:latest"` | 默认 Docker 镜像 |
| `docker.build_context` | string | `"infra/docker"` | Docker 构建上下文 |

#### model_routing.yaml 参数

| 参数路径 | 类型 | 默认值 | 说明 |
|---------|------|--------|------|
| `default_profile` | string | `"default"` | 默认配置文件名 |
| `endpoints.<name>.provider` | string | - | 服务提供商 |
| `endpoints.<name>.api_key_env` | string | - | API Key 环境变量名 |
| `endpoints.<name>.api_base_env` | string | - | Base URL 环境变量名 |
| `profiles.<name>.<load>.primary.model` | string | - | 模型名称 |
| `profiles.<name>.<load>.primary.endpoint` | string | - | 端点名称 |
| `profiles.<name>.<load>.primary.max_context` | integer | - | 最大上下文长度 |
| `truncation.trigger_ratio` | float | `0.8` | 触发截断阈值 |
| `truncation.target_ratio` | float | `0.6` | 截断目标比例 |
| `truncation.keep_system` | boolean | `true` | 保留系统消息 |
| `truncation.keep_recent_turns` | integer | `10` | 保留最近轮数 |

#### state_machine.yaml 参数

| 参数路径 | 类型 | 默认值 | 说明 |
|---------|------|--------|------|
| `initial_state` | string | - | 初始状态名 |
| `states.<name>.agent` | string | - | Agent 名称 |
| `states.<name>.mode` | string | - | Agent 模式（可选）|
| `states.<name>.inputs` | object | - | 输入文件映射 |
| `states.<name>.outputs` | object | - | 输出文件映射 |
| `states.<name>.next_on_success` | string | - | 成功后的下一状态 |
| `states.<name>.next_on_failure` | string | - | 失败后的下一状态 |
| `states.<name>.terminal` | boolean | `false` | 是否为终止状态 |

### C. 环境变量完整列表

| 环境变量 | 必需 | 默认值 | 说明 |
|---------|------|--------|------|
| `OPENAI_API_KEY` | ✅ | - | OpenAI API Key |
| `OPENAI_BASE_URL` | ❌ | `https://api.openai.com/v1` | OpenAI API Base URL |
| `S2_API_KEY` | ❌ | - | Semantic Scholar API Key |
| `GITHUB_TOKEN` | ❌ | - | GitHub Personal Access Token |
| `LOG_LEVEL` | ❌ | - | 日志级别（覆盖 runtime.yaml）|
| `ENABLE_TRACE` | ❌ | - | 启用 trace（覆盖 runtime.yaml）|
| `EXECUTION_MODE` | ❌ | - | 执行模式（覆盖 runtime.yaml）|
| `DOCKER_IMAGE_TAG` | ❌ | `latest` | Docker 镜像标签 |
| `DEV_MODE` | ❌ | `false` | 开发模式 |
| `SENTRY_DSN` | ❌ | - | Sentry DSN（错误追踪）|

### D. 配置模板

#### 最小可运行配置

```bash
# .env
OPENAI_API_KEY=sk-xxxxx
```

```yaml
# config/runtime.yaml
workspace:
  default_root: "./workspace"

logging:
  level: "INFO"
```

```yaml
# config/model_routing.yaml
default_profile: default

endpoints:
  relay:
    provider: openai
    api_key_env: OPENAI_API_KEY
    api_base_env: OPENAI_BASE_URL

profiles:
  default:
    heavy:
      primary:
        model: "gpt-3.5-turbo"
        endpoint: relay
```

```yaml
# config/state_machine.yaml
initial_state: HELLO

states:
  HELLO:
    agent: hello
    outputs:
      hello_file: hello.txt
    next_on_success: done
    next_on_failure: failed
  
  done:
    terminal: true
  
  failed:
    terminal: true
```

### E. 配置迁移指南

#### 从旧版本迁移

如果你从旧版本的 ResearchOS 迁移，请注意以下变更：

**v0.x → v1.0**

1. 配置文件位置变更：
   - 旧：`config.yaml`（单文件）
   - 新：`config/*.yaml`（多文件）

2. 环境变量变更：
   - 旧：`API_KEY`
   - 新：`OPENAI_API_KEY`

3. 日志配置变更：
   - 旧：`log_level`
   - 新：`logging.level`

**迁移步骤：**

```bash
# 1. 备份旧配置
cp config.yaml config.yaml.backup

# 2. 创建新配置文件
cp config/runtime.yaml.example config/runtime.yaml
cp config/model_routing.yaml.example config/model_routing.yaml

# 3. 迁移环境变量
# 编辑 .env 文件，将 API_KEY 改为 OPENAI_API_KEY

# 4. 验证新配置
python -m researchos.cli validate-config

# 5. 测试运行
python -m researchos.cli run --dry-run
```

### F. 性能调优建议

#### 1. 上下文管理

```yaml
# 激进截断（节省成本）
truncation:
  trigger_ratio: 0.6
  target_ratio: 0.4
  keep_recent_turns: 5

# 保守截断（保持上下文）
truncation:
  trigger_ratio: 0.9
  target_ratio: 0.7
  keep_recent_turns: 20
```

#### 2. 模型选择

```yaml
# 成本优先
profiles:
  cost_optimized:
    heavy:
      primary:
        model: "gpt-3.5-turbo"
        max_context: 16000
    medium:
      primary:
        model: "gpt-3.5-turbo"
        max_context: 8000
    light:
      primary:
        model: "gpt-3.5-turbo"
        max_context: 4000

# 质量优先
profiles:
  quality_optimized:
    heavy:
      primary:
        model: "gpt-4"
        max_context: 128000
    medium:
      primary:
        model: "gpt-4"
        max_context: 32000
    light:
      primary:
        model: "gpt-3.5-turbo"
        max_context: 16000
```

#### 3. 并发控制

```yaml
# 高并发（需要更多资源）
agent_behavior:
  max_empty_reply: 3
  max_nudge_finish: 3
  max_validation_retries: 5

# 低并发（节省资源）
agent_behavior:
  max_empty_reply: 1
  max_nudge_finish: 1
  max_validation_retries: 2
```

### G. 安全配置建议

#### 1. API Key 管理

```bash
# ❌ 不要这样做
OPENAI_API_KEY=sk-xxxxx  # 硬编码在脚本中

# ✅ 推荐做法
# 使用 .env 文件（开发环境）
echo "OPENAI_API_KEY=sk-xxxxx" > .env
chmod 600 .env

# 使用密钥管理服务（生产环境）
export OPENAI_API_KEY=$(aws secretsmanager get-secret-value --secret-id openai-key --query SecretString --output text)
```

#### 2. 日志安全

```yaml
# 生产环境：不记录敏感信息
logging:
  level: "INFO"  # 不使用 DEBUG
  json: true     # 结构化日志便于过滤

# 开发环境：详细日志
logging:
  level: "DEBUG"
  json: false
```

#### 3. 网络安全

```yaml
# 使用 HTTPS 端点
endpoints:
  relay:
    provider: openai
    api_key_env: OPENAI_API_KEY
    api_base_env: OPENAI_BASE_URL  # 确保是 https://

# ❌ 不要使用 HTTP
# api_base_env: http://insecure-api.com
```

## 获取帮助

如果遇到配置问题：

1. 查看本文档的"故障排查"部分
2. 查看配置系统概览：`config/README.md`
3. 查看日志文件：`workspace/_runtime/logs/`
4. 运行配置验证：`python -m researchos.cli validate-config`
5. 提交 Issue：https://github.com/MengkunLiang/DIG-ResearchOS/issues

## 贡献

欢迎贡献配置相关的改进：

- 新的配置选项
- 配置验证规则
- 配置文档改进
- 配置示例和模板

请参考 [CONTRIBUTING.md](../CONTRIBUTING.md) 了解贡献指南。
