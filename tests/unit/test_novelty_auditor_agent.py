"""T4.5 Novelty Auditor Agent 单元测试。

测试覆盖：
1. AgentSpec配置
2. system_prompt生成
3. initial_user_message生成
4. validate_outputs - 成功场景
5. validate_outputs - 各种失败场景
"""

from __future__ import annotations

from pathlib import Path

import pytest

from researchos.agents.novelty_auditor import NoveltyAuditorAgent
from researchos.runtime.agent import ExecutionContext
from researchos.time_utils import recent_year_from


@pytest.fixture
def temp_workspace(tmp_path):
    """创建临时workspace。"""
    workspace = tmp_path / "test_workspace"
    workspace.mkdir()

    # 创建必需的目录结构
    (workspace / "literature").mkdir()
    (workspace / "ideation").mkdir()

    return workspace


@pytest.fixture
def novelty_auditor_agent():
    """创建Novelty Auditor Agent实例。"""
    return NoveltyAuditorAgent()


def _write_design_rationale_tuples(workspace: Path, anchors: list[str]) -> None:
    tuple_dir = workspace / "ideation" / "_design_rationale_tuples"
    tuple_dir.mkdir(parents=True, exist_ok=True)
    for anchor in anchors:
        (tuple_dir / f"{anchor}.json").write_text(
            (
                '{"source_id":"'
                + anchor
                + '","design_rationale":"fixture rationale",'
                + '"contribution_type":"improvement"}\n'
            ),
            encoding="utf-8",
        )


def test_novelty_auditor_agent_spec(novelty_auditor_agent):
    """测试Novelty Auditor Agent的AgentSpec配置。"""
    spec = novelty_auditor_agent.spec
    assert spec.name == "novelty_auditor"
    assert spec.model_tier == "heavy"
    assert spec.llm_profile == "deepseek"
    assert "read_file" in spec.tool_names
    assert "write_file" in spec.tool_names
    assert "search_papers" in spec.tool_names
    assert "fetch_paper_metadata" in spec.tool_names
    assert "extract_mechanism_tuple" in spec.tool_names
    assert "compare_mechanism_tuples" in spec.tool_names
    assert "finish_task" in spec.tool_names
    assert spec.temperature == 0.3
    assert "ideation/" in spec.allowed_read_prefixes
    assert "literature/" in spec.allowed_read_prefixes
    assert "ideation/" in spec.allowed_write_prefixes
    assert spec.max_steps == 1000
    assert spec.max_tokens_total == 600_000


def test_novelty_auditor_system_prompt(novelty_auditor_agent, temp_workspace):
    """测试system prompt生成。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("""
research_direction: "Test research direction"
keywords: ["test", "research"]
""")

    # 创建hypotheses.md
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_content = """# 研究假设

## H1: 第一个假设

### 背景
这是第一个假设的背景。

### 核心假设
我们假设方法A可以提升性能。

## H2: 第二个假设

### 背景
这是第二个假设的背景。
"""
    hyp_path.write_text(hyp_content)

    # 创建synthesis.md
    syn_path = temp_workspace / "literature" / "synthesis.md"
    syn_path.write_text("# 文献综述\n\n这是文献综述内容。" + "x" * 1000)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4.5",
        run_id="test-run-1",
        mode=None,
    )

    prompt = novelty_auditor_agent.system_prompt(ctx)
    assert "Novelty Auditor" in prompt or "新颖性审计" in prompt
    assert "Test research direction" in prompt
    assert "H1" in prompt or "H2" in prompt
    assert "Level" in prompt  # 新颖性等级
    assert f"year_from={recent_year_from(1)}" in prompt
    assert "year_from={{" not in prompt


def test_novelty_auditor_initial_user_message(novelty_auditor_agent, temp_workspace):
    """测试初始用户消息。"""
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4.5",
        run_id="test-run-1",
        mode=None,
    )

    msg = novelty_auditor_agent.initial_user_message(ctx)
    assert "T4.5" in msg or "新颖性审计" in msg
    assert "hypotheses.md" in msg
    assert "novelty_audit.md" in msg


def test_validate_outputs_success(novelty_auditor_agent, temp_workspace):
    """测试输出校验（成功场景）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\n")

    # 创建hypotheses.md
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_content = """# 研究假设

## H1: 第一个假设

### 背景
这是第一个假设的背景。

### 核心假设
我们假设方法A可以提升性能。

## H2: 第二个假设

### 背景
这是第二个假设的背景。
"""
    hyp_path.write_text(hyp_content)

    # 创建novelty_audit.md（包含所有假设的审计）
    audit_path = temp_workspace / "ideation" / "novelty_audit.md"
    audit_content = """# 新颖性审计报告

生成时间: 2026-04-19
审计的假设数量: 2

---

## H1: 第一个假设

### 假设摘要
方法A可以提升性能。

### 搜索策略
- 查询1: "method A performance" - 命中15篇
- 查询2: "efficient method A" - 命中10篇

### 相似工作分析

#### High Overlap（高度重叠）
无高度重叠的工作。

#### Medium Overlap（中度重叠）
- **None**: 无中度重叠的工作；Similar Work (Smith et al., 2025, arXiv:2501.12345) 只是低度相关。
  - 相似点: 都使用了方法A
  - 差异点: 我们的应用场景不同

#### Low Overlap（低度重叠）
- **Related Work 1** (Jones et al., 2025, arXiv:2502.23456) - 相关领域
- **Related Work 2** (Brown et al., 2025, arXiv:2503.34567) - 相关方法

### 与已有方法对比
基于 comparison_table.csv 的分析：
- 方法X: 与我们的假设相关但不同
- 方法Y: 解决不同的问题

### 新颖性判定

**新颖性等级**: Level 2 - 中度新颖

**判定理由**:
基于搜索结果，发现有一些相关工作，但我们的方法在应用场景上有明确的差异。
差异点包括：1) 不同的数据集，2) 不同的优化目标，3) 不同的技术细节。

**差异化优势**:
- 优势1: 应用场景新颖
- 优势2: 技术组合独特
- 优势3: 理论分析更深入

**风险提示**:
无重大撞车风险。

---

## H2: 第二个假设

### 假设摘要
第二个假设的摘要。

### 搜索策略
- 查询1: "hypothesis 2 keywords" - 命中20篇
- 查询2: "related work" - 命中18篇

### 相似工作分析

#### High Overlap（高度重叠）
无高度重叠的工作。

#### Medium Overlap（中度重叠）
无中度重叠的工作。

#### Low Overlap（低度重叠）
- **Related Work 3** (Lee et al., 2025, arXiv:2504.45678) - 相关但不同

### 与已有方法对比
基于 comparison_table.csv 的分析：
- 方法Z: 不同的技术路线

### 新颖性判定

**新颖性等级**: Level 3 - 高度新颖

**判定理由**:
这是一个开创性的方向，近期文献中未见类似工作。

**差异化优势**:
- 优势1: 全新的方法范式
- 优势2: 解决了未被解决的问题
- 优势3: 有理论创新

**风险提示**:
无重大风险。

---

## 总体评估

### 新颖性分布
- Level 3（高度新颖）: 1个假设
- Level 2（中度新颖）: 1个假设
- Level 1（低度新颖）: 0个假设
- Level 0（无新颖性）: 0个假设

### 建议

✅ **建议继续**: 所有假设都具有足够的新颖性，可以进入实验阶段。

### 需要补充的Baseline
当前baseline覆盖充分。

### CDR Gate
Collision Axis: pass
Ambition Axis: pass
Contribution Distance: medium
Final Gate Verdict: pass
"""
    audit_path.write_text(audit_content)
    tuple_dir = temp_workspace / "ideation" / "_mechanism_tuples"
    tuple_dir.mkdir()
    (tuple_dir / "H1.json").write_text('{"source_id":"H1"}\n', encoding="utf-8")
    (tuple_dir / "H2.json").write_text('{"source_id":"H2"}\n', encoding="utf-8")
    _write_design_rationale_tuples(temp_workspace, ["H1", "H2"])

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4.5",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = novelty_auditor_agent.validate_outputs(ctx)
    assert ok, f"Validation failed: {err}"


def test_validate_outputs_requires_mechanism_tuples(novelty_auditor_agent, temp_workspace):
    """T4.5 必须落盘每个假设的 mechanism tuple，不能只写文字审计。"""
    (temp_workspace / "project.yaml").write_text("research_direction: Test\n")
    (temp_workspace / "ideation" / "hypotheses.md").write_text(
        "# 研究假设\n\n## H1: 假设1\n\n内容..."
    )
    (temp_workspace / "ideation" / "novelty_audit.md").write_text(
        """# 新颖性审计报告

## H1: 假设1

### 新颖性判定
**新颖性等级**: Level 2 - 中度新颖

### 相似工作分析
High Overlap: none
Medium Overlap: none
"""
        + "x" * 520,
        encoding="utf-8",
    )
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4.5",
        run_id="test-run-no-tuples",
        mode=None,
    )

    ok, err = novelty_auditor_agent.validate_outputs(ctx)

    assert not ok
    assert "_mechanism_tuples" in err


def test_validate_outputs_missing_audit(novelty_auditor_agent, temp_workspace):
    """测试输出校验（缺少novelty_audit.md）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\n")

    # 创建hypotheses.md
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_path.write_text("# 研究假设\n\n## H1: 假设1\n\n内容...")

    # 不创建novelty_audit.md

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4.5",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = novelty_auditor_agent.validate_outputs(ctx)
    assert not ok
    assert "novelty_audit.md" in err


def test_validate_outputs_missing_level(novelty_auditor_agent, temp_workspace):
    """测试输出校验（缺少新颖性等级）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\n")

    # 创建hypotheses.md
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_path.write_text("# 研究假设\n\n## H1: 假设1\n\n内容...")

    # 创建novelty_audit.md（但没有Level标记）
    audit_path = temp_workspace / "ideation" / "novelty_audit.md"
    audit_content = """# 新颖性审计报告

## H1: 假设1

这是一些审计内容，但没有新颖性等级标记。
""" + "x" * 500
    audit_path.write_text(audit_content)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4.5",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = novelty_auditor_agent.validate_outputs(ctx)
    assert not ok
    assert "Level" in err or "等级" in err


def test_validate_outputs_missing_hypothesis(novelty_auditor_agent, temp_workspace):
    """测试输出校验（缺少对某个假设的审计）。"""
    # 创建project.yaml
    project_path = temp_workspace / "project.yaml"
    project_path.write_text("research_direction: Test\n")

    # 创建hypotheses.md（有H1和H2）
    hyp_path = temp_workspace / "ideation" / "hypotheses.md"
    hyp_path.write_text("# 研究假设\n\n## H1: 假设1\n\n内容...\n\n## H2: 假设2\n\n内容...")

    # 创建novelty_audit.md（只审计了H1，缺少H2）
    audit_path = temp_workspace / "ideation" / "novelty_audit.md"
    audit_content = """# 新颖性审计报告

## H1: 假设1

### 新颖性判定
**新颖性等级**: Level 2 - 中度新颖

这是H1的审计内容。
""" + "x" * 500
    audit_path.write_text(audit_content)

    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4.5",
        run_id="test-run-1",
        mode=None,
    )

    ok, err = novelty_auditor_agent.validate_outputs(ctx)
    assert not ok
    assert "H2" in err


def test_validate_outputs_requires_collision_cases_when_overlap_reported(
    novelty_auditor_agent,
    temp_workspace,
):
    """T4.5 审计提到 High/Medium overlap 时必须有归档文件。"""
    (temp_workspace / "project.yaml").write_text("research_direction: Test\n")
    (temp_workspace / "ideation" / "hypotheses.md").write_text(
        "# 研究假设\n\n## H1: 假设1\n\n内容..."
    )
    (temp_workspace / "ideation" / "novelty_audit.md").write_text(
        """# 新颖性审计报告

## H1: 假设1

### 相似工作分析
#### High Overlap（高度重叠）
- **Same Idea** (A et al., 2025)

### 新颖性判定
**新颖性等级**: Level 0 - 无新颖性

### CDR Gate
Collision Axis: fail
Ambition Axis: fail
Contribution Distance: low
Final Gate Verdict: return to T4
"""
        + "x" * 520,
        encoding="utf-8",
    )
    tuple_dir = temp_workspace / "ideation" / "_mechanism_tuples"
    tuple_dir.mkdir()
    (tuple_dir / "H1.json").write_text('{"source_id":"H1"}\n', encoding="utf-8")
    _write_design_rationale_tuples(temp_workspace, ["H1"])
    ctx = ExecutionContext(
        workspace_dir=temp_workspace,
        project_id="test_project",
        task_id="T4.5",
        run_id="test-run-overlap",
        mode=None,
    )

    ok, err = novelty_auditor_agent.validate_outputs(ctx)
    assert not ok
    assert "collision_cases.md" in err
