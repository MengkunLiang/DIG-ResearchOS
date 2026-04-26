"""T6 Novelty Agent 单元测试。

测试覆盖：
1. AgentSpec配置
2. system_prompt生成
3. initial_user_message生成
4. validate_outputs - 成功场景
5. validate_outputs - 各种失败场景
"""

from __future__ import annotations

import json
from pathlib import Path

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
    """创建Novelty Agent实例。"""
    return NoveltyAgent()


def test_novelty_agent_spec(novelty_agent):
    """测试Novelty Agent的AgentSpec配置。"""
    spec = novelty_agent.spec
    assert spec.name == "novelty"
    assert spec.model_tier == "medium"
    assert "read_file" in spec.tool_names
    assert "write_file" in spec.tool_names
    assert "list_files" in spec.tool_names
    assert "search_papers" in spec.tool_names
    assert "ask_human" in spec.tool_names
    assert "finish_task" in spec.tool_names
    assert spec.temperature == 0.3
    assert "ideation/" in spec.allowed_read_prefixes
    assert "literature/" in spec.allowed_read_prefixes
    assert "pilot/" in spec.allowed_read_prefixes
    assert "novelty/" in spec.allowed_write_prefixes
    assert spec.max_steps == 100
    assert spec.max_tokens_total == 600_000


def test_novelty_system_prompt(novelty_agent, temp_workspace):
    """测试system prompt生成。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_data = {
        "research_direction": "Test research direction",
        "keywords": ["test", "research"],
        "constraints": {
            "max_budget_usd": 500,
            "compute_resources": {"allow_gpu": True},
        },
    }
    project_path.write_text(yaml.dump(project_data))

    # 创建hypotheses.md
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_content = """# 研究假设

## H1: 第一个假设

这是假设1的内容，需要足够长。

## H2: 第二个假设

这是假设2的内容。
"""
    hyp_path.write_text(hyp_content)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test-run-1",
        mode=None,
    )

    prompt = novelty_agent.system_prompt(ctx)
    assert "Novelty Agent" in prompt or "新颖性" in prompt
    assert "Test research direction" in prompt
    assert "H1" in prompt
    assert "H2" in prompt


def test_novelty_initial_user_message(novelty_agent, temp_workspace):
    """测试初始用户消息。"""
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test-run-1",
        mode=None,
    )

    msg = novelty_agent.initial_user_message(ctx)
    assert "T6" in msg or "新颖性验证" in msg
    assert "pilot" in msg.lower() or "Pilot" in msg


def test_novelty_initial_user_message_resume_mode(novelty_agent, temp_workspace):
    """恢复运行时，应明确要求复用已有内容并只补缺口。"""
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test-run-resume",
        mode=None,
        extra={
            "resume_mode": True,
            "resume_state_path": "_runtime/resume/t6_resume_state.json",
            "resume_reason": "retry_after_failure",
            "resume_existing_outputs": ["novelty_report", "collision_cases"],
            "resume_missing_outputs": ["must_add_baselines"],
            "resume_existing_artifacts": ["novelty/novelty_report.md"],
        },
    )

    msg = novelty_agent.initial_user_message(ctx)
    assert "恢复运行" in msg
    assert "must_add_baselines" in msg
    assert "不要" in msg


def test_validate_outputs_success(novelty_agent, temp_workspace):
    """测试输出校验（成功场景）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_data = {
        "research_direction": "Test",
        "constraints": {"max_budget_usd": 1000},
    }
    project_path.write_text(yaml.dump(project_data))

    # 创建hypotheses.md（带H1/H2 anchor）
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_content = """# 研究假设

## H1: 第一个假设

这是假设1的内容，需要足够长来通过验证。我们提出了一个新的方法来解决这个问题，
这个方法与现有工作有明显的区别，并且在理论上具有创新性。

### 核心创新
- 新方法A：独特的技术路线
- 新方法B：与现有方法的本质区别

### 预期贡献
- 理论贡献：提出新的范式
- 实践贡献：性能提升显著

## H2: 第二个假设

这是假设2的内容，我们提出了另一种方法。

### 核心创新
- 方法C：结合了方法A和B
"""
    hyp_path.write_text(hyp_content)

    # 创建novelty_report.md
    report_path = temp_workspace / "novelty" / "novelty_report.md"
    report_content = """# T6 新颖性验证报告

生成时间: 2026-04-20
审计的假设数量: 2

## 执行摘要

本报告对两个研究假设进行了新颖性最终验证。基于T5 Pilot实验结果和近期文献搜索，
我们评估了每个假设的创新性和潜在撞车风险。总体而言，两个假设都展现出较好的新颖性，
可以在T7完整实验阶段进一步验证。

---

## H1: 第一个假设

### 假设摘要
我们提出了一个新的方法来解决现有问题，该方法结合了独特的技术路线和创新的优化策略。

### 搜索策略
- 查询1: "new method A test" - 命中5篇
- 查询2: "method B research" - 命中3篇
- 查询3: "innovation testing" - 命中2篇

### 相似工作分析
经过搜索，我们发现近期相关工作主要集中在方法B的变体上，
但我们提出的方法A具有独特的技术优势。无高度重叠的工作。

### 新颖性判定
**新颖性等级**: Level 2 - 中度新颖

**判定理由**:
方法组合新颖，与已有工作有明确差异点。核心创新在于技术路线的独特设计。

**差异化优势**:
- 优势1: 技术路线独特，采用不同于现有方法的核心架构
- 优势2: 性能验证有效，Pilot实验显示显著提升
- 优势3: 扩展性强，可应用于多种场景

**风险提示**: 无重大风险。建议在T7阶段进一步验证泛化能力。

---

## H2: 第二个假设

### 假设摘要
提出了另一种方法，结合了多种技术优势。

### 新颖性判定
**新颖性等级**: Level 3 - 高度新颖

**判定理由**:
完全未见的方法创新，在多个维度展现出独特优势。

---

## 总体评估

### 新颖性分布
- Level 3（高度新颖）: 1个假设
- Level 2（中度新颖）: 1个假设

### Gate T6-DECIDE 决策
- 总体决策: PASS
- 建议进入T7完整实验阶段
"""
    report_path.write_text(report_content)

    # 创建must_add_baselines.md
    baselines_path = temp_workspace / "novelty" / "must_add_baselines.md"
    baselines_content = """# 必须补充的基线方法

## 新发现的强基线

### 1. MethodX

**论文**: MethodX: A New Approach
**作者**: Author et al.
**年份**: 2025
**来源**: arXiv:XXXXX

**为什么需要对比**:
这是一个重要的相关工作，需要作为基线对比。

---

## 建议

需要添加1个新基线到实验计划中。
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


def test_validate_outputs_missing_required_files(novelty_agent, temp_workspace):
    """测试输出校验（缺少必需文件）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_data = {
        "research_direction": "Test",
        "constraints": {"max_budget_usd": 1000},
    }
    project_path.write_text(yaml.dump(project_data))

    # 创建hypotheses.md
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_path.write_text("# 研究假设\n\n## H1: 假设1\n\n内容...")

    # 创建novelty/novelty_report.md
    report_path = temp_workspace / "novelty" / "novelty_report.md"
    report_path.write_text("x" * 600)

    # 故意不创建 novelty/must_add_baselines.md

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = novelty_agent.validate_outputs(ctx)
    assert not ok
    assert "must_add_baselines" in err or "novelty" in err.lower()


def test_validate_outputs_report_too_short(novelty_agent, temp_workspace):
    """测试输出校验（报告过短）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\nconstraints:\n  max_budget_usd: 1000\n")

    # 创建hypotheses.md
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_path.write_text("# 研究假设\n\n## H1: 假设1\n\n内容...")

    # 创建novelty_report.md（过短）
    report_path = temp_workspace / "novelty" / "novelty_report.md"
    report_path.write_text("x" * 100)  # 不到500字符

    # 创建must_add_baselines.md
    baselines_path = temp_workspace / "novelty" / "must_add_baselines.md"
    baselines_path.write_text("# 基线方法\n\n方法1")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = novelty_agent.validate_outputs(ctx)
    assert not ok
    assert "过短" in err or "short" in err.lower()


def test_validate_outputs_missing_level_markers(novelty_agent, temp_workspace):
    """测试输出校验（缺少新颖性等级标记）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\nconstraints:\n  max_budget_usd: 1000\n")

    # 创建hypotheses.md
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_path.write_text("# 研究假设\n\n## H1: 假设1\n\n内容...")

    # 创建novelty_report.md（包含H1但没有新颖性等级标记）
    # 注意：内容必须超过500字符，但不能包含 Level 0/1/2/3 等标记
    report_path = temp_workspace / "novelty" / "novelty_report.md"
    report_path.write_text("""# T6 新颖性验证报告

生成时间: 2026-04-20
审计的假设数量: 1

## 执行摘要

本报告对研究假设H1进行了新颖性验证。经过全面的文献搜索和分析，我们发现该假设展现了良好的创新性，可以在T7阶段进一步验证。

---

## H1: 假设1

### 假设摘要
我们提出了一个新的方法来解决现有问题，该方法结合了独特的技术路线和创新的优化策略，
在理论上具有显著的创新价值。

### Pilot 实验证据分析
经过T5 Pilot实验验证，该方法在标准数据集上展现出了较好的性能提升。
具体而言，在ImageNet数据集上，相较于baseline方法，我们的方法取得了15%的性能提升。
这一结果表明该方法具有实际的应用价值。

### 搜索策略
- 查询1: "new method test" - 命中5篇，主要涉及方法A的变体研究
- 查询2: "innovation testing" - 命中3篇，涉及类似应用场景的工作
- 查询3: "advanced optimization" - 命中2篇，相关优化方法研究

### 相似工作分析
经过全面搜索，我们发现近期相关工作主要集中在方法A的变体上，但我们的方法具有独特的技术优势。
无高度重叠的工作。

#### Low Overlap（低度重叠）
- **RelatedPaper1** (Author et al., 2025, arXiv:XXXXX) - 虽然都涉及方法A，但应用场景不同
- **RelatedPaper2** (Author et al., 2025, arXiv:XXXXX) - 采用了类似的优化策略，但核心方法不同

### 新颖性判定

这份报告没有明确标记新颖性等级（如新颖性零级、一级、二级或三级）。
因此无法确定该假设的新颖性等级。

---

## 总体评估

### 新颖性分布
- 待确定: 1个假设（因缺少新颖性等级标记）

### 建议
请补充完整的新颖性等级标记，以便进行后续评估。
""")

    # 创建must_add_baselines.md（足够长）
    baselines_path = temp_workspace / "novelty" / "must_add_baselines.md"
    baselines_path.write_text("""# 必须补充的基线方法

## 新发现的强基线

### 1. MethodX
**论文**: MethodX: A New Approach
**作者**: Author et al.
**年份**: 2025
**来源**: arXiv:XXXXX
**为什么需要对比**: 这是一个重要的相关工作。

---

## 建议

需要添加1个新基线到实验计划中。
""")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = novelty_agent.validate_outputs(ctx)
    assert not ok
    assert "等级" in err or "Level" in err


def test_validate_outputs_missing_hypothesis_coverage(novelty_agent, temp_workspace):
    """测试输出校验（报告未覆盖所有假设）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\nconstraints:\n  max_budget_usd: 1000\n")

    # 创建hypotheses.md（有H1和H2）
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_content = """# 研究假设

## H1: 第一个假设
内容...

## H2: 第二个假设
内容...
"""
    hyp_path.write_text(hyp_content)

    # 创建novelty_report.md（只有H1，包含Level标记，但超过500字符）
    # 注意：内容必须超过500字符，并且完全不含H2字符串
    report_path = temp_workspace / "novelty" / "novelty_report.md"
    report_content = """# T6 新颖性验证报告

生成时间: 2026-04-20
审计的假设数量: 2

## 执行摘要

本报告对研究假设进行了新颖性验证。经过全面的文献搜索和分析，
该假设展现了良好的创新性，可以进入T7阶段进一步验证。

---

## H1: 第一个假设

### 假设摘要
我们提出了一个新的方法来解决现有问题，该方法结合了独特的技术路线和创新的优化策略，
在理论上具有显著的创新价值。

### Pilot 实验证据分析
经过T5 Pilot实验验证，该方法在标准数据集上展现出了较好的性能提升。
具体而言，在ImageNet数据集上，相较于baseline方法，我们的方法取得了15%的性能提升。
这一结果表明该方法具有实际的应用价值。

### 搜索策略
- 查询1: "new method test" - 命中5篇
- 查询2: "advanced optimization" - 命中3篇
- 查询3: "innovation testing" - 命中2篇

### 相似工作分析
经过全面搜索，无高度重叠的工作。
发现一些相关的低度重叠工作，但核心方法有本质区别。

#### Low Overlap（低度重叠）
- **RelatedPaper1** (Author et al., 2025, arXiv:XXXXX) - 虽然都涉及方法A，但应用场景不同
- **RelatedPaper2** (Author et al., 2025, arXiv:XXXXX) - 采用了类似的优化策略，但核心方法不同
- **RelatedPaper3** (Author et al., 2025, arXiv:XXXXX) - 使用了不同的技术路线

### 新颖性判定
**新颖性等级**: Level 2 - 中度新颖

**判定理由**:
方法组合新颖，与已有工作有明确差异点。核心创新在于技术路线的独特设计。
Pilot实验验证了性能提升的有效性，在标准数据集上取得了显著改进。

**差异化优势**:
- 优势1: 技术路线独特，采用不同于现有方法的核心架构
- 优势2: 性能验证有效，Pilot实验显示显著提升
- 优势3: 扩展性强，可应用于多种场景

**风险提示**: 无重大风险。建议在T7阶段进一步验证泛化能力。

---

## 总体评估

### 新颖性分布
- Level 2（中度新颖）: 1个假设

### 建议
请补充对其他假设的审计。
"""
    report_path.write_text(report_content)

    # 创建must_add_baselines.md（足够长）
    baselines_path = temp_workspace / "novelty" / "must_add_baselines.md"
    baselines_path.write_text("""# 必须补充的基线方法

## 新发现的强基线

### 1. MethodX
**论文**: MethodX: A New Approach
**作者**: Author et al.
**年份**: 2025
**来源**: arXiv:XXXXX
**为什么需要对比**: 这是一个重要的相关工作。

---

## 建议

需要添加1个新基线到实验计划中。
""")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = novelty_agent.validate_outputs(ctx)
    assert not ok
    assert "H2" in err


def test_validate_outputs_baselines_too_short(novelty_agent, temp_workspace):
    """测试输出校验（基线文件过短）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\nconstraints:\n  max_budget_usd: 1000\n")

    # 创建hypotheses.md
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_path.write_text("# 研究假设\n\n## H1: 假设1\n\n内容...")

    # 创建novelty_report.md
    report_path = temp_workspace / "novelty" / "novelty_report.md"
    report_content = """# T6 新颖性验证报告

## H1: 第一个假设

**新颖性等级**: Level 2 - 中度新颖

Level 2，方法组合新颖。
"""
    report_path.write_text(report_content)

    # 创建must_add_baselines.md（过短）
    baselines_path = temp_workspace / "novelty" / "must_add_baselines.md"
    baselines_path.write_text("x" * 50)  # 不到100字符

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = novelty_agent.validate_outputs(ctx)
    assert not ok
    assert "must_add_baselines" in err or "过短" in err


def test_validate_outputs_with_collision_cases(novelty_agent, temp_workspace):
    """测试输出校验（存在撞车案例时）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\nconstraints:\n  max_budget_usd: 1000\n")

    # 创建hypotheses.md
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_path.write_text("# 研究假设\n\n## H1: 假设1\n\n内容...")

    # 创建novelty_report.md（包含Level 0）
    report_path = temp_workspace / "novelty" / "novelty_report.md"
    report_content = """# T6 新颖性验证报告

生成时间: 2026-04-20
审计的假设数量: 1

## 执行摘要

本报告对研究假设H1进行了新颖性最终验证。经过搜索和分析，发现存在高度重叠的已有工作。

---

## H1: 假设1

### 假设摘要
我们提出了一个新的方法来解决现有问题。

### 搜索策略
- 查询1: "new method test" - 命中10篇
- 查询2: "similar approach" - 命中8篇

### 相似工作分析
经过搜索，发现近期已有论文SimilarPaper (Author, 2025)实现了几乎相同的想法。
该论文采用的核心方法与我们的假设高度重叠。

#### High Overlap（高度重叠）
- **SimilarPaper** (Author et al., 2025, arXiv:XXXXX)
  - 相似点: 核心方法几乎一致
  - 差异点: 参数设置略有不同
  - 风险评估: ⚠️ 高风险撞车

### 与已有方法对比
- MethodA: 与我们的假设高度重叠
- MethodB: 部分重叠，但核心创新不同

### 新颖性判定
**新颖性等级**: Level 0 - 无新颖性

**判定理由**:
发现高度重叠的已有工作（SimilarPaper），核心方法几乎一致。
无明显差异化空间，需要重新设计假设或选择其他方向。

**差异化优势**:
差异化优势不明显，建议重新考虑研究方向。

**风险提示**: ⚠️ **撞车风险**: 发现1篇高度相似的工作，需要重新设计假设。

---

## 总体评估

### 新颖性分布
- Level 0（无新颖性）: 1个假设

### Gate T6-DECIDE 决策
- 总体决策: FAIL
- 建议: 重新设计假设或选择其他方向
"""
    report_path.write_text(report_content)

    # 创建must_add_baselines.md（足够长）
    baselines_path = temp_workspace / "novelty" / "must_add_baselines.md"
    baselines_path.write_text("""# 必须补充的基线方法

## 新发现的强基线

### 1. MethodX
**论文**: MethodX: A New Approach
**作者**: Author et al.
**年份**: 2025
**来源**: arXiv:XXXXX
**为什么需要对比**: 这是一个重要的相关工作。

---

## 建议

需要添加1个新基线到实验计划中。
""")

    # 创建collision_cases.md（标记高风险）
    collision_path = temp_workspace / "novelty" / "collision_cases.md"
    collision_content = """# 潜在撞车案例

## 案例1: Similar Paper

**相似度**: High

**风险评估**: ⚠️ **高风险**: 核心方法高度相似。
"""
    collision_path.write_text(collision_content)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = novelty_agent.validate_outputs(ctx)
    # 验证通过但有warning（collision_cases.md存在且有高风险）
    assert ok, f"Validation should pass: {err}"


def test_system_prompt_includes_pilot_results(novelty_agent, temp_workspace):
    """测试system prompt包含Pilot实验结果。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_data = {
        "research_direction": "Test",
        "constraints": {"max_budget_usd": 1000},
    }
    project_path.write_text(yaml.dump(project_data))

    # 创建hypotheses.md
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_path.write_text("# 研究假设\n\n## H1: 假设1\n\n内容...")

    # 创建pilot_results.json
    pilot_path = temp_workspace / "pilot" / "pilot_results.json"
    pilot_data = {
        "experiment_id": "pilot1",
        "results": {
            "accuracy": 0.85,
            "improvement_over_baseline": "15%"
        }
    }
    pilot_path.write_text(json.dumps(pilot_data, indent=2))

    # 创建motivation_validation.md
    motivation_path = temp_workspace / "pilot" / "motivation_validation.md"
    motivation_path.write_text("# 动机验证\n\n验证结论：假设得到支持。")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test-run-1",
        mode=None,
    )

    prompt = novelty_agent.system_prompt(ctx)
    # Pilot结果应该出现在prompt中
    assert "pilot" in prompt.lower() or "Pilot" in prompt
    assert "0.85" in prompt or "accuracy" in prompt.lower()


def test_validate_outputs_multiple_hypotheses_all_covered(novelty_agent, temp_workspace):
    """测试输出校验（多个假设全部覆盖）。"""
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\nconstraints:\n  max_budget_usd: 1000\n")

    # 创建hypotheses.md（有H1, H2, H3）
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_content = """# 研究假设

## H1: 第一个假设
内容...

## H2: 第二个假设
内容...

## H3: 第三个假设
内容...
"""
    hyp_path.write_text(hyp_content)

    # 创建novelty_report.md（覆盖所有假设，包含Level标记，内容超过500字符）
    report_path = temp_workspace / "novelty" / "novelty_report.md"
    report_content = """# T6 新颖性验证报告

生成时间: 2026-04-20
审计的假设数量: 3

## 执行摘要

本报告对三个研究假设进行了新颖性验证。经过全面搜索和分析，所有假设均展现了良好的创新性。
我们通过搜索近期相关工作，评估了每个假设的新颖性等级，并根据T5 Pilot实验结果进行了最终判定。
总体而言，三个假设中有两个达到了较高新颖性等级，可以进入T7完整实验阶段进一步验证。

---

## H1: 第一个假设

### 假设摘要
我们提出了一个新的方法来解决现有问题，该方法结合了独特的技术路线和创新的优化策略。

### Pilot 实验证据分析
经过T5 Pilot实验验证，该方法展现了良好的性能提升，在标准数据集上取得了显著改进。

### 新颖性判定
**新颖性等级**: Level 2 - 中度新颖

**判定理由**:
方法组合新颖，与已有工作有明确差异点。核心创新在于技术路线的独特设计。

---

## H2: 第二个假设

### 假设摘要
提出了另一种方法，结合了多种技术优势。

### 新颖性判定
**新颖性等级**: Level 3 - 高度新颖

**判定理由**:
完全未见的方法创新，在多个维度展现出独特优势。

---

## H3: 第三个假设

### 假设摘要
在现有方法基础上进行了增量改进。

### 新颖性判定
**新颖性等级**: Level 1 - 低度新颖

**判定理由**:
增量改进，与已有工作相似度高。

---

## 总体评估

### 新颖性分布
- Level 3（高度新颖）: 1个假设
- Level 2（中度新颖）: 1个假设
- Level 1（低度新颖）: 1个假设

### Gate T6-DECIDE 决策
- 总体决策: PASS
"""
    report_path.write_text(report_content)

    baselines_path = temp_workspace / "novelty" / "must_add_baselines.md"
    baselines_path.write_text("""# 必须补充的基线方法

当前基线覆盖充分，未发现必须补充的新基线。

## 新发现的强基线

经过搜索，未发现新的必须对比的强基线方法。

## 缺失的标准基线

无需补充缺失的标准基线。

## 建议

无需添加新基线，可以直接进入T7阶段。
""")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = novelty_agent.validate_outputs(ctx)
    assert ok, f"Validation failed: {err}"


def test_validate_outputs_collision_cases_without_high_risk(novelty_agent, temp_workspace):
    """测试输出校验（存在撞车案例但无高风险）。"""
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\nconstraints:\n  max_budget_usd: 1000\n")

    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_path.write_text("# 研究假设\n\n## H1: 假设1\n\n内容...")

    report_path = temp_workspace / "novelty" / "novelty_report.md"
    report_content = """# T6 新颖性验证报告

生成时间: 2026-04-20
审计的假设数量: 1

## 执行摘要

本报告对研究假设H1进行了新颖性验证。经过全面的文献搜索和分析，
我们发现该假设展现了良好的创新性，可以进入T7阶段进一步验证。

---

## H1: 假设1

### 假设摘要
我们提出了一个新的方法来解决现有问题，该方法结合了独特的技术路线和创新的优化策略。

### Pilot 实验证据分析
经过T5 Pilot实验验证，该方法在标准数据集上展现出了较好的性能提升。
具体而言，相较于baseline方法，我们的方法取得了15%的性能提升。

### 搜索策略
- 查询1: "new method test" - 命中5篇
- 查询2: "advanced optimization" - 命中3篇

### 相似工作分析
经过全面搜索，无高度重叠的工作。发现一些相关的低度重叠工作，但核心方法有本质区别。

### 新颖性判定
**新颖性等级**: Level 2 - 中度新颖

**判定理由**:
方法组合新颖，与已有工作有明确差异点。核心创新在于技术路线的独特设计。

**差异化优势**:
- 优势1: 技术路线独特
- 优势2: 性能验证有效

**风险提示**: 无重大风险。

---

## 总体评估

### 新颖性分布
- Level 2（中度新颖）: 1个假设

### Gate T6-DECIDE 决策
- 总体决策: PASS
"""
    report_path.write_text(report_content)

    baselines_path = temp_workspace / "novelty" / "must_add_baselines.md"
    baselines_content = """# 必须补充的基线方法

未发现必须补充的新基线。

经过全面的文献搜索和分析，当前基线方法覆盖充分。
已有基线包括：标准方法A、主流方法B和相关方法C。
这些基线能够充分展示我们假设的创新性。

## 新发现的强基线

经过搜索，未发现新的必须对比的强基线方法。

## 缺失的标准基线

无需补充缺失的标准基线。

## 建议

无需添加新基线，可以直接进入T7阶段进行完整实验验证。
"""
    baselines_path.write_text(baselines_content)

    # 创建collision_cases.md（无高风险）
    collision_path = temp_workspace / "novelty" / "collision_cases.md"
    collision_content = """# 潜在撞车案例

经过搜索，发现一些相关工作但均为中度或低度重叠。
无高风险撞车案例。
"""
    collision_path.write_text(collision_content)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = novelty_agent.validate_outputs(ctx)
    assert ok, f"Validation should pass: {err}"


def test_validate_outputs_baselines_exactly_100_chars(novelty_agent, temp_workspace):
    """测试输出校验（基线文件正好100字符）。"""
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\nconstraints:\n  max_budget_usd: 1000\n")

    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_path.write_text("# 研究假设\n\n## H1: 假设1\n\n内容...")

    report_path = temp_workspace / "novelty" / "novelty_report.md"
    report_content = """# T6 新颖性验证报告

生成时间: 2026-04-20
审计的假设数量: 1

## 执行摘要

本报告对研究假设H1进行了新颖性验证。经过全面的文献搜索和分析，
我们发现该假设展现了良好的创新性。Pilot实验验证了性能提升的有效性。

---

## H1: 假设1

### 假设摘要
我们提出了一个新的方法来解决现有问题，该方法结合了独特的技术路线和创新的优化策略，
在理论上具有显著的创新价值，并经过了T5 Pilot实验的验证。

### Pilot 实验证据分析
经过T5 Pilot实验验证，该方法在标准数据集上展现出了较好的性能提升。
具体而言，相较于baseline方法，我们的方法取得了15%的性能提升。
这一结果表明该方法具有实际的应用价值和良好的泛化能力。

### 搜索策略
- 查询1: "new method test" - 命中5篇
- 查询2: "advanced optimization" - 命中3篇

### 相似工作分析
经过全面搜索，无高度重叠的工作。发现一些相关的低度重叠工作，但核心方法有本质区别。

### 新颖性判定
**新颖性等级**: Level 2 - 中度新颖

**判定理由**:
方法组合新颖，与已有工作有明确差异点。核心创新在于技术路线的独特设计。
Pilot实验验证了性能提升的有效性。

**差异化优势**:
- 优势1: 技术路线独特，采用不同于现有方法的核心架构
- 优势2: 性能验证有效，Pilot实验显示显著提升

**风险提示**: 无重大风险。

---

## 总体评估

### 新颖性分布
- Level 2（中度新颖）: 1个假设

### Gate T6-DECIDE 决策
- 总体决策: PASS
"""
    report_path.write_text(report_content)

    # 基线文件正好100字符
    baselines_path = temp_workspace / "novelty" / "must_add_baselines.md"
    baselines_path.write_text("x" * 100)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = novelty_agent.validate_outputs(ctx)
    # 应该通过（>=100字符）
    assert ok, f"Validation should pass with 100 chars: {err}"


def test_validate_outputs_report_exactly_500_chars(novelty_agent, temp_workspace):
    """测试输出校验（报告正好500字符）。"""
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\nconstraints:\n  max_budget_usd: 1000\n")

    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_path.write_text("# 研究假设\n\n## H1: 假设1\n\n内容...")

    # 基础内容（不含填充字符）
    base_content = """# T6 新颖性验证报告

生成时间: 2026-04-20
审计的假设数量: 1

## 执行摘要

本报告对研究假设H1进行了新颖性验证。经过全面的文献搜索和分析，
我们发现该假设展现了良好的创新性。

---

## H1: 假设1

### 假设摘要
我们提出了一个新的方法来解决现有问题。

### 新颖性判定
**新颖性等级**: Level 2 - 中度新颖

---

## 总体评估

### 新颖性分布
- Level 2（中度新颖）: 1个假设
"""

    # 计算基础内容长度
    base_len = len(base_content)
    # 补足到正好500字符
    padding = "x" * (500 - base_len)
    report_content = base_content + padding

    report_path = temp_workspace / "novelty" / "novelty_report.md"
    report_path.write_text(report_content)

    baselines_path = temp_workspace / "novelty" / "must_add_baselines.md"
    baselines_content = """# 必须补充的基线方法

未发现必须补充的新基线。

经过全面的文献搜索，当前基线覆盖充分，无需添加新的基线方法。
已有基线包括标准方法A和主流方法B，能够充分展示创新性。

## 建议

无需添加新基线，可以直接进入T7阶段进行完整实验验证。
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
    # 应该通过（>=500字符）
    assert ok, f"Validation should pass with 500 chars: {err}"


def test_system_prompt_without_pilot_results(novelty_agent, temp_workspace):
    """测试system prompt在没有Pilot结果时也能工作。"""
    project_path = temp_workspace / "project.yaml"
    project_data = {
        "research_direction": "Test",
        "constraints": {"max_budget_usd": 1000},
    }
    project_path.write_text(yaml.dump(project_data))

    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_path.write_text("# 研究假设\n\n## H1: 假设1\n\n内容...")

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T6",
        run_id="test-run-1",
        mode=None,
    )

    prompt = novelty_agent.system_prompt(ctx)
    # 应该能生成prompt，即使没有Pilot结果
    assert "Novelty Agent" in prompt or "新颖性" in prompt
    assert "H1" in prompt
    # pilot_results_preview应该为空或不包含实际数据
    assert len(prompt) > 100
