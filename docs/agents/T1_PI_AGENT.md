# T1/T7.5 PI Agent 实现文档

## 概述

PI Agent（项目初始化与评估Agent）是ResearchOS pipeline的起点和关键决策点，负责两个核心任务：
- **T1 (init模式)**: 通过三轮对话引导用户明确研究方向，产出项目配置和种子数据
- **T7.5 (evaluate模式)**: 评估实验结果，决定后续路径（继续迭代/准备写作/放弃）

**在Pipeline中的位置**: T1（项目初始化）和 T7.5（实验评估）

**代码位置**: 
- Agent实现: `/home/liangmengkun/ResearchOS/researchos/agents/pi.py`
- Prompt模板: `/home/liangmengkun/ResearchOS/researchos/prompts/pi.j2`

## 设计规格

- **Agent名称**: `pi`
- **模型层级**: `heavy`（需要深度推理和对话能力）
- **Temperature**: 0.3（保持稳定和一致性）
- **工具**: `read_file`, `write_file`, `ask_human`, `finish_task`, `process_seed_paper`
- **最大步数**: 30
- **Token预算**: 100,000
- **超时时间**: 1800秒（30分钟）

## T1 Init模式

### 输入

#### 必需输入
- `user_topic`: 用户提供的初始研究方向（通过ExecutionContext.extra传入）

#### 可选输入
- 用户在对话中提供的种子论文、初步想法、约束条件等

### 输出

#### 产出文件

1. **project.yaml**: 项目配置文件（必须符合project.schema.json）
2. **state.yaml**: 任务状态文件

**可选文件**（如果用户提供了种子数据）:
3. **user_seeds/seed_papers.jsonl**: 种子论文列表
4. **user_seeds/seed_ideas.md**: 用户的初步想法
5. **user_seeds/seed_constraints.md**: 硬约束清单
6. **user_seeds/seed_external_resources.jsonl**: 外部资源

### 三轮对话流程

#### 第1轮：明确研究边界
**目标**: 确定研究的核心问题和边界

**询问内容**:
- 研究方向的具体定义（不要太宽泛）
- 目标会议/期刊（如NeurIPS、ICML、ACL等）
- 硬约束（如必须在某个数据集上测试、必须可复现等）
- 时间和预算限制

**输出**: 初步的研究边界定义

#### 第2轮：收集已有资源
**目标**: 了解用户已有的知识和资源

**询问内容**:
- 已读过的相关论文（anchor papers）
- 初步的想法或假设
- 技术约束（如只能用开源模型、GPU限制等）
- 已有的代码库、数据集、预训练模型等外部资源

**输出**: seed_papers.jsonl, seed_ideas.md, seed_constraints.md, seed_external_resources.jsonl（可选）

#### 第3轮：确认项目配置
**目标**: 生成project.yaml草案并确认

**流程**:
1. 基于前两轮对话生成project.yaml草案
2. 使用`ask_human`展示草案，询问是否需要修改
3. 根据用户反馈调整
4. 最终确认后写入文件

**输出**: project.yaml

### 输出格式

#### project.yaml
```yaml
research_direction: "离散扩散模型在语言生成中的应用"
keywords:
  - "discrete diffusion"
  - "language models"
  - "factorized gap"
target_venue: "NeurIPS"
constraints:
  max_budget_usd: 500.0
  max_gpu_hours: 100
  must_use_open_source: true
  reproducibility_required: true
timeline:
  start_date: "2024-01-15"
  target_submission: "2024-05-15"
```

#### seed_papers.jsonl
每行一个JSON对象：
```json
{"id": "arxiv:2301.12345", "title": "Discrete Diffusion Models", "role": "anchor", "year": 2023, "why_relevant": "提出了factorized gap的概念"}
{"id": "s2:abc123", "title": "Language Generation", "role": "reference", "year": 2024, "why_relevant": "baseline方法"}
```

**字段说明**:
- `id`: 论文唯一标识（arxiv:xxx 或 s2:xxx）
- `title`: 论文标题
- `role`: 角色（"anchor"=核心论文, "reference"=参考论文, "baseline"=基线方法）
- `year`: 发表年份
- `why_relevant`: 为什么相关（可选）

#### seed_ideas.md
```markdown
# 初步想法

## 想法1: 改进factorized gap
当前方法的问题是...
可能的改进方向是...

## 想法2: 新的评估指标
现有指标的局限性...
```

#### seed_constraints.md
```markdown
# 硬约束清单

1. 必须在WMT2014数据集上测试
2. 必须与Transformer baseline对比
3. 训练时间不超过24小时（单卡V100）
4. 必须开源代码
```

#### seed_external_resources.jsonl（可选）
```json
{"type": "dataset", "name": "WMT2014", "source": "huggingface:wmt14", "notes": "英德翻译"}
{"type": "baseline_repo", "name": "fairseq", "source": "github:facebookresearch/fairseq", "notes": "Transformer baseline"}
{"type": "pretrained_model", "name": "bert-base", "source": "huggingface:bert-base-uncased", "notes": "用于初始化"}
```

**type字段可选值**: `dataset`, `baseline_repo`, `pretrained_model`, `docker_image`, `tool`, `script`, `other`

**source格式**: 必须以以下前缀之一开头：
- `huggingface:` - HuggingFace资源
- `github:` - GitHub仓库
- `docker:` - Docker镜像
- `pip:` - Python包
- `url:` - 通用URL
- `local:` - 本地路径

## T7.5 Evaluate模式

### 输入

#### 必需输入
- `experiments/results_summary.json`: 实验结果汇总
- `experiments/iteration_log.md`: 实验迭代日志
- `ideation/exp_plan.yaml`: 实验计划

### 输出

#### 产出文件
- `evaluation/evaluation_decision.md`: 评估决策报告

### Workflow

#### Step 1: 读取实验结果
读取所有实验相关文件，了解：
- 实验是否成功完成
- 关键指标是否达到预期
- 遇到了什么问题
- 花费了多少资源

#### Step 2: 判断Situation
根据实验结果判断当前状态（A/B/C/D）：

**Situation A**: 实验成功，结果显著
- 关键指标超过baseline
- 结果可复现
- 有足够的分析支持

**Situation B**: 实验部分成功，需要迭代
- 部分指标达标，部分未达标
- 发现了新的问题或改进方向
- 还有预算和时间继续迭代

**Situation C**: 实验失败，但有价值
- 实验失败，但发现了有价值的负面结果
- 可以作为ablation study或分析
- 可能需要调整假设

**Situation D**: 实验失败，无价值
- 实验失败且没有发现有价值的信息
- 假设可能根本不成立
- 建议放弃或重新设计

#### Step 3: 提出Options
根据Situation提出后续建议：

**对于Situation A**:
- Option 1: 进入T8写作阶段
- Option 2: 补充更多实验（如果时间允许）

**对于Situation B**:
- Option 1: 调整超参数重新实验（回到T6）
- Option 2: 修改假设（回到T4）
- Option 3: 补充文献调研（回到T2/T3）

**对于Situation C**:
- Option 1: 将负面结果作为分析写入论文
- Option 2: 调整研究方向

**对于Situation D**:
- Option 1: 放弃当前方向
- Option 2: 重新设计（回到T1）

#### Step 4: 产出决策文档
使用`write_file`写入`evaluation/evaluation_decision.md`，并调用`finish_task`完成任务。

### 输出格式

#### evaluation_decision.md
```markdown
# T7.5 实验评估决策

## Situation: B（部分成功，需要迭代）

### 实验结果摘要
- 实验1（H1-baseline）: BLEU 28.5（目标30.0）- 接近但未达标
- 实验2（H1-optimized）: BLEU 29.8（目标30.0）- 非常接近
- 实验3（H2-variant）: 失败（OOM错误）

### 关键发现
1. H1假设基本成立，但需要更多调优
2. H2的模型规模过大，需要缩减
3. 数据增强策略有效（+1.3 BLEU）

### 资源消耗
- GPU时间: 45小时（预算100小时）
- 成本: $135（预算$500）
- 剩余预算充足

## Options

### Option 1: 调整超参数重新实验（推荐）
- 针对H1进行网格搜索（学习率、batch size）
- 预计需要20 GPU小时
- 成功概率: 高（已经很接近目标）

### Option 2: 缩减H2模型规模
- 将模型参数减半
- 重新训练
- 预计需要30 GPU小时

### Option 3: 补充ablation study
- 分析数据增强的贡献
- 分析各个模块的作用
- 为论文准备更充分的分析

## 建议
选择Option 1，因为H1已经非常接近目标，调优后很可能达标。
同时可以并行进行Option 3的ablation study。
```

## 校验逻辑

### T1 Init模式校验

`validate_outputs`检查：

1. **project.yaml存在且符合schema**
   - 必需字段：`research_direction`, `keywords`, `target_venue`
   - 可选字段：`constraints`, `timeline`

2. **伦理审查（Ethical Screening）**
   - 检查research_direction和keywords是否包含敏感词
   - 敏感类别：weapons, surveillance, manipulation, privacy, discrimination
   - 如果检测到敏感词，返回警告

3. **Seed文件存在**
   - `user_seeds/seed_papers.jsonl`（可以为空）
   - `user_seeds/seed_ideas.md`（可以为空）
   - `user_seeds/seed_constraints.md`（可以为空）

4. **外部资源格式校验（如果存在）**
   - `seed_external_resources.jsonl`的type字段必须合法
   - source字段必须以规定前缀开头

### T7.5 Evaluate模式校验

`validate_outputs`检查：

1. **evaluation_decision.md存在**
2. **必须包含Situation章节**
3. **必须包含Options建议**

## 工具使用

### 可用工具列表

- `read_file`: 读取文件内容
- `write_file`: 写入文件
- `ask_human`: 与用户交互（三轮对话的核心）
- `finish_task`: 完成任务
- `process_seed_paper`: 处理用户提供的种子论文（提取metadata）

### ask_human工具使用示例

```python
# 第1轮对话
response = ask_human(
    "请告诉我您的研究方向的具体定义。例如：\n"
    "- 您想解决什么问题？\n"
    "- 目标会议是什么？\n"
    "- 有什么硬约束？"
)

# 第3轮确认
response = ask_human(
    "我已经生成了project.yaml草案：\n\n"
    f"{yaml_content}\n\n"
    "请确认是否需要修改？（回复'确认'或提出修改意见）"
)
```

## 与其他Agent的交互

### T1模式
- **被依赖**: T2 Scout Agent（需要project.yaml）
- **依赖**: 无（是pipeline的起点）

### T7.5模式
- **依赖**: T6 Experimenter Agent（需要results_summary.json）
- **被依赖**: T8 Writer Agent（如果决定进入写作）或回到T4/T6（如果决定继续迭代）

## 已知限制和注意事项

1. **对话轮数限制**: 当前设计为三轮对话，如果用户信息不足可能需要更多轮次
2. **伦理审查**: 当前的敏感词列表是基础版本，可能需要扩展
3. **外部资源验证**: 只验证格式，不验证资源是否真实可用
4. **T7.5决策**: 依赖LLM的判断，可能需要人工复核

## 测试

运行测试：
```bash
# 单元测试
pytest tests/unit/test_pi_agent.py -v

# 集成测试
pytest tests/integration/test_pi_agent_e2e.py -v
```

测试覆盖：
- T1 init模式的三轮对话流程
- project.yaml schema校验
- 伦理审查逻辑
- seed文件格式校验
- T7.5 evaluate模式的决策逻辑

## 使用示例

### T1 Init模式
```python
from researchos.agents.pi import PIAgent
from researchos.runtime.agent import ExecutionContext

agent = PIAgent()
ctx = ExecutionContext(
    workspace_dir=Path("/path/to/workspace"),
    task_id="T1",
    mode="init",
    extra={"user_topic": "discrete diffusion language models"}
)

result = await agent.run(ctx)
```

### T7.5 Evaluate模式
```python
agent = PIAgent()
ctx = ExecutionContext(
    workspace_dir=Path("/path/to/workspace"),
    task_id="T7.5",
    mode="evaluate"
)

result = await agent.run(ctx)
```

## 配置说明

### model_routing.yaml配置
```yaml
heavy:
  provider: "anthropic"
  model: "claude-opus-4"
  max_tokens: 4096
  supports_thinking: true
```

### runtime.yaml配置
```yaml
agents:
  pi:
    max_retries: 3
    timeout_seconds: 1800
    enable_thinking: true  # T1需要深度推理
```

详见 ResearchOS Runtime Dev Spec §6 和 §17。
