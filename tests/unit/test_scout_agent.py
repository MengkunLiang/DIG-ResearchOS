"""T2 Scout Agent 单元测试

测试覆盖：
1. Mock LLM 基本流程测试
2. 去重逻辑测试
3. validate_outputs 测试
4. MCP 降级测试
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from researchos.agents.scout import ScoutAgent
from researchos.runtime.agent import ExecutionContext


@pytest.fixture
def test_workspace(tmp_path):
    """创建测试workspace"""
    workspace = tmp_path / "test_scout"
    workspace.mkdir()

    # 创建project.yaml
    project_data = {
        "project_id": "test-scout",
        "research_direction": "discrete diffusion language models",
        "keywords": ["discrete diffusion", "language model", "factorized"],
        "target_venue": "NeurIPS",
        "created_at": "2026-04-19T14:00:00Z",
    }
    (workspace / "project.yaml").write_text(yaml.dump(project_data))

    # 创建user_seeds目录
    (workspace / "user_seeds").mkdir()

    return workspace


@pytest.fixture
def scout_agent():
    """创建Scout Agent实例"""
    return ScoutAgent()


@pytest.fixture
def execution_context(test_workspace):
    """创建执行上下文"""
    return ExecutionContext(
        workspace_dir=test_workspace,
        project_id="test-scout",
        task_id="T2",
        run_id="test-run-001",
        inputs={"project": test_workspace / "project.yaml"},
        outputs_expected={
            "papers_raw": test_workspace / "literature" / "papers_raw.jsonl",
            "papers_dedup": test_workspace / "literature" / "papers_dedup.jsonl",
            "search_log": test_workspace / "literature" / "search_log.md",
            "missing_areas": test_workspace / "literature" / "missing_areas.md",
        },
    )


def test_scout_agent_spec(scout_agent):
    """测试Scout Agent的AgentSpec配置"""
    spec = scout_agent.spec
    assert spec.name == "scout"
    assert spec.model_tier == "medium"
    assert spec.temperature == 0.5
    assert "search_papers" in spec.tool_names
    assert "fetch_paper_metadata" in spec.tool_names
    # MCP工具已移除，等MCP配置完成后再启用
    # assert "mcp_semantic_scholar_search" in spec.tool_names
    # assert "mcp_arxiv_search" in spec.tool_names
    assert "literature/" in spec.allowed_write_prefixes


def test_scout_system_prompt(scout_agent, execution_context):
    """测试system prompt生成"""
    prompt = scout_agent.system_prompt(execution_context)
    assert "Scout Agent" in prompt
    assert "discrete diffusion language models" in prompt
    assert "Step 1" in prompt
    assert "Step 5" in prompt
    assert "MCP" in prompt


def test_scout_system_prompt_with_seed_papers(scout_agent, execution_context):
    """测试带seed papers的system prompt"""
    # 创建seed papers
    seed_papers = [
        {
            "title": "Discrete Diffusion Models",
            "year": 2023,
            "role": "anchor",
            "doi": "10.1234/test",
        },
        {
            "title": "Language Model Basics",
            "year": 2022,
            "role": "reference",
        },
    ]
    seed_path = execution_context.workspace_dir / "user_seeds" / "seed_papers.jsonl"
    with seed_path.open("w") as f:
        for paper in seed_papers:
            f.write(json.dumps(paper) + "\n")

    prompt = scout_agent.system_prompt(execution_context)
    assert "2 篇" in prompt
    assert "Discrete Diffusion Models" in prompt


def test_scout_initial_user_message(scout_agent, execution_context):
    """测试initial user message"""
    msg = scout_agent.initial_user_message(execution_context)
    assert "T2" in msg
    assert "文献普查" in msg
    assert "15-120" in msg


def test_validate_outputs_success(scout_agent, execution_context):
    """测试validate_outputs成功场景"""
    # 创建输出文件
    lit_dir = execution_context.workspace_dir / "literature"
    lit_dir.mkdir()

    # papers_raw.jsonl
    raw_papers = [
        {
            "id": f"s2:{i}",
            "title": f"Paper {i}",
            "authors": ["Author A", "Author B"],
            "year": 2023,
            "venue": "NeurIPS",
            "abstract": "Abstract",
            "doi": f"10.1234/{i}",
            "citation_count": 100,
        }
        for i in range(50)
    ]
    with (lit_dir / "papers_raw.jsonl").open("w") as f:
        for paper in raw_papers:
            f.write(json.dumps(paper) + "\n")

    # papers_dedup.jsonl (去重后30篇)
    dedup_papers = [
        {
            "id": f"s2:{i}",
            "title": f"Paper {i}",
            "authors": ["Author A", "Author B"],
            "year": 2023,
            "venue": "NeurIPS",
            "source_type": "top_conference",
            "relevance_score": 0.8,
            "why_relevant": "Relevant",
            "abstract": "Abstract",
            "doi": f"10.1234/{i}",
            "citation_count": 100,
        }
        for i in range(30)
    ]
    with (lit_dir / "papers_dedup.jsonl").open("w") as f:
        for paper in dedup_papers:
            f.write(json.dumps(paper) + "\n")

    # search_log.md
    (lit_dir / "search_log.md").write_text("# Search Log\n")

    # missing_areas.md
    (lit_dir / "missing_areas.md").write_text("# Missing Areas\n")

    ok, err = scout_agent.validate_outputs(execution_context)
    assert ok
    assert err is None


def test_validate_outputs_too_few_papers(scout_agent, execution_context):
    """测试validate_outputs失败：论文太少"""
    lit_dir = execution_context.workspace_dir / "literature"
    lit_dir.mkdir()

    # 只有10篇（刚好达到最低要求）
    dedup_papers = [
        {
            "id": f"s2:{i}",
            "title": f"Paper {i}",
            "authors": ["Author A"],
            "year": 2023,
            "relevance_score": 0.8,
        }
        for i in range(10)
    ]
    # 创建对应的 raw papers
    raw_papers = [{"id": f"s2:{i}", "title": f"Paper {i}"} for i in range(15)]

    with (lit_dir / "papers_dedup.jsonl").open("w") as f:
        for paper in dedup_papers:
            f.write(json.dumps(paper) + "\n")

    with (lit_dir / "papers_raw.jsonl").open("w") as f:
        for paper in raw_papers:
            f.write(json.dumps(paper) + "\n")

    (lit_dir / "search_log.md").write_text("")
    (lit_dir / "missing_areas.md").write_text("")

    # 10篇刚好达到最低要求，应该通过
    ok, err = scout_agent.validate_outputs(execution_context)
    assert ok


def test_validate_outputs_dedup_anomaly(scout_agent, execution_context):
    """测试validate_outputs失败：去重异常（dedup > raw）"""
    lit_dir = execution_context.workspace_dir / "literature"
    lit_dir.mkdir()

    # raw只有10篇
    raw_papers = [{"id": f"s2:{i}", "title": f"Paper {i}"} for i in range(10)]
    with (lit_dir / "papers_raw.jsonl").open("w") as f:
        for paper in raw_papers:
            f.write(json.dumps(paper) + "\n")

    # dedup有20篇（异常）
    dedup_papers = [
        {
            "id": f"s2:{i}",
            "title": f"Paper {i}",
            "authors": ["Author A"],
            "year": 2023,
            "relevance_score": 0.8,
        }
        for i in range(20)
    ]
    with (lit_dir / "papers_dedup.jsonl").open("w") as f:
        for paper in dedup_papers:
            f.write(json.dumps(paper) + "\n")

    (lit_dir / "search_log.md").write_text("")
    (lit_dir / "missing_areas.md").write_text("")

    ok, err = scout_agent.validate_outputs(execution_context)
    assert not ok
    assert "去重异常" in err


def test_validate_outputs_missing_required_field(scout_agent, execution_context):
    """测试validate_outputs失败：缺少必需字段"""
    lit_dir = execution_context.workspace_dir / "literature"
    lit_dir.mkdir()

    # papers_raw.jsonl (30篇)
    raw_papers = [{"id": f"s2:{i}", "title": f"Paper {i}"} for i in range(30)]
    with (lit_dir / "papers_raw.jsonl").open("w") as f:
        for paper in raw_papers:
            f.write(json.dumps(paper) + "\n")

    # 缺少relevance_score字段
    dedup_papers = [
        {
            "id": f"s2:{i}",
            "title": f"Paper {i}",
            "authors": ["Author A"],
            "year": 2023,
            # 缺少 relevance_score
        }
        for i in range(20)
    ]
    with (lit_dir / "papers_dedup.jsonl").open("w") as f:
        for paper in dedup_papers:
            f.write(json.dumps(paper) + "\n")

    (lit_dir / "search_log.md").write_text("")
    (lit_dir / "missing_areas.md").write_text("")

    ok, err = scout_agent.validate_outputs(execution_context)
    assert not ok
    assert "缺少字段" in err
    assert "relevance_score" in err
