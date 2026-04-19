"""T2 Scout Agent 集成测试

使用MockLLMClient模拟完整执行流程。
"""

import json
from pathlib import Path

import pytest
import yaml

from researchos.agents.scout import ScoutAgent
from researchos.runtime.agent import ExecutionContext


@pytest.fixture
def integration_workspace(tmp_path):
    """创建集成测试workspace"""
    workspace = tmp_path / "integration_scout"
    workspace.mkdir()

    # 创建project.yaml
    project_data = {
        "project_id": "integration-scout",
        "direction": "discrete diffusion language models",
        "keywords": ["discrete diffusion", "language model"],
        "target_venue": "NeurIPS",
    }
    (workspace / "project.yaml").write_text(yaml.dump(project_data))

    # 创建user_seeds目录（空）
    (workspace / "user_seeds").mkdir()
    (workspace / "user_seeds" / "seed_papers.jsonl").write_text("")
    (workspace / "user_seeds" / "seed_constraints.md").write_text("")

    return workspace


@pytest.fixture
def scout_context(integration_workspace):
    """创建Scout执行上下文"""
    return ExecutionContext(
        workspace_dir=integration_workspace,
        project_id="integration-scout",
        task_id="T2",
        run_id="integration-run-001",
        inputs={"project": integration_workspace / "project.yaml"},
        outputs_expected={
            "papers_raw": integration_workspace / "literature" / "papers_raw.jsonl",
            "papers_dedup": integration_workspace / "literature" / "papers_dedup.jsonl",
            "search_log": integration_workspace / "literature" / "search_log.md",
            "missing_areas": integration_workspace / "literature" / "missing_areas.md",
        },
    )


def test_scout_integration_mock_flow(scout_context):
    """集成测试：使用Mock数据模拟完整流程"""
    agent = ScoutAgent()

    # 验证agent配置
    assert agent.spec.name == "scout"

    # 生成system prompt
    prompt = agent.system_prompt(scout_context)
    assert "discrete diffusion language models" in prompt

    # 生成initial message
    msg = agent.initial_user_message(scout_context)
    assert "T2" in msg

    # 模拟LLM产出：创建输出文件
    lit_dir = scout_context.workspace_dir / "literature"
    lit_dir.mkdir()

    # 模拟papers_raw.jsonl（50篇原始结果）
    raw_papers = []
    for i in range(50):
        paper = {
            "id": f"s2:paper{i}",
            "title": f"Discrete Diffusion Paper {i}",
            "authors": [f"Author {i}", f"Coauthor {i}"],
            "year": 2023 if i % 2 == 0 else 2024,
            "venue": "NeurIPS" if i % 3 == 0 else "ICML",
            "abstract": f"This paper studies discrete diffusion models {i}",
            "doi": f"10.1234/paper{i}",
            "citation_count": 50 + i,
        }
        raw_papers.append(paper)

    with (lit_dir / "papers_raw.jsonl").open("w") as f:
        for paper in raw_papers:
            f.write(json.dumps(paper, ensure_ascii=False) + "\n")

    # 模拟papers_dedup.jsonl（去重后35篇）
    dedup_papers = []
    for i in range(35):
        paper = {
            "id": f"s2:paper{i}",
            "title": f"Discrete Diffusion Paper {i}",
            "authors": [f"Author {i}", f"Coauthor {i}"],
            "year": 2023 if i % 2 == 0 else 2024,
            "venue": "NeurIPS" if i % 3 == 0 else "ICML",
            "source_type": "top_conference",
            "relevance_score": 0.9 - (i * 0.01),
            "why_relevant": f"Addresses discrete diffusion in language models {i}",
            "abstract": f"This paper studies discrete diffusion models {i}",
            "doi": f"10.1234/paper{i}",
            "citation_count": 50 + i,
        }
        dedup_papers.append(paper)

    with (lit_dir / "papers_dedup.jsonl").open("w") as f:
        for paper in dedup_papers:
            f.write(json.dumps(paper, ensure_ascii=False) + "\n")

    # 模拟search_log.md
    search_log = """# T2 Scout 检索日志

## 检索式
1. "discrete diffusion language models" → Semantic Scholar: 25篇, arXiv: 8篇
2. "factorized gap in diffusion" → Semantic Scholar: 17篇, arXiv: 5篇

## 去重统计
- 原始结果: 50篇
- DOI去重后: 45篇
- 标题相似度去重后: 35篇
- 最终: 35篇

## MCP工具使用
- mcp_semantic_scholar_search: 成功 5次, 失败 0次
- mcp_arxiv_search: 成功 3次, 失败 0次
- search_papers（降级）: 0次
"""
    (lit_dir / "search_log.md").write_text(search_log)

    # 模拟missing_areas.md
    missing_areas = """# 文献缺口分析

## 覆盖良好的领域
- 离散扩散模型基础理论（20篇）
- 语言模型应用（15篇）

## 覆盖不足的领域
- factorized gap 的理论分析（仅2篇）

## 建议
- T3 Reader 重点关注 factorized gap 相关论文
"""
    (lit_dir / "missing_areas.md").write_text(missing_areas)

    # 验证输出
    ok, err = agent.validate_outputs(scout_context)
    assert ok, f"Validation failed: {err}"

    # 验证文件内容
    assert (lit_dir / "papers_raw.jsonl").exists()
    assert (lit_dir / "papers_dedup.jsonl").exists()
    assert (lit_dir / "search_log.md").exists()
    assert (lit_dir / "missing_areas.md").exists()

    # 验证papers数量
    with (lit_dir / "papers_raw.jsonl").open() as f:
        raw_count = len(f.readlines())
    with (lit_dir / "papers_dedup.jsonl").open() as f:
        dedup_count = len(f.readlines())

    assert raw_count == 50
    assert dedup_count == 35
    assert dedup_count < raw_count  # 去重有效


def test_scout_integration_with_seed_papers(integration_workspace):
    """集成测试：带seed papers的场景"""
    # 创建seed papers
    seed_papers = [
        {
            "title": "Foundational Paper on Discrete Diffusion",
            "year": 2022,
            "role": "anchor",
            "doi": "10.1234/foundation",
            "authors": ["Pioneer Author", "Second Author"],
        },
        {
            "title": "Survey on Language Models",
            "year": 2023,
            "role": "reference",
            "authors": ["Survey Author"],
        },
    ]
    seed_path = integration_workspace / "user_seeds" / "seed_papers.jsonl"
    with seed_path.open("w") as f:
        for paper in seed_papers:
            f.write(json.dumps(paper) + "\n")

    ctx = ExecutionContext(
        workspace_dir=integration_workspace,
        project_id="integration-scout",
        task_id="T2",
        run_id="integration-run-002",
        inputs={"project": integration_workspace / "project.yaml"},
        outputs_expected={
            "papers_raw": integration_workspace / "literature" / "papers_raw.jsonl",
            "papers_dedup": integration_workspace / "literature" / "papers_dedup.jsonl",
            "search_log": integration_workspace / "literature" / "search_log.md",
            "missing_areas": integration_workspace / "literature" / "missing_areas.md",
        },
    )

    agent = ScoutAgent()
    prompt = agent.system_prompt(ctx)

    # 验证prompt包含seed papers信息
    assert "2 篇" in prompt
    assert "Foundational Paper on Discrete Diffusion" in prompt
    assert "anchor" in prompt
