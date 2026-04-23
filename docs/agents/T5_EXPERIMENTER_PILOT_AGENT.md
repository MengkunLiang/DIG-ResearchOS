# T5 Experimenter Agent (试点实验 Pilot) 实现文档

## 概述

Experimenter Agent（实验执行Agent）的 Pilot 模式是ResearchOS pipeline中的试点验证环节，负责在T4.5 Novelty Auditor通过后执行小规模试点实验。它通过快速验证研究假设的可行性，收集动机验证证据，为后续的完整实验（T7）提供决策依据。

**在Pipeline中的位置**: T5（试点实验阶段）

**前置条件**: T4.5 Novelty Auditor 的新颖性预审通过

**代码位置**:
- Agent实现: `/home/liangmengkun/ResearchOS/researchos/agents/experimenter.py`
- Prompt模板: `/home/liangmengkun/ResearchOS/researchos/prompts/experimenter.j2`
- 模式: `pilot`（试点实验模式）

**与 T7 Full 的关系**: T5 和 T7 共享同一个 `ExperimenterAgent` 类，通过 `mode` 参数区分：
- `mode="pilot"`: T5 试点实验模式
- `mode="full"`: T7 完整实验模式

## 设计规格

- **Agent名称**: `experimenter`
- **模式**: `pilot`
- **模型层级**: `medium`（需要代码生成和调试能力）
- **Temperature**: 0.3（保持代码生成的稳定性）
- **工具**: `read_file`, `write_file`, `list_files`, `append_file`, `bash_run`, `docker_exec`, `finish_task`
- **最大步数**: 100（建议 100 步内完成）
- **Token预算**: 400,000
- **超时时间**: 7200秒（2小时）

## T5 Pilot 与 T7 Full 的核心区别

| 方面 | T5 Pilot | T7 Full |
|------|----------|---------|
| **数据规模** | 5-10% 数据 | 100% 数据 |
| **随机种子** | 固定 seed=42 | Seed ensemble（3-5个种子） |
| **实验目标** | 验证假设可行性 | 收集完整实验证据 |
| **必需检查** | Smoke test（冒烟测试） | Ablation（消融实验，最少3条） |
| **输出重点** | Motivation validation | Results summary + Ablations |
| **预算** | 建议 2 小时，400K tokens | 最多 4 小时，600K tokens |
| **迭代次数** | 通常 1 轮 | 最多 5 轮 |
| **输出目录** | `pilot/` | `experiments/` |

## 输入

### 必需输入
- `ideation/exp_plan.yaml`: T4产出的实验计划
- `ideation/hypotheses.md`: T4产出的研究假设
- `project.yaml`: 项目配置
- `ideation/novelty_audit.md`: T4.5产出的新颖性预审报告

### 可选输入
- `user_seeds/seed_external_resources.jsonl`: 用户提供的外部资源（数据集、模型等）

### 输入格式示例

**exp_plan.yaml**:
```yaml
project_id: "discrete-diffusion-lang"
hypothesis_count: 3
experiments:
  - experiment_id: "exp-h1-pilot"
    name: "H1试点实验"
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
      pilot_size: "450K sentence pairs"  # 10% 数据
    metrics:
      primary: "BLEU"
      secondary: ["perplexity", "training_time"]
    expected_results:
      primary_target: 28.0  # Pilot 目标通常低于 Full
      baseline_value: 27.0
      improvement_threshold: 1.0
    compute_estimate:
      gpu_type: "V100"
      gpu_hours: 2  # Pilot 通常 2-4 小时
      memory_gb: 16
```

## 输出

### 产出文件

1. **pilot/pilot_plan.yaml**: 试点实验计划（必需）
2. **pilot/pilot_code/run_pilot.py**: 可执行的试点代码（必需，必须支持 `--smoke_test` 和 `--seed` 参数）
3. **pilot/pilot_results.json**: 试点实验结果（必需，必须包含 `seed=42`）
4. **pilot/motivation_validation.md**: 动机验证报告（必需，必须包含 PASS/REVISE/FAIL 判定）
5. **pilot/smoke_test_passed.marker**: 冒烟测试通过标记（必需，鲁棒性要求 §3.1）
6. **pilot/docker_digests.txt**: Docker 镜像 digest（必需，鲁棒性要求 §8.2）

### 输出格式

#### pilot_results.json

```json
{
  "project_id": "discrete-diffusion-lang",
  "mode": "pilot",
  "seed": 42,
  "total_experiments": 2,
  "completed": 2,
  "failed": 0,
  "total_gpu_hours": 3.5,
  "total_cost_usd": 10.5,
  "experiments": [
    {
      "experiment_id": "exp-h1-pilot",
      "name": "H1试点实验",
      "hypothesis_ref": "H1",
      "status": "DONE",
      "run_id": "r1_h1-pilot_42_20240120T143000",
      "metrics": {
        "BLEU": 28.2,
        "perplexity": 13.1,
        "training_time": 1.8
      },
      "baseline_metrics": {
        "BLEU": 27.0,
        "perplexity": 13.8
      },
      "improvement": {
        "BLEU": 1.2,
        "perplexity": -0.7
      },
      "target_met": true,
      "gpu_hours": 1.8,
      "cost_usd": 5.4,
      "notes": "试点实验成功，改进方向有效"
    },
    {
      "experiment_id": "exp-h1-smoke",
      "name": "H1冒烟测试",
      "hypothesis_ref": "H1",
      "status": "DONE",
      "run_id": "r1_h1-smoke_42_20240120T141000",
      "metrics": {
        "BLEU": 15.3,
        "training_time": 0.2
      },
      "gpu_hours": 0.2,
      "cost_usd": 0.6,
      "notes": "冒烟测试通过，代码可正常运行"
    }
  ],
  "smoke_test_passed": true,
  "motivation_validated": true,
  "summary": {
    "hypotheses_validated": ["H1"],
    "key_findings": [
      "H1的改进方向有效，试点实验达到目标",
      "adaptive scheduling在小规模数据上有明显提升",
      "代码实现无明显bug，可以进入完整实验"
    ],
    "recommendation": "PASS - 建议进入T6新颖性验证和T7完整实验"
  }
}
```

**必需字段说明**:
- `mode`: 必须为 `"pilot"`
- `seed`: 必须为 `42`（固定种子）
- `smoke_test_passed`: 必须为 `true`
- `motivation_validated`: 必须为 `true` 或 `false`
- `experiments`: 实验结果列表，至少包含 1 个 smoke test 和 1 个 pilot 实验

#### motivation_validation.md

```markdown
# 动机验证报告

## 判定结果

**PASS** ✓

## 验证的假设

### H1: Factorized Gap 改进

**假设陈述**: 通过引入 adaptive gap scheduling 和 learnable gap parameters，可以提升 discrete diffusion 模型在机器翻译任务上的性能。

**动机**: 现有的 discrete diffusion 模型使用固定的 gap schedule，无法适应不同的数据分布和任务特点。

## 试点实验证据

### 实验设置
- 数据规模: 450K sentence pairs（10% WMT2014）
- 随机种子: 42（固定）
- 训练时长: 1.8 GPU小时

### 关键结果
- **BLEU**: 28.2（基线 27.0，提升 +1.2）
- **Perplexity**: 13.1（基线 13.8，改进 -0.7）
- **训练时间**: 1.8小时（符合预期）

### 动机验证分析

#### 1. 假设的核心动机是否成立？
**是**。试点实验表明，adaptive gap scheduling 确实能够提升模型性能：
- BLEU 提升 1.2 分（+4.4%），超过预期阈值 1.0
- Perplexity 降低 0.7（-5.1%），表明模型拟合能力增强
- 训练曲线显示 adaptive scheduling 在早期收敛更快

#### 2. 改进方向是否有效？
**是**。两个关键改进都有贡献：
- Adaptive gap scheduling: 通过观察训练日志，发现模型能够自动调整 gap，适应不同训练阶段
- Learnable gap parameters: 参数在训练过程中逐渐优化，最终收敛到合理值

#### 3. 是否存在明显的实现问题？
**否**。冒烟测试通过，代码运行稳定：
- 无 nan/inf loss
- 无 OOM 错误
- 训练曲线平滑，无异常波动

#### 4. 是否值得投入完整实验？
**是**。试点实验的正面结果表明：
- 假设的动机成立，改进方向有效
- 代码实现质量良好，无明显bug
- 小规模数据上的提升有望在完整数据上保持或扩大
- 预估完整实验的成功概率 > 70%

## 风险评估

### 低风险
- 代码质量: 冒烟测试通过，无明显bug
- 计算资源: 试点实验在预算内完成

### 中风险
- 扩展性: 小规模数据的提升能否在完整数据上保持？需要在T7验证
- 超参数: 当前超参数可能不是最优，可能需要调优

### 高风险
- 无

## 建议的后续步骤

1. **进入T6新颖性验证**: 基于试点实验结果，搜索近期相关工作，确认新颖性
2. **进入T7完整实验**: 在完整数据集上验证假设，收集全面证据
3. **补充消融实验**: 在T7中分离 adaptive scheduling 和 learnable parameters 的贡献
4. **超参数调优**: 在T7中进行网格搜索，寻找最优超参数

## 判定依据

根据 ResearchOS 鲁棒性要求（§3.1 Smoke Test + Motivation Validation）：

- ✓ 冒烟测试通过（smoke_test_passed.marker 存在）
- ✓ 试点实验达到预期目标（BLEU 28.2 > 目标 28.0）
- ✓ 改进方向有效（两个关键改进都有贡献）
- ✓ 无明显实现问题（无 nan/inf/OOM）
- ✓ 值得投入完整实验（成功概率 > 70%）

**最终判定**: PASS

---

**生成时间**: 2024-01-20T15:30:00  
**生成者**: T5 Experimenter Agent (pilot mode)
```

**判定说明**:
- **PASS**: 动机成立，试点实验成功，建议进入T6和T7
- **REVISE**: 动机部分成立，但需要调整假设或实验设计
- **FAIL**: 动机不成立，试点实验失败，建议重新构思假设

#### smoke_test_passed.marker

```
PASSED
timestamp: 2024-01-20T14:15:00
experiment_id: exp-h1-smoke
run_id: r1_h1-smoke_42_20240120T141000
duration_seconds: 720
```

#### pilot_code/run_pilot.py

生成的代码必须支持以下参数：

```python
#!/usr/bin/env python3
"""
H1 Pilot Experiment: Factorized Gap Improvement

支持参数:
  --smoke_test: 冒烟测试模式（使用极小数据集，快速验证代码可运行）
  --seed: 随机种子（默认 42）
  --config: 配置文件路径
"""

import argparse
import json
import random
import numpy as np
import torch

def set_seed(seed: int):
    """设置随机种子，确保可复现"""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke_test", action="store_true", help="冒烟测试模式")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--config", type=str, default="config.yaml", help="配置文件")
    args = parser.parse_args()
    
    # 设置随机种子
    set_seed(args.seed)
    
    # 加载配置
    # ...
    
    # 如果是冒烟测试，使用极小数据集
    if args.smoke_test:
        print("Running smoke test with minimal data...")
        # 使用 100 条数据，训练 10 步
        train_size = 100
        max_steps = 10
    else:
        print("Running pilot experiment...")
        # 使用 10% 数据
        train_size = 450000
        max_steps = 10000
    
    # 训练逻辑
    # ...
    
    # 保存结果
    results = {
        "seed": args.seed,
        "smoke_test": args.smoke_test,
        "metrics": {
            "BLEU": 28.2,
            "perplexity": 13.1,
        }
    }
    
    with open("results.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print("Experiment completed successfully!")

if __name__ == "__main__":
    main()
```

## Workflow

### 执行流程

#### Step 1: Integrity Gate 检查（鲁棒性要求 §2.5）

在开始实验前，执行 Integrity Gate 检查：
- 检查 `ideation/hypotheses.md` 存在且非空
- 检查 `ideation/novelty_audit.md` 存在（T4.5 通过标志）
- 检查 `ideation/exp_plan.yaml` 格式正确

如果检查失败，终止任务并报告错误。

#### Step 2: 读取实验计划

- 使用 `read_file` 读取 `ideation/exp_plan.yaml`
- 读取 `ideation/hypotheses.md` 了解假设背景
- 读取 `project.yaml` 获取预算和约束
- 读取 `ideation/novelty_audit.md` 了解新颖性预审结果

#### Step 3: 生成试点实验计划

基于完整实验计划，生成试点版本：
- 数据规模缩减到 5-10%
- 训练步数缩减到 10-20%
- 固定随机种子为 42
- 添加冒烟测试实验

写入 `pilot/pilot_plan.yaml`。

#### Step 4: 生成试点代码

生成 `pilot/pilot_code/run_pilot.py`，必须包含：
- `--smoke_test` 参数支持
- `--seed` 参数支持
- 固定种子设置逻辑
- 小规模数据加载逻辑

#### Step 5: 执行冒烟测试（鲁棒性要求 §3.1）

**目的**: 快速验证代码可运行，避免浪费资源在有bug的代码上

**执行**:
```bash
python pilot/pilot_code/run_pilot.py --smoke_test --seed 42
```

**检查**:
- 代码能否正常运行？
- 是否有 nan/inf loss？
- 是否有 OOM 错误？
- 训练曲线是否正常？

**结果**:
- 如果通过，创建 `pilot/smoke_test_passed.marker`
- 如果失败，分析错误并修复，最多重试 3 次

#### Step 6: 执行试点实验

**执行**:
```bash
python pilot/pilot_code/run_pilot.py --seed 42 --config pilot/config.yaml
```

**监控**:
- 实时监控训练日志
- 检查 loss 是否收敛
- 检查 metrics 是否符合预期

**收集结果**:
- 保存 metrics 到 `pilot/pilot_results.json`
- 保存训练日志到 `pilot/logs.txt`

#### Step 7: 动机验证分析

基于试点实验结果，分析：
1. 假设的核心动机是否成立？
2. 改进方向是否有效？
3. 是否存在明显的实现问题？
4. 是否值得投入完整实验？

生成 `pilot/motivation_validation.md`，必须包含明确的判定：
- **PASS**: 动机成立，建议进入T6和T7
- **REVISE**: 动机部分成立，需要调整假设
- **FAIL**: 动机不成立，建议重新构思

#### Step 8: 记录 Docker 镜像（鲁棒性要求 §8.2）

记录使用的 Docker 镜像的精确 digest，确保环境可复现：

```bash
docker inspect researchos/pytorch:latest | jq -r '.[0].RepoDigests[0]' > pilot/docker_digests.txt
```

#### Step 9: 完成任务

调用 `finish_task` 完成任务。

## 验证规则

`validate_outputs` 检查（来源: academic-research-skills）：

### 1. Integrity Gate（§2.5）
- 检查 `ideation/hypotheses.md` 存在且非空
- 检查 `ideation/novelty_audit.md` 存在（T4.5 通过标志）
- 检查 `ideation/exp_plan.yaml` 格式正确

### 2. 基本文件存在性
- `pilot/pilot_results.json`
- `pilot/motivation_validation.md`
- `pilot/pilot_code/run_pilot.py`

### 3. Smoke Test 检查（§3.1）
- 必须存在 `pilot/smoke_test_passed.marker`
- 如果缺失，验证失败

### 4. Motivation Validation 判定检查
- `pilot/motivation_validation.md` 必须包含明确判定（PASS/REVISE/FAIL）
- 如果缺失判定，验证失败

### 5. 固定种子检查（§3.3）
- `pilot/pilot_results.json` 中的 `seed` 必须为 `42`
- 如果不是 42，验证失败

### 6. Docker Digest 检查（§8.2）
- 必须存在 `pilot/docker_digests.txt`
- 如果缺失，验证失败

### 7. 代码参数检查
- `pilot/pilot_code/run_pilot.py` 应包含 `--smoke_test` 参数支持
- `pilot/pilot_code/run_pilot.py` 应包含 `--seed` 参数支持
- 如果缺失，记录警告（不阻断）

### 8. 7 AI Research Failure Mode 检查（来源: academic-research-skills）

检查常见的 AI 错误模式：
- **FM1**: Implementation Bugs（检查 loss 是否发散）
- **FM2**: Hallucinated Results（交叉验证关键数字）
- **FM3**: Shortcut Reliance（消融实验是否分离组件）
- **FM4**: Bug-as-Insight Reframing（检查结果是否符合预期）
- **FM5**: Methodology Fabrication（验证方法描述与实现一致）
- **FM6**: Frame-Lock（检查是否有多视角分析）
- **FM7**: Citation Hallucinations（验证引用存在）

如果发现 HIGH 严重性问题，记录警告但不阻断（允许用户决定）。

### 9. Material Passport（来源: academic-research-skills）

生成 `pilot/manifest.yaml`，记录：
- 输出文件列表和校验和
- 输入文件列表和校验和
- 生成时间和 Agent 信息

用途：后续 Agent（T6, T7）可以验证输入文件是否变化。

## 工具使用

### 可用工具列表

- `read_file`: 读取实验计划和配置
- `write_file`: 写入实验代码、配置、结果
- `list_files`: 列出目录内容
- `append_file`: 追加内容到文件（如日志）
- `bash_run`: 执行shell命令（安装依赖、运行脚本等）
- `docker_exec`: 在Docker容器中执行命令（推荐用于实验）
- `finish_task`: 完成任务

### 推荐执行模式

**Docker隔离模式**（推荐）:
```python
# 冒烟测试
result = docker_exec(
    image="researchos/pytorch:latest",
    command="python run_pilot.py --smoke_test --seed 42",
    workdir="/workspace/pilot/pilot_code",
    gpu=True,
    timeout=600  # 10分钟
)

# 试点实验
result = docker_exec(
    image="researchos/pytorch:latest",
    command="python run_pilot.py --seed 42 --config config.yaml",
    workdir="/workspace/pilot/pilot_code",
    gpu=True,
    timeout=7200  # 2小时
)
```

## 与其他Agent的交互

- **依赖**: T4 Ideation Agent（需要 `exp_plan.yaml` 和 `hypotheses.md`）和 T4.5 Novelty Auditor（需要 `novelty_audit.md`）
- **被依赖**: T6 Novelty Agent（使用 `pilot_results.json` 和 `motivation_validation.md` 进行新颖性最终验证）

## 已知限制和注意事项

1. **固定种子**: Pilot 模式强制使用 seed=42，无法测试多种子鲁棒性
2. **小规模数据**: 5-10% 数据可能无法充分反映完整数据的特性
3. **简化实验**: Pilot 通常不包含完整的消融实验和超参数调优
4. **时间限制**: 2小时超时可能不足以完成某些复杂实验
5. **动机验证主观性**: PASS/REVISE/FAIL 判定依赖 Agent 的分析能力
6. **Docker 环境**: 需要 Docker 环境进行隔离执行以确保可复现性

## 测试

运行测试：
```bash
# 单元测试
pytest tests/unit/test_experimenter_agent.py::test_pilot_mode -v

# 集成测试（需要Docker）
pytest tests/integration/test_experimenter_pilot_e2e.py -v
```

测试覆盖：
- Integrity Gate 检查
- Smoke test 执行
- 固定种子验证
- Motivation validation 生成
- Docker digest 记录

## 使用示例

```python
from researchos.agents.experimenter import ExperimenterAgent
from researchos.runtime.agent import ExecutionContext

agent = ExperimenterAgent()
ctx = ExecutionContext(
    workspace_dir=Path("/path/to/workspace"),
    task_id="T5",
    mode="pilot"  # 关键：指定 pilot 模式
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
    pilot:
      max_retries: 3
      timeout_seconds: 7200  # 2小时
      enable_docker: true
      docker_image: "researchos/pytorch:latest"
      gpu_enabled: true
      fixed_seed: 42
      smoke_test_required: true
```

详见 ResearchOS Runtime Dev Spec §5（T5 Pilot Experimenter）和 academic-research-skills（Integrity Gate + Smoke Test）。
