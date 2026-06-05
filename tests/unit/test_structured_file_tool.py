"""write_structured_file 工具的单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from researchos.tools.structured_file import WriteStructuredFileTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    """创建临时 workspace 目录。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def policy(workspace_dir: Path) -> WorkspaceAccessPolicy:
    """创建 WorkspaceAccessPolicy 实例。"""
    return WorkspaceAccessPolicy(
        workspace_dir=workspace_dir,
        allowed_read_prefixes=[""],
        allowed_write_prefixes=["", "subdir/", "literature/"],
    )


@pytest.fixture
def tool(policy: WorkspaceAccessPolicy) -> WriteStructuredFileTool:
    """创建 WriteStructuredFileTool 实例。"""
    return WriteStructuredFileTool(policy)


@pytest.mark.asyncio
async def test_write_structured_file_valid_yaml(tool: WriteStructuredFileTool, workspace_dir: Path):
    """测试写入符合 schema 的数据（YAML 格式）。"""
    result = await tool.execute(
        path="project.yaml",
        schema_name="project",
        format="yaml",
        data={
            "project_id": "test-project",
            "research_direction": "AI research",
            "keywords": ["AI", "ML"],
            "constraints": {
                "max_budget_usd": 100.0,
                "compute_resources": {"allow_gpu": True, "max_memory_gb": 16}
            },
            "created_at": "2026-04-21T10:00:00Z",
            "seed_ensemble": {
                "tier1_seeds": [42, 123],
                "tier2_seeds": [789],
                "tier3_seeds": [999]
            }
        }
    )

    assert result.ok
    assert "成功写入" in result.content
    assert "project.yaml" in result.content

    # 验证文件内容正确
    project_file = workspace_dir / "project.yaml"
    assert project_file.exists()

    content = project_file.read_text(encoding="utf-8")
    assert "project_id: test-project" in content
    assert "research_direction: AI research" in content
    assert "tier1_seeds:" in content

    # 验证 YAML 可以正确解析
    data = yaml.safe_load(content)
    assert data["project_id"] == "test-project"
    assert data["keywords"] == ["AI", "ML"]
    assert data["seed_ensemble"]["tier1_seeds"] == [42, 123]


@pytest.mark.asyncio
async def test_write_structured_file_valid_json(tool: WriteStructuredFileTool, workspace_dir: Path):
    """测试写入符合 schema 的数据（JSON 格式）。"""
    result = await tool.execute(
        path="project.json",
        schema_name="project",
        format="json",
        data={
            "project_id": "test-project",
            "research_direction": "AI research",
            "keywords": ["AI", "ML"],
            "constraints": {
                "max_budget_usd": 100.0,
                "compute_resources": {"allow_gpu": True, "max_memory_gb": 16}
            },
            "created_at": "2026-04-21T10:00:00Z",
            "seed_ensemble": {
                "tier1_seeds": [42, 123],
                "tier2_seeds": [789],
                "tier3_seeds": [999]
            }
        }
    )

    assert result.ok
    assert "成功写入" in result.content

    # 验证文件内容正确
    project_file = workspace_dir / "project.json"
    assert project_file.exists()

    content = project_file.read_text(encoding="utf-8")
    data = json.loads(content)
    assert data["project_id"] == "test-project"
    assert data["keywords"] == ["AI", "ML"]


@pytest.mark.asyncio
async def test_write_structured_file_missing_required_field(tool: WriteStructuredFileTool, workspace_dir: Path):
    """测试写入缺少必需字段的数据。"""
    result = await tool.execute(
        path="project.yaml",
        schema_name="project",
        format="yaml",
        data={
            "project_id": "test-project",
            # 缺少 research_direction, created_at 等必需字段
        }
    )

    assert not result.ok
    assert "Schema 验证失败" in result.content
    assert result.error == "schema_validation_failed"


@pytest.mark.asyncio
async def test_write_structured_file_wrong_type(tool: WriteStructuredFileTool, workspace_dir: Path):
    """测试字段类型错误。"""
    result = await tool.execute(
        path="project.yaml",
        schema_name="project",
        format="yaml",
        data={
            "project_id": "test-project",
            "research_direction": "AI research",
            "created_at": "2026-04-21T10:00:00Z",
            "keywords": "AI, ML",  # 错误：应该是数组
            "constraints": {
                "max_budget_usd": 100.0,
                "compute_resources": {"allow_gpu": True, "max_memory_gb": 16}
            },
            "seed_ensemble": {
                "tier1_seeds": [42],
                "tier2_seeds": [789],
                "tier3_seeds": [999]
            }
        }
    )

    assert not result.ok
    assert "Schema 验证失败" in result.content


@pytest.mark.asyncio
async def test_write_structured_file_constraints_wrong_format(tool: WriteStructuredFileTool, workspace_dir: Path):
    """测试 constraints 格式错误（数组而非对象）。"""
    result = await tool.execute(
        path="project.yaml",
        schema_name="project",
        format="yaml",
        data={
            "project_id": "test-project",
            "research_direction": "AI research",
            "keywords": ["AI", "ML"],
            "constraints": ["以 arXiv 论文作为基础"],  # 错误：应该是对象
            "created_at": "2026-04-21T10:00:00Z",
            "seed_ensemble": {
                "tier1_seeds": [42],
                "tier2_seeds": [789],
                "tier3_seeds": [999]
            }
        }
    )

    assert not result.ok
    assert "Schema 验证失败" in result.content


@pytest.mark.asyncio
async def test_write_structured_file_seed_ensemble_wrong_format(tool: WriteStructuredFileTool, workspace_dir: Path):
    """测试 seed_ensemble 格式错误（数组而非对象）。"""
    result = await tool.execute(
        path="project.yaml",
        schema_name="project",
        format="yaml",
        data={
            "project_id": "test-project",
            "research_direction": "AI research",
            "keywords": ["AI", "ML"],
            "constraints": {
                "max_budget_usd": 100.0,
                "compute_resources": {"allow_gpu": True, "max_memory_gb": 16}
            },
            "created_at": "2026-04-21T10:00:00Z",
            "seed_ensemble": [  # 错误：应该是对象
                {"source": "arxiv_id", "value": "2601.03192"}
            ]
        }
    )

    assert not result.ok
    assert "Schema 验证失败" in result.content


@pytest.mark.asyncio
async def test_write_structured_file_creates_parent_dirs(tool: WriteStructuredFileTool, workspace_dir: Path):
    """测试自动创建父目录。"""
    result = await tool.execute(
        path="subdir/nested/project.yaml",
        schema_name="project",
        format="yaml",
        data={
            "project_id": "test-project",
            "research_direction": "AI research",
            "keywords": ["AI", "ML"],
            "constraints": {
                "max_budget_usd": 100.0,
                "compute_resources": {"allow_gpu": True, "max_memory_gb": 16}
            },
            "created_at": "2026-04-21T10:00:00Z",
            "seed_ensemble": {
                "tier1_seeds": [42],
                "tier2_seeds": [789],
                "tier3_seeds": [999]
            }
        }
    )

    assert result.ok

    # 验证文件和父目录都存在
    project_file = workspace_dir / "subdir" / "nested" / "project.yaml"
    assert project_file.exists()
    assert project_file.parent.exists()


@pytest.mark.asyncio
async def test_write_structured_file_bridge_domain_plan_requires_literature_path(
    tool: WriteStructuredFileTool,
    workspace_dir: Path,
):
    """bridge_domain_plan 不能写到根目录，避免 T2 读取不到。"""
    result = await tool.execute(
        path="bridge_domain_plan.json",
        schema_name="bridge_domain_plan",
        format="json",
        data={
            "semantics": "bridge_domain_plan",
            "source": "auto",
            "bridge_domains": [],
        },
    )

    assert not result.ok
    assert result.error == "wrong_artifact_path"
    assert "literature/bridge_domain_plan.json" in result.content
    assert not (workspace_dir / "bridge_domain_plan.json").exists()


@pytest.mark.asyncio
async def test_write_structured_file_bridge_domain_plan_valid_json(
    tool: WriteStructuredFileTool,
    workspace_dir: Path,
):
    """T1 可以用结构化工具写入正式 bridge_domain_plan。"""
    result = await tool.execute(
        path="literature/bridge_domain_plan.json",
        schema_name="bridge_domain_plan",
        format="json",
        data={
            "version": "1.0",
            "semantics": "bridge_domain_plan",
            "source": "mixed",
            "bridge_domains": [
                {
                    "bridge_id": "b1",
                    "name": "Causal robustness",
                    "why": "May provide useful retrieval analogies.",
                    "priority": "must_explore",
                    "queries": ["causal robustness recommendation"],
                    "source": "user",
                }
            ],
        },
    )

    assert result.ok
    plan_path = workspace_dir / "literature" / "bridge_domain_plan.json"
    assert plan_path.exists()
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert data["semantics"] == "bridge_domain_plan"
    assert data["bridge_domains"][0]["bridge_id"] == "b1"


@pytest.mark.asyncio
async def test_write_structured_file_jsonl_format(tool: WriteStructuredFileTool, workspace_dir: Path):
    """测试 JSONL 格式（数组数据）。"""
    result = await tool.execute(
        path="papers.jsonl",
        schema_name="papers_dedup",
        format="jsonl",
        data=[
            {
                "id": "2401.00001",
                "title": "Paper 1",
                "authors": ["Author A"],
                "year": 2024,
                "source": "arxiv",
                "venue": "arXiv",
                "source_type": "preprint",
                "relevance_score": 0.95,
                "why_relevant": "Directly related to the research topic",
                "abstract": "This is an abstract about discrete diffusion for language.",
                "citation_count": 10,
                "url": "https://arxiv.org/abs/2401.00001"
            },
            {
                "id": "2401.00002",
                "title": "Paper 2",
                "authors": ["Author B"],
                "year": 2024,
                "source": "arxiv",
                "venue": "arXiv",
                "source_type": "preprint",
                "relevance_score": 0.90,
                "why_relevant": "Related to the research topic",
                "abstract": "This is an abstract about diffusion for language generation.",
                "citation_count": 5,
                "url": "https://arxiv.org/abs/2401.00002"
            }
        ]
    )

    assert result.ok

    # 验证 JSONL 格式正确
    papers_file = workspace_dir / "papers.jsonl"
    assert papers_file.exists()

    lines = papers_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2

    paper1 = json.loads(lines[0])
    assert paper1["title"] == "Paper 1"

    paper2 = json.loads(lines[1])
    assert paper2["title"] == "Paper 2"


@pytest.mark.asyncio
async def test_write_structured_file_jsonl_single_object(tool: WriteStructuredFileTool, workspace_dir: Path):
    """测试 JSONL 格式（单个对象）。"""
    result = await tool.execute(
        path="paper.jsonl",
        schema_name="papers_dedup",
        format="jsonl",
        data={
            "id": "2401.00001",
            "title": "Paper 1",
            "authors": ["Author A"],
            "year": 2024,
            "source": "arxiv",
            "venue": "arXiv",
            "source_type": "preprint",
            "relevance_score": 0.95,
            "why_relevant": "Directly related to the research topic",
            "abstract": "This is an abstract about discrete diffusion for language.",
            "citation_count": 10,
            "url": "https://arxiv.org/abs/2401.00001"
        }
    )

    assert result.ok

    # 验证 JSONL 格式正确（单行）
    paper_file = workspace_dir / "paper.jsonl"
    assert paper_file.exists()

    content = paper_file.read_text(encoding="utf-8").strip()
    paper = json.loads(content)
    assert paper["title"] == "Paper 1"


@pytest.mark.asyncio
async def test_write_structured_file_unsupported_format(tool: WriteStructuredFileTool, workspace_dir: Path):
    """测试不支持的格式。"""
    result = await tool.execute(
        path="project.xml",
        schema_name="project",
        format="xml",  # 不支持的格式
        data={
            "project_id": "test-project",
            "research_direction": "AI research",
            "keywords": ["AI"],
            "constraints": {
                "max_budget_usd": 100.0,
                "compute_resources": {"allow_gpu": True, "max_memory_gb": 16}
            },
            "created_at": "2026-04-21T10:00:00Z",
            "seed_ensemble": {
                "tier1_seeds": [42],
                "tier2_seeds": [789],
                "tier3_seeds": [999]
            }
        }
    )

    assert not result.ok
    assert "不支持的格式" in result.content
    assert result.error == "unsupported_format"
