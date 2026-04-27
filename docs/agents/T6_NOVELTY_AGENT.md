# T6 Novelty Agent 实现文档

## 概述

Novelty Agent（新颖性最终验证Agent）是ResearchOS pipeline中基于实验证据进行新颖性验证的环节。它在T5 Pilot实验完成后执行，基于实验结果验证研究假设的新颖性，搜索近期相关工作，补充必须的基线方法，并做出最终的 PASS/REVISE/FAIL 决策。

**在Pipeline中的位置**: T6（新颖性最终验证阶段）

**代码位置**:
- Agent实现: `/home/liangmengkun/ResearchOS/researchos/agents/novelty.py`
- Prompt模板: `/home/liangmengkun/ResearchOS/researchos/prompts/novelty.j2`

## T4.5 vs T6 新颖性验证的区别

> ⚠️ **重要区分**: T4.5 NoveltyAuditor 和 T6 Novelty 是两个不同的阶段。

| 方面 | T4.5 NoveltyAuditor | T6 Novelty |
|------|---------------------|------------|
| **时机** | T4 Ideation 后，Pilot 前 | T5 Pilot 后，Full 前 |
| **输入** | 纯假设，无实验结果 | 有 Pilot 实验证据 |
| **目的** | 预审假设新颖性，识别撞车风险 | 基于实验验证新颖性，补充基线 |
| **模型** | heavy (deep_reasoning) | medium |
| **输出目录** | `ideation/` | `novelty/` |
| **Gate** | 无（直接进入 T5） | T6-DECIDE (PASS/REVISE/FAIL) |
| **关键区别** | 无实验证据，纯假设审计 | 有 Pilot 证据支撑判断 |

### 为什么 T6 在 Pilot 实验后？

T4.5 只能做**假设层面的审计**，因为没有实验结果。T6 之所以在 Pilot 后执行，是因为：

1. **实验证据支撑**: Pilot 实验结果可以验证假设是否成立
2. **更准确的撞车判断**: 有实验数据后能更准确地判断是否与已有工作重叠
3. **基线补充**: 需要根据实验结果补充对比的基线方法

### T6 的输出目标

T6 产出**最终的新颖性报告**，包含：
- 基于实验验证的新颖性等级（可能与 T4.5 不同）
- 必须补充的基线方法列表
- T6-DECIDE 决策（PASS/REVISE/FAIL）

## 设计规格

- **Agent名称**: `novelty`
- **模型层级**: `medium`（主要是搜索和对比分析）
- **Temperature**: 0.3（保持客观和一致性）
- **工具**: `read_file`, `write_file`, `list_files`, `search_papers`, `fetch_paper_metadata`, `finish_task`
- **最大步数**: 50
- **Token预算**: 300,000
- **超时时间**: 3600秒（1小时）

## 输入

### 必需输入
- `ideation/hypotheses.md`: T4产出的研究假设
- `pilot/pilot_results.json`: T5产出的Pilot实验结果
- `pilot/motivation_validation.md`: T5产出的动机验证报告

### 可选输入
- `literature/synthesis.md`: T3.5产出的文献综述
- `ideation/novelty_audit.md`: T4.5的新颖性审计报告（优先参考，用于增量复核）

### 输入格式示例

**pilot_results.json**:
```json
{
  "project_id": "discrete-diffusion-lang",
  "total_experiments": 3,
  "completed": 2,
  "failed": 1,
  "experiments": [
    {
      "experiment_id": "pilot-h1-baseline",
      "hypothesis_ref": "H1",
      "status": "DONE",
      "metrics": {
        "BLEU": 29.2,
        "perplexity": 12.5
      },
      "notes": "初步验证H1改进方向有效"
    }
  ]
}
```

## 输出

### 产出文件

1. **novelty/novelty_report.md**: 新颖性最终报告（必需）
2. **novelty/collision_cases.md**: 潜在撞车案例（如果有）
3. **novelty/must_add_baselines.md**: 必须补充的基线方法（必需）

### 输出格式

#### novelty_report.md

```markdown
# 新颖性最终报告

生成时间: 2024-01-22 10:00:00
基于: T5 Pilot 实验结果

## T6-DECIDE: PASS

### 决策依据

- 所有假设新颖性等级 ≥ Level 2
- Pilot 实验验证了核心假设 H1 成立
- 未发现高风险撞车案例

## 审计摘要

- 总假设数: 3
- Level 3（高新颖性）: 1个 (H2)
- Level 2（中等新颖性）: 2个 (H1, H3)
- 潜在撞车风险: 无高风险

## H1: Pilot 实验验证结果

### 新颖性等级: Level 2（中等新颖性）

### 实验证据
- Pilot BLEU: 29.2（相比 baseline 28.5 提升 +0.7）
- 实验验证了 adaptive gap scheduling 的有效性
- 消融实验确认了关键组件的贡献

### 相关工作搜索
[与 T4.5 类似，但基于实验数据更新]

### 更新后的新颖性分析
- **实验支持**: Pilot 结果支持 H1 成立
- **差异化**: 与 [arxiv:2312.12345] 的差异在离散空间特殊处理
- **风险**: 低

## 必须补充的基线方法

详见 must_add_baselines.md

## 下一步行动

1. 继续 T7 完整实验
2. 补充 must_add_baselines.md 中的基线方法
3. 确保在论文中明确对比所有相关工作
```

#### must_add_baselines.md

```markdown
# 必须补充的基线方法

生成时间: 2024-01-22 10:00:00

## 背景

根据 T6 新颖性审计，以下基线方法必须在 T7 完整实验中进行对比。

## 必须对比的基线

### 1. [arxiv:2312.12345] Adaptive Diffusion Scheduling

**论文**: "Adaptive Diffusion Scheduling for Continuous Models"
**发表**: ICML 2024

**对比原因**:
- 与 H1 方法最相关的已有工作
- 需要明确区分连续 vs 离散扩散的差异

**建议的对比实验**:
- 使用他们的方法在离散空间复现
- 对比我们的方法在离散空间的改进

### 2. [arxiv:2305.12345] Standard Discrete Diffusion

**论文**: "Discrete Diffusion Models for Language Generation"
**发表**: NeurIPS 2023

**对比原因**:
- 标准的离散扩散基线
- 证明我们的改进不是简单的超参数调优

## 可选对比的基线

### 3. [arxiv:2401.67890] Dynamic Gap Selection

**论文**: "Dynamic Gap Selection in Discrete Models"
**发表**: arXiv 2024

**对比原因**:
- 类似的 gap 改进思路
- 应用场景不同（图生成 vs 语言生成）

**是否必需**: 可选（取决于论文篇幅）

## 对比实验清单

| 基线方法 | 来源 | 必须对比 | 理由 |
|---------|------|---------|------|
| Standard Discrete Diffusion | [arxiv:2305.12345] | 是 | 核心基线 |
| Adaptive Diffusion [arxiv:2312.12345] | [arxiv:2312.12345] | 是 | 最相关工作 |
| Dynamic Gap Selection | [arxiv:2401.67890] | 否 | 可选 |

## T7 实验计划建议

在 T7 完整实验中，确保：
1. 使用相同的评估指标和数据集
2. 报告统计显著性
3. 提供详细的消融分析
```

## Workflow

### 执行流程

#### Step 1: 读取所有输入
- 使用`read_file`读取`ideation/hypotheses.md`
- 读取`pilot/pilot_results.json`（获取实验证据）
- 读取`pilot/motivation_validation.md`（获取动机验证）
- 读取`ideation/novelty_audit.md`（参考 T4.5 的判断）

#### Step 2: 分析 Pilot 实验结果
- 提取每个假设的实验验证状态
- 识别哪些假设得到实验支持
- 分析失败的假设原因

#### Step 3: 小范围增量搜索近期相关工作
T6 不应把 T4.5 的全量 novelty search 原样重跑，而应：
- 先复用 `ideation/novelty_audit.md` 的结论
- 只补搜 1-2 个高风险或不确定假设
- 只关注新出现的工作和缺失 baseline
- 每个假设最多 1-2 个 query，`max_results` 明显缩小

#### Step 4: 更新新颖性等级
根据实验证据调整新颖性等级：
- **升级**: 如果实验结果显示比预期更好的差异化
- **降级**: 如果发现更相关的已发表工作

#### Step 5: 生成 must_add_baselines.md
识别必须在论文中对比的基线方法：
- 最相关的已有工作
- 标准的基线方法
- 近期发表的类似方法

#### Step 6: 生成 novelty_report.md
包含：
- T6-DECIDE 决策
- 每个假设的更新新颖性分析
- 实验证据总结
- 下一步建议

#### Step 7: T6-DECIDE Gate
输出 T6-DECIDE 决策：

| 决策 | 条件 | 后续动作 |
|------|------|----------|
| PASS | 所有假设 Level 2+ 且 Pilot 充分验证 | 进入 T7 完整实验 |
| REVISE | 存在 Level 1 假设或 Pilot 部分验证 | 修改假设或补充验证 |
| FAIL | 存在 Level 0 假设或 Pilot 未验证核心假设 | 重新构思 |

#### Step 8: 完成任务
调用`finish_task`完成任务。

## T6-DECIDE 决策标准

### PASS 标准

1. **新颖性**: 所有假设新颖性等级 ≥ Level 2
2. **实验验证**: 核心假设 (H1) 有 Pilot 实验支持
3. **撞车风险**: 无高风险撞车案例
4. **基线对比**: 有明确的基线对比计划

### REVISE 标准

1. **新颖性**: 存在 Level 1 假设
2. **实验验证**: Pilot 只部分验证了核心假设
3. **基线缺失**: 缺少必须对比的关键基线

### FAIL 标准

1. **新颖性**: 存在 Level 0 假设（几乎相同的工作已发表）
2. **实验验证**: Pilot 未验证核心假设（实验失败）
3. **撞车风险**: 发现高风险撞车案例，且无法差异化

## 校验逻辑

`validate_outputs`检查：

1. **novelty_report.md存在且有内容**
   - 长度至少500字符
   - 必须包含 T6-DECIDE 决策（PASS/REVISE/FAIL）
   - 必须包含新颖性等级（Level 0/1/2/3）

2. **must_add_baselines.md存在且有内容**
   - 长度至少200字符
   - 包含至少一个必须对比的基线

3. **decision 有效性**
   - decision 必须是 PASS/REVISE/FAIL 之一
   - decision 与报告内容一致

## 与其他Agent的交互

- **依赖**: T5 Experimenter（需要pilot_results.json）和 T4.5 NoveltyAuditor（参考novelt_audit.md）
- **被依赖**: T7 Experimenter（使用novelt_report.md和must_add_baselines.md）

## 已知限制和注意事项

1. **搜索覆盖**: 只能搜索公开发表的论文，无法发现未公开的工作
2. **时间窗口**: 如果在 Pilot 和 T6 之间有新的论文发表，可能漏检
3. **实验依赖**: T6 的判断依赖于 Pilot 实验的质量
4. **决策主观性**: T6-DECIDE 决策可能因人而异

## 测试

运行测试：
```bash
# 单元测试
pytest tests/unit/test_novelty_agent.py -v

# 集成测试（需要mock search_papers）
pytest tests/integration/test_novelty_e2e.py -v
```

测试覆盖：
- Pilot 结果解析
- 新颖性等级更新逻辑
- 基线识别
- T6-DECIDE 决策生成

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
  novelty:
    max_retries: 3
    timeout_seconds: 3600
    enable_thinking: true
```

详见 ResearchOS Runtime Dev Spec §6 和 §17。
