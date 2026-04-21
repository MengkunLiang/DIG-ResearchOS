# T4 Ideation Agent 实现文档

## 概述

Ideation Agent（假设生成Agent）是ResearchOS pipeline中的创新核心，负责基于文献综述生成研究假设和实验计划。它通过深度推理，结合文献综述、缺口分析和用户种子想法，产出3-6个可测试的研究假设，并为每个假设设计详细的实验计划。整个过程通过两轮Human Gate确认，确保假设的质量和可行性。

**在Pipeline中的位置**: T4（假设生成阶段）

**代码位置**: 
- Agent实现: `/home/liangmengkun/ResearchOS/researchos/agents/ideation.py`
- Prompt模板: `/home/liangmengkun/ResearchOS/researchos/prompts/ideation.j2`

## 设计规格

- **Agent名称**: `ideation`
- **模型层级**: `heavy`（需要深度推理和创造性思维）
- **LLM Profile**: 默认（不使用deep_reasoning profile）
- **Temperature**: 0.75（鼓励创造性，但不过度随机）
- **工具**: `read_file`, `write_file`, `list_files`, `ask_human`, `finish_task`
- **最大步数**: 40
- **Token预算**: 500,000（需要处理大量文献综述）
- **超时时间**: 3600秒（1小时）

## 输入

### 必需输入
- `project.yaml`: 项目配置文件（包含研究方向、约束等）
- `literature/synthesis.md`: T3.5产出的文献综述（包含5个必需章节）
- `literature/comparison_table.csv`: 论文对比表

### 可选输入
- `literature/missing_areas.md`: T2阶段标注的文献缺口
- `user_seeds/seed_ideas.md`: 用户的初步想法

### 输入格式示例

**synthesis.md** (必须包含5个章节):
```markdown
# 文献综述

## 1. 方法家族分类 (Method Families)
...

## 2. 共同假设 (Shared Assumptions)
...

## 3. 性能-效率前沿 (Performance-Efficiency Frontier)
...

## 4. 技术趋势 (Trends)
...

## 5. 可操作研究问题 (Actionable Research Questions)
...
```

## 输出

### 产出文件

1. **ideation/hypotheses.md**: 研究假设文档（3-6个假设）
2. **ideation/exp_plan.yaml**: 实验计划（符合exp_plan.schema.json）
3. **ideation/risks.md**: 风险评估（至少3条风险）

### 输出格式

#### hypotheses.md

```markdown
# 研究假设

## H1: [假设标题]

### 核心假设
[1-2句话描述核心假设]

### 动机
[为什么这个假设值得研究？基于文献综述的哪些发现？]

### 预期贡献
[如果假设成立，会带来什么贡献？]

### 可测试性
[如何验证这个假设？需要什么实验？]

### 相关文献
- [arxiv:2301.12345]: [相关性说明]
- [s2:abc123]: [相关性说明]

## H2: [假设标题]
...

## H3: [假设标题]
...
```

**假设命名规范**:
- 使用 `## H1`, `## H2`, `## H3` 等格式
- 每个假设必须有唯一的anchor（H1, H2, ...）
- exp_plan.yaml中的hypothesis_ref必须引用这些anchor

#### exp_plan.yaml

```yaml
project_id: "discrete-diffusion-lang"
hypothesis_count: 3
experiments:
  - experiment_id: "exp-h1-baseline"
    name: "H1基线实验"
    hypothesis_ref: "H1"
    objective: "验证factorized gap改进是否有效"
    method:
      description: "实现改进的factorized gap算法"
      baseline: "标准discrete diffusion"
      key_changes:
        - "使用adaptive gap scheduling"
        - "引入learnable gap parameters"
    dataset:
      name: "WMT2014"
      split: "en-de"
      size: "4.5M sentence pairs"
    metrics:
      primary: "BLEU"
      secondary: ["perplexity", "training_time"]
    expected_results:
      primary_target: 30.0
      baseline_value: 28.5
      improvement_threshold: 1.5
    compute_estimate:
      gpu_type: "V100"
      gpu_hours: 24
      memory_gb: 32
    dependencies: []
    
  - experiment_id: "exp-h1-ablation"
    name: "H1消融实验"
    hypothesis_ref: "H1"
    objective: "分析各个改进组件的贡献"
    method:
      description: "逐个移除改进组件"
      variants:
        - "without adaptive scheduling"
        - "without learnable parameters"
        - "full model"
    dataset:
      name: "WMT2014"
      split: "en-de"
      size: "4.5M sentence pairs"
    metrics:
      primary: "BLEU"
      secondary: ["perplexity"]
    compute_estimate:
      gpu_type: "V100"
      gpu_hours: 48
      memory_gb: 32
    dependencies: ["exp-h1-baseline"]
    
  - experiment_id: "exp-h2-pilot"
    name: "H2预实验"
    hypothesis_ref: "H2"
    objective: "验证新架构的可行性"
    method:
      description: "在小规模数据上测试新架构"
      baseline: "Transformer"
    dataset:
      name: "IWSLT2017"
      split: "en-de"
      size: "200K sentence pairs"
    metrics:
      primary: "BLEU"
      secondary: ["convergence_speed"]
    expected_results:
      primary_target: 28.0
      baseline_value: 27.0
    compute_estimate:
      gpu_type: "V100"
      gpu_hours: 8
      memory_gb: 16
    dependencies: []
```

**必需字段说明**:
- `experiment_id`: 实验唯一标识
- `name`: 实验名称
- `hypothesis_ref`: 引用的假设anchor（如"H1"、"H2"）
- `objective`: 实验目标
- `method`: 方法描述
- `dataset`: 数据集信息
- `metrics`: 评估指标
- `compute_estimate`: 计算资源估算
- `dependencies`: 依赖的其他实验（实验ID列表）

#### risks.md

```markdown
# 风险评估

## 风险1: 计算资源不足

### 描述
H1和H2的实验都需要大量GPU时间，总计约80 GPU小时。

### 影响
如果GPU资源不足，可能无法完成所有实验。

### 缓解措施
- 优先执行H1基线实验（最有可能成功）
- H2先做pilot实验验证可行性
- 考虑使用更小的数据集（IWSLT代替WMT）

### 概率
中等（50%）

## 风险2: 假设不成立

### 描述
H1的改进可能不如预期显著，BLEU提升可能小于1.5。

### 影响
需要调整假设或寻找新的改进方向。

### 缓解措施
- 准备了H2和H3作为备选方向
- 即使H1失败，ablation study也有价值

### 概率
中等（40%）

## 风险3: 基线复现困难

### 描述
原论文的基线结果可能难以复现（超参数、随机种子等）。

### 影响
无法准确评估改进效果。

### 缓解措施
- 使用官方代码库（fairseq）
- 多次运行取平均值
- 记录所有超参数和随机种子

### 概率
低（20%）
```

**风险文档要求**:
- 至少3条风险
- 每条风险必须包含：描述、影响、缓解措施、概率
- 使用 `## 风险X` 或 `## Risk X` 格式

## Workflow

### 两轮Gate确认流程

#### Gate 1: 假设草案确认

**时机**: 生成hypotheses.md草案后

**流程**:
1. 基于synthesis.md生成3-6个研究假设
2. 使用`ask_human`展示假设草案
3. 询问用户：
   - 哪些假设最有潜力？
   - 是否需要调整或补充？
   - 是否有遗漏的方向？
4. 根据反馈调整假设
5. 确认后写入`ideation/hypotheses.md`

**ask_human示例**:
```
我已经基于文献综述生成了以下研究假设：

H1: 改进factorized gap的自适应调度
- 动机: 现有方法使用固定gap，可能不适应不同阶段
- 预期贡献: 提升生成质量1-2 BLEU

H2: 引入层次化离散表示
- 动机: 单层离散化可能损失信息
- 预期贡献: 更好的语义保留

H3: 结合连续和离散扩散
- 动机: 两种方法各有优势
- 预期贡献: 兼顾质量和效率

请问：
1. 您认为哪个假设最有潜力？
2. 是否需要调整或补充？
3. 是否有遗漏的方向？
```

#### Gate 2: 实验计划确认

**时机**: 生成exp_plan.yaml和risks.md后

**流程**:
1. 为每个假设设计实验计划
2. 估算计算资源和成本
3. 识别风险并提出缓解措施
4. 使用`ask_human`展示完整计划
5. 询问用户：
   - 实验设计是否合理？
   - 资源估算是否可接受？
   - 风险评估是否完整？
6. 根据反馈调整
7. 确认后写入文件

**ask_human示例**:
```
我已经为3个假设设计了实验计划：

实验总览：
- 5个实验（2个H1，2个H2，1个H3）
- 总GPU时间: 80小时
- 预估成本: $240（按$3/GPU小时）
- 预计完成时间: 2周

关键风险：
1. 计算资源不足（概率50%）
2. H1假设不成立（概率40%）
3. 基线复现困难（概率20%）

请问：
1. 实验设计是否合理？
2. 资源估算是否可接受？（您的预算是$500）
3. 是否需要调整实验优先级？
```

### 详细执行流程

#### Step 1: 读取所有输入
- 使用`read_file`读取synthesis.md（完整内容）
- 读取comparison_table.csv
- 读取missing_areas.md（如果存在）
- 读取seed_ideas.md（如果存在）
- 读取project.yaml（获取约束和预算）

#### Step 2: 分析文献综述
从synthesis.md的5个章节中提取：
1. **方法家族**: 识别主流方法和边缘方法
2. **共同假设**: 找出可以挑战的假设（创新机会）
3. **性能-效率前沿**: 识别空白区域
4. **技术趋势**: 预测未来方向
5. **可操作研究问题**: 直接转化为假设

#### Step 3: 生成假设草案
基于分析生成3-6个假设，每个假设必须：
- 有明确的核心假设（1-2句话）
- 有充分的动机（基于文献综述）
- 有清晰的预期贡献
- 可测试（能设计实验验证）
- 引用相关文献

**假设生成策略**:
- **挑战共同假设**: 质疑领域内的默认假设
- **填补空白区域**: 在性能-效率前沿找空白
- **组合现有方法**: 将不同家族的方法结合
- **跟随趋势**: 基于技术趋势提出新方向
- **用户想法**: 结合seed_ideas.md中的想法

#### Step 4: Gate 1确认
使用`ask_human`展示假设草案，收集反馈，调整后写入`ideation/hypotheses.md`。

#### Step 5: 设计实验计划
为每个假设设计1-3个实验：
- **基线实验**: 验证核心假设
- **消融实验**: 分析各组件贡献
- **对比实验**: 与现有方法对比

**实验设计原则**:
- 每个实验必须有明确的objective
- 必须指定baseline和key_changes
- 必须定义primary metric和expected results
- 必须估算compute resources
- 必须标注dependencies（实验依赖关系）

**资源估算**:
- 根据数据集大小、模型规模估算GPU时间
- 检查是否超过project.yaml中的max_budget_usd
- 如果单个实验成本超过预算85%，返回错误

#### Step 6: 识别风险
列出至少3条风险：
- **计算资源风险**: GPU不足、时间不够
- **技术风险**: 假设不成立、基线难复现
- **数据风险**: 数据集不可用、质量问题
- **依赖风险**: 外部库、预训练模型不可用

每条风险必须包含：
- 描述
- 影响
- 缓解措施
- 概率（高/中/低或百分比）

#### Step 7: Gate 2确认
使用`ask_human`展示完整计划，收集反馈，调整后写入`ideation/exp_plan.yaml`和`ideation/risks.md`。

#### Step 8: 完成任务
调用`finish_task`完成任务。

## 校验逻辑

`validate_outputs`检查：

1. **hypotheses.md存在且有内容**
   - 长度至少500字符
   - 必须包含假设anchor（## H1, ## H2等）
   - 至少1个假设

2. **exp_plan.yaml符合schema**
   - 必须包含`experiments`字段
   - 至少1个实验
   - 每个实验必须有必需字段：`experiment_id`, `name`, `hypothesis_ref`, `objective`, `method`, `dataset`, `metrics`, `compute_estimate`

3. **hypothesis_ref引用一致性**
   - exp_plan.yaml中的每个`hypothesis_ref`必须存在于hypotheses.md中
   - 例如：如果exp_plan中引用"H1"，hypotheses.md中必须有`## H1`

4. **risks.md存在且有内容**
   - 至少3条风险
   - 每条风险必须有`## 风险`或`## Risk`标记

5. **预算检查**
   - 每个实验的成本不超过max_budget_usd的85%
   - 成本估算：`gpu_hours * $3.0`（假设$3/GPU小时）

## 工具使用

### 可用工具列表

- `read_file`: 读取文献综述和项目配置
- `write_file`: 写入假设、实验计划、风险文档
- `list_files`: 列出文献笔记（如果需要）
- `ask_human`: 两轮Gate确认
- `finish_task`: 完成任务

### ask_human使用注意事项

1. **清晰的问题**: 明确询问用户需要做什么决策
2. **提供上下文**: 展示足够的信息让用户理解
3. **具体的选项**: 给出具体的选项或示例
4. **等待反馈**: 根据用户反馈调整，不要假设用户会接受

## 与其他Agent的交互

- **依赖**: T3.5 Reader Agent（需要synthesis.md）
- **被依赖**: T4.5 Novelty Auditor Agent（使用hypotheses.md）和 T5 Experimenter Agent（使用exp_plan.yaml）

## 已知限制和注意事项

1. **创造性vs可行性**: Temperature 0.75鼓励创造性，但可能生成过于激进的假设
2. **文献覆盖**: 假设质量依赖于synthesis.md的质量
3. **资源估算**: GPU时间估算可能不准确，需要实际测试
4. **依赖关系**: 实验依赖关系可能复杂，需要仔细设计
5. **Gate确认**: 依赖用户的专业判断，用户可能不熟悉某些技术细节

## 测试

运行测试：
```bash
# 单元测试
pytest tests/unit/test_ideation_agent.py -v

# 集成测试（需要mock LLM）
pytest tests/integration/test_ideation_agent_e2e.py -v
```

测试覆盖：
- 假设生成逻辑
- 实验计划schema校验
- hypothesis_ref引用一致性检查
- 预算检查逻辑
- 风险文档格式校验

## 使用示例

```python
from researchos.agents.ideation import IdeationAgent
from researchos.runtime.agent import ExecutionContext

agent = IdeationAgent()
ctx = ExecutionContext(
    workspace_dir=Path("/path/to/workspace"),
    task_id="T4",
    mode=None  # ideation只有一个模式
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
  ideation:
    max_retries: 3
    timeout_seconds: 3600
    temperature: 0.75  # 鼓励创造性
```

**注意**: Ideation Agent 不使用 `deep_reasoning` LLM profile，而是使用默认配置。这是因为当前实现中 `deep_reasoning` profile 尚未完全配置。

详见 ResearchOS Runtime Dev Spec §6 和 §17。
