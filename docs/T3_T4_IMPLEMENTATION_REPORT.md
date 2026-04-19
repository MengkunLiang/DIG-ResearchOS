# T3和T4 Agent实现报告

**日期**: 2026-04-19  
**状态**: ✅ 实现完成，所有单元测试通过

## 实现总览

### 新增Agent

1. **T3 Reader Agent（深度阅读）**
   - 文件：`researchos/agents/reader.py` (189行)
   - Prompt：`researchos/prompts/reader.j2` (239行)
   - 支持两种模式：read（T3）和synthesize（T3.5）

2. **T4 Ideation Agent（假设生成）**
   - 文件：`researchos/agents/ideation.py` (170行)
   - Prompt：`researchos/prompts/ideation.j2` (354行)
   - 两轮Gate交互：T4-DECIDE-1（选方向）+ T4-DECIDE-2（确认计划）

### 测试结果

- **单元测试**: 16/16 通过 ✅
  - T3 Reader Agent: 9个测试
  - T4 Ideation Agent: 7个测试
- **全量测试**: 106/106 通过 ✅
- **无回归**: 所有现有测试保持通过

## T3 Reader Agent详细说明

### 功能概述

T3 Reader Agent负责深度阅读论文并生成结构化笔记，分为两个阶段：

**T3 (read模式)**：
- 输入：`literature/papers_dedup.jsonl`（30-80篇论文）
- 处理：逐篇精读，提取关键信息
- 输出：
  - `literature/paper_notes/{id}.md`：每篇论文的11项结构化笔记
  - `literature/comparison_table.csv`：论文对比表
  - `literature/related_work.bib`：BibTeX引用库

**T3.5 (synthesize模式)**：
- 输入：所有paper_notes + comparison_table
- 处理：综合分析，提取模式和趋势
- 输出：`literature/synthesis.md`（5个必需章节）

### AgentSpec配置

```python
AgentSpec(
    name="reader",
    model_tier="medium",  # read模式，synthesize会override为heavy
    tool_names=[
        "read_file",
        "write_file",
        "append_file",
        "list_files",
        "fetch_paper_pdf",
        "extract_pdf_text",
        "finish_task",
    ],
    max_steps=80,
    max_tokens_total=400_000,
    max_wall_seconds=7200,  # 2小时
    temperature=0.5,
    allowed_read_prefixes=["", "literature/"],
    allowed_write_prefixes=["literature/"],
    prompt_template="reader.j2",
)
```

### paper_note.md结构（11项checklist）

1. Problem & Motivation
2. Method Overview
3. Key Results（必须包含具体数字）
4. Claims vs Evidence（表格形式）
5. Limitations
6. Relevance to Our Research
7. Technical Details Worth Noting
8. Strengths
9. Weaknesses / Gaps
10. Key Quotes
11. My Questions

### synthesis.md结构（5个必需章节）

1. **方法家族分类**：将论文聚类成3-5个家族
2. **共同假设**：领域内可能值得质疑的2-4个假设
3. **性能-效率前沿**：Pareto最优点分析
4. **技术趋势**：近12月vs更早期的对比
5. **可操作研究问题**：3-6个可推进的研究问题

### 鲁棒性设计

- **PDF失败降级**：PDF读取失败时基于abstract生成note，标注`[ABSTRACT-ONLY]`
- **禁止编造**：数字必须具体，不能写"显著提升"
- **Context管理**：每篇读完立即写note，避免token爆炸
- **容错处理**：单篇失败不影响整体流程

### 输出校验

**read模式**：
- ✅ paper_notes目录存在且有≥15篇note
- ✅ comparison_table.csv存在且格式正确
- ✅ related_work.bib存在且包含BibTeX条目

**synthesize模式**：
- ✅ synthesis.md存在
- ✅ 包含5个必需章节（关键词检测）
- ✅ 长度≥2000字符

## T4 Ideation Agent详细说明

### 功能概述

T4 Ideation Agent负责基于文献综合生成研究假设和实验计划，通过两轮Gate与用户交互确认。

### AgentSpec配置

```python
AgentSpec(
    name="ideation",
    model_tier="heavy",
    llm_profile="deep_reasoning",
    tool_names=[
        "read_file",
        "write_file",
        "list_files",
        "ask_human",
        "finish_task",
    ],
    max_steps=40,
    max_tokens_total=500_000,
    max_wall_seconds=3600,  # 1小时
    temperature=0.75,  # 鼓励divergence
    allowed_read_prefixes=["", "literature/", "user_seeds/"],
    allowed_write_prefixes=["ideation/"],
    prompt_template="ideation.j2",
)
```

### 两轮Gate交互流程

**阶段A - 发散 + Gate1（T4-DECIDE-1）**：
1. 读取synthesis.md，理解Q1-QN和研究缺口
2. 生成3-5个候选研究方向，每个包含：
   - 一句话Pitch
   - 对应的Q（synthesis.md中的问题）
   - 三维评分（Novelty/Feasibility/Impact，1-5分）
   - 关键风险
3. 写入`ideation/_candidate_directions.json`
4. 使用`ask_human`呈现候选方向，用户选择：
   - [选方向i] → 进入阶段B
   - [合并X+Y] → 回Step 3
   - [新想法] → 回Step 3
   - [全拒] → 计数器++，<3次回Step 2，≥3次异常

**阶段B - 展开 + Gate2（T4-DECIDE-2）**：
1. 展开选定方向，产出3个文件：
   - `ideation/hypotheses.md`：研究假设（带H1/H2等anchor）
   - `ideation/exp_plan.yaml`：实验计划（符合schema）
   - `ideation/risks.md`：Top 3风险分析
2. 使用`ask_human`让用户确认计划：
   - [确认] → finish_task
   - [修改假设/计划/风险] → 修改后回Step 2
   - [换方向] → 回阶段A

### 输出文件格式

**hypotheses.md**：
- 每个假设用`## H1`、`## H2`标记anchor
- 包含：背景、核心假设、预期结果、风险
- 至少500字符

**exp_plan.yaml**：
- 严格符合`exp_plan.schema.json`
- `hypothesis_ref`必须指向存在的anchor（如`#H1`）
- 包含：datasets、baselines、our_method、metrics、success_criteria、steps、compute_estimate
- compute_estimate ≤ budget × 0.85

**risks.md**：
- 至少3条风险
- 每条包含：描述、Early Signal、Mitigation、Kill Criteria

### 避免伪创新模式

❌ **禁止**：
- "方法A + 方法B"的简单拼接
- "+10% accuracy with 100× compute"
- "在新数据集上测试XXX"

✅ **鼓励**：
- 解决文献中明确指出的问题
- 基于理论分析提出新方法
- 发现现有方法的根本性缺陷

### 输出校验

- ✅ hypotheses.md有H1/H2等anchor
- ✅ hypotheses.md长度≥500字符
- ✅ exp_plan.yaml符合schema
- ✅ hypothesis_ref指向存在的anchor
- ✅ risks.md至少3条风险
- ✅ compute_estimate ≤ budget × 0.85

## 集成和配置

### 更新的文件

1. **`researchos/agents/registry.py`**
   - 添加ReaderAgent和IdeationAgent到AGENT_REGISTRY
   - 添加T3、T3.5、T4到TASK_TO_AGENT_MAP

2. **`config/state_machine.yaml`**
   - 添加T3、T3.5、T4状态
   - 配置完整的T1→T2→T3→T3.5→T4流程

### 完整workflow

```yaml
T1 (pi, init) → T2 (scout) → T3 (reader, read) → T3.5 (reader, synthesize) → T4 (ideation) → done
```

## 测试覆盖

### T3 Reader Agent测试（9个）

1. `test_reader_agent_spec`：验证AgentSpec配置
2. `test_reader_system_prompt_read_mode`：验证read模式prompt
3. `test_reader_system_prompt_synthesize_mode`：验证synthesize模式prompt
4. `test_reader_initial_user_message_read_mode`：验证read模式初始消息
5. `test_reader_initial_user_message_synthesize_mode`：验证synthesize模式初始消息
6. `test_validate_outputs_read_mode_success`：验证read模式输出（成功）
7. `test_validate_outputs_read_mode_missing_notes`：验证read模式输出（缺少笔记）
8. `test_validate_outputs_synthesize_mode_success`：验证synthesize模式输出（成功）
9. `test_validate_outputs_synthesize_mode_missing_sections`：验证synthesize模式输出（缺少章节）

### T4 Ideation Agent测试（7个）

1. `test_ideation_agent_spec`：验证AgentSpec配置
2. `test_ideation_system_prompt`：验证system prompt生成
3. `test_ideation_initial_user_message`：验证初始消息
4. `test_validate_outputs_success`：验证输出（成功场景）
5. `test_validate_outputs_missing_hypothesis_anchor`：验证缺少anchor时失败
6. `test_validate_outputs_invalid_exp_plan`：验证exp_plan schema错误时失败
7. `test_validate_outputs_budget_exceeded`：验证预算超限时失败

## 端到端测试（需要真实LLM）

### 前置条件

配置API key：

```bash
# 方法1：配置Anthropic API
export ANTHROPIC_API_KEY="your-api-key-here"

# 方法2：配置OpenAI API（需修改config/model_routing.yaml）
export OPENAI_API_KEY="your-api-key-here"
```

### 测试步骤

#### 1. 初始化测试workspace

```bash
cd /home/liangmengkun/ResearchOS
TEST_WS=/tmp/researchos_t1_t4_test
python -m researchos.cli init-workspace --workspace $TEST_WS
```

#### 2. 运行T1（项目初始化）

```bash
python -m researchos.cli run-task T1 \
  --workspace $TEST_WS \
  --no-banner
```

**预期输出**：
- `project.yaml`：项目配置
- `state.yaml`：状态记录
- 可能有`user_seeds/seed_papers.jsonl`（如果提供种子论文）

#### 3. 运行T2（文献检索）

```bash
python -m researchos.cli run-task T2 \
  --workspace $TEST_WS \
  --no-banner
```

**预期输出**：
- `literature/papers_raw.jsonl`：原始论文（30-80篇）
- `literature/papers_dedup.jsonl`：去重后论文
- `literature/search_log.md`：搜索日志
- `literature/missing_areas.md`：缺口分析

#### 4. 运行T3（深度阅读）

```bash
python -m researchos.cli run-task T3 \
  --workspace $TEST_WS \
  --no-banner
```

**预期输出**：
- `literature/paper_notes/*.md`：至少15篇笔记
- `literature/comparison_table.csv`：对比表
- `literature/related_work.bib`：BibTeX库

**预计时间**：30-60分钟（取决于论文数量）

#### 5. 运行T3.5（文献综合）

```bash
python -m researchos.cli run-task T3.5 \
  --workspace $TEST_WS \
  --no-banner
```

**预期输出**：
- `literature/synthesis.md`：包含5个必需章节

**预计时间**：10-20分钟

#### 6. 运行T4（假设生成）

```bash
python -m researchos.cli run-task T4 \
  --workspace $TEST_WS \
  --no-banner
```

**预期输出**：
- `ideation/hypotheses.md`：研究假设
- `ideation/exp_plan.yaml`：实验计划
- `ideation/risks.md`：风险分析

**预计时间**：15-30分钟（包含两轮Gate交互）

**注意**：T4需要人工交互（两轮Gate），无法完全自动化。

### 验证输出

```bash
# 检查所有输出文件
ls -lh $TEST_WS/project.yaml
ls -lh $TEST_WS/literature/papers_dedup.jsonl
ls -lh $TEST_WS/literature/paper_notes/
ls -lh $TEST_WS/literature/synthesis.md
ls -lh $TEST_WS/ideation/

# 验证输出格式
python -m researchos.cli validate --workspace $TEST_WS
```

## 已知限制

1. **T3 PDF处理**：
   - 依赖pdfplumber库
   - 某些PDF可能解析失败（已有降级策略）
   - 大型PDF可能超时

2. **T3.5 Context限制**：
   - 如果论文数量>80篇，可能需要分批处理
   - 当前使用comparison_table而非重读所有notes

3. **T4 Gate交互**：
   - 需要人工参与，无法完全自动化
   - 连续3次拒绝后会终止

4. **计算成本**：
   - T3使用medium tier，处理30-80篇论文成本约$5-15
   - T3.5使用heavy tier，成本约$2-5
   - T4使用heavy tier + deep_reasoning，成本约$3-8
   - 总计：$10-28（取决于论文数量和交互轮次）

## 下一步工作

1. **集成测试**：编写T3和T4的集成测试（mock LLM）
2. **端到端测试**：使用真实API完整运行T1→T4
3. **文档完善**：更新README.zh-CN.md
4. **性能优化**：
   - T3批量处理优化
   - T3.5 context压缩
   - T4 prompt优化
5. **T5-T9实现**：后续阶段的agent开发

## 提交记录

```
commit 95bee74
feat: 实现T3 Reader Agent和T4 Ideation Agent

新增功能：
- T3 Reader Agent（深度阅读）：逐篇精读论文，生成结构化笔记
- T3.5文献综合：综合所有笔记，产出synthesis.md
- T4 Ideation Agent（假设生成）：两轮Gate交互生成研究假设和实验计划

实现细节：
- researchos/agents/reader.py: 支持read和synthesize两种模式
- researchos/agents/ideation.py: 使用heavy tier + deep_reasoning
- researchos/prompts/reader.j2: 完整的T3/T3.5 prompt模板
- researchos/prompts/ideation.j2: 两阶段Gate交互流程
- 更新registry和state_machine配置支持T3/T4
- 新增16个单元测试，全部通过（106/106测试通过）

测试覆盖：
- T3: 9个单元测试（spec、prompt、validation）
- T4: 7个单元测试（spec、prompt、validation、budget检查）
- 所有测试通过，无回归

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

## 结论

✅ **T3和T4 Agent实现完成**
✅ **所有单元测试通过（106/106）**
✅ **代码已提交到git仓库**
⚠️ **端到端测试需要配置API key**

准备就绪，可以进行真实LLM测试或继续后续开发工作。
