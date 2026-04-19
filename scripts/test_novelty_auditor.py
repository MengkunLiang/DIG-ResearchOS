#!/usr/bin/env python3
"""T4.5 Novelty Auditor Agent 真实LLM测试脚本。

测试流程：
1. 准备测试workspace和输入数据
2. 运行Novelty Auditor agent
3. 验证输出文件
4. 记录测试结果
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from researchos.agents.novelty_auditor import NoveltyAuditorAgent
from researchos.runtime.agent import ExecutionContext


def prepare_test_workspace(workspace_dir: Path):
    """准备测试workspace和输入数据。"""
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # 创建目录结构
    (workspace_dir / "literature").mkdir(exist_ok=True)
    (workspace_dir / "ideation").mkdir(exist_ok=True)

    # 创建project.yaml
    project_yaml = workspace_dir / "project.yaml"
    project_yaml.write_text("""
research_direction: "Efficient Transformer Inference with Learnable Sparse Attention"
keywords:
  - "sparse attention"
  - "transformer inference"
  - "efficiency"
  - "learnable pruning"
constraints:
  max_budget_usd: 500
  max_duration_days: 30
  compute_resources:
    allow_gpu: true
    max_gpu_hours: 100
""")

    # 创建hypotheses.md
    hypotheses_md = workspace_dir / "ideation" / "hypotheses.md"
    hypotheses_md.write_text("""# 研究假设

## H1: 学习式稀疏注意力可以在保持性能的同时显著降低推理成本

### 背景
现有的Transformer模型在长文本推理时计算成本高昂，主要瓶颈在于注意力机制的O(n²)复杂度。
虽然已有一些稀疏注意力方法（如Longformer、BigBird），但它们大多使用固定的稀疏模式，
无法根据输入内容动态调整。我们观察到，不同的输入对注意力的需求是不同的，
因此提出学习式稀疏注意力机制。

### 核心假设
通过引入可学习的注意力剪枝模块，模型可以在推理时动态决定哪些注意力连接是必要的，
从而在保持准确率的同时将FLOPs降低到原来的40%以下。

### 预期结果
- 在LongBench数据集上，准确率≥95%的全注意力基线
- FLOPs < 60%的全注意力基线
- 推理延迟 < 70%的全注意力基线

### 风险
主要风险是学习的稀疏模式可能不够稳定，导致在某些输入上性能下降。
如果出现这种情况，我们将尝试引入正则化或多阶段训练策略。

## H2: 基于梯度的注意力重要性估计可以指导高效的稀疏模式学习

### 背景
现有的注意力剪枝方法大多基于启发式规则（如保留top-k个最大的注意力权重），
缺乏理论指导。我们提出使用梯度信息来估计每个注意力连接的重要性，
这样可以更准确地识别哪些连接对最终输出影响最大。

### 核心假设
通过计算注意力权重对输出的梯度，我们可以量化每个注意力连接的重要性，
并使用这个信息来训练一个轻量级的重要性预测器，在推理时无需计算梯度即可预测重要性。

### 预期结果
- 重要性预测器的准确率（与真实梯度的相关性）≥0.85
- 使用预测的重要性进行剪枝后，性能保持率≥95%
- 重要性预测器的额外开销 < 5%的总推理时间

### 风险
梯度计算可能在训练时引入额外开销，且预测器可能难以泛化到训练时未见过的输入分布。
""")

    # 创建synthesis.md
    synthesis_md = workspace_dir / "literature" / "synthesis.md"
    synthesis_md.write_text("""# 文献综述

## Q1: 如何降低Transformer的推理成本？

现有方法主要分为三类：

### 1. 固定稀疏模式
- **Longformer** (Beltagy et al., 2020): 使用滑动窗口+全局注意力的固定模式
- **BigBird** (Zaheer et al., 2020): 结合随机、窗口和全局注意力
- **局限性**: 无法根据输入内容动态调整，可能在某些任务上次优

### 2. 动态稀疏注意力
- **Reformer** (Kitaev et al., 2020): 使用LSH哈希来近似注意力
- **Linformer** (Wang et al., 2020): 使用低秩投影降低复杂度
- **局限性**: 近似误差可能影响性能，且某些方法需要预先知道序列长度

### 3. 学习式剪枝
- **DynaBERT** (Hou et al., 2020): 动态调整模型宽度和深度
- **PoWER-BERT** (Goyal et al., 2020): 基于词重要性的早停机制
- **局限性**: 主要关注层级或词级剪枝，较少关注注意力级别的细粒度剪枝

## Q2: 如何评估注意力连接的重要性？

现有方法：
- **基于权重大小**: 简单但不准确，因为权重大小不等于重要性
- **基于信息论**: 使用互信息等指标，但计算成本高
- **基于梯度**: 一些工作（如Network Pruning）使用梯度来评估重要性，但在注意力剪枝中应用较少

## 研究缺口

1. **缺乏端到端的学习式稀疏注意力框架**: 现有方法要么使用固定模式，要么需要复杂的两阶段训练
2. **缺乏理论指导的重要性评估**: 大多数方法基于启发式规则，缺乏理论分析
3. **缺乏在长文本推理任务上的系统评估**: 现有工作主要在分类任务上评估，长文本推理的评估较少
""")

    # 创建comparison_table.csv
    comparison_table_csv = workspace_dir / "literature" / "comparison_table.csv"
    comparison_table_csv.write_text("""Method,Year,Venue,Sparsity,Dynamic,Learnable,Performance
Longformer,2020,arXiv,Fixed,No,No,Good
BigBird,2020,NeurIPS,Fixed,No,No,Good
Reformer,2020,ICLR,Dynamic,Yes,No,Medium
Linformer,2020,arXiv,Fixed,No,No,Medium
DynaBERT,2020,arXiv,Dynamic,Yes,Yes,Good
PoWER-BERT,2020,EMNLP,Dynamic,Yes,Yes,Good
""")

    print(f"✅ 测试workspace准备完成: {workspace_dir}")


async def run_novelty_auditor_test():
    """运行Novelty Auditor agent测试。"""
    print("\n" + "="*80)
    print("T4.5 Novelty Auditor Agent 真实LLM测试")
    print("="*80 + "\n")

    # 准备测试workspace
    workspace_dir = Path("/home/liangmengkun/tmp/test_novelty_auditor")
    prepare_test_workspace(workspace_dir)

    # 创建agent实例
    agent = NoveltyAuditorAgent()
    print(f"✅ Agent创建成功: {agent.spec.name}")
    print(f"   - Model tier: {agent.spec.model_tier}")
    print(f"   - LLM profile: {agent.spec.llm_profile}")
    print(f"   - Temperature: {agent.spec.temperature}")
    print(f"   - Max steps: {agent.spec.max_steps}")
    print(f"   - Max tokens: {agent.spec.max_tokens_total}")

    # 创建执行上下文
    ctx = ExecutionContext(
        workspace_dir=workspace_dir,
        project_id="test_novelty_auditor",
        task_id="T4.5",
        run_id="test-run-1",
        mode=None,
    )

    # 生成system prompt
    print("\n" + "-"*80)
    print("生成System Prompt...")
    print("-"*80)
    system_prompt = agent.system_prompt(ctx)
    print(f"✅ System prompt生成成功 ({len(system_prompt)} 字符)")
    print(f"\n前500字符预览:\n{system_prompt[:500]}...\n")

    # 生成初始用户消息
    initial_msg = agent.initial_user_message(ctx)
    print(f"✅ 初始用户消息: {initial_msg}\n")

    print("-"*80)
    print("⚠️  注意: 真实LLM调用需要配置API密钥和运行runtime")
    print("    本测试脚本验证了agent的配置和prompt生成")
    print("    要运行完整的LLM测试，请使用: researchos run T4.5")
    print("-"*80)

    # 验证输出（模拟场景）
    print("\n" + "-"*80)
    print("验证输出校验逻辑...")
    print("-"*80)

    # 创建模拟输出
    (workspace_dir / "ideation" / "novelty_audit.md").write_text("""# 新颖性审计报告

生成时间: 2026-04-19
审计的假设数量: 2

---

## H1: 学习式稀疏注意力可以在保持性能的同时显著降低推理成本

### 假设摘要
通过可学习的注意力剪枝模块，动态决定必要的注意力连接，降低FLOPs至40%以下。

### 搜索策略
- 查询1: "learnable sparse attention transformer" - 命中25篇
- 查询2: "dynamic attention pruning inference" - 命中18篇
- 查询3: "efficient transformer long context" - 命中30篇

### 相似工作分析

#### High Overlap（高度重叠）
无高度重叠的工作。

#### Medium Overlap（中度重叠）
- **Adaptive Attention Span** (Sukhbaatar et al., 2019, ICML)
  - 相似点: 都是学习式的注意力调整
  - 差异点: 他们调整注意力范围，我们调整稀疏模式

#### Low Overlap（低度重叠）
- **Longformer** (Beltagy et al., 2020) - 固定稀疏模式
- **BigBird** (Zaheer et al., 2020) - 固定稀疏模式
- **Reformer** (Kitaev et al., 2020) - LSH近似

### 与已有方法对比
基于 comparison_table.csv 的分析：
- Longformer/BigBird: 固定模式，我们是学习式
- DynaBERT/PoWER-BERT: 层级/词级剪枝，我们是注意力级剪枝

### 新颖性判定

**新颖性等级**: Level 2 - 中度新颖

**判定理由**:
虽然学习式注意力调整的想法已有先例（如Adaptive Attention Span），
但在稀疏注意力模式学习上的应用是新颖的。我们的方法在细粒度（注意力连接级别）
上进行学习，这与现有工作有明确区别。

**差异化优势**:
- 优势1: 细粒度的注意力连接级剪枝
- 优势2: 端到端学习，无需两阶段训练
- 优势3: 在长文本推理任务上的系统评估

**风险提示**:
需要在实验中明确与Adaptive Attention Span等工作的区别。

---

## H2: 基于梯度的注意力重要性估计可以指导高效的稀疏模式学习

### 假设摘要
使用梯度信息估计注意力连接重要性，训练轻量级预测器用于推理时预测。

### 搜索策略
- 查询1: "gradient-based attention importance" - 命中12篇
- 查询2: "attention pruning importance estimation" - 命中15篇
- 查询3: "neural network pruning gradient" - 命中40篇

### 相似工作分析

#### High Overlap（高度重叠）
无高度重叠的工作。

#### Medium Overlap（中度重叠）
无中度重叠的工作。

#### Low Overlap（低度重叠）
- **Network Pruning via Transformable Architecture Search** (Dong et al., 2019) - 使用梯度但不是注意力
- **Movement Pruning** (Sanh et al., 2020) - 基于梯度的权重剪枝

### 与已有方法对比
基于 comparison_table.csv 的分析：
现有方法主要使用启发式规则（权重大小、固定模式），较少使用梯度信息。

### 新颖性判定

**新颖性等级**: Level 3 - 高度新颖

**判定理由**:
基于梯度的注意力重要性估计在文献中应用较少，这是一个开创性的方向。
虽然梯度在网络剪枝中有应用，但在注意力级别的细粒度剪枝中使用梯度指导
是新颖的。结合轻量级预测器的设计也很有创意。

**差异化优势**:
- 优势1: 理论指导的重要性评估（基于梯度）
- 优势2: 轻量级预测器设计，推理时无需计算梯度
- 优势3: 可解释性强，梯度直接反映重要性

**风险提示**:
无重大风险，但需要验证预测器的泛化能力。

---

## 总体评估

### 新颖性分布
- Level 3（高度新颖）: 1个假设
- Level 2（中度新颖）: 1个假设
- Level 1（低度新颖）: 0个假设
- Level 0（无新颖性）: 0个假设

### 建议

✅ **建议继续**: 所有假设都具有足够的新颖性，可以进入实验阶段。
H2的新颖性尤其突出，建议作为核心贡献重点展开。

### 需要补充的Baseline
建议在 exp_plan.yaml 中补充以下baseline：
- **Adaptive Attention Span** (Sukhbaatar et al., 2019, ICML) - 与H1直接相关，需要对比
""")

    # 验证输出
    ok, err = agent.validate_outputs(ctx)
    if ok:
        print("✅ 输出校验通过")
    else:
        print(f"❌ 输出校验失败: {err}")
        return False

    # 检查输出文件
    print("\n" + "-"*80)
    print("检查输出文件...")
    print("-"*80)

    audit_file = workspace_dir / "ideation" / "novelty_audit.md"
    if audit_file.exists():
        content = audit_file.read_text()
        print(f"✅ novelty_audit.md 存在 ({len(content)} 字符)")
        print(f"\n前800字符预览:\n{content[:800]}...\n")
    else:
        print("❌ novelty_audit.md 不存在")
        return False

    print("\n" + "="*80)
    print("测试完成！")
    print("="*80)
    print("\n总结:")
    print("✅ Agent配置正确")
    print("✅ System prompt生成成功")
    print("✅ 输出校验逻辑正确")
    print("✅ 模拟输出通过验证")
    print("\n要运行完整的LLM测试，请使用:")
    print("  cd /home/liangmengkun/ResearchOS")
    print("  researchos run T4.5 --workspace /home/liangmengkun/tmp/test_novelty_auditor")

    return True


if __name__ == "__main__":
    success = asyncio.run(run_novelty_auditor_test())
    sys.exit(0 if success else 1)
