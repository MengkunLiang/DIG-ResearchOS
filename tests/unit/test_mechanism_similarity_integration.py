"""机制相似度搜索集成测试。

测试 NoveltyAgent 的机制相似度搜索功能与现有功能的集成。
"""

from __future__ import annotations

import pytest
import yaml

from researchos.agents.novelty import NoveltyAgent
from researchos.runtime.agent import ExecutionContext


@pytest.fixture
def temp_workspace(tmp_path):
    """创建临时workspace。"""
    workspace = tmp_path / "test_workspace"
    workspace.mkdir()

    # 创建必需的目录结构
    (workspace / "literature").mkdir()
    (workspace / "ideation").mkdir()
    (workspace / "pilot").mkdir()
    (workspace / "novelty").mkdir()

    return workspace


@pytest.fixture
def novelty_agent():
    """创建 Novelty Agent 实例。"""
    return NoveltyAgent()


def test_mechanism_keywords_extraction_in_context(novelty_agent, temp_workspace):
    """测试在实际上下文中提取机制关键词。"""
    # 创建包含技术术语的假设
    hypothesis = {
        "title": "Improving NLP with Transformer and BERT",
        "content": (
            "We propose a novel approach that combines Transformer architecture "
            "with BERT-style pretraining. Our method uses self-attention mechanisms "
            "and is trained with AdamW optimizer. We apply LoRA for efficient fine-tuning."
        ),
    }

    keywords = novelty_agent._extract_mechanism_keywords(hypothesis)

    # 验证提取的关键词
    assert len(keywords) > 0
    assert "transformer" in keywords
    assert "bert" in keywords
    assert "attention" in keywords or "self-attention" in keywords
    assert "adamw" in keywords
    assert "lora" in keywords


def test_mechanism_search_with_empty_keywords(novelty_agent):
    """测试空关键词的机制搜索。"""
    papers = novelty_agent._search_similar_mechanisms([], None)
    assert papers == []


def test_mechanism_search_with_keywords(novelty_agent):
    """测试有关键词的机制搜索能正常运行。"""
    keywords = ["transformer", "bert", "attention"]

    # 应该能正常运行不抛异常
    papers = novelty_agent._search_similar_mechanisms(keywords, None)

    # 当前实现返回空列表
    assert papers == []


def test_validate_outputs_with_mechanism_similarity(novelty_agent, temp_workspace):
    """测试包含机制相似度搜索的输出验证。"""
    # 创建 project.yaml
    project_path = temp_workspace / "project.yaml"
    project_data = {
        "research_direction": "NLP with Transformers",
        "constraints": {"max_budget_usd": 1000},
    }
    project_path.write_text(yaml.dump(project_data))

    # 创建 hypotheses.md（包含技术术语）
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_content = """# 研究假设

## H1: Transformer-based Text Generation

我们提出一个基于 Transformer 架构的文本生成方法。
该方法使用 BERT 预训练和 self-attention 机制。
我们采用 AdamW 优化器和 LoRA 进行高效微调。

### 核心创新
- 新的 attention 机制设计
- 结合 BERT 和 GPT 的优势
- 使用 LoRA 降低训练成本

### 预期贡献
- 性能提升：相比 baseline 提升 15%
- 效率改进：训练时间减少 50%
"""
    hyp_path.write_text(hyp_content)

    # 创建 novelty_report.md（包含机制相似度搜索）
    report_path = temp_workspace / "novelty" / "novelty_report.md"
    report_content = """# T6 新颖性验证报告

生成时间: 2026-04-21
审计的假设数量: 1

## 执行摘要

本报告对研究假设 H1 进行了新颖性验证。我们执行了任务相似度搜索和机制相似度搜索，
发现该假设展现了良好的创新性。虽然使用了常见的 Transformer 和 BERT 技术，
但我们的组合方式和优化策略具有独特性。

---

## H1: Transformer-based Text Generation

### 假设摘要
我们提出一个基于 Transformer 架构的文本生成方法，结合 BERT 预训练和新的 attention 机制。

### Pilot 实验证据分析
Pilot 实验验证了该方法的有效性，在标准数据集上取得了 15% 的性能提升。
训练效率也得到了显著改进，训练时间减少了 50%。

### 搜索策略
- 查询1: "transformer text generation" - 命中 12 篇（任务相似度）
- 查询2: "BERT pretraining generation" - 命中 8 篇（任务相似度）
- 查询3: "transformer attention mechanism" - 命中 15 篇（机制相似度）
- 查询4: "LoRA fine-tuning" - 命中 10 篇（机制相似度）

说明：
- 任务相似度搜索：关注文本生成任务的相关工作
- 机制相似度搜索：关注使用 Transformer、attention、LoRA 等技术的工作
- 机制关键词：transformer, bert, attention, self-attention, adamw, lora

### 相似工作分析

#### High Overlap（高度重叠）
无高度重叠的工作。

#### Medium Overlap（中度重叠）
- **Efficient Transformers for NLP** (Smith et al., 2025, arXiv:2501.12345)
  - 相似点: 都使用 Transformer 和 LoRA 进行高效训练
  - 差异点: 我们的 attention 机制设计不同，且结合了 BERT 预训练
  - Pilot验证: Pilot 实验验证了我们的 attention 机制的优势

#### Low Overlap（低度重叠）
- **BERT for Generation Tasks** (Jones et al., 2025, arXiv:2501.23456) - 使用 BERT 但不涉及 LoRA
- **Attention Mechanisms Survey** (Brown et al., 2025, arXiv:2501.34567) - 综述性工作

### 与已有方法对比
基于 comparison_table.csv 的分析：
- Standard Transformer: 我们的方法在效率上有显著优势（Pilot 验证）
- BERT-based Generation: 我们的 attention 机制更适合生成任务（Pilot 验证）

### 新颖性判定

**新颖性等级**: Level 2 - 中度新颖

**判定理由**:
1. Pilot 实验证据：验证了 15% 性能提升和 50% 效率改进
2. 搜索结果分析：虽然使用常见技术（Transformer, BERT, LoRA），但组合方式新颖
3. 机制相似度分析：发现使用相似机制的工作，但我们的 attention 设计有独特性
4. 差异点显著性：Pilot 实验验证了我们的差异化优势

**差异化优势**:
- 优势1: 新的 attention 机制设计，Pilot 验证了性能提升
- 优势2: BERT 和 GPT 的有效结合，Pilot 验证了泛化能力
- 优势3: LoRA 高效微调，Pilot 验证了训练效率改进

**风险提示**: 无重大风险。建议在 T7 阶段进一步验证泛化能力。

---

## 总体评估

### 新颖性分布
- Level 2（中度新颖）: 1个假设

### Pilot 验证覆盖率
- 充分验证: 1个假设

### Gate T6-DECIDE 决策

| 假设 | 新颖性等级 | Pilot验证 | 决策 |
|------|-----------|----------|------|
| H1 | Level 2 | 充分 | PASS |

**总体决策**: PASS
- 假设达到 Level 2 且 Pilot 充分验证，可以进入 T7 完整实验

### 建议
建议进入 T7 阶段，进一步验证方法在更多数据集上的泛化能力。
"""
    report_path.write_text(report_content)

    # 创建 must_add_baselines.md
    baselines_path = temp_workspace / "novelty" / "must_add_baselines.md"
    baselines_content = """# 必须补充的基线方法

## 新发现的强基线

### 1. Efficient Transformers for NLP

**论文**: Efficient Transformers for NLP
**作者**: Smith et al.
**年份**: 2025
**来源**: arXiv:2501.12345

**为什么需要对比**:
这是一个使用相似技术机制（Transformer + LoRA）的重要工作，
需要作为基线对比以展示我们方法的独特性。

**已有方法对比表中的状态**: 缺失

**建议添加位置**: 在 exp_plan.yaml 的 baselines 部分

---

## 建议

需要添加 1 个新基线到实验计划中，优先级：高。
"""
    baselines_path.write_text(baselines_content)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = novelty_agent.validate_outputs(ctx)
    assert ok, f"Validation failed: {err}"


def test_extract_mechanism_keywords_from_multiple_hypotheses(novelty_agent):
    """测试从多个假设中提取机制关键词。"""
    hypotheses = [
        {
            "title": "H1: CNN for Image Classification",
            "content": "We use Convolutional Neural Networks with ResNet architecture.",
        },
        {
            "title": "H2: Transformer for NLP",
            "content": "We apply Transformer with BERT pretraining and attention mechanisms.",
        },
        {
            "title": "H3: RL for Robotics",
            "content": "We use Reinforcement Learning with PPO and actor-critic architecture.",
        },
    ]

    all_keywords = []
    for hyp in hypotheses:
        keywords = novelty_agent._extract_mechanism_keywords(hyp)
        all_keywords.extend(keywords)

    # 验证提取了不同类型的技术术语
    assert "cnn" in all_keywords or "convolutional neural network" in all_keywords
    assert "resnet" in all_keywords
    assert "transformer" in all_keywords
    assert "bert" in all_keywords
    assert "attention" in all_keywords
    assert "reinforcement learning" in all_keywords or "rl" in all_keywords
    assert "ppo" in all_keywords
    assert "actor-critic" in all_keywords


def test_mechanism_keywords_case_insensitive(novelty_agent):
    """测试机制关键词提取不区分大小写。"""
    hypothesis_upper = {
        "title": "Using TRANSFORMER and BERT",
        "content": "We apply TRANSFORMER architecture with BERT pretraining.",
    }

    hypothesis_lower = {
        "title": "Using transformer and bert",
        "content": "We apply transformer architecture with bert pretraining.",
    }

    hypothesis_mixed = {
        "title": "Using Transformer and Bert",
        "content": "We apply Transformer architecture with Bert pretraining.",
    }

    keywords_upper = novelty_agent._extract_mechanism_keywords(hypothesis_upper)
    keywords_lower = novelty_agent._extract_mechanism_keywords(hypothesis_lower)
    keywords_mixed = novelty_agent._extract_mechanism_keywords(hypothesis_mixed)

    # 所有情况都应该提取到相同的关键词（小写）
    assert "transformer" in keywords_upper
    assert "bert" in keywords_upper
    assert "transformer" in keywords_lower
    assert "bert" in keywords_lower
    assert "transformer" in keywords_mixed
    assert "bert" in keywords_mixed
