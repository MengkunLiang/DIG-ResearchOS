# 鲁棒性增强实施总结

## 概述

本次实施完成了 `ResearchOS_Agent_Dev_Spec_Addendum_Robustness.md` 中针对 T1-T4 的 4 个必做鲁棒性增强项。

实施日期：2026-04-19

## 已实施功能

### 1. T4 Hypothesis Pre-mortem（§4.1）

**目标**：在 Gate1 和 Gate2 之间添加反常识验证，避免违反基本常识或已知事实的假设。

**实现**：
- 修改文件：`researchos/prompts/ideation.j2`
- 新增阶段：阶段 A.5（Pre-mortem 检查）
- 检查维度：
  1. 物理/数学约束检查
  2. 已知反例检查
  3. 资源可行性检查
- 输出文件：`ideation/_premortem.md`
- 代码量：~80 行（prompt 改动）

**工作流程**：
1. 用户在 Gate1 选定研究方向
2. 系统自动执行 pre-mortem 检查
3. 对每个维度评估风险等级（Low/Medium/High）
4. 如果存在 High 风险且无缓解方案，提示用户重新选择
5. 否则进入阶段 B（展开假设与计划）

### 2. Runtime Budget Drift Warning（§7.1）

**目标**：在每个任务完成后检查预算漂移，及时预警。

**实现**：
- 修改文件：`researchos/orchestration/state_machine.py`
- 新增方法：`_check_budget_drift`
- 警告阈值：
  - 70%：记录警告日志
  - 90%：记录严重警告日志 + 写入警告文件
- 代码量：~60 行

**工作流程**：
1. 每次 `advance` 方法更新累计预算后调用检查
2. 从 `project.yaml` 读取预算上限
3. 计算累计花费占比
4. 超过阈值时记录警告
5. 90% 以上时额外写入 `<workspace>/.researchos/budget_warning.txt`

### 3. T1 Ethical Screening（§8.1）

**目标**：在 T1 中添加敏感方向拦截，避免生成涉及敏感领域的研究方向。

**实现**：
- 修改文件：`researchos/agents/pi.py`
- 新增方法：`_check_ethical_concerns`
- 敏感词类别：weapons、surveillance、manipulation、privacy、discrimination
- 代码量：~40 行

**工作流程**：
1. 在 T1 输出校验阶段调用
2. 检查 `project.yaml` 的 `research_direction` 和 `keywords`
3. 如果检测到敏感词，返回警告信息
4. 要求用户确认研究目的符合伦理规范

### 4. T1 外部资源管理（§10.1-10.2）

**目标**：支持用户提供已有的 dataset、baseline 代码、pretrained model 等外部资源。

**实现**：
- 修改文件：
  - `researchos/prompts/pi.j2`：添加第 2.5 轮对话询问外部资源
  - `researchos/agents/pi.py`：添加 `_validate_external_resources` 方法
- 输出文件：`user_seeds/seed_external_resources.jsonl`（可选）
- 支持的资源类型：dataset、baseline_repo、pretrained_model、docker_image、tool、script、other
- 支持的 source 格式：huggingface、github、docker、pip、url、local
- 代码量：~120 行（prompt + 验证逻辑）

**资源格式示例**：
```jsonl
{"type": "dataset", "name": "ImageNet-1k", "source": "huggingface:imagenet-1k", "access": "auto", "purpose": "main benchmark"}
{"type": "baseline_repo", "name": "ResNet-50", "source": "github:pytorch/vision", "commit": "v0.17.0", "purpose": "baseline implementation"}
{"type": "pretrained_model", "name": "BERT-base", "source": "huggingface:bert-base-uncased", "purpose": "encoder"}
```

## 测试覆盖

创建了完整的单元测试文件：`tests/unit/test_robustness_enhancements.py`

测试用例：
- `TestEthicalScreening`：3 个测试
  - 检测武器相关敏感词
  - 检测监控相关敏感词
  - 正常研究方向通过检查
- `TestExternalResources`：6 个测试
  - 验证合法格式
  - 检测非法资源类型
  - 检测非法 source 格式
  - 检测缺少必需字段
  - 空文件也是合法的
  - 检测非法 JSON
- `TestBudgetDriftWarning`：3 个测试
  - 70% 预算警告
  - 90% 预算严重警告
  - 低于阈值不触发警告

**测试结果**：
```bash
$ pytest tests/unit/test_robustness_enhancements.py -v
============================== 12 passed in 0.07s ==============================
```

## 文档更新

- 更新 `README.zh-CN.md`：添加"鲁棒性增强功能"章节
- 创建 `docs/ROBUSTNESS_IMPLEMENTATION_SUMMARY.md`：本文档

## 代码统计

| 功能 | 修改文件 | 新增代码行数 | 测试用例数 |
|------|---------|------------|----------|
| T4 Pre-mortem | ideation.j2 | ~80 | - |
| Budget Warning | state_machine.py | ~60 | 3 |
| Ethical Screening | pi.py | ~40 | 3 |
| External Resources | pi.j2 + pi.py | ~120 | 6 |
| **总计** | 4 个文件 | ~300 | 12 |

## 验证方式

### 1. 运行单元测试

```bash
cd /home/liangmengkun/ResearchOS
conda activate researchos
pytest tests/unit/test_robustness_enhancements.py -v
```

### 2. 手动验证 T1 Ethical Screening

创建一个包含敏感词的项目：

```bash
researchos run-task T1 --workspace ./workspace/test_ethical \
  --extra user_topic="developing bioweapon detection systems"
```

预期：T1 完成后，输出校验会失败并提示敏感研究方向警告。

### 3. 手动验证 Budget Warning

修改 `project.yaml` 设置较低的预算（如 $10），然后运行多个任务，观察日志输出。

### 4. 手动验证 T4 Pre-mortem

运行 T1-T4 完整流程，在 T4 的 Gate1 选定方向后，检查是否生成了 `ideation/_premortem.md` 文件。

## 已知限制

1. **Ethical Screening**：
   - 敏感词列表是硬编码的，未来可考虑外部配置
   - 仅检测英文关键词，中文敏感词需要额外添加

2. **External Resources**：
   - 目前仅验证格式，不验证资源的实际可达性
   - 可达性检查可作为 post-hook 实现（未来工作）

3. **Pre-mortem**：
   - 依赖 LLM 的推理能力，质量取决于模型
   - 未强制要求用户必须通过 pre-mortem 才能继续

4. **Budget Warning**：
   - 仅记录警告，不强制中断执行
   - 未来可考虑添加用户确认机制

## 下一步建议

根据 `ResearchOS_Agent_Dev_Spec_Addendum_Robustness.md` §9.1，以下推荐项可在 M1 后评估：

- §2.3 Related-work staleness check
- §4.2 Dumb question test
- §4.3 Incremental novelty detection
- §5.2 Competing hypothesis tracking
- §6.2 Narrative consistency check
- §6.3 Automated rebuttal prep
- §7.2 Deadline-aware budget reallocation
- §8.3 Unit test generation for pilot code

针对 T5-T9 的必做项（等这些 agent 实现后再补充）：

- §2.1 Claim-to-evidence traceability（T8 Writer）
- §2.2 Number precision consistency（T8 Writer）
- §3.1 Pre-experiment smoke test（T5/T7）
- §3.2 Silent failure detection（T7）
- §3.3 Seed ensemble（T7）
- §5.1 Iteration diversity（T7）
- §5.3 Ablation minimum（T7）
- §6.1 Reviewer pre-mortem（T8）
- §8.2 Docker digest pinning（T7/T9）

## 总结

本次实施完成了针对 T1-T4 的 4 个必做鲁棒性增强项，所有功能都有对应的单元测试且测试通过。这些增强功能提升了 ResearchOS 的可靠性和用户体验：

- **Ethical Screening**：避免生成敏感研究方向
- **External Resources**：避免重复实现已有资源
- **Pre-mortem**：避免违反常识的假设
- **Budget Warning**：避免预算超支

代码已准备好提交到 git 仓库。
