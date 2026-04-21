# ResearchOS 故障排查指南

> **版本**: v1.0  
> **更新日期**: 2026-04-21

---

## 一、Docker 模式问题

### 1.1 镜像构建失败

**症状**:
```
ERROR: failed to solve: DeadlineExceeded
```

**可能原因**:
- 网络连接问题
- 磁盘空间不足
- Docker 版本过旧

**解决方案**:
```bash
# 检查磁盘空间
df -h

# 清理 Docker 缓存
docker system prune -a

# 检查 Docker 版本
docker --version

# 使用国内镜像源（如果在中国）
# 编辑 /etc/docker/daemon.json
{
  "registry-mirrors": ["https://docker.mirrors.ustc.edu.cn"]
}
sudo systemctl restart docker
```

### 1.2 容器无法访问 GPU

**症状**:
```python
torch.cuda.is_available()  # 返回 False
```

**可能原因**:
- nvidia-docker2 未安装
- 未使用 `--gpus all` 标志
- NVIDIA 驱动版本不兼容

**解决方案**:
```bash
# 检查宿主机 GPU
nvidia-smi

# 检查 nvidia-docker2
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi

# 重新安装 nvidia-docker2
sudo apt-get purge nvidia-docker2
sudo apt-get install nvidia-docker2
sudo systemctl restart docker
```

### 1.3 权限错误

**症状**:
```
Permission denied: /workspace/xxx
```

**可能原因**:
- Workspace 目录权限不正确
- SELinux 阻止挂载

**解决方案**:
```bash
# 检查权限
ls -la workspace/

# 修改权限
chmod -R 755 workspace/

# 如果使用 SELinux，添加 :z 标志
docker run --rm -it \
  -v $(pwd)/workspace:/workspace:z \
  researchos/system:latest \
  run --workspace /workspace
```

### 1.4 环境变量未生效

**症状**:
```
AuthenticationError: Incorrect API key provided
```

**可能原因**:
- 环境变量未正确传递
- 环境变量名称错误

**解决方案**:
```bash
# 检查环境变量
docker run --rm -it \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  researchos/system:latest \
  bash -c "echo \$OPENAI_API_KEY"

# 使用 --env-file
docker run --rm -it \
  --env-file .env \
  researchos/system:latest \
  run --workspace /workspace
```

### 1.5 容器内网络不通

**症状**:
```
ConnectionError: Failed to connect to API
```

**可能原因**:
- Docker 网络配置问题
- 防火墙阻止

**解决方案**:
```bash
# 测试网络
docker run --rm -it \
  researchos/system:latest \
  bash -c "curl -I https://www.google.com"

# 检查 Docker 网络
docker network ls
docker network inspect bridge

# 重启 Docker
sudo systemctl restart docker
```

### 1.6 日志文件不存在

**症状**:
```
FileNotFoundError: workspace/_runtime/logs/researchos.log
```

**可能原因**:
- Workspace 未正确挂载
- 日志目录未初始化

**解决方案**:
```bash
# 初始化 workspace
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  init-workspace --workspace /workspace

# 运行任意命令创建日志
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  status --workspace /workspace
```

---

## 二、宿主机模式问题

### 2.1 Conda 环境不一致警告

**症状**:
```
[env-warning] 检测到当前 shell 环境与实际解释器可能不一致
```

**解决方案**:
```bash
# 方案 1: 使用 conda run
conda run -n researchos python -m researchos.cli <command>

# 方案 2: 确保 PATH 正确
export PATH=/home/liangmengkun/.conda/envs/researchos/bin:$PATH

# 方案 3: 在正确的环境中运行
conda activate researchos
python -m researchos.cli <command>
```

### 2.2 依赖缺失

**症状**:
```
ModuleNotFoundError: No module named 'xxx'
```

**解决方案**:
```bash
# 安装缺失的包
pip install xxx

# 或重新安装所有依赖
pip install -e '.[dev]'

# 或使用 conda
conda install xxx
```

### 2.3 Python 版本不兼容

**症状**:
```
SyntaxError: invalid syntax
```

**可能原因**:
- Python 版本低于 3.11

**解决方案**:
```bash
# 检查 Python 版本
python --version

# 创建新的 conda 环境
conda create -n researchos python=3.11 -y
conda activate researchos
pip install -e '.[dev]'
```

---

## 三、API 问题

### 3.1 API Key 未设置

**症状**:
```
AuthenticationError: Incorrect API key provided
```

**解决方案**:
```bash
# 方法 1: 设置环境变量
export OPENAI_API_KEY=sk-your-actual-key-here
export OPENAI_BASE_URL=https://api.openai.com/v1

# 方法 2: 创建 .env 文件
cat > .env <<EOF
OPENAI_API_KEY=sk-your-actual-key-here
OPENAI_BASE_URL=https://api.openai.com/v1
EOF

# 方法 3: Docker 环境传递
docker run --rm -it \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e OPENAI_BASE_URL=$OPENAI_BASE_URL \
  researchos/system:latest \
  run --workspace /workspace
```

### 3.2 API 连接超时

**症状**:
```
TimeoutError: Request timed out
```

**解决方案**:
```bash
# 检查网络连接
curl -I https://api.openai.com

# 测试 API 连接
curl -H "Authorization: Bearer $OPENAI_API_KEY" \
     $OPENAI_BASE_URL/models

# 增加超时时间（在 config/runtime.yaml 中配置）
# 或稍后重试
```

### 3.3 API 调用失败

**症状**:
```
APIError: Invalid request
```

**可能原因**:
- API Key 无效
- Base URL 错误
- 模型名称错误

**解决方案**:
```bash
# 检查 API Key
echo $OPENAI_API_KEY

# 检查 Base URL
echo $OPENAI_BASE_URL

# 检查模型配置
cat config/model_routing.yaml

# 运行自检
researchos selftest
```

---

## 四、Workflow 问题

### 4.1 任务找不到

**症状**:
```
Prerequisites not met for t1: Unknown task: t1
```

**解决方案**:
```bash
# 检查 state_machine.yaml 中的任务定义
cat config/state_machine.yaml | grep -A5 "states:"

# 可用任务包括: HELLO, T1, T2, T3, T3.5, T4, T4.5, T5, T6, T7, T7.5, T8-*, T9
```

### 4.2 Validation 失败

**症状**:
```
error: Validation failed. Missing expected output: xxx
```

**可能原因**:
- Agent 未生成必需的输出文件
- 输出文件格式不正确

**解决方案**:
```bash
# 检查 workspace 目录
ls -la workspace/

# 查看日志
tail -f workspace/_runtime/logs/researchos.log

# 使用 DEBUG 级别重新运行
researchos run-task --workspace ./workspace --task <task-name> --log-level DEBUG
```

### 4.3 State Machine 卡住

**症状**:
```
State machine stuck at state: xxx
```

**可能原因**:
- Agent 陷入无限循环
- 输出验证失败次数过多

**解决方案**:
```bash
# 查看状态
researchos status --workspace ./workspace

# 查看日志
tail -f workspace/_runtime/logs/researchos.log

# 重置状态机
rm workspace/_runtime/state.yaml

# 从特定任务重新开始
researchos run-task --workspace ./workspace --task <task-name>
```

### 4.4 Gate 无响应

**症状**:
```
Waiting for human input at gate: xxx
```

**解决方案**:
```bash
# 检查是否需要人工确认
# Gate 会在终端提示用户输入

# 如果在 Docker 容器中，确保使用 -it 标志
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run --workspace /workspace
```

---

## 五、配置问题

### 5.1 配置文件格式错误

**症状**:
```
yaml.scanner.ScannerError: mapping values are not allowed here
```

**可能原因**:
- YAML 语法错误
- 缩进不正确

**解决方案**:
```bash
# 验证 YAML 语法
python -c "import yaml; yaml.safe_load(open('config/runtime.yaml'))"

# 使用在线 YAML 验证器
# https://www.yamllint.com/
```

### 5.2 配置文件缺失

**症状**:
```
FileNotFoundError: config/runtime.yaml
```

**解决方案**:
```bash
# 检查配置文件
ls -la config/

# 从示例复制
cp config/runtime.yaml.example config/runtime.yaml
cp config/model_routing.yaml.example config/model_routing.yaml
```

### 5.3 模型配置错误

**症状**:
```
KeyError: 'heavy'
```

**可能原因**:
- model_routing.yaml 配置不完整

**解决方案**:
```bash
# 检查模型配置
cat config/model_routing.yaml

# 确保包含 heavy, medium, light 三个层级
```

---

## 六、性能问题

### 6.1 运行速度慢

**可能原因**:
- 网络延迟
- LLM API 响应慢
- 上下文过长

**解决方案**:
```yaml
# 调整上下文截断策略（config/model_routing.yaml）
truncation:
  trigger_ratio: 0.6  # 更激进的截断
  target_ratio: 0.4
  keep_recent_turns: 5
```

### 6.2 内存占用过高

**可能原因**:
- 上下文过长
- 日志文件过大

**解决方案**:
```bash
# 清理日志
rm workspace/_runtime/logs/*.log

# 清理 trace
rm -rf workspace/_runtime/traces/*

# 调整日志级别
# 在 config/runtime.yaml 中设置 logging.level: "WARNING"
```

### 6.3 磁盘空间不足

**症状**:
```
OSError: [Errno 28] No space left on device
```

**解决方案**:
```bash
# 检查磁盘空间
df -h

# 清理 Docker 缓存
docker system prune -a

# 清理 workspace
rm -rf workspace/_runtime/traces/*
rm -rf workspace/_runtime/logs/*.log
```

---

## 七、日志分析

### 7.1 查看日志

```bash
# 实时查看日志
tail -f workspace/_runtime/logs/researchos.log

# 查看最近 100 行
tail -n 100 workspace/_runtime/logs/researchos.log

# 搜索错误
grep "ERROR" workspace/_runtime/logs/researchos.log

# 高亮显示错误和警告
tail -f workspace/_runtime/logs/researchos.log | grep --color=auto -E 'ERROR|WARNING|$'
```

### 7.2 日志级别

```bash
# 使用 DEBUG 级别（详细日志）
researchos run --workspace ./workspace --log-level DEBUG

# 使用 WARNING 级别（只记录警告和错误）
researchos run --workspace ./workspace --log-level WARNING
```

### 7.3 Trace 分析

```bash
# 查看 trace
researchos trace --workspace ./workspace --run-id <run-id>

# 列出所有 trace
ls -la workspace/_runtime/traces/
```

---

## 八、容器环境问题

### 8.1 容器检测失败

**症状**:
```
docker_exec 工具行为异常
```

**可能原因**:
- 容器检测失败
- Docker 嵌套问题

**解决方案**:
```bash
# 检查是否在容器内运行
docker run --rm -it \
  researchos/system:latest \
  bash -c "test -f /.dockerenv && echo 'In container' || echo 'Not in container'"

# 查看日志
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  researchos/system:latest \
  run-task --workspace /workspace --task hello --mock --log-level DEBUG | grep docker_exec
```

### 8.2 容器内执行异常

**症状**:
```
容器内运行时行为异常
```

**解决方案**:
```bash
# 进入容器 shell 调试
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  --entrypoint bash \
  researchos/system:latest

# 容器内手动运行命令
python -m researchos.cli status --workspace /workspace
python -m researchos.cli run-task --workspace /workspace --task hello --mock
```

---

## 九、常见错误代码

| 错误代码 | 说明 | 解决方案 |
|---------|------|----------|
| `AuthenticationError` | API Key 无效 | 检查 OPENAI_API_KEY |
| `TimeoutError` | 请求超时 | 检查网络连接 |
| `ValidationError` | 输出验证失败 | 检查输出文件格式 |
| `FileNotFoundError` | 文件不存在 | 检查文件路径 |
| `ModuleNotFoundError` | 模块缺失 | 安装依赖 |
| `PermissionError` | 权限不足 | 修改文件权限 |
| `OSError` | 系统错误 | 检查磁盘空间 |

---

## 十、获取帮助

如果以上方法无法解决问题：

1. 查看 [Docker 使用指南](docker-usage.md)
2. 查看 [配置文档](configuration.md)
3. 查看 [快速开始指南](QUICKSTART.md)
4. 查看日志文件：`workspace/_runtime/logs/researchos.log`
5. 运行自检：`researchos selftest`
6. 提交 Issue：https://github.com/MengkunLiang/DIG-ResearchOS/issues

提交 Issue 时请包含：
- 错误信息
- 日志文件
- 运行环境（Docker 或宿主机）
- 复现步骤

---

**维护者**: ResearchOS 开发团队  
**最后更新**: 2026-04-21
