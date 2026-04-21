# ResearchOS 本机测试指南

本文档提供 ResearchOS 系统的完整测试指南，包括环境准备、单元测试、集成测试和端到端测试。

## 目录

- [环境准备](#环境准备)
- [快速测试](#快速测试)
- [单元测试](#单元测试)
- [集成测试](#集成测试)
- [端到端测试](#端到端测试)
- [Docker 测试](#docker-测试)
- [性能测试](#性能测试)
- [故障排除](#故障排除)

---

## 环境准备

### 1. 系统要求

**最低配置**：
- CPU: 4 核
- 内存: 8GB
- 磁盘: 20GB 可用空间
- 操作系统: Ubuntu 22.04 / macOS 12+ / Windows 11 (WSL2)

**推荐配置**：
- CPU: 8 核
- 内存: 16GB
- 磁盘: 50GB 可用空间
- GPU: NVIDIA GPU (CUDA 12.4+)

### 2. 依赖安装

#### 方式 A：使用 Conda（推荐）

```bash
# 创建 conda 环境
conda create -n researchos python=3.11 -y
conda activate researchos

# 安装依赖
cd /home/liangmengkun/ResearchOS
pip install -r requirements.txt
pip install -r requirements-llm.txt
pip install -r requirements-dev.txt

# 安装 ResearchOS（可编辑模式）
pip install -e .
```

#### 方式 B：使用 Docker

```bash
cd /home/liangmengkun/ResearchOS

# 构建镜像
bash infra/docker/build.sh

# 验证镜像
docker images | grep researchos
```

### 3. 环境变量配置

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，填入你的 API Key
nano .env
```

**必需配置**：
```bash
OPENAI_API_KEY=sk-your-openai-api-key-here
OPENAI_BASE_URL=https://api.openai.com/v1
```

**可选配置**：
```bash
S2_API_KEY=your-semantic-scholar-api-key
RESEARCHER_EMAIL=your-email@example.com
ANTHROPIC_API_KEY=your-anthropic-api-key
```

### 4. 验证安装

```bash
# 检查 Python 环境
python --version  # 应该是 3.11.x

# 检查 ResearchOS 安装
researchos --help

# 检查 LLM 连接
researchos selftest
```

---

## 快速测试

### 系统自检

```bash
# 完整自检（包括 LLM 连接测试）
researchos selftest

# 预期输出：
# ✓ LLM endpoint reachable
# ✓ Model routing configured
# ✓ Workspace writable
```

### 运行单个测试

```bash
# 测试单个文件
pytest tests/unit/test_container_detection.py -v

# 测试单个函数
pytest tests/unit/test_container_detection.py::test_is_running_in_container -v
```

### 运行所有单元测试

```bash
# 运行所有单元测试（快速）
pytest tests/unit/ -v

# 预期时间：2-5 分钟
# 预期结果：所有测试通过
```

---

## 单元测试

### 测试覆盖率

```bash
# 运行测试并生成覆盖率报告
pytest tests/unit/ --cov=researchos --cov-report=html --cov-report=term

# 查看 HTML 报告
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
```

**目标覆盖率**：
- 核心模块（runtime, orchestration）: 80%+
- Agent 模块: 70%+
- 工具模块: 75%+

### 按模块测试

#### 1. Runtime 核心测试

```bash
# 容器检测
pytest tests/unit/test_container_detection.py -v

# 工具注册
pytest tests/unit/test_tool_registry.py -v

# 状态机
pytest tests/unit/test_state_machine.py -v
```

#### 2. Agent 测试

```bash
# PI Agent
pytest tests/unit/test_pi_agent.py -v

# Scout Agent
pytest tests/unit/test_scout_agent.py -v

# Experimenter Agent
pytest tests/unit/test_experimenter_agent.py -v
```

#### 3. 工具测试

```bash
# 文件系统工具
pytest tests/unit/test_filesystem_tools.py -v

# Docker 执行工具
pytest tests/unit/test_docker_exec.py -v

# 种子上传工具
pytest tests/unit/test_upload_seed_materials.py -v

# LaTeX 编译工具
pytest tests/unit/test_latex_compile.py -v
```

#### 4. 鲁棒性增强测试

```bash
# 声明追溯验证
pytest tests/unit/test_claim_traceability.py -v

# 数值一致性验证
pytest tests/unit/test_number_consistency.py -v

# 迭代死锁检测
pytest tests/unit/test_iteration_deadlock.py -v

# 机制相似度搜索
pytest tests/unit/test_mechanism_similarity.py -v

# Seed ensemble
pytest tests/unit/test_seed_ensemble.py -v

# Clone repo
pytest tests/unit/test_clone_repo.py -v
```

### 测试选项

```bash
# 详细输出
pytest tests/unit/ -v

# 显示打印输出
pytest tests/unit/ -s

# 只运行失败的测试
pytest tests/unit/ --lf

# 并行运行（需要 pytest-xdist）
pytest tests/unit/ -n auto

# 停在第一个失败
pytest tests/unit/ -x

# 生成 JUnit XML 报告
pytest tests/unit/ --junitxml=test-results.xml
```

---

## 集成测试

### 1. 工作空间初始化测试

```bash
# 创建测试工作空间
researchos init-workspace \
  --workspace /tmp/test-workspace \
  --project-id test-project \
  --topic "Test Topic"

# 验证目录结构
ls -la /tmp/test-workspace/

# 预期输出：
# - project.yaml
# - state.yaml
# - .researchos/
# - literature/
# - ideation/
# - experiments/
```

### 2. 单任务执行测试

```bash
# 测试 T1 PI Agent（使用 mock 模式）
researchos run-task T1_PI \
  --workspace /tmp/test-workspace \
  --mock

# 验证输出
cat /tmp/test-workspace/project.yaml
cat /tmp/test-workspace/.researchos/traces/*.jsonl
```

### 3. 工具集成测试

#### 种子上传工具测试

```bash
# 准备测试文件
echo "Test PDF content" > /tmp/test.pdf
echo "Test data" > /tmp/test_data.csv
mkdir -p /tmp/test_code && echo "print('hello')" > /tmp/test_code/main.py

# 测试 PDF 上传
python -c "
import asyncio
from pathlib import Path
from researchos.tools.upload_seed_materials import UploadSeedPdfTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy

async def test():
    policy = WorkspaceAccessPolicy(Path('/tmp/test-workspace'), [''], [''])
    tool = UploadSeedPdfTool(policy)
    result = await tool.execute(
        source_path='/tmp/test.pdf',
        paper_id='test_paper_001',
        metadata={'title': 'Test Paper'}
    )
    print(result.content)

asyncio.run(test())
"

# 验证上传结果
ls -la /tmp/test-workspace/user_seeds/pdfs/
```

#### LaTeX 编译测试

```bash
# 创建测试 LaTeX 文件
mkdir -p /tmp/test-workspace/drafts
cat > /tmp/test-workspace/drafts/test.tex << 'EOF'
\documentclass{article}
\begin{document}
Hello, ResearchOS!
\end{document}
EOF

# 测试编译（容器内模式）
python -c "
import asyncio
from pathlib import Path
from researchos.tools.latex_compile import LatexCompileTool
from researchos.tools.docker_exec import DockerExecTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy

async def test():
    policy = WorkspaceAccessPolicy(Path('/tmp/test-workspace'), ['', 'drafts/'], ['', 'drafts/'])
    docker_tool = DockerExecTool(policy)
    tool = LatexCompileTool(docker_tool)
    result = await tool.execute(tex_path='drafts/test.tex')
    print(result.content)

asyncio.run(test())
"

# 验证 PDF 生成
ls -la /tmp/test-workspace/drafts/test.pdf
```

### 4. Skill 系统测试

```bash
# 列出所有可用 skills
researchos list-skills --workspace /tmp/test-workspace

# 详细模式
researchos list-skills --workspace /tmp/test-workspace --verbose

# 运行一个 skill（如果有）
# researchos run-skill example-skill --workspace /tmp/test-workspace
```

---

## 端到端测试

### 1. 完整 Pipeline 测试（Mock 模式）

```bash
# 创建测试工作空间
researchos init-workspace \
  --workspace /tmp/e2e-test \
  --project-id e2e-test \
  --topic "End-to-End Test"

# 运行完整 pipeline（使用 mock 模式避免实际 LLM 调用）
# 注意：需要在代码中实现 mock 模式
researchos run \
  --workspace /tmp/e2e-test \
  --skip-startup-selftest

# 监控进度
tail -f /tmp/e2e-test/.researchos/logs/researchos.log
```

### 2. 单阶段端到端测试

#### T1 PI Agent 端到端

```bash
# 准备输入
mkdir -p /tmp/t1-test
cat > /tmp/t1-test/project.yaml << 'EOF'
project_id: t1-test
topic: "Improving Neural Machine Translation"
research_direction: "NLP"
domain: "Machine Learning"
EOF

# 运行 T1
researchos run-task T1_PI --workspace /tmp/t1-test

# 验证输出
cat /tmp/t1-test/project.yaml
cat /tmp/t1-test/ideation/research_questions.md
```

#### T2 Scout Agent 端到端

```bash
# 准备输入（需要 T1 输出）
# ... (复制 T1 输出)

# 创建种子论文列表
cat > /tmp/t2-test/seed_papers.jsonl << 'EOF'
{"title": "Attention Is All You Need", "arxiv_id": "1706.03762"}
{"title": "BERT: Pre-training of Deep Bidirectional Transformers", "arxiv_id": "1810.04805"}
EOF

# 运行 T2
researchos run-task T2_SCOUT --workspace /tmp/t2-test

# 验证输出
ls -la /tmp/t2-test/literature/
cat /tmp/t2-test/literature/literature_summary.md
```

### 3. Experimenter 调试循环测试

```bash
# 创建测试实验代码（故意包含错误）
mkdir -p /tmp/exp-test/experiments/code
cat > /tmp/exp-test/experiments/code/train.py << 'EOF'
import sys
# 故意缺少依赖
import transformers_missing_module

def main():
    print("Training...")

if __name__ == "__main__":
    main()
EOF

# 运行 Experimenter（应该自动检测并修复错误）
researchos run-task T7_EXPERIMENTER_FULL --workspace /tmp/exp-test

# 验证调试日志
cat /tmp/exp-test/experiments/runs/*/debug_log.txt
```

---

## Docker 测试

### 1. 构建测试

```bash
# 构建镜像
cd /home/liangmengkun/ResearchOS
bash infra/docker/build.sh

# 验证镜像大小（应该 < 10GB）
docker images researchos/system:latest

# 验证镜像内容
docker run --rm researchos/system:latest python --version
docker run --rm researchos/system:latest researchos --help
```

### 2. 容器内测试

```bash
# 运行容器并进入 shell
docker run --rm -it \
  -v $(pwd)/workspace:/workspace \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  researchos/system:latest \
  bash

# 在容器内运行测试
pytest tests/unit/ -v

# 验证容器检测
python -c "from researchos.runtime.container_detection import is_running_in_container; print(is_running_in_container())"
# 预期输出：True
```

### 3. Docker Compose 测试

```bash
# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f

# 运行命令
docker-compose run --rm researchos selftest

# 停止服务
docker-compose down
```

### 4. GPU 测试（如果有 GPU）

```bash
# 验证 GPU 可用性
docker run --rm --gpus all \
  researchos/system:latest \
  python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"

# 预期输出：CUDA available: True
```

---

## 性能测试

### 1. 启动时间测试

```bash
# 测试 CLI 启动时间
time researchos --help

# 预期：< 2 秒
```

### 2. 工具执行性能测试

```bash
# 测试文件读写性能
pytest tests/performance/test_filesystem_performance.py -v

# 测试 Docker 执行性能
pytest tests/performance/test_docker_exec_performance.py -v
```

### 3. 内存使用测试

```bash
# 监控内存使用
/usr/bin/time -v researchos run-task T1_PI --workspace /tmp/perf-test

# 查看 Maximum resident set size
```

---

## 故障排除

### 常见问题

#### 1. 测试失败：ModuleNotFoundError

**问题**：
```
ModuleNotFoundError: No module named 'researchos'
```

**解决**：
```bash
# 确保在正确的 conda 环境
conda activate researchos

# 重新安装
pip install -e .
```

#### 2. Docker 构建失败

**问题**：
```
ERROR: failed to solve: process "/bin/sh -c pip install ..." did not complete successfully
```

**解决**：
```bash
# 清理 Docker 缓存
docker builder prune -af

# 使用国内镜像重新构建
bash infra/docker/build.sh
```

#### 3. GPU 不可用

**问题**：
```
CUDA available: False
```

**解决**：
```bash
# 安装 nvidia-container-toolkit
sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker

# 验证
docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi
```

#### 4. LLM 连接失败

**问题**：
```
researchos selftest
✗ LLM endpoint unreachable
```

**解决**：
```bash
# 检查环境变量
echo $OPENAI_API_KEY
echo $OPENAI_BASE_URL

# 测试网络连接
curl -H "Authorization: Bearer $OPENAI_API_KEY" \
  $OPENAI_BASE_URL/models

# 检查代理设置
echo $HTTP_PROXY
echo $HTTPS_PROXY
```

#### 5. 权限错误

**问题**：
```
PermissionError: [Errno 13] Permission denied: '/workspace/...'
```

**解决**：
```bash
# 修复工作空间权限
sudo chown -R $USER:$USER /tmp/test-workspace

# 或在 Docker 中使用正确的用户
docker run --rm -it --user $(id -u):$(id -g) ...
```

### 调试技巧

#### 1. 启用详细日志

```bash
# 设置日志级别为 DEBUG
export LOG_LEVEL=DEBUG
researchos run-task T1_PI --workspace /tmp/debug-test --log-level DEBUG
```

#### 2. 查看 Trace 文件

```bash
# 列出所有 trace
ls -la /tmp/test-workspace/.researchos/traces/

# 查看最新的 trace
researchos trace <run_id> --workspace /tmp/test-workspace

# 查看原始 JSONL
researchos trace <run_id> --workspace /tmp/test-workspace --raw
```

#### 3. 使用 Python 调试器

```python
# 在测试中添加断点
import pdb; pdb.set_trace()

# 或使用 ipdb（更友好）
import ipdb; ipdb.set_trace()
```

#### 4. 检查状态文件

```bash
# 查看当前状态
researchos status --workspace /tmp/test-workspace

# 手动检查 state.yaml
cat /tmp/test-workspace/state.yaml
```

---

## 测试清单

在提交代码前，确保以下测试全部通过：

- [ ] 所有单元测试通过（`pytest tests/unit/ -v`）
- [ ] 测试覆盖率 > 75%（`pytest --cov=researchos`）
- [ ] 系统自检通过（`researchos selftest`）
- [ ] Docker 镜像构建成功（`bash infra/docker/build.sh`）
- [ ] 容器内测试通过（`docker run ... pytest tests/unit/`）
- [ ] 工作空间初始化测试通过
- [ ] 至少一个端到端测试通过
- [ ] 文档更新完成
- [ ] 代码格式检查通过（`black . && isort .`）
- [ ] 类型检查通过（`mypy researchos`）

---

## 持续集成

### GitHub Actions 配置

```yaml
# .github/workflows/test.yml
name: Test

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: pip install -e .
      - run: pytest tests/unit/ --cov=researchos --cov-report=xml
      - uses: codecov/codecov-action@v3
```

---

## 参考资料

- [pytest 文档](https://docs.pytest.org/)
- [Docker 测试最佳实践](https://docs.docker.com/develop/dev-best-practices/)
- [Python 单元测试指南](https://docs.python.org/3/library/unittest.html)
- [ResearchOS 开发指南](./AGENT_DEVELOPMENT_GUIDE.md)
