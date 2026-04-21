# ResearchOS 快速入门指南

> **版本**: v1.0  
> **更新日期**: 2026-04-21

---

## 一、环境准备

### 1.1 系统要求

**Docker 模式（推荐）**:
- Docker 20.10+
- （可选）nvidia-docker2（GPU 支持）
- 至少 20GB 磁盘空间

**宿主机模式（开发调试）**:
- Python 3.11
- Conda
- OpenAI API Key

### 1.2 选择运行模式

ResearchOS 支持两种运行模式：

| 模式 | 适用场景 | 优点 | 缺点 |
|------|---------|------|------|
| Docker | 生产部署、论文复现、快速体验 | 零配置、完全可复现 | 需要 Docker |
| 宿主机 | 开发调试、修改代码 | 直接调试、快速迭代 | 需要配置环境 |

---

## 二、Docker 模式快速开始（推荐）

### 2.1 构建镜像

```bash
# 克隆仓库
git clone https://github.com/MengkunLiang/DIG-ResearchOS.git
cd DIG-ResearchOS

# 构建 Docker 镜像
bash infra/docker/build.sh
```

### 2.2 设置环境变量

```bash
# 设置 API Key
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://api.openai.com/v1"
```

### 2.3 初始化 Workspace

```bash
# 创建 workspace 目录
mkdir -p workspace

# 初始化 workspace
bash infra/docker/run.sh init-workspace --workspace /workspace
```

### 2.4 运行任务

```bash
# 运行 Mock 模式测试（不需要 API Key）
bash infra/docker/run.sh run-task --workspace /workspace --task hello --mock

# 运行完整 pipeline
bash infra/docker/run.sh run --workspace /workspace

# 运行单个任务
bash infra/docker/run.sh run-task --workspace /workspace --task T1
```

### 2.5 查看日志

```bash
# 实时查看日志
tail -f workspace/_runtime/logs/researchos.log

# 查看最近 100 行
tail -n 100 workspace/_runtime/logs/researchos.log

# 搜索错误
grep "ERROR" workspace/_runtime/logs/researchos.log
```

---

## 三、宿主机模式快速开始（开发调试）

### 3.1 安装依赖

```bash
# 克隆仓库
git clone https://github.com/MengkunLiang/DIG-ResearchOS.git
cd DIG-ResearchOS

# 创建并激活 conda 环境
conda env create -f environment.yml
conda activate researchos

# 安装 ResearchOS
pip install -e '.[dev]'
```

### 3.2 设置环境变量

```bash
# 创建 .env 文件
cat > .env <<EOF
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=https://api.openai.com/v1
EOF
```

### 3.3 初始化 Workspace

```bash
# 初始化 workspace
python -m researchos.cli init-workspace --workspace ./workspace/demo

# 或使用便捷命令
researchos init-workspace --workspace ./workspace/demo
```

### 3.4 运行任务

```bash
# 运行 Mock 模式测试
python -m researchos.cli run-task --workspace ./workspace/demo --task hello --mock

# 运行完整 pipeline
python -m researchos.cli run --workspace ./workspace/demo

# 运行单个任务
python -m researchos.cli run-task --workspace ./workspace/demo --task T1
```

---

## 四、项目结构

```
workspace/
├── project.yaml              # 项目配置
├── _runtime/                 # 运行时数据
│   ├── traces/              # 执行跟踪
│   ├── logs/                # 日志
│   └── state.yaml           # 状态机状态
├── user_seeds/              # 用户输入
│   ├── seed_papers.jsonl    # 种子论文
│   ├── seed_ideas.md        # 初始想法
│   ├── seed_constraints.md  # 研究约束
│   └── seed_external_resources.jsonl  # 外部资源
├── literature/              # 文献数据（T2-T3）
│   ├── papers_raw.jsonl
│   ├── papers_dedup.jsonl
│   ├── paper_notes/
│   ├── comparison_table.csv
│   └── synthesis.md
├── ideation/                # 假设生成（T4-T4.5）
│   ├── hypotheses.md
│   ├── exp_plan.yaml
│   ├── risks.md
│   └── novelty_audit.md
├── pilot/                   # 试点实验（T5）
│   ├── pilot_results.json
│   ├── motivation_validation.md
│   └── docker_digests.txt
├── novelty/                 # 新颖性验证（T6）
│   ├── novelty_report.md
│   ├── collision_cases.md
│   └── must_add_baselines.md
├── experiments/             # 完整实验（T7）
│   ├── results_summary.json
│   ├── iteration_log.md
│   ├── ablations.csv
│   └── docker_digests.txt
├── evaluation/              # 评估（T7.5）
│   └── evaluation_decision.md
├── drafts/                  # 论文草稿（T8）
│   ├── outline.md
│   ├── paper.tex
│   ├── self_check.md
│   └── review_rounds/
└── submission/              # 投稿准备（T9）
    ├── bundle/
    ├── migration_report.md
    └── bundle.zip
```

---

## 五、常用命令

### 5.1 Docker 模式

```bash
# 显示帮助
bash infra/docker/run.sh --help

# 初始化 workspace
bash infra/docker/run.sh init-workspace --workspace /workspace

# 运行完整 pipeline
bash infra/docker/run.sh run --workspace /workspace

# 运行单个任务
bash infra/docker/run.sh run-task --workspace /workspace --task <task-name>

# 查看状态
bash infra/docker/run.sh status --workspace /workspace

# 查看 trace
bash infra/docker/run.sh trace --workspace /workspace --run-id <run-id>

# 进入容器 shell
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  --entrypoint bash \
  researchos/system:latest
```

### 5.2 宿主机模式

```bash
# 显示帮助
researchos --help

# 初始化 workspace
researchos init-workspace --workspace ./workspace/demo

# 运行完整 pipeline
researchos run --workspace ./workspace/demo

# 运行单个任务
researchos run-task --workspace ./workspace/demo --task <task-name>

# 查看状态
researchos status --workspace ./workspace/demo

# 查看 trace
researchos trace --workspace ./workspace/demo --run-id <run-id>

# 运行自检
researchos selftest
```

---

## 六、工作流程

ResearchOS 的完整工作流程包含以下阶段：

```
T1 (PI Agent - init)
  ↓ 项目配置、种子数据
T2 (Scout Agent)
  ↓ 论文检索、去重
T3 (Reader Agent - read)
  ↓ 论文笔记
T3.5 (Reader Agent - synthesize)
  ↓ 文献综述
T4 (Ideation Agent)
  ↓ 研究假设、实验计划
T4.5 (NoveltyAuditor Agent)
  ↓ 新颖性预审
T5 (Experimenter Agent - pilot)
  ↓ 试点实验结果
T6 (Novelty Agent)
  ↓ 新颖性最终验证
T7 (Experimenter Agent - full)
  ↓ 完整实验结果
T7.5 (PI Agent - evaluate)
  ↓ 评估决策
T8 (Writer + Reviewer Agents) ⚠️ 规划中
  ↓ 论文草稿
T9 (Submission Agent) ⚠️ 规划中
  ↓ 投稿包
```

---

## 七、配置说明

### 7.1 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `OPENAI_API_KEY` | ✅ | OpenAI API Key |
| `OPENAI_BASE_URL` | ❌ | OpenAI API Base URL（默认：https://api.openai.com/v1）|

### 7.2 配置文件

| 文件 | 位置 | 说明 |
|------|------|------|
| `runtime.yaml` | `config/` | Runtime 核心配置 |
| `model_routing.yaml` | `config/` | 模型路由配置 |
| `state_machine.yaml` | `config/` | 状态机定义 |
| `mcp.yaml` | `config/` | MCP 工具配置（可选）|
| `gates.yaml` | `config/` | Gate 配置（可选）|

详细配置说明请参考 [配置文档](configuration.md)。

---

## 八、故障排查

### 8.1 Docker 模式常见问题

**问题 1：镜像构建失败**
```bash
# 检查 Docker 是否安装
docker --version

# 检查磁盘空间
df -h

# 清理 Docker 缓存
docker system prune -a
```

**问题 2：容器无法访问 GPU**
```bash
# 检查 nvidia-docker2
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi

# 重新安装 nvidia-docker2
sudo apt-get install nvidia-docker2
sudo systemctl restart docker
```

**问题 3：环境变量未生效**
```bash
# 检查环境变量
docker run --rm -it \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  researchos/system:latest \
  bash -c "echo \$OPENAI_API_KEY"
```

### 8.2 宿主机模式常见问题

**问题 1：依赖安装失败**
```bash
# 更新 conda
conda update conda

# 重新创建环境
conda env remove -n researchos
conda env create -f environment.yml
```

**问题 2：API 调用失败**
```bash
# 检查环境变量
echo $OPENAI_API_KEY

# 测试 API 连接
curl -H "Authorization: Bearer $OPENAI_API_KEY" \
     $OPENAI_BASE_URL/models
```

**问题 3：日志文件不存在**
```bash
# 初始化 workspace
researchos init-workspace --workspace ./workspace/demo

# 检查日志目录
ls -la workspace/demo/_runtime/logs/
```

更多故障排查信息请参考 [故障排查指南](TROUBLESHOOTING.md)。

---

## 九、下一步

- 阅读 [Docker 使用指南](docker-usage.md) - 了解 Docker 模式的详细用法
- 阅读 [配置文档](configuration.md) - 了解如何自定义配置
- 阅读 [Agent 文档](agents/README.md) - 了解各个 Agent 的功能
- 阅读 [开发指南](AGENT_DEVELOPMENT_GUIDE.md) - 了解如何开发新的 Agent

---

## 十、获取帮助

如有问题，请：

1. 查看本文档的"故障排查"部分
2. 查看 [故障排查指南](TROUBLESHOOTING.md)
3. 查看日志文件：`workspace/_runtime/logs/researchos.log`
4. 提交 Issue：https://github.com/MengkunLiang/DIG-ResearchOS/issues

---

**维护者**: ResearchOS 开发团队  
**最后更新**: 2026-04-21
