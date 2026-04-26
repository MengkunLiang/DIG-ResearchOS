"""T3/T3.5 Reader Agent 单元测试。

测试覆盖：
1. read模式基本流程
2. synthesize模式基本流程
3. validate_outputs - read模式
4. validate_outputs - synthesize模式
5. 边界情况处理
"""

from __future__ import annotations

from pathlib import Path

import pytest

from researchos.agents.reader import ReaderAgent
from researchos.runtime.agent import ExecutionContext


def _structured_note(paper_id: str, *, abstract_only: bool = False) -> str:
    status = "[ABSTRACT-ONLY]" if abstract_only else "[FULL-TEXT]"
    verification = "metadata_verified (confidence: 0.95)"
    evidence_line = "- N/A for abstract-only note\n" if abstract_only else "- Accuracy: 88.1 [Evidence: Results section]\n"
    return f"""# {paper_id}

- **ID**: {paper_id}
- **Authors**: A, B
- **Venue**: TestConf (2025)
- **DOI/arXiv**: arxiv:2501.00001
- **Citations**: 10
- **Verification**: {verification}
- **Status**: {status}

## 1. Problem & Motivation
problem

## 2. Method Overview
method

## 3. Key Results
{evidence_line}

## 4. Claims vs Evidence
| Claim | Evidence | Strength |
|-------|----------|----------|
| test | test | Strong |

## 5. Limitations
- limit

## 6. Relevance to Our Research
- relevant

## 7. Technical Details Worth Noting
- detail

## 8. Strengths
- strong

## 9. Weaknesses / Gaps
- weak

## 10. Key Quotes
> "quote"

## 11. My Questions
- question
"""


@pytest.fixture
def temp_workspace(tmp_path):
    """创建临时workspace。"""
    workspace = tmp_path / "test_workspace"
    workspace.mkdir()

    # 创建必需的目录结构
    (workspace / "literature").mkdir()
    (workspace / "literature" / "paper_notes").mkdir()

    return workspace


@pytest.fixture
def reader_agent():
    """创建Reader Agent实例。"""
    return ReaderAgent()


def test_reader_agent_spec(reader_agent):
    """测试Reader Agent的AgentSpec配置。"""
    spec = reader_agent.spec
    assert spec.name == "reader"
    assert spec.model_tier == "medium"
    assert "read_file" in spec.tool_names
    assert "write_file" in spec.tool_names
    assert "fetch_paper_pdf" in spec.tool_names
    assert "extract_pdf_text" in spec.tool_names
    assert spec.temperature == 0.5
    assert "literature/" in spec.allowed_read_prefixes
    assert "literature/" in spec.allowed_write_prefixes


def test_reader_system_prompt_read_mode(reader_agent, temp_workspace):
    """测试read模式的system prompt生成。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("direction: Test research direction\n")

    # 创建papers_dedup.jsonl
    dedup_path = temp_workspace / "literature" / "papers_dedup.jsonl"
    dedup_path.write_text('{"id": "test1", "title": "Test Paper"}\n')

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
    )

    prompt = reader_agent.system_prompt(ctx)
    assert "Reader Agent" in prompt
    assert "T3" in prompt or "深度阅读" in prompt
    assert "paper_notes" in prompt


def test_reader_system_prompt_read_mode_includes_seed_priority(reader_agent, temp_workspace):
    """测试read模式会把 seed papers 标成最高优先级。"""
    (temp_workspace / "project.yaml").write_text("direction: Test research direction\n")
    (temp_workspace / "literature" / "papers_dedup.jsonl").write_text(
        '{"id": "paper1", "title": "Seed Paper A"}\n{"id": "paper2", "title": "Other Paper"}\n'
    )
    (temp_workspace / "user_seeds").mkdir(exist_ok=True)
    (temp_workspace / "user_seeds" / "seed_papers.jsonl").write_text(
        '{"title": "Seed Paper A", "role": "anchor"}\n{"title": "Seed Paper Missing", "role": "anchor"}\n'
    )

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
    )

    prompt = reader_agent.system_prompt(ctx)
    assert "最高优先级必读对象" in prompt
    assert "Seed Paper A" in prompt
    assert "尚未在 `papers_dedup.jsonl` 中匹配到" in prompt


def test_reader_system_prompt_synthesize_mode(reader_agent, temp_workspace):
    """测试synthesize模式的system prompt生成。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("direction: Test research direction\n")

    # 创建paper_notes目录和一些笔记
    notes_dir = temp_workspace / "literature" / "paper_notes"
    (notes_dir / "note1.md").write_text("# Test Note 1")
    (notes_dir / "note2.md").write_text("# Test Note 2")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3.5",
        run_id="test-run-1",
        mode="synthesize",
    )

    prompt = reader_agent.system_prompt(ctx)
    assert "Reader Agent" in prompt
    assert "T3.5" in prompt or "综合" in prompt
    assert "synthesis.md" in prompt


def test_reader_initial_user_message_read_mode(reader_agent, temp_workspace):
    """测试read模式的初始用户消息。"""
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
    )

    msg = reader_agent.initial_user_message(ctx)
    assert "T3" in msg or "深度阅读" in msg
    assert "papers_dedup.jsonl" in msg


def test_reader_initial_user_message_read_mode_resume(reader_agent, temp_workspace):
    """测试read模式在已有进度时提示继续执行。"""
    (temp_workspace / "literature" / "paper_notes" / "done_paper.md").write_text("# done")
    (temp_workspace / "literature" / "deep_read_queue_pending.jsonl").write_text(
        '{"paper_id": "paper2", "normalized_id": "paper2", "queue_rank": 1}\n'
    )

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
        extra={"is_resume": True, "resume_reason": "interrupted"},
    )

    msg = reader_agent.initial_user_message(ctx)
    assert "继续T3" in msg
    assert "只处理尚未完成的论文" in msg
    assert "deep_read_queue_pending.jsonl" in msg
    assert "补齐已有笔记缺失的表格/Bib条目" in msg
    assert "seed papers 必须最高优先级" in msg


def test_reader_initial_user_message_synthesize_mode(reader_agent, temp_workspace):
    """测试synthesize模式的初始用户消息。"""
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3.5",
        run_id="test-run-1",
        mode="synthesize",
    )

    msg = reader_agent.initial_user_message(ctx)
    assert "T3.5" in msg or "综合" in msg
    assert "synthesis.md" in msg


def test_validate_outputs_read_mode_success(reader_agent, temp_workspace):
    """测试read模式输出校验（成功场景）。"""
    # 创建 deep_read_queue 和对应笔记
    queue_path = temp_workspace / "literature" / "deep_read_queue.jsonl"
    queue_path.write_text(
        "\n".join(
            f'{{"paper_id": "paper{i}", "normalized_id": "paper{i}", "title": "Paper {i}", "relevance_score": 0.8, "access_score_estimate": 0.7, "access_score": 0.7, "evidence_level": "PARTIAL_TEXT", "seed_priority": false, "queue_rank": {i+1}, "read_priority": 0.8, "target_bucket": "target"}}'
            for i in range(18)
        )
        + "\n"
    )

    notes_dir = temp_workspace / "literature" / "paper_notes"
    for i in range(18):
        (notes_dir / f"paper{i}.md").write_text(_structured_note(f"paper{i}"))

    # 创建comparison_table.csv
    ct_path = temp_workspace / "literature" / "comparison_table.csv"
    ct_path.write_text("id,title,year\ntest1,Test Paper,2023\n")

    # 创建related_work.bib
    bib_path = temp_workspace / "literature" / "related_work.bib"
    bib_path.write_text("@article{test2023,\n  title={Test},\n  year={2023}\n}\n")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
    )

    ok, err = reader_agent.validate_outputs(ctx)
    assert ok, f"Validation failed: {err}"


def test_validate_outputs_read_mode_missing_notes(reader_agent, temp_workspace):
    """测试read模式输出校验（缺少笔记）。"""
    queue_path = temp_workspace / "literature" / "deep_read_queue.jsonl"
    queue_path.write_text(
        "\n".join(
            f'{{"paper_id": "paper{i}", "normalized_id": "paper{i}", "title": "Paper {i}", "relevance_score": 0.8, "access_score_estimate": 0.7, "access_score": 0.7, "evidence_level": "PARTIAL_TEXT", "seed_priority": false, "queue_rank": {i+1}, "read_priority": 0.8, "target_bucket": "target"}}'
            for i in range(18)
        )
        + "\n"
    )

    # 只创建5篇笔记（少于 deep_read_min）
    notes_dir = temp_workspace / "literature" / "paper_notes"
    for i in range(5):
        (notes_dir / f"paper{i}.md").write_text(_structured_note(f"paper{i}"))

    # 创建其他必需文件
    ct_path = temp_workspace / "literature" / "comparison_table.csv"
    ct_path.write_text("id,title,year\ntest1,Test Paper,2023\n")

    bib_path = temp_workspace / "literature" / "related_work.bib"
    bib_path.write_text("@article{test2023,\n  title={Test},\n  year={2023}\n}\n")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
    )

    ok, err = reader_agent.validate_outputs(ctx)
    assert not ok
    assert "deep_read_queue" in err or "至少需要完成" in err


def test_validate_outputs_read_mode_requires_seed_queue_coverage(reader_agent, temp_workspace):
    """测试read模式输出校验会要求队列中的 seed paper 优先完成。"""
    queue_path = temp_workspace / "literature" / "deep_read_queue.jsonl"
    queue_path.write_text(
        "\n".join(
            [
                '{"paper_id": "seed_paper", "normalized_id": "seed_paper", "title": "Seed Paper", "relevance_score": 0.95, "access_score_estimate": 0.9, "access_score": 1.0, "evidence_level": "FULL_TEXT", "seed_priority": true, "queue_rank": 1, "read_priority": 100.9, "target_bucket": "seed"}',
            ]
            + [
                f'{{"paper_id": "paper{i}", "normalized_id": "paper{i}", "title": "Paper {i}", "relevance_score": 0.8, "access_score_estimate": 0.7, "access_score": 0.7, "evidence_level": "PARTIAL_TEXT", "seed_priority": false, "queue_rank": {i+2}, "read_priority": 0.8, "target_bucket": "target"}}'
                for i in range(17)
            ]
        )
        + "\n"
    )

    notes_dir = temp_workspace / "literature" / "paper_notes"
    for i in range(17):
        (notes_dir / f"paper{i}.md").write_text(_structured_note(f"paper{i}"))
    (notes_dir / "overflow_note.md").write_text(_structured_note("overflow_note"))

    ct_path = temp_workspace / "literature" / "comparison_table.csv"
    ct_path.write_text("id,title,year\ntest1,Test Paper,2023\n")
    bib_path = temp_workspace / "literature" / "related_work.bib"
    bib_path.write_text("@article{test2023,\n  title={Test},\n  year={2023}\n}\n")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
    )

    ok, err = reader_agent.validate_outputs(ctx)
    assert not ok
    assert "seed papers" in err


def test_reader_system_prompt_read_mode_includes_resume_progress(reader_agent, temp_workspace):
    """测试read模式prompt会暴露已有进度，指导断点续跑。"""
    (temp_workspace / "project.yaml").write_text("direction: Test research direction\n")
    (temp_workspace / "literature" / "papers_dedup.jsonl").write_text(
        '{"id": "paper1", "title": "Test Paper 1"}\n{"id": "paper2", "title": "Test Paper 2"}\n'
    )
    (temp_workspace / "literature" / "paper_notes" / "paper1.md").write_text("# Paper 1")
    (temp_workspace / "literature" / "deep_read_queue_pending.jsonl").write_text(
        '{"paper_id": "paper2", "normalized_id": "paper2", "queue_rank": 1, "title": "Test Paper 2"}\n'
    )

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3",
        run_id="test-run-1",
        mode="read",
        extra={"is_resume": True, "resumed_from_run_id": "t3-run-001", "resume_reason": "interrupted"},
    )

    prompt = reader_agent.system_prompt(ctx)
    assert "当前已有进度" in prompt
    assert "已有 1 篇笔记" in prompt
    assert "deep_read_queue_pending.jsonl" in prompt
    assert "先做账目对齐" in prompt
    assert "只补未完成论文" in prompt


def test_validate_outputs_synthesize_mode_success(reader_agent, temp_workspace):
    """测试synthesize模式输出校验（成功场景）。"""
    # 创建synthesis.md，包含5个必需章节和论文引用
    syn_path = temp_workspace / "literature" / "synthesis.md"
    synthesis_content = """# 文献综述

## 方法家族分类

根据对现有文献的深入分析，我们发现当前方法主要可分为以下三类：

### 1. Attention-based方法
这类方法通过注意力机制来捕捉序列中的长距离依赖关系。如[paper_001]和[paper_002]所展示的，注意力机制能够有效地建模全局上下文信息。Transformer架构[paper_003]是这一类别的代表性工作，它通过多头自注意力机制实现了并行计算，同时保持了强大的建模能力。在后续的研究中，BERT[paper_004]和GPT系列[paper_005]进一步推动了预训练语言模型的发展，展示了大规模预训练的有效性。

### 2. Convolution-based方法
基于卷积神经网络的方法通过局部感受野来提取特征。如[paper_006]所示，卷积操作在图像处理领域取得了巨大成功，近年来也被广泛应用于自然语言处理任务[paper_007]。TextCNN[paper_008]是这一类别的代表性工作，它通过多尺度卷积核来捕捉不同范围的n-gram特征。

### 3. Hybrid方法
混合方法试图结合注意力机制和卷积操作的优点。如[paper_009]所提出的方法，通过层次化设计实现了效率和性能的平衡。Conformer[paper_010]和EfficientFormer[paper_011]等模型代表了这一方向的重要进展。

## 共同假设

通过分析这些方法，我们发现它们共享以下基本假设[paper_012]：

1. **局部特征重要性**：无论是注意力权重还是卷积核，都强调局部特征的重要性。这表明在自然语言处理中，上下文信息的重要性是不均匀的，某些词汇和短语对语义理解贡献更大。

2. **层级表示学习**：通过多层网络的堆叠，可以学习到越来越抽象的特征表示。底层网络捕捉词汇和语法特征，高层网络捕捉语义和语用特征。这一假设在大多数深度学习模型中都得到验证[paper_013]。

3. **表示的平滑性**：连续的表示空间有助于模型的泛化能力。通过将离散的语言符号映射到连续的向量空间，模型能够更好地捕捉词语之间的语义相似性[paper_014]。

## 性能-效率前沿

根据[paper_015]和[paper_016]的详细评估，我们绘制了当前方法的性能-效率权衡曲线。在准确率方面，Transformer-based方法表现最佳，但计算复杂度较高。在效率方面，轻量级模型如[paper_017]提出的方法具有明显优势。

具体来说，在标准的GLUE基准测试[paper_018]上，BERT和RoBERTa等大型预训练模型达到了人类水平的表现，但在资源受限的场景下，这些模型的部署面临挑战。DistilBERT[paper_019]通过知识蒸馏将模型大小减少40%，同时保持95%的性能。

## 技术趋势

当前研究呈现以下主要趋势：

1. **模型压缩**：通过知识蒸馏、量化等技术减小模型尺寸[paper_020]。这种方法可以在保持一定性能的同时显著降低推理成本。

2. **高效注意力**：设计更高效的注意力机制，如稀疏注意力[paper_021]、线性注意力[paper_022]等。这些方法试图在保持注意力机制优点的同时降低其计算复杂度。

3. **自适应计算**：根据输入复杂度动态调整计算量[paper_023]。对于简单的输入使用较少的计算资源，对于复杂的输入使用更多的计算资源。

4. **多模态融合**：将文本与图像、语音等其他模态的信息进行融合[paper_024]。CLIP[paper_025]和GPT-4V[paper_026]代表了这一方向的重要进展。

## 可操作研究问题

基于以上分析，我们提出以下值得深入研究的问题：

1. 如何设计同时兼顾效率和准确率的统一架构？这需要在模型设计和训练策略上进行创新。

2. 如何利用预训练语言模型的强大能力？few-shot和zero-shot学习[paper_027]为我们提供了新的研究方向。

3. 如何在资源受限的场景下部署高性能模型？模型压缩[paper_028]和知识蒸馏[paper_029]是关键的技术手段。

4. 如何处理分布外数据？当前模型在分布内数据上表现优异，但在分布外数据上性能下降明显[paper_030]。这是一个重要的研究方向，因为它直接关系到模型的鲁棒性和实际应用价值。

在深入分析现有文献后，我们注意到一个有趣的现象：尽管不同的方法在架构设计上存在显著差异，但它们在许多任务上都取得了相当接近的性能。这暗示着可能存在一个性能上界，而当前的模型已经接近这个上界。

未来的研究应该关注如何打破这个上界。可能的途径包括：设计更有效的预训练任务[paper_031]、探索新的模型架构[paper_032]、利用外部知识[paper_033]等。

此外，我们还注意到模型评估协议的不一致性。不同的论文使用不同的评估指标、数据划分和训练设置，这使得直接比较不同方法变得困难。建立统一的评估标准和基准测试[paper_034]对于推动领域发展至关重要。

最后，模型的可解释性也是一个重要议题。虽然深度模型取得了巨大成功，但它们的决策过程往往不透明。理解模型如何做出预测[paper_035]不仅有助于改进模型设计，也能增强用户对模型的信任。

总之，这一领域充满了机遇和挑战。我们期待看到更多创新性的工作来解决上述问题，推动自然语言处理技术的进一步发展。

"""  # 超过 2500 字符，确保通过验证

    syn_path.write_text(synthesis_content)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3.5",
        run_id="test-run-1",
        mode="synthesize",
    )

    ok, err = reader_agent.validate_outputs(ctx)
    assert ok, f"Validation failed: {err}"


def test_validate_outputs_synthesize_mode_missing_sections(reader_agent, temp_workspace):
    """测试synthesize模式输出校验（缺少章节）。"""
    # 创建synthesis.md，但缺少某些章节
    syn_path = temp_workspace / "literature" / "synthesis.md"
    synthesis_content = """# 文献综述

## 方法家族分类
这是方法家族分类章节...

## 共同假设
这是共同假设章节...

""" + "x" * 2000

    syn_path.write_text(synthesis_content)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T3.5",
        run_id="test-run-1",
        mode="synthesize",
    )

    ok, err = reader_agent.validate_outputs(ctx)
    assert not ok
    assert "缺少" in err or "章节" in err
