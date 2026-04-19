# ResearchOS 鲁棒性增强需求分析

基于 `ResearchOS_Agent_Dev_Spec_Addendum_Robustness.md` 的分析

## 当前状态

已实现：
- ✅ T1 PI Agent（项目初始化）
- ✅ T2 Scout Agent（文献检索）
- ✅ T3 Reader Agent（深度阅读）
- ✅ T3.5 Reader Agent（文献综合）
- ✅ T4 Ideation Agent（假设生成）

## 必做项分析（针对T1-T4）

### 1. T1相关必做项

#### 1.1 Ethical screening（§8.1）- 敏感方向拦截

**问题**：T1可能生成涉及敏感领域的研究方向（生物武器、监控技术等）

**方案**：
- 在T1的`validate_outputs`中添加敏感词检测
- 检查`project.yaml`的`research_direction`和`keywords`
- 如果检测到敏感词，返回警告并要求用户确认

**实现位置**：
- `researchos/agents/pi.py` - 添加`_check_ethical_concerns`方法
- `researchos/schemas/ethical_keywords.yaml` - 敏感词列表

**代码量**：~30行

**示例**：
```python
SENSITIVE_KEYWORDS = {
    "weapons": ["weapon", "explosive", "bioweapon"],
    "surveillance": ["surveillance", "tracking", "monitoring people"],
    "manipulation": ["manipulation", "deception", "fake news generation"],
}

def _check_ethical_concerns(self, project_data):
    direction = project_data.get("research_direction", "").lower()
    keywords = [k.lower() for k in project_data.get("keywords", [])]
    
    concerns = []
    for category, words in SENSITIVE_KEYWORDS.items():
        for word in words:
            if word in direction or any(word in kw for kw in keywords):
                concerns.append((category, word))
    
    if concerns:
        return False, f"检测到敏感研究方向: {concerns}，请确认是否继续"
    return True, None
```

#### 1.2 外部资源管理（§10.1-10.2）- seed_external_resources.jsonl

**问题**：用户通常已有可用的dataset、baseline代码、pretrained model，让agent从零搜索既慢又容易出错

**方案**：
- T1新增产出：`user_seeds/seed_external_resources.jsonl`
- 支持7种资源类型：dataset、baseline_repo、pretrained_model等
- T1 post-hook检查外部资源可达性

**实现位置**：
- `researchos/agents/pi.py` - 在三轮对话中询问外部资源
- `researchos/prompts/pi.j2` - 添加外部资源询问环节
- `researchos/hooks/external_resources.py` - 可达性检查hook

**代码量**：~100行（prompt改动 + hook）

**示例资源格式**：
```jsonl
{"type": "dataset", "name": "ImageNet-1k", "source": "huggingface:imagenet-1k", "access": "auto", "purpose": "main benchmark"}
{"type": "baseline_repo", "name": "ResNet-50", "source": "github:pytorch/vision", "commit": "v0.17.0", "purpose": "baseline implementation"}
{"type": "pretrained_model", "name": "BERT-base", "source": "huggingface:bert-base-uncased", "purpose": "encoder"}
```

### 2. T4相关必做项

#### 2.1 Hypothesis pre-mortem（§4.1）- 反常识验证

**问题**：T4生成的假设可能违反基本常识或已知事实，导致后续实验白费

**方案**：
- 在Gate1后，对每个候选方向做"反常识验证"
- 让LLM扮演skeptic角色，提出3个"为什么这个假设可能是错的"
- 如果无法回答这些质疑，标记为高风险

**实现位置**：
- `researchos/prompts/ideation.j2` - 在Gate1和Gate2之间添加pre-mortem环节
- 在生成候选方向后，对每个方向执行pre-mortem

**代码量**：prompt改动（~50行新增内容）

**示例prompt片段**：
```
## Pre-mortem检查（在Gate1之后）

对于用户选定的方向，执行以下反常识验证：

1. **物理/数学约束检查**：这个假设是否违反已知的物理定律或数学原理？
2. **已知反例检查**：文献中是否已有反例证明这个方向不可行？
3. **资源可行性检查**：在给定的预算和时间内，这个假设是否可验证？

对每个问题，写出：
- 潜在问题：可能的致命缺陷
- 缓解方案：如何规避或验证
- 风险评级：Low/Medium/High

如果任何一项是High风险且无缓解方案，建议用户重新选择方向。
```

### 3. Runtime相关必做项

#### 3.1 Budget drift warning（§7.1）- 预算漂移预警

**问题**：用户设定预算$100，但T2-T4累计已花费$85，T5还没开始就快超预算

**方案**：
- 在每个task完成后，计算累计花费
- 如果超过预算的70%，发出警告
- 如果超过90%，要求用户确认是否继续

**实现位置**：
- `researchos/runtime/orchestrator.py` - 在`AgentRunner`中添加预算跟踪
- `researchos/orchestration/state_machine.py` - 在状态推进时检查预算

**代码量**：~80行

**示例**：
```python
class BudgetTracker:
    def __init__(self, max_budget_usd):
        self.max_budget = max_budget_usd
        self.spent = 0.0
        
    def add_cost(self, cost_usd):
        self.spent += cost_usd
        ratio = self.spent / self.max_budget
        
        if ratio > 0.9:
            raise BudgetExceededError(
                f"预算即将耗尽: ${self.spent:.2f} / ${self.max_budget:.2f} (90%)"
            )
        elif ratio > 0.7:
            logger.warning(
                f"预算警告: ${self.spent:.2f} / ${self.max_budget:.2f} (70%)"
            )
```

## 实施优先级

根据Addendum §9.1，按成本/收益排序：

### 第一批（最高优先级）
1. **T4 Hypothesis pre-mortem**（§4.1）
   - 成本：prompt改动
   - 收益：T4质量巨大提升
   - 预计工作量：2-3小时

2. **Runtime Budget drift warning**（§7.1）
   - 成本：~80行
   - 收益：用户信任硬指标
   - 预计工作量：4-6小时

### 第二批（高优先级）
3. **T1 Ethical screening**（§8.1）
   - 成本：~30行
   - 收益：合规 + 避免敏感方向
   - 预计工作量：2-3小时

4. **T1 外部资源管理**（§10.1-10.2）
   - 成本：~100行
   - 收益：避免重复实现baseline
   - 预计工作量：6-8小时

## 其他必做项（T5-T9相关，暂不实施）

以下必做项与T5-T9相关，等这些agent实现后再补充：

- §2.1 Claim-to-evidence traceability（T8 Writer）
- §2.2 Number precision consistency（T8 Writer）
- §3.1 Pre-experiment smoke test（T5/T7）
- §3.2 Silent failure detection（T7）
- §3.3 Seed ensemble（T7）
- §5.1 Iteration diversity（T7）
- §5.3 Ablation minimum（T7）
- §6.1 Reviewer pre-mortem（T8）
- §8.2 Docker digest pinning（T7/T9）

## 推荐项（暂不实施）

根据Addendum §9.2，以下推荐项在M1后评估：

- §2.3 Related-work staleness check
- §4.2 Dumb question test
- §4.3 Incremental novelty detection
- §5.2 Competing hypothesis tracking
- §6.2 Narrative consistency check
- §6.3 Automated rebuttal prep
- §7.2 Deadline-aware budget reallocation
- §8.3 Unit test generation for pilot code

## 实施计划

### 阶段1：真实LLM测试（当前）
- 配置API key
- 运行T1→T2→T3→T3.5→T4完整流程
- 记录所有bug和问题
- 修复致命和严重bug

### 阶段2：第一批鲁棒性增强（预计1周）
- 实现T4 Hypothesis pre-mortem
- 实现Runtime Budget drift warning
- 编写单元测试
- 更新文档

### 阶段3：第二批鲁棒性增强（预计1周）
- 实现T1 Ethical screening
- 实现T1外部资源管理
- 编写单元测试
- 更新文档

### 阶段4：集成测试和文档（预计3天）
- 端到端测试所有增强功能
- 更新README和使用文档
- 提交代码

## 总工作量估算

- 真实LLM测试 + bug修复：1-2天
- 第一批鲁棒性增强：5-7天
- 第二批鲁棒性增强：5-7天
- 集成测试和文档：2-3天
- **总计**：13-19天

## 成功标准

1. ✅ T1-T4真实LLM测试全部通过
2. ✅ 所有致命和严重bug已修复
3. ✅ T4 Hypothesis pre-mortem实现并测试通过
4. ✅ Runtime Budget drift warning实现并测试通过
5. ✅ T1 Ethical screening实现并测试通过
6. ✅ T1外部资源管理实现并测试通过
7. ✅ 所有单元测试通过
8. ✅ 文档更新完整

## 风险和缓解

### 风险1：真实LLM测试发现大量bug
**缓解**：优先修复致命bug，轻微bug可延后

### 风险2：鲁棒性增强实现复杂度超预期
**缓解**：先实现最小可行版本，后续迭代优化

### 风险3：API成本超预算
**缓解**：使用较小的测试数据集，控制测试次数

## 下一步行动

1. **立即**：配置API key，运行真实LLM测试
2. **测试完成后**：分析bug，制定修复计划
3. **bug修复后**：开始第一批鲁棒性增强
4. **持续**：更新文档，记录所有改动
