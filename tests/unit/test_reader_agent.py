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
    # 创建paper_notes（至少15篇）
    notes_dir = temp_workspace / "literature" / "paper_notes"
    for i in range(20):
        (notes_dir / f"note{i}.md").write_text(f"# Paper {i}")

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
    # 只创建5篇笔记（少于15篇）
    notes_dir = temp_workspace / "literature" / "paper_notes"
    for i in range(5):
        (notes_dir / f"note{i}.md").write_text(f"# Paper {i}")

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
    assert "15篇" in err or "paper_notes" in err


def test_validate_outputs_synthesize_mode_success(reader_agent, temp_workspace):
    """测试synthesize模式输出校验（成功场景）。"""
    # 创建synthesis.md，包含5个必需章节
    syn_path = temp_workspace / "literature" / "synthesis.md"
    synthesis_content = """# 文献综述

## 方法家族分类
这是方法家族分类章节...

## 共同假设
这是共同假设章节...

## 性能-效率前沿
这是前沿分析章节...

## 技术趋势
这是趋势分析章节...

## 可操作研究问题
这是研究问题章节...

""" + "x" * 2000  # 确保长度足够

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
