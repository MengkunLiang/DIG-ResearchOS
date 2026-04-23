# Scripts 目录说明

本文档介绍 `/home/liangmengkun/ResearchOS/scripts/` 目录下所有调试和测试脚本。

---

## 快速使用

```bash
# 进入 conda 环境
conda activate researchos

# 调试 HelloAgent (Mock模式)
python scripts/debug_hello_agent.py --mock

# 调试 T1 PIAgent (Mock模式)
python scripts/debug_t1_agent.py --mock

# 调试 T2 ScoutAgent (Mock模式)
python scripts/debug_t2_agent.py --mock

# T1+T2 真实API调试
python scripts/real_debug_t1_t2.py --all

# 测试所有 Agent
python scripts/test_all_agents_real_api.py
```

---

## 调试脚本 (Debug)

### debug_hello_agent.py - HelloAgent 调试

**用途**: 调试最小的 HelloAgent，验证 AgentRunner 基础功能。

**用法**:
```bash
python scripts/debug_hello_agent.py --mock [--workspace ./workspace]
```

**参数**:
- `--mock`: 使用 Mock LLM（必须）
- `--workspace`: 工作目录，默认 `./workspace`

**输出产物**:
- `hello.txt`: Hello 测试文件
- `_runtime/traces/`: 执行轨迹

---

### debug_t1_agent.py - T1 PIAgent 调试

**用途**: 调试项目初始化 Agent (PIAgent)，验证 init 模式。

**用法**:
```bash
python scripts/debug_t1_agent.py --mock [--workspace ./workspace/debug_t1]
```

**参数**:
- `--mock`: 使用 Mock LLM（必须）
- `--workspace`: 工作目录

**输出产物**:
- `project.yaml`: 项目配置
- `user_seeds/seed_papers.jsonl`: 种子论文
- `user_seeds/seed_ideas.md`: 种子想法
- `user_seeds/seed_constraints.md`: 约束条件

---

### debug_t2_agent.py - T2 ScoutAgent 调试

**用途**: 调试文献普查 Agent (ScoutAgent)，验证 search 模式。

**用法**:
```bash
python scripts/debug_t2_agent.py --mock [--workspace ./workspace/debug_t2]
```

**参数**:
- `--mock`: 使用 Mock LLM（必须）
- `--workspace`: 工作目录

**前置条件**: 需要存在 `project.yaml`

**输出产物**:
- `literature/papers_raw.jsonl`: 原始检索结果
- `literature/papers_dedup.jsonl`: 去重后论文
- `literature/search_log.md`: 检索日志
- `literature/missing_areas.md`: 文献缺口分析

---

### debug_arxiv_api.py - arXiv API 调试

**用途**: 独立调试 arXiv API，验证 API 调用、速率限制、重试机制。

**用法**:
```bash
pip install httpx
python scripts/debug_arxiv_api.py
```

**测试内容**:
1. 基本 API 调用
2. 带延迟的多次调用
3. 获取特定 arXiv ID
4. 带重试机制的调用
5. 数据真实性验证

---

### real_debug_t1_t2.py - T1+T2 真实 API 调试

**用途**: 使用真实 LLM 和 API 运行 T1+T2，验证端到端流程。

**用法**:
```bash
# 运行完整流程
python scripts/real_debug_t1_t2.py --all

# 只运行 T1
python scripts/real_debug_t1_t2.py --run-t1

# 只运行 T2 (需要先运行 T1)
python scripts/real_debug_t1_t2.py --run-t2

# 自定义参数
python scripts/real_debug_t1_t2.py --all \
    --workspace ./workspace/real_debug \
    --topic "agent memory retrieval"
```

**参数**:
- `--workspace`: 工作目录
- `--topic`: 研究主题
- `--run-t1`: 运行 T1
- `--run-t2`: 运行 T2
- `--all`: 运行 T1 和 T2

---

## T1 Agent 测试

### test_t1_simple.py

**用途**: 使用 SingleTaskRunner 测试 T1 PIAgent 的基本功能。

**注意**: 此脚本包含硬编码的 API key，仅用于开发测试。

### test_t1_with_topic.py

**用途**: 测试通过 ExecutionContext.extra 传递 user_topic 的方式运行 T1。

**注意**: 此脚本包含硬编码的 API key，仅用于开发测试。

---

## T2 Agent 测试

### test_t2_scout.py

**用途**: T2 ScoutAgent 单元测试。

### test_t2_t4.py

**用途**: T2+T4 联合测试，验证文献普查到创意生成的流程。

---

## T3 Agent 测试

### test_t3_simple.py

**用途**: T3 简单测试。

### test_t3_reader.py

**用途**: T3 Reader 测试，验证论文阅读能力。

### test_t3_5_synthesis.py

**用途**: T3+T5 综合测试，验证阅读到综合的流程。

---

## T4 Agent 测试

### test_t4_simple.py

**用途**: T4 简单测试。

### test_t4_ideation.py

**用途**: T4 Ideation 测试，验证创意生成能力。

---

## T5/T6 Agent 测试

### test_t5_pilot.py

**用途**: T5 Pilot 测试，验证试点实验能力。

### test_t6.py

**用途**: T6 测试，验证最终报告生成能力。

---

## 综合测试

### test_all_agents_real_api.py - 所有 Agent 真实 API 测试

**用途**: 使用真实 LLM 测试所有 Agent。

**用法**:
```bash
python scripts/test_all_agents_real_api.py
python scripts/test_all_agents_real_api.py --agent hello
python scripts/test_all_agents_real_api.py --workspace /tmp/test_agents --verbose
```

**参数**:
- `--agent`: 指定测试的 Agent（可选，默认测试所有）
- `--workspace`: 工作目录
- `--verbose`: 详细输出

---

### test_collab_chain.py - 协作链测试

**用途**: 测试多个 Agent 之间的协作流程。

---

### test_content_quality.py - 内容质量测试

**用途**: 验证 Agent 产出的内容质量。

---

### test_multi_source_search.py - 多源搜索测试

**用途**: 测试从多个来源检索文献的能力。

---

### test_novelty_auditor.py - 新颖性审计测试

**用途**: 测试新颖性审计功能。

---

### test_resume_mechanism.py - 恢复机制测试

**用途**: 测试任务中断后的恢复能力。

---

### test_docker_exec.py - Docker 执行测试

**用途**: 测试在 Docker 容器中执行代码的能力。

---

## 工具脚本

### validate_artifact.py

**用途**: 验证产出工件的格式和内容。

---

## 常见问题

### Q: 调试脚本报 `ModuleNotFoundError`

确保从项目根目录运行，并正确设置 PYTHONPATH：
```bash
cd /home/liangmengkun/ResearchOS
export PYTHONPATH="${PWD}:${PYTHONPATH}"
python scripts/debug_hello_agent.py --mock
```

或者脚本已自动处理路径（在脚本开头添加了 PROJECT_ROOT）。

### Q: Mock 模式和非 Mock 模式的区别

- **Mock 模式**: 使用模拟的 LLM 响应，不需要 API key，速度快但无法验证真实 API 调用。
- **非 Mock 模式**: 使用真实的 LLM API，需要配置 `config/model_routing.yaml`，会产生真实费用。

### Q: 如何查看执行轨迹

执行后，检查 `_runtime/traces/` 目录：
```bash
ls -la workspace/_runtime/traces/
cat workspace/_runtime/traces/*.jsonl
```

---

## 目录结构

```
scripts/
├── debug_*.py              # 调试脚本（Mock模式）
├── real_debug_*.py         # 真实API调试
├── test_*.py              # 各Agent单元测试
├── test_all_agents_*.py    # 综合测试
└── validate_*.py          # 验证工具
```