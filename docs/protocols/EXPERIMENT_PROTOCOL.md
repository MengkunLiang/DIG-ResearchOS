# Experimenter Agent 实验协议

本文档定义 Experimenter Agent（T5 Pilot / T7 Full）的详细执行协议，供 prompt 模板引用。

## 目录

1. [T5 Pilot 协议](#t5-pilot-协议)
2. [T7 Full 协议](#t7-full-协议)
3. [执行流程](#执行流程)
4. [输出格式](#输出格式)
5. [错误处理](#错误处理)

---

## T5 Pilot 协议

### 目标
快速验证假设可行性，使用最小资源完成"冒烟测试"。

### 硬性规则

| 规则 | 要求 | 原因 |
|------|------|------|
| Smoke Test | 代码必须支持 `--smoke_test` 参数 | 验证代码可运行 |
| batch_size | 2 | 最小资源 |
| max_steps | 3 | 快速验证 |
| seed | 42 | 可复现性 |
| 数据量 | 5-10% 训练数据 | 最小资源 |
| wandb | 禁用 | Pilot 阶段不需要 |
| checkpoint | 禁用 | Pilot 阶段不需要 |
| 超参搜索 | 禁用 | Pilot 阶段不需要 |

### Smoke Test 检查清单

```bash
python run.py --smoke_test --seed 42
```

验证项：
- [ ] forward pass 成功
- [ ] backward pass 成功
- [ ] optimizer.step() 成功
- [ ] 输出 "smoke_test: PASS"
- [ ] exit code = 0
- [ ] 创建 `smoke_test_passed.marker`

### Pilot 结果判定

| 判定 | 条件 | 下一步 |
|------|------|--------|
| PASS | 关键指标达到预期的 80% | 进入 T7 Full |
| REVISE | 方向正确但需要调优 | 修改后重试 Pilot |
| FAIL | 假设被证伪或无法验证 | 回 T4 重新假设 |

### 预算限制

| 指标 | 限制 |
|------|------|
| max_steps | 100 |
| 时间 | 2 小时 |
| tokens | 400K |

---

## T7 Full 协议

### 目标
执行完整实验计划，收集全面结果，支持论文发表。

### Ablation 实验规则

每个假设必须满足：

| 要求 | 最小数量 | 说明 |
|------|----------|------|
| 完整方法 | 1 | 基准 |
| 消融类型 | ≥3 | 移除关键组件 |
| Seed Ensemble | 3 | headline 结果 |

### Ablation 类型参考

```
remove_component_A      # 移除组件A
remove_component_B      # 移除组件B
replace_with_baseline   # 替换为基线
reduce_scale            # 缩减规模
no_regularization       # 无正则化
```

### Seed Ensemble 分层

| 层级 | Seeds | 用途 |
|------|-------|------|
| headline | [42, 43, 44] | 论文主要结果 |
| final_method | [42, 43] | 最终方法对比 |
| ablation | [42] | 消融实验 |

### Silent Failure 检测

检查 `logs.txt` 中的异常模式：

| 模式 | 检测关键词 | 严重性 | 处理 |
|------|-----------|--------|------|
| Loss 发散 | `nan`, `inf`, `NaN` | HIGH | 标记 quality_status=questionable |
| OOM | `OutOfMemoryError`, `CUDA OOM` | HIGH | 缩减模型/数据 |
| 不收敛 | loss 不下降 > 1000 steps | MEDIUM | 检查学习率 |
| 梯度爆炸 | `gradient norm.*inf` | HIGH | 梯度裁剪 |
| 梯度消失 | `gradient norm.*0.0` | MEDIUM | 检查初始化 |

### 迭代多样性规则

- 最多 5 轮迭代
- 每轮必须有实质性改进（+1% 或显著）
- 避免重复调参（相同超参数组合）
- 鼓励探索不同方向（架构、数据增强）

### 预算限制

| 指标 | 限制 |
|------|------|
| 总 GPU 时间 | < 20 GPU-h |
| max_steps | 150 |
| 时间 | 4 小时 |
| tokens | 600K |

警告阈值：GPU 使用 > 70% 时发出警告

---

## 执行流程

### Step 1: 读取输入

```python
exp_plan = read_file("ideation/exp_plan.yaml")
hypotheses = read_file("ideation/hypotheses.md")
project = read_file("project.yaml")
```

### Step 2: 准备环境

对每个实验：
1. 创建目录：`experiments/runs/{run_id}/`
2. 检查依赖：数据集、模型、库
3. 安装缺失依赖

### Step 3: 执行实验

```python
# 生成 run_id
run_id = f"exp_{experiment_name}_{timestamp}"

# 写入配置
config = {
    "experiment_id": run_id,
    "dataset": dataset,
    "method": method,
    "seed": seed,
    ...
}
write_file(f"experiments/runs/{run_id}/config.yaml", config)

# 执行
result = docker_exec(
    command=f"python run.py --config config.yaml",
    workdir=f"experiments/runs/{run_id}",
    timeout=timeout,
    gpu=True
)
```

### Step 4: 收集结果

每个实验必须收集：

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | DONE/FAILED/TIMEOUT |
| metrics | dict | 指标字典 |
| duration_seconds | int | 运行时间 |
| logs | string | 完整日志 |
| error | string | 错误信息（如失败） |

### Step 5: 汇总报告

1. `results_summary.json` - 汇总所有实验
2. `iteration_log.md` - 迭代过程记录
3. `ablations.csv` - Ablation 结果
4. `seed_ensemble_summary.json` - Seed 集成汇总
5. `iteration_diversity_check.md` - 多样性检查

---

## 输出格式

### results_summary.json

```json
{
  "exp_plan_ref": "ideation/exp_plan.yaml",
  "total_experiments": 5,
  "completed": 4,
  "failed": 1,
  "total_gpu_hours": 15.5,
  "experiments": [
    {
      "experiment_id": "exp_baseline_20260419_120000",
      "name": "Baseline实验",
      "hypothesis_ref": "H1",
      "tier": "headline",
      "status": "DONE",
      "metrics": {"accuracy": 0.87, "f1_score": 0.85},
      "seed_runs": [
        {"seed": 42, "accuracy": 0.87},
        {"seed": 43, "accuracy": 0.86},
        {"seed": 44, "accuracy": 0.88}
      ],
      "quality_status": "ok",
      "duration_seconds": 3600,
      "run_dir": "experiments/runs/exp_baseline_20260419_120000"
    }
  ],
  "summary": {
    "best_method": "our_method",
    "best_accuracy": 0.92,
    "success_criteria_met": true
  }
}
```

### ablations.csv

```csv
experiment_id,hypothesis_ref,ablation_type,metric,value,baseline_value,delta
exp_h1_ablation1,H1,remove_component_A,accuracy,0.85,0.87,-0.02
exp_h1_ablation2,H1,remove_component_B,accuracy,0.80,0.87,-0.07
exp_h1_ablation3,H1,replace_with_baseline,accuracy,0.83,0.87,-0.04
```

### iteration_diversity_check.md

```markdown
## 迭代多样性检查

### Iteration 1
- 探索方向：baseline 对比
- 超参数：lr=1e-4, batch_size=32
- 结果：accuracy=0.85

### Iteration 2
- 探索方向：增加数据增强
- 超参数：lr=1e-4, batch_size=32, augmentation=strong
- 结果：accuracy=0.87 (+2.4%)
- 多样性：✓ 新方向

### 判定
- 总迭代数：2
- 重复调参：0
- 实质性改进：✓
```

---

## 错误处理

### 实验失败处理流程

```
1. 捕获错误（stderr）
2. 记录到 logs.txt
3. 标记 status="FAILED"
4. 记录失败原因到 iteration_log.md
5. 继续下一个实验
```

### 重试策略

| 错误类型 | 重试次数 | 缓解措施 |
|----------|----------|----------|
| OOM | 2 | 缩减 batch_size / 模型规模 |
| Timeout | 1 | 检查计算估计是否准确 |
| Crash | 2 | 捕获异常，保存检查点 |
| Resource | 1 | 等待资源释放 |

### 超时处理

- 每个实验设置 timeout = compute_estimate.gpu_hours × 1.5
- 超时后：
  - 标记为 "TIMEOUT"
  - 保存已有结果
  - 分析部分完成的原因

---

## 红线规则

1. **不编造结果**：所有 metrics 必须来自真实实验
2. **完整记录**：失败也要记录完整错误
3. **独立运行**：每个实验独立 run_id
4. **资源约束**：不超过预算
5. **可复现**：保存完整配置和环境

---

## 参考

- [T7 Experimenter Agent 实现文档](../../agents/T7_EXPERIMENTER_FULL_AGENT.md)
- [ResearchOS Runtime Dev Spec §3, §5, §7, §8](../../reference_materials/ResearchOS_Runtime_Dev_Spec.md)
