# ResearchOS 配置管理指南

> **版本**: v2.0
> **更新日期**: 2026-04-23

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
- Docker 环境：通过 `-e` 标志或 `--env-file` 传递
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

# 容器检测
execution:
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

- **`detect_container`**: 容器检测
  - `true`: 自动检测是否在容器内运行
  - `false`: 不检测容器环境
  - 推荐：`true`

**容器检测行为**：
- 容器内：直接执行命令，避免嵌套 Docker
- 宿主机：使用 Docker 隔离执行（如果需要）

##### Docker 配置

- **`default_image`**: 默认 Docker 镜像
  - 用于隔离执行的镜像名称
  - 默认：`researchos/system:latest`

- **`build_context`**: Docker 构建上下文
  - Dockerfile 所在目录
  - 默认：`infra/docker`

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

# 上下文截断策略
truncation:
  trigger_ratio: 0.8      # 触发截断的阈值（80%）
  target_ratio: 0.6       # 截断后的目标比例（60%）
  keep_system: true       # 保留系统消息
  keep_recent_turns: 10   # 保留最近 N 轮对话
```

#### 配置项说明

##### Endpoints 配置

- **`provider`**: 服务提供商类型
  - 可选值：`openai`, `anthropic`, `azure`
  - 决定使用哪个 SDK

- **`api_key_env`**: API Key 环境变量名
  - 从环境变量读取 API Key
  - 推荐：`OPENAI_API_KEY`

- **`api_base_env`**: Base URL 环境变量名
  - 从环境变量读取 API Base URL
  - 推荐：`OPENAI_BASE_URL`

##### Profiles 配置

每个 profile 定义三个负载等级：

- `primary.endpoint` / `fallback[].endpoint` 决定这条路由实际走哪个 endpoint
- 每个 endpoint 都可以有自己独立的 `provider`、`api_key_env`、`api_base_env`
- 这意味着你可以在同一个 profile 里混用不同 provider
  - 例如：`heavy.primary -> siliconflow(openai-compatible)`
  - 例如：`heavy.fallback -> anthropic`
  - 例如：`medium.primary -> openai`
- Agent 代码通常只声明 `model_tier="heavy|medium|light"`，真正选到哪个 provider 由路由配置决定
- 可参考 `config/model_routing.multi_provider.example.yaml`

- **`heavy`**: 重负载任务
  - 用于：T1 PI, T3.5 Synthesis, T4 Ideation, T4.5 Novelty Auditor, T8 Writer/Reviewer
  - 推荐模型：GPT-4, Claude Opus

- **`medium`**: 中等负载任务
  - 用于：T2 Scout, T3 Reader, T5/T7 Experimenter, T6 Novelty, T9 Submission
  - 推荐模型：GPT-3.5-turbo, Claude Sonnet

- **`light`**: 轻负载任务
  - 用于：简单任务、快速响应
  - 推荐模型：GPT-3.5-turbo, Claude Haiku

##### Truncation 配置

- **`trigger_ratio`**: 触发截断的阈值
  - 当上下文使用率超过此值时触发截断
  - 推荐值：0.8（80%）

- **`target_ratio`**: 截断后的目标比例
  - 截断后上下文使用率的目标值
  - 推荐值：0.6（60%）

- **`keep_system`**: 是否保留系统消息
  - `true`: 始终保留系统消息
  - `false`: 可以截断系统消息

- **`keep_recent_turns`**: 保留最近 N 轮对话
  - 截断时保留最近的对话轮数
  - 推荐值：10

## 状态机配置

### `config/state_machine.yaml`

定义 Agent 工作流和状态转换。

#### 完整示例

```yaml
initial_state: T1-INIT

states:
  T1-INIT:
    agent: pi
    mode: init
    inputs: {}
    outputs:
      project_config: "project.yaml"
      seed_papers: "user_seeds/seed_papers.jsonl"
      seed_ideas: "user_seeds/seed_ideas.md"
    next_on_success: T2-SCOUT
    next_on_failure: failed

  T2-SCOUT:
    agent: scout
    inputs:
      project_config: "project.yaml"
      seed_papers: "user_seeds/seed_papers.jsonl"
    outputs:
      papers_raw: "literature/papers_raw.jsonl"
      papers_dedup: "literature/papers_dedup.jsonl"
    next_on_success: T3-READ
    next_on_failure: failed

  # ... 其他状态定义

  failed:
    terminal: true
```

#### 配置项说明

- **`initial_state`**: 初始状态名称
  - 工作流的起点

- **`states.<name>.agent`**: Agent 名称
  - 对应 `researchos/agents/` 中的 Agent 类

- **`states.<name>.mode`**: Agent 模式（可选）
  - 某些 Agent 支持多种模式（如 PI 的 init/evaluate）

- **`states.<name>.inputs`**: 输入文件映射
  - 键：Agent 期望的输入名称
  - 值：workspace 中的文件路径

- **`states.<name>.outputs`**: 输出文件映射
  - 键：Agent 产出的输出名称
  - 值：workspace 中的文件路径

- **`states.<name>.next_on_success`**: 成功后的下一状态
  - 状态名称或 `null`（终止）

- **`states.<name>.next_on_failure`**: 失败后的下一状态
  - 状态名称或 `null`（终止）

- **`states.<name>.terminal`**: 是否为终止状态
  - `true`: 工作流在此结束
  - `false`: 继续执行

## MCP 工具配置

### `config/mcp.yaml`

定义 Model Context Protocol (MCP) 工具配置。

#### 完整示例

```yaml
servers:
  arxiv:
    command: "npx"
    args:
      - "-y"
      - "@modelcontextprotocol/server-arxiv"
    env:
      NODE_OPTIONS: "--max-old-space-size=4096"

tools:
  search_arxiv:
    server: arxiv
    enabled: true
    description: "Search arXiv papers"
```

#### 配置项说明

- **`servers.<name>.command`**: 服务器启动命令
- **`servers.<name>.args`**: 命令参数
- **`servers.<name>.env`**: 环境变量

- **`tools.<name>.server`**: 关联的服务器名称
- **`tools.<name>.enabled`**: 是否启用
- **`tools.<name>.description`**: 工具描述

## Gate 配置

### `config/gates.yaml`

定义 Human Gate 和条件检查。

#### 完整示例

```yaml
gates:
  T4-GATE1:
    type: human
    prompt: "请审核研究假设草案"
    options:
      - approve
      - revise
      - reject

  T4-GATE2:
    type: human
    prompt: "请审核实验计划"
    options:
      - approve
      - revise
```

#### 配置项说明

- **`gates.<name>.type`**: Gate 类型
  - `human`: 需要人工确认
  - `auto`: 自动检查

- **`gates.<name>.prompt`**: 提示信息
- **`gates.<name>.options`**: 可选操作

## 配置最佳实践

### 1. 环境分离

```bash
# 开发环境
.env.development

# 生产环境
.env.production

# 测试环境
.env.test
```

使用不同的 `.env` 文件管理不同环境的配置。

### 2. 敏感信息保护

```bash
# ❌ 不要提交到版本控制
.env

# ✅ 提交模板文件
.env.example
```

在 `.gitignore` 中添加：
```
.env
.env.*
!.env.example
```

### 3. 配置验证

```bash
# 验证配置文件格式
python -m researchos.cli validate-config

# 测试运行（不实际执行）
python -m researchos.cli run --dry-run
```

### 4. Docker 环境配置

```bash
# 方法 1：使用 -e 标志
docker run --rm -it \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e OPENAI_BASE_URL=$OPENAI_BASE_URL \
  researchos/system:latest

# 方法 2：使用 --env-file
docker run --rm -it \
  --env-file .env \
  researchos/system:latest

# 方法 3：使用便捷脚本（推荐）
bash infra/docker/run.sh run --workspace /workspace
```

### 5. 日志级别选择

```yaml
# 开发环境：详细日志
logging:
  level: "DEBUG"
  json: false

# 生产环境：结构化日志
logging:
  level: "INFO"
  json: true

# 故障排查：最详细日志
logging:
  level: "DEBUG"
  json: true
```

## 配置验证

### 手动验证

```bash
# 检查配置文件语法
python -c "import yaml; yaml.safe_load(open('config/runtime.yaml'))"

# 验证环境变量
python -c "import os; print(os.getenv('OPENAI_API_KEY'))"

# 测试 API 连接
python -m researchos.cli selftest
```

### 自动验证

```bash
# 运行配置验证
python -m researchos.cli validate-config

# 输出示例
✅ runtime.yaml: 有效
✅ model_routing.yaml: 有效
✅ state_machine.yaml: 有效
⚠️  mcp.yaml: 未找到（可选）
✅ 环境变量: OPENAI_API_KEY 已设置
✅ 环境变量: OPENAI_BASE_URL 已设置
```

## 故障排查

### 问题 1：API Key 未生效

**症状**：运行时提示缺少 API Key

**可能原因**：
- `.env` 文件不存在或位置错误
- 环境变量名称错误
- Docker 容器未传递环境变量

**解决方法**：
```bash
# 检查 .env 文件
cat .env

# 检查环境变量
echo $OPENAI_API_KEY

# Docker 环境：确保传递环境变量
docker run --rm -it \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  researchos/system:latest \
  bash -c "echo \$OPENAI_API_KEY"
```

### 问题 2：配置文件格式错误

**症状**：启动时报 YAML 解析错误

**可能原因**：
- YAML 语法错误
- 缩进不正确
- 特殊字符未转义

**解决方法**：
```bash
# 验证 YAML 语法
python -c "import yaml; yaml.safe_load(open('config/runtime.yaml'))"

# 使用在线 YAML 验证器
# https://www.yamllint.com/
```

### 问题 3：模型调用失败

**症状**：LLM API 调用失败

**可能原因**：
- API Key 无效
- Base URL 错误
- 模型名称错误
- 网络连接问题

**解决方法**：
```bash
# 测试 API 连接
curl -H "Authorization: Bearer $OPENAI_API_KEY" \
     $OPENAI_BASE_URL/models

# 检查模型配置
cat config/model_routing.yaml

# 运行自检
python -m researchos.cli selftest
```

### 问题 4：日志文件不存在

**症状**：找不到日志文件

**可能原因**：
- Workspace 未初始化
- 日志目录权限问题
- Docker 挂载问题

**解决方法**：
```bash
# 初始化 workspace
python -m researchos.cli init-workspace --workspace ./workspace

# 检查日志目录
ls -la workspace/_runtime/logs/

# Docker 环境：确保挂载 workspace
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  status --workspace /workspace
```

### 问题 5：容器内执行异常

**症状**：容器内运行时行为异常

**可能原因**：
- 容器检测失败
- Docker 嵌套问题
- 权限问题

**解决方法**：
```bash
# 检查容器检测
docker run --rm -it \
  researchos/system:latest \
  bash -c "test -f /.dockerenv && echo 'In container' || echo 'Not in container'"

# 查看日志
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run-task --workspace /workspace --task hello --mock --log-level DEBUG
```

## 配置参考

### 完整参数列表

#### runtime.yaml 参数

| 参数路径 | 类型 | 默认值 | 说明 |
|---------|------|--------|------|
| `workspace.default_root` | string | `"./workspace"` | 工作空间根目录 |
| `workspace.runtime_dir` | string | `"_runtime"` | Runtime 目录名 |
| `logging.level` | string | `"INFO"` | 日志级别 |
| `logging.json` | boolean | `true` | 是否使用 JSON 格式 |
| `human_interface.backend` | string | `"cli"` | 人机接口类型 |
| `agent_behavior.max_empty_reply` | integer | `2` | 最大空回复次数 |
| `agent_behavior.max_nudge_finish` | integer | `2` | 最大推动完成次数 |
| `agent_behavior.max_validation_retries` | integer | `3` | 验证重试次数 |
| `debug.enable_trace` | boolean | `true` | 是否启用 trace |
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

### 环境变量完整列表

| 环境变量 | 必需 | 默认值 | 说明 |
|---------|------|--------|------|
| `OPENAI_API_KEY` | ✅ | - | OpenAI API Key |
| `OPENAI_BASE_URL` | ❌ | `https://api.openai.com/v1` | OpenAI API Base URL |
| `S2_API_KEY` | ❌ | - | Semantic Scholar API Key |
| `GITHUB_TOKEN` | ❌ | - | GitHub Personal Access Token |
| `LOG_LEVEL` | ❌ | - | 日志级别（覆盖 runtime.yaml）|
| `ENABLE_TRACE` | ❌ | - | 启用 trace（覆盖 runtime.yaml）|

## 性能调优建议

### 1. 上下文管理

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

### 2. 模型选择

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

### 3. 并发控制

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

## 安全配置建议

### 1. API Key 管理

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

### 2. 日志安全

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

### 3. 网络安全

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
2. 查看日志文件：`workspace/_runtime/logs/`
3. 运行配置验证：`python -m researchos.cli validate-config`
4. 查看 [Docker 使用指南](docker-usage.md)
5. 查看 [故障排查指南](TROUBLESHOOTING.md)
6. 提交 Issue：https://github.com/MengkunLiang/DIG-ResearchOS/issues

## 贡献

欢迎贡献配置相关的改进：

- 新的配置选项
- 配置验证规则
- 配置文档改进
- 配置示例和模板

请参考 [CONTRIBUTING.md](../CONTRIBUTING.md) 了解贡献指南。
