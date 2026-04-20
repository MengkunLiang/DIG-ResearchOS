# ResearchOS 故障排查指南

> **版本**: v1.0
> **更新日期**: 2026-04-20

---

## 一、环境问题

### 1.1 Conda 环境不一致警告

**症状**:
```
[env-warning] 检测到当前 shell 环境与实际解释器可能不一致。
当前 Python 解释器是 /usr/local/anaconda3/bin/python3.11，但激活的 conda 环境目录是 /home/liangmengkun/.conda/envs/claude-code。
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

### 1.2 依赖缺失

**症状**:
```
ModuleNotFoundError: No module named 'xxx'
```

**解决方案**:
```bash
# 安装缺失的包
pip install xxx

# 或重新安装所有依赖
pip install -e .
```

---

## 二、API 问题

### 2.1 API Key 未设置

**症状**:
```
AuthenticationError: Incorrect API key provided
```

**解决方案**:
```bash
export OPENAI_API_KEY=sk-your-actual-key-here
```

### 2.2 API 连接超时

**症状**:
```
TimeoutError: Request timed out
```

**解决方案**:
- 检查网络连接
- 增加超时时间（在 `config/runtime.yaml` 中配置）
- 稍后重试

---

## 三、Workflow 问题

### 3.1 任务找不到

**症状**:
```
Prerequisites not met for t1: Unknown task: t1
```

**解决方案**:
```bash
# 检查 state_machine.yaml 中的任务定义
cat config/state_machine.yaml | grep -A5 "states:"

# 可用任务包括: HELLO, T1, T2, T3, T3.5, T4, T4.5, T5, T6, T7, T8-WRITE, T8-DRAFT, T9
```

### 3.2 Validation 失败

**症状**:
```
error: Validation failed. Missing expected output: xxx
```

**解决方案**:
- 检查 workspace 目录结构
- 确保前置任务的输出文件已生成
- 查看 `logs/` 目录中的详细日志

### 3.3 用户交互失败

**症状**:
```
EOFError: EOF when reading a line
```

**解决方案**:
- 在交互式终端中运行
- 或预先准备好所有需要的输入文件

---

## 四、Skills 问题

### 4.1 Skills 加载失败

**症状**:
```
ConfigurationError: Skill tools must be a list
```

**解决方案**:
- 检查 SKILL.md 中的 `allowed-tools` 格式
- 确保是列表格式：`allowed_tools: [Bash(*), Read, Write]`
- 或逗号分隔字符串：`allowed_tools: "Bash(*), Read, Write"`

### 4.2 Skill 执行失败

**症状**:
```
ToolExecutionError: Skill execution failed
```

**解决方案**:
- 检查 skill 目录结构（必须有 SKILL.md）
- 检查 skill 的 allowed_tools 是否包含所需工具
- 查看日志获取详细错误信息

---

## 五、Docker 问题

### 5.1 Docker 不可用

**症状**:
```
Docker not available, falling back to host mode
```

**解决方案**:
```bash
# 安装 Docker
# macOS: brew install --cask docker
# Ubuntu: sudo apt-get install docker.io

# 确保 Docker 服务运行
sudo systemctl start docker

# 添加用户到 docker 组
sudo usermod -aG docker $USER
```

### 5.2 GPU 不可用

**症状**:
```
GPU requested but not available
```

**解决方案**:
- 检查 CUDA 安装：`nvidia-smi`
- 确保 project.yaml 中 `compute_resources.allow_gpu: false`
- 或在支持 GPU 的机器上运行

---

## 六、配置问题

### 6.1 配置文件解析错误

**症状**:
```
ConfigurationError: Invalid YAML
```

**解决方案**:
```bash
# 验证 YAML 语法
python -c "import yaml; yaml.safe_load(open('config/xxx.yaml'))"
```

### 6.2 模型配置错误

**症状**:
```
Invalid model name: gpt-5
```

**解决方案**:
- 检查 `config/model_routing.yaml`
- 使用有效的模型名：`gpt-4o`, `gpt-4-turbo`, `o3` 等

---

## 七、日志与调试

### 7.1 查看详细日志

```bash
# 设置日志级别
export LOG_LEVEL=DEBUG

# 运行命令
python -m researchos.cli --workspace /path/to/project run-task T1 --log-level DEBUG
```

### 7.2 追踪执行

```bash
# 查看运行历史
python -m researchos.cli --workspace /path/to/project trace <run_id>

# 查看状态
python -m researchos.cli --workspace /path/to/project status
```

### 7.3 验证配置

```bash
# 验证所有配置
python -m researchos.cli validate-config

# 验证 workspace
python -m researchos.cli --workspace /path/to/project validate
```

---

## 八、获取帮助

### 8.1 自检命令

```bash
# LLM 连通性测试
python -m researchos.cli selftest

# 完整配置验证
python -m researchos.cli validate-config
```

### 8.2 查看帮助

```bash
# 主帮助
python -m researchos.cli --help

# 子命令帮助
python -m researchos.cli run-task --help
python -m researchos.cli run-skill --help
```

### 8.3 反馈问题

- GitHub Issues: https://github.com/MengkunLiang/DIG-ResearchOS/issues
- 请提供:
  - 完整的错误信息
  - 复现步骤
  - 环境信息（Python 版本、操作系统等）