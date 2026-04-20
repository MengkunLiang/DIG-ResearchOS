# T7 Experimenter Agent (完整实验) 实现文档

## 概述

Experimenter Agent（实验执行Agent）是ResearchOS pipeline中的核心执行环节，负责在T6 Novelty验证通过后执行完整实验计划。它收集全面结果，支持多轮迭代和消融实验，是将研究假设转化为最终论文证据的关键环节。

**在Pipeline中的位置**: T7（完整实验阶段）

**前置条件**: T6 Novelty Agent 的 T6-DECIDE 决策为 PASS

**代码位置**:
- Agent实现: `/home/liangmengkun/ResearchOS/researchos/agents/experimenter.py`
- Prompt模板: `/home/liangmengkun/ResearchOS/researchos/prompts/experimenter.j2`
- 模式: `full`（完整实验模式）

## 设计规格

- **Agent名称**: `experimenter`
- **模型层级**: `medium`（需要代码生成和调试能力）
- **Temperature**: 0.3（保持代码生成的稳定性）
- **工具**: `read_file`, `write_file`, `list_files`, `bash_run`, `docker_exec`, `finish_task`
- **最大步数**: 100（实验可能需要多次调试）
- **Token预算**: 500,000
- **超时时间**: 14400秒（4小时）

## 输入

### 必需输入
- `ideation/exp_plan.yaml`: T4产出的实验计划
- `ideation/hypotheses.md`: T4产出的研究假设
- `project.yaml`: 项目配置
- `novelty/novelty_report.md`: T6产出的新颖性最终报告
- `novelty/must_add_baselines.md`: T6要求必须补充的基线方法

### 可选输入
- `pilot/pilot_code/`: T5产出的预实验代码（如果有）
- `pilot/pilot_results.json`: T5产出的试点实验结果

### 输入格式示例

**exp_plan.yaml**:
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
```

## 输出

### 产出文件

1. **experiments/results_summary.json**: 实验结果汇总（必需）
2. **experiments/iteration_log.md**: 实验迭代日志（必需）
3. **experiments/runs/{run_id}/**: 每个实验的详细结果目录

### 输出格式

#### results_summary.json

```json
{
  "project_id": "discrete-diffusion-lang",
  "total_experiments": 3,
  "completed": 2,
  "failed": 1,
  "total_gpu_hours": 45.5,
  "total_cost_usd": 136.5,
  "experiments": [
    {
      "experiment_id": "exp-h1-baseline",
      "name": "H1基线实验",
      "hypothesis_ref": "H1",
      "status": "DONE",
      "run_id": "r1_h1-baseline_42_20240120T143000",
      "metrics": {
        "BLEU": 29.8,
        "perplexity": 12.3,
        "training_time": 18.5
      },
      "baseline_metrics": {
        "BLEU": 28.5,
        "perplexity": 13.1
      },
      "improvement": {
        "BLEU": 1.3,
        "perplexity": -0.8
      },
      "target_met": false,
      "gpu_hours": 18.5,
      "cost_usd": 55.5,
      "notes": "接近目标但未达到，可能需要调优超参数"
    },
    {
      "experiment_id": "exp-h1-ablation",
      "name": "H1消融实验",
      "hypothesis_ref": "H1",
      "status": "DONE",
      "run_id": "r1_h1-ablation_42_20240120T163000",
      "metrics": {
        "BLEU": 29.2,
        "perplexity": 12.7
      },
      "ablation_results": [
        {"variant": "without adaptive scheduling", "BLEU": 28.8},
        {"variant": "without learnable parameters", "BLEU": 29.0},
        {"variant": "full model", "BLEU": 29.8}
      ],
      "gpu_hours": 24.0,
      "cost_usd": 72.0,
      "notes": "adaptive scheduling贡献+1.0 BLEU，learnable parameters贡献+0.8 BLEU"
    },
    {
      "experiment_id": "exp-h2-pilot",
      "name": "H2预实验",
      "hypothesis_ref": "H2",
      "status": "FAILED",
      "error": "OOM: GPU memory exceeded",
      "gpu_hours": 3.0,
      "cost_usd": 9.0,
      "notes": "模型规模过大，需要缩减参数量或使用更大GPU"
    }
  ],
  "summary": {
    "hypotheses_validated": ["H1"],
    "hypotheses_failed": ["H2"],
    "key_findings": [
      "H1的改进有效，但需要进一步调优才能达到目标",
      "adaptive scheduling和learnable parameters都有贡献",
      "H2的模型规模需要重新设计"
    ],
    "next_steps": [
      "对H1进行超参数网格搜索",
      "缩减H2的模型规模",
      "考虑使用A100 GPU"
    ]
  }
}
```

**必需字段说明**:
- `experiments`: 实验结果列表
- 每个实验必须包含：`experiment_id`, `status`
- `status`可选值：`DONE`, `FAILED`, `RUNNING`, `TIMEOUT`

#### iteration_log.md

```markdown
# 实验迭代日志

## 迭代1: 2024-01-20

### 执行的实验
1. exp-h1-baseline
2. exp-h1-ablation
3. exp-h2-pilot

### 结果摘要
- H1基线实验: BLEU 29.8（目标30.0）- 接近但未达标
- H1消融实验: 成功，识别了各组件的贡献
- H2预实验: 失败（OOM错误）

### 关键发现
1. H1的改进方向正确，adaptive scheduling贡献+1.0 BLEU
2. learnable parameters贡献+0.8 BLEU
3. H2的模型规模过大，需要缩减

### 遇到的问题
1. H1的BLEU距离目标还差0.2，可能需要调优学习率
2. H2的模型参数量达到1.5B，超过V100的16GB显存
3. 数据加载速度较慢，可能需要优化dataloader

### 下一步计划
1. 对H1进行超参数网格搜索（学习率、batch size、warmup steps）
2. 将H2的模型规模缩减到500M参数
3. 优化数据加载pipeline

### 资源消耗
- GPU时间: 45.5小时（预算100小时）
- 成本: $136.5（预算$500）
- 剩余预算: 54.5 GPU小时，$363.5

## 迭代2: 2024-01-22（计划中）
...
```

#### experiments/runs/{run_id}/目录结构

每个实验运行都有独立的目录：

```
experiments/runs/r1_h1-baseline_42_20240120T143000/
├── config.yaml           # 本次运行的完整配置
├── metrics.json          # 最终指标
├── metrics_curve.jsonl   # 训练曲线（可选）
├── logs.txt              # stdout/stderr日志
├── status                # 单行文本: DONE/FAILED/RUNNING/TIMEOUT
├── commit_hash           # 单行文本: git commit hash（如果有）
└── duration_seconds      # 单行文本: 运行时长（秒）
```

**config.yaml**:
```yaml
experiment_id: "exp-h1-baseline"
hypothesis_ref: "H1"
model:
  type: "discrete_diffusion"
  hidden_size: 768
  num_layers: 12
  num_heads: 12
  adaptive_scheduling: true
  learnable_gap: true
dataset:
  name: "WMT2014"
  split: "en-de"
  train_size: 4500000
  val_size: 3000
training:
  batch_size: 32
  learning_rate: 0.0001
  warmup_steps: 4000
  max_steps: 100000
  gradient_accumulation: 4
  seed: 42
hardware:
  gpu_type: "V100"
  num_gpus: 1
  mixed_precision: true
```

**metrics.json**:
```json
{
  "BLEU": 29.8,
  "perplexity": 12.3,
  "training_time_hours": 18.5,
  "final_loss": 2.34,
  "best_checkpoint_step": 85000,
  "convergence_step": 80000
}
```

**metrics_curve.jsonl** (可选，每行一个step):
```jsonl
{"step": 1000, "loss": 5.23, "val_bleu": 12.3, "timestamp": "2024-01-20T14:35:00"}
{"step": 2000, "loss": 4.87, "val_bleu": 15.6, "timestamp": "2024-01-20T14:40:00"}
...
{"step": 100000, "loss": 2.34, "val_bleu": 29.8, "timestamp": "2024-01-20T20:15:00"}
```

**status**:
```
DONE
```

**duration_seconds**:
```
66600
```

## Workflow

### 执行流程

#### Step 1: 读取实验计划
- 使用`read_file`读取`ideation/exp_plan.yaml`
- 读取`ideation/hypotheses.md`了解假设背景
- 读取`project.yaml`获取预算和约束

#### Step 2: 检查依赖关系
- 分析exp_plan.yaml中的`dependencies`字段
- 构建实验执行顺序（拓扑排序）
- 例如：exp-h1-ablation依赖exp-h1-baseline，必须先执行baseline

#### Step 3: 准备实验环境
- 检查所需的数据集是否可用
- 检查所需的baseline代码是否存在
- 如果有pilot_code，复用其中的代码框架

#### Step 4: 逐个执行实验
对每个实验：

**4.1 生成实验代码**
- 基于method描述生成训练脚本
- 生成配置文件（config.yaml）
- 生成数据加载代码
- 生成评估脚本

**4.2 执行实验**
- 使用`docker_exec`在隔离环境中运行
- 或使用`bash_run`直接运行（如果不需要隔离）
- 实时监控日志输出

**4.3 收集结果**
- 解析训练日志提取metrics
- 保存到runs/{run_id}/目录
- 更新status文件

**4.4 处理失败**
如果实验失败：
- 分析错误日志
- 尝试修复（如调整batch size、减少模型规模）
- 最多重试3次
- 如果仍然失败，标记为FAILED并继续下一个实验

#### Step 5: 生成汇总报告
- 汇总所有实验结果到results_summary.json
- 生成iteration_log.md
- 分析哪些假设得到验证，哪些失败
- 提出下一步建议

#### Step 6: 完成任务
调用`finish_task`完成任务。

### 迭代模式（可选）

如果实验需要多轮迭代（如超参数调优）：

1. **第1轮**: 执行所有基线实验
2. **分析结果**: 识别需要调优的实验
3. **第2轮**: 执行调优实验（网格搜索、ablation等）
4. **重复**: 直到达到目标或耗尽预算

当前实现支持最多5轮迭代（可在exp_plan.yaml中配置）。

## 实验执行模式

### Docker隔离模式（推荐）

使用`docker_exec`在隔离容器中运行实验：

**优点**:
- 环境隔离，不影响主系统
- 可以限制资源（CPU、内存、GPU）
- 可以使用预配置的镜像（包含PyTorch、TensorFlow等）

**使用示例**:
```python
result = docker_exec(
    image="researchos/pytorch:latest",
    command="python train.py --config config.yaml",
    workdir="/workspace/experiments/runs/r1_h1-baseline_42_20240120T143000",
    gpu=True,
    timeout=86400  # 24小时
)
```

### 直接执行模式

使用`bash_run`直接运行：

**优点**:
- 简单，无需Docker
- 调试方便

**缺点**:
- 可能污染主环境
- 资源限制困难

**使用示例**:
```python
result = bash_run(
    command="python train.py --config config.yaml",
    cwd="/path/to/experiments/runs/r1_h1-baseline_42_20240120T143000",
    timeout=86400
)
```

## 校验逻辑

`validate_outputs`检查：

1. **results_summary.json存在且格式正确**
   - 必须包含`experiments`字段
   - 至少1个实验结果

2. **每个实验结果的必需字段**
   - `experiment_id`: 实验ID
   - `status`: 状态（DONE/FAILED/RUNNING/TIMEOUT）

3. **iteration_log.md存在且有内容**
   - 长度至少100字符
   - 包含实验记录

4. **runs目录结构**（可选检查）
   - 每个DONE的实验应该有对应的runs/{run_id}/目录
   - 目录中应该包含config.yaml、metrics.json、status等文件

## 工具使用

### 可用工具列表

- `read_file`: 读取实验计划和配置
- `write_file`: 写入实验代码、配置、结果
- `list_files`: 列出目录内容
- `bash_run`: 执行shell命令（安装依赖、运行脚本等）
- `docker_exec`: 在Docker容器中执行命令（推荐用于实验）
- `finish_task`: 完成任务

### docker_exec使用注意事项

1. **镜像选择**: 使用预配置的镜像（如researchos/pytorch:latest）
2. **GPU访问**: 设置`gpu=True`启用GPU
3. **超时设置**: 根据实验预估时间设置合理的timeout
4. **工作目录**: 使用绝对路径，确保代码和数据可访问
5. **日志收集**: 捕获stdout和stderr，保存到logs.txt

### bash_run使用注意事项

1. **环境激活**: 如果需要conda环境，先激活
2. **路径问题**: 使用绝对路径避免路径错误
3. **后台运行**: 长时间任务考虑使用nohup或screen
4. **错误处理**: 检查返回码，处理失败情况

## 预算管理

### 预算检查

在执行每个实验前，检查：
1. 已用GPU时间 + 预估GPU时间 < 总预算
2. 已用成本 + 预估成本 < 总预算

如果超预算：
- 触发`T7-BUDGET-GATE`
- 询问用户是否继续
- 用户可以选择：增加预算、跳过部分实验、终止

### 成本估算

```python
# 估算单个实验的成本
gpu_hours = experiment["compute_estimate"]["gpu_hours"]
gpu_type = experiment["compute_estimate"]["gpu_type"]

# 价格表（示例）
GPU_PRICES = {
    "V100": 3.0,   # $/hour
    "A100": 5.0,
    "T4": 1.5,
}

estimated_cost = gpu_hours * GPU_PRICES.get(gpu_type, 3.0)
```

## 与其他Agent的交互

- **依赖**: T4 Ideation Agent（需要exp_plan.yaml）和 T6 Novelty Agent（需要novelt_report.md和must_add_baselines.md）
- **被依赖**: T7.5 PI Agent（使用results_summary.json进行评估）

## 已知限制和注意事项

1. **实验时间**: 长时间实验可能超过Agent的超时时间（4小时）
2. **资源竞争**: 多个实验可能竞争GPU资源
3. **错误恢复**: 实验失败后的自动修复能力有限
4. **代码质量**: 生成的代码可能需要人工review
5. **依赖管理**: 复杂的依赖关系可能导致执行顺序问题

## 测试

运行测试：
```bash
# 单元测试
pytest tests/unit/test_experimenter_agent.py -v

# 集成测试（需要Docker）
pytest tests/integration/test_experimenter_e2e.py -v
```

测试覆盖：
- 实验计划解析
- 依赖关系分析
- 结果汇总生成
- 预算检查逻辑
- Docker执行（mock）

## 使用示例

```python
from researchos.agents.experimenter import ExperimenterAgent
from researchos.runtime.agent import ExecutionContext

agent = ExperimenterAgent()
ctx = ExecutionContext(
    workspace_dir=Path("/path/to/workspace"),
    task_id="T6"
)

result = await agent.run(ctx)
```

## 配置说明

### model_routing.yaml配置
```yaml
medium:
  provider: "anthropic"
  model: "claude-sonnet-4"
  max_tokens: 4096
```

### runtime.yaml配置
```yaml
agents:
  experimenter:
    max_retries: 3
    timeout_seconds: 14400  # 4小时
    enable_docker: true
    docker_image: "researchos/pytorch:latest"
    gpu_enabled: true
```

### Docker镜像配置

推荐使用预配置的Docker镜像，包含：
- Python 3.11+
- PyTorch 2.0+
- TensorFlow 2.13+
- 常用ML库（transformers, datasets, wandb等）
- CUDA 11.8+

详见 `/home/liangmengkun/ResearchOS/sandbox/Dockerfile`

详见 ResearchOS Runtime Dev Spec §6 和 §17。
