# ResearchOS 日志系统

本文档介绍 ResearchOS 的日志系统，包括日志位置、查看方法、配置选项和调试技巧。

## 目录

- [日志位置](#日志位置)
- [日志级别](#日志级别)
- [查看日志](#查看日志)
- [Docker 模式下的日志](#docker-模式下的日志)
- [日志配置](#日志配置)
- [调试技巧](#调试技巧)
- [常见问题](#常见问题)

---

## 日志位置

### 宿主机模式

所有运行时日志存储在 workspace 的 `_runtime/logs/` 目录下：

```
workspace/
└── _runtime/
    └── logs/
        └── researchos.log    # 主日志文件
```

### Docker 模式

Docker 模式下，日志同样存储在 `workspace/_runtime/logs/` 目录。由于 workspace 通过 `-v` 挂载到容器内，日志文件会持久化到宿主机。

**容器内路径**：`/workspace/_runtime/logs/researchos.log`  
**宿主机路径**：`$(pwd)/workspace/_runtime/logs/researchos.log`

---

## 日志级别

ResearchOS 支持以下日志级别（按严重程度递增）：

| 级别 | 说明 | 用途 |
|------|------|------|
| `DEBUG` | 调试信息 | 详细的执行流程、变量值、中间状态 |
| `INFO` | 一般信息 | 正常的操作流程、关键步骤完成 |
| `WARNING` | 警告信息 | 潜在问题、降级行为、配置建议 |
| `ERROR` | 错误信息 | 执行失败、异常捕获、需要人工介入 |

### 默认级别

- 生产运行：`INFO`
- 开发调试：`DEBUG`

---

## 查看日志

### 实时查看（宿主机模式）

```bash
# 实时跟踪日志输出
tail -f workspace/_runtime/logs/researchos.log

# 只显示最近 50 行
tail -n 50 workspace/_runtime/logs/researchos.log

# 实时查看并高亮错误
tail -f workspace/_runtime/logs/researchos.log | grep --color=auto -E 'ERROR|WARNING|$'
```

### 查看历史日志

```bash
# 查看完整日志
cat workspace/_runtime/logs/researchos.log

# 使用 less 分页查看
less workspace/_runtime/logs/researchos.log

# 搜索特定内容
grep "agent_start" workspace/_runtime/logs/researchos.log

# 查看最近 100 行
tail -n 100 workspace/_runtime/logs/researchos.log
```

### 过滤特定事件

```bash
# 查看所有 Agent 启动事件
grep "agent_start" workspace/_runtime/logs/researchos.log

# 查看所有错误
grep "ERROR" workspace/_runtime/logs/researchos.log

# 查看特定 Agent 的日志
grep "scout" workspace/_runtime/logs/researchos.log

# 查看 LLM 调用
grep "llm_request" workspace/_runtime/logs/researchos.log
```

### 分析日志统计

```bash
# 统计各级别日志数量
grep -c "INFO" workspace/_runtime/logs/researchos.log
grep -c "WARNING" workspace/_runtime/logs/researchos.log
grep -c "ERROR" workspace/_runtime/logs/researchos.log

# 统计 Agent 执行次数
grep -c "agent_start" workspace/_runtime/logs/researchos.log

# 查看最常见的错误
grep "ERROR" workspace/_runtime/logs/researchos.log | sort | uniq -c | sort -rn
```

---

## Docker 模式下的日志

### 查看容器内日志

**方法 1：通过宿主机挂载目录**

由于 workspace 挂载到宿主机，可以直接在宿主机查看：

```bash
# 宿主机上实时查看
tail -f workspace/_runtime/logs/researchos.log
```

**方法 2：进入容器查看**

```bash
# 启动容器并进入 shell
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  --entrypoint bash \
  researchos/system:latest

# 容器内查看日志
tail -f /workspace/_runtime/logs/researchos.log
```

**方法 3：使用 docker exec**

```bash
# 找到运行中的容器 ID
docker ps

# 在运行中的容器内执行命令
docker exec -it <container-id> tail -f /workspace/_runtime/logs/researchos.log
```

### 容器标准输出

除了日志文件，ResearchOS 也会将日志输出到标准输出（stdout）。可以通过 `docker logs` 查看：

```bash
# 查看容器标准输出
docker logs <container-id>

# 实时跟踪
docker logs -f <container-id>

# 查看最近 100 行
docker logs --tail 100 <container-id>
```

### 日志持久化

**重要**：确保 workspace 目录已挂载，否则容器退出后日志会丢失。

```bash
# 正确：挂载 workspace（日志持久化）
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run --workspace /workspace

# 错误：不挂载 workspace（日志会丢失）
docker run --rm -it \
  researchos/system:latest \
  run --workspace /workspace
```

---

## 日志配置

### 命令行参数

所有 CLI 命令都支持 `--log-level` 参数：

```bash
# 使用 DEBUG 级别（详细日志）
python -m researchos.cli run --workspace workspace --log-level DEBUG

# 使用 WARNING 级别（只记录警告和错误）
python -m researchos.cli run --workspace workspace --log-level WARNING

# Docker 模式
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run --workspace /workspace --log-level DEBUG
```

### 日志格式

ResearchOS 使用结构化日志（structured logging），基于 `structlog` 库。

**JSON 格式（默认）**：

```json
{"event": "agent_start", "agent": "scout", "task": "T2", "timestamp": "2024-04-20T10:30:00.123456Z", "level": "info"}
```

**控制台格式（开发模式）**：

```
2024-04-20 10:30:00 [info] agent_start agent=scout task=T2
```

### 环境变量

可以通过环境变量配置日志行为：

```bash
# 设置日志级别
export RESEARCHOS_LOG_LEVEL=DEBUG

# 禁用 JSON 格式（使用控制台格式）
export RESEARCHOS_LOG_JSON=false
```

---

## 调试技巧

### 1. 追踪 Agent 执行流程

```bash
# 查看所有 Agent 的启动和完成
grep -E "agent_start|agent_done" workspace/_runtime/logs/researchos.log

# 查看特定 Agent 的完整执行
grep "scout" workspace/_runtime/logs/researchos.log | less
```

### 2. 诊断 LLM 调用问题

```bash
# 查看所有 LLM 请求
grep "llm_request" workspace/_runtime/logs/researchos.log

# 查看 LLM 错误
grep -E "llm_request.*ERROR|llm_error" workspace/_runtime/logs/researchos.log

# 统计 LLM 调用次数
grep -c "llm_request" workspace/_runtime/logs/researchos.log
```

### 3. 检查工具执行

```bash
# 查看所有工具调用
grep "tool_call" workspace/_runtime/logs/researchos.log

# 查看 docker_exec 执行
grep "docker_exec" workspace/_runtime/logs/researchos.log

# 查看 LaTeX 编译
grep "latex_compile" workspace/_runtime/logs/researchos.log
```

### 4. 分析性能瓶颈

```bash
# 查看耗时较长的操作
grep "duration" workspace/_runtime/logs/researchos.log | sort -t: -k2 -n

# 查看超时事件
grep "timeout" workspace/_runtime/logs/researchos.log
```

### 5. 调试预算和限流

```bash
# 查看预算警告
grep "budget_warning" workspace/_runtime/logs/researchos.log

# 查看限流事件
grep "rate_limit" workspace/_runtime/logs/researchos.log
```

### 6. 使用 jq 解析 JSON 日志

如果日志是 JSON 格式，可以使用 `jq` 进行高级查询：

```bash
# 安装 jq（如果未安装）
sudo apt-get install jq  # Ubuntu/Debian
brew install jq          # macOS

# 提取所有错误事件
cat workspace/_runtime/logs/researchos.log | jq 'select(.level == "error")'

# 统计各 Agent 的调用次数
cat workspace/_runtime/logs/researchos.log | jq -r '.agent' | sort | uniq -c

# 查看特定时间范围的日志
cat workspace/_runtime/logs/researchos.log | jq 'select(.timestamp >= "2024-04-20T10:00:00")'

# 提取 LLM 请求的 token 使用
cat workspace/_runtime/logs/researchos.log | jq 'select(.event == "llm_request") | {agent, tokens}'
```

### 7. 实时监控关键事件

```bash
# 监控错误和警告
tail -f workspace/_runtime/logs/researchos.log | grep --color=auto -E 'ERROR|WARNING'

# 监控 Agent 状态变化
tail -f workspace/_runtime/logs/researchos.log | grep --color=auto -E 'agent_start|agent_done'

# 监控预算使用
tail -f workspace/_runtime/logs/researchos.log | grep --color=auto 'budget'
```

---

## 常见问题

### Q1: 日志文件不存在

**症状**：`workspace/_runtime/logs/researchos.log` 文件不存在

**原因**：
- Workspace 未初始化
- 首次运行尚未创建日志文件

**解决方法**：
```bash
# 初始化 workspace
python -m researchos.cli init-workspace --workspace workspace

# 或者运行任意命令，日志文件会自动创建
python -m researchos.cli status --workspace workspace
```

### Q2: 日志文件过大

**症状**：`researchos.log` 文件占用大量磁盘空间

**原因**：
- 长时间运行积累了大量日志
- DEBUG 级别产生过多日志

**解决方法**：
```bash
# 清空日志文件（保留文件）
> workspace/_runtime/logs/researchos.log

# 或者删除并重新创建
rm workspace/_runtime/logs/researchos.log

# 使用更高的日志级别
python -m researchos.cli run --workspace workspace --log-level WARNING
```

### Q3: Docker 容器内看不到日志

**症状**：容器内 `/workspace/_runtime/logs/` 目录为空

**原因**：
- Workspace 未正确挂载
- 日志目录未创建

**解决方法**：
```bash
# 确保挂载 workspace
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run --workspace /workspace

# 检查挂载是否成功
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  --entrypoint bash \
  researchos/system:latest \
  -c "ls -la /workspace/_runtime/logs/"
```

### Q4: 日志格式难以阅读

**症状**：JSON 格式日志不便于人工阅读

**解决方法**：
```bash
# 方法 1：使用 jq 格式化
cat workspace/_runtime/logs/researchos.log | jq '.'

# 方法 2：使用 Python 格式化
python -m json.tool < workspace/_runtime/logs/researchos.log

# 方法 3：禁用 JSON 格式（需要修改代码或配置）
# 在 cli.py 中调用 configure_logging(json_logs=False)
```

### Q5: 日志中缺少关键信息

**症状**：日志级别过高，缺少调试信息

**解决方法**：
```bash
# 使用 DEBUG 级别重新运行
python -m researchos.cli run --workspace workspace --log-level DEBUG

# 或者设置环境变量
export RESEARCHOS_LOG_LEVEL=DEBUG
python -m researchos.cli run --workspace workspace
```

### Q6: 容器退出后日志丢失

**症状**：容器停止后无法查看日志

**原因**：
- Workspace 未挂载
- 使用了 `--rm` 标志但未挂载日志目录

**解决方法**：
```bash
# 确保挂载 workspace（推荐）
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run --workspace /workspace

# 或者不使用 --rm，容器退出后仍可查看
docker run -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run --workspace /workspace

# 事后查看容器日志
docker logs <container-id>
```

---

## 日志轮转（未实现）

当前版本的 ResearchOS 不支持自动日志轮转。如果需要管理日志文件大小，可以：

1. **手动清理**：定期删除或归档旧日志
2. **使用外部工具**：如 `logrotate`（Linux）
3. **脚本自动化**：编写清理脚本

**示例清理脚本**：

```bash
#!/bin/bash
# 归档并压缩旧日志

LOG_FILE="workspace/_runtime/logs/researchos.log"
ARCHIVE_DIR="workspace/_runtime/logs/archive"

mkdir -p "$ARCHIVE_DIR"

if [ -f "$LOG_FILE" ]; then
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    gzip -c "$LOG_FILE" > "$ARCHIVE_DIR/researchos_$TIMESTAMP.log.gz"
    > "$LOG_FILE"  # 清空当前日志
    echo "日志已归档到 $ARCHIVE_DIR/researchos_$TIMESTAMP.log.gz"
fi
```

---

## 最佳实践

### 1. 开发调试时使用 DEBUG 级别

```bash
python -m researchos.cli run --workspace workspace --log-level DEBUG
```

### 2. 生产运行时使用 INFO 级别

```bash
python -m researchos.cli run --workspace workspace --log-level INFO
```

### 3. 定期检查日志文件大小

```bash
du -h workspace/_runtime/logs/researchos.log
```

### 4. 使用 tail -f 实时监控

```bash
tail -f workspace/_runtime/logs/researchos.log | grep --color=auto -E 'ERROR|WARNING|$'
```

### 5. 结合 trace 命令诊断问题

```bash
# 查看执行跟踪
python -m researchos.cli trace --workspace workspace --run-id <run-id>

# 结合日志分析
grep "<run-id>" workspace/_runtime/logs/researchos.log
```

---

## 相关文档

- [Docker 使用指南](docker-usage.md)
- [Runtime 开发规范](/home/liangmengkun/reference_materials/ResearchOS_Runtime_Dev_Spec.md)
- [故障排查指南](docker-usage.md#故障排查)

---

## 反馈与支持

如有问题，请提交 Issue 到 GitHub 仓库：
https://github.com/MengkunLiang/DIG-ResearchOS/issues
