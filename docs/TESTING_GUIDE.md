# ResearchOS 测试指南

> **文档版本**: v1.0
> **更新日期**: 2026-04-21
> **适用版本**: ResearchOS Runtime

---

## 一、测试概述

### 1.1 测试类型

| 类型 | 说明 | 运行命令 |
|------|------|----------|
| 单元测试 | 测试单个模块功能 | `pytest tests/unit/` |
| 集成测试 | 测试多模块协作 | `pytest tests/integration/` |
| 真实 API 测试 | 使用真实 LLM API | `python scripts/test_all_agents_real_api.py` |
| 链路测试 | 测试 Agent 协作链 | `python scripts/test_collab_chain.py` |
| 断点恢复测试 | 测试暂停/恢复机制 | `python scripts/test_resume_mechanism.py` |

### 1.2 测试环境要求

- Python 3.11+
- conda 环境 `researchos`
- OpenAI API Key（真实 API 测试需要）
- Docker（可选，用于 Docker 执行测试）

---

## 二、快速开始

### 2.1 运行所有单元测试

```bash
cd /home/liangmengkun/ResearchOS
conda activate researchos
python -m pytest tests/unit/ -v
```

**预期结果**: 324 个测试全部通过

### 2.2 运行单个 Agent 测试

```bash
# 测试 hello agent
python scripts/test_all_agents_real_api.py --agent hello

# 测试 pi agent
python scripts/test_all_agents_real_api.py --agent pi

# 测试 reader agent
python scripts/test_all_agents_real_api.py --agent reader
```

### 2.3 运行完整真实 API 测试

```bash
python scripts/test_all_agents_real_api.py --verbose
```

**预期结果**: 10/10 Agent 测试通过

---

## 三、详细测试说明

### 3.1 单元测试

#### 3.1.1 测试 Agent 实现

```bash
# 测试所有 Agent
python -m pytest tests/unit/test_*_agent.py -v

# 测试特定 Agent
python -m pytest tests/unit/test_pi_agent.py -v
python -m pytest tests/unit/test_scout_agent.py -v
python -m pytest tests/unit/test_reader_agent.py -v
```

#### 3.1.2 测试 State Machine

```bash
python -m pytest tests/unit/test_state_machine*.py -v
```

#### 3.1.3 测试 Skills 系统

```bash
python -m pytest tests/unit/test_skill*.py -v
python -m pytest tests/unit/test_skills*.py -v
```

#### 3.1.4 测试 Docker 执行

```bash
python -m pytest tests/unit/test_search_and_docker_tools.py -v
```

### 3.2 集成测试

#### 3.2.1 多 Agent 协作链测试

```bash
python scripts/test_collab_chain.py --workspace /tmp/collab_chain_test --verbose
```

**测试内容**:
- T3-Reader → T4-Ideation → T5-Experimenter 数据流
- 文件命名和路径验证
- 输出格式校验

#### 3.2.2 T5 Pilot 集成测试

```bash
python scripts/test_t5_pilot.py
```

**测试内容**:
- Pilot workspace 准备
- ExperimenterAgent (pilot 模式) 生成 prompt
- 输出校验逻辑

### 3.3 高级测试

#### 3.3.1 断点恢复机制测试

```bash
python scripts/test_resume_mechanism.py --verbose
```

**测试内容**:
- PAUSED 状态检测
- WAITING_HUMAN 状态检测
- Resume 场景检测
- 状态持久化

#### 3.3.2 内容质量测试

```bash
python scripts/test_content_quality.py --verbose
```

**测试内容**:
- 引用幻觉检测
- 数字幻觉检测
- 逻辑一致性检测
- LaTeX 编译测试

#### 3.3.3 Docker 执行测试

```bash
python scripts/test_docker_exec.py --verbose
```

**测试内容**:
- 容器检测机制
- Docker CLI 可用性
- 镜像白名单机制

### 3.4 CLI 测试

```bash
# LLM 连接测试
python -m researchos.cli selftest

# Workspace 初始化
python -m researchos.cli --workspace /tmp/test_eval init-workspace

# 运行单个 task
python -m researchos.cli --workspace /tmp/test_eval run-task HELLO
```

---

## 四、测试配置

### 4.1 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `OPENAI_API_KEY` | OpenAI API 密钥 | 是 |
| `OPENAI_BASE_URL` | API 端点 | 否 |
| `RESEARCHOS_NO_BANNER` | 禁用启动 banner | 否 |

### 4.2 配置文件

| 文件 | 用途 |
|------|------|
| `config/state_machine.yaml` | 工作流状态定义 |
| `config/runtime.yaml` | 运行时配置 |
| `config/model_routing.yaml` | 模型路由配置 |
| `config/gates.yaml` | Gate 配置 |

### 4.3 Skills 配置

```bash
# 验证 Skills 加载
python -c "from researchos.skills.loader import discover_skills; from pathlib import Path; print(discover_skills(Path('skills')))"
```

---

## 五、常见问题

### 5.1 测试失败排查

#### 5.1.1 ImportError

```
ModuleNotFoundError: No module named 'researchos'
```

**解决方案**:
```bash
pip install -e .
```

#### 5.1.2 API Key 错误

```
AuthenticationError: Invalid API key
```

**解决方案**:
```bash
export OPENAI_API_KEY="your-correct-key"
```

#### 5.1.3 Docker 不可用

```
Docker exec timed out
```

**解决方案**: 确保 Docker 已安装并运行，或检查 `config/runtime.yaml` 中的 `execution.mode`

### 5.2 测试超时

**解决方案**:
```bash
# 增加超时时间
export RESEARCHOS_TIMEOUT=300
```

### 5.3 清理测试环境

```bash
# 清理临时文件
rm -rf /tmp/test_*

# 清理 Docker 容器
docker container prune -f
```

---

## 六、测试覆盖

### 6.1 Agent 测试覆盖

**所有 11 个 Agent 已实现并测试通过**:

| Agent | 单元测试 | 真实 API 测试 | 说明 |
|-------|----------|--------------|------|
| HelloAgent | ✅ | ✅ | 系统测试 |
| PIAgent | ✅ | ✅ | 项目初始化和评估 |
| ScoutAgent | ✅ | ✅ | 文献检索 |
| ReaderAgent | ✅ | ✅ | 文献阅读和综述 |
| IdeationAgent | ✅ | ✅ | 假设生成 |
| NoveltyAuditorAgent | ✅ | ✅ | 新颖性预审 |
| ExperimenterAgent | ✅ | ✅ | 实验执行（pilot/full） |
| NoveltyAgent | ✅ | ✅ | 新颖性验证 |
| WriterAgent | ✅ | ✅ | 论文撰写 |
| ReviewerAgent | ✅ | ✅ | 论文审查 |
| SubmissionAgent | ✅ | ✅ | 投稿准备 |

**测试统计**:
- 单元测试：324 个测试通过
- 真实 API 测试：10/10 Agent 通过
- 集成测试：T3→T4→T5 协作链通过

### 6.2 系统功能测试覆盖

| 功能 | 测试状态 |
|------|----------|
| State Machine | ✅ 完整 |
| Skills System | ✅ 完整 |
| Docker Execution | ✅ 完整 |
| Resume Mechanism | ✅ 完整 |
| Content Quality | ✅ 完整 |

---

## 七、持续集成

### 7.1 本地 CI 检查

```bash
# 运行所有检查
python -m pytest tests/unit/ -v
python scripts/test_resume_mechanism.py
python scripts/test_content_quality.py
```

### 7.2 测试报告

```bash
# 生成 HTML 报告
python -m pytest tests/unit/ --html=report.html --self-contained-html

# 生成覆盖率报告
python -m pytest tests/unit/ --cov=researchos --cov-report=html
```

---

## 八、联系与支持

- **项目主页**: https://github.com/MengkunLiang/DIG-ResearchOS
- **问题反馈**: GitHub Issues
- **文档**: [docs/](docs/)
