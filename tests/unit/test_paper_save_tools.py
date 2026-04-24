"""论文保存工具测试。

测试 save_papers_raw 和 save_papers_dedup 工具。
这些工具自动处理数据格式转换和 schema 验证。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from researchos.testing.fixtures import tmp_workspace, tool_registry, workspace_policy
from researchos.tools.paper_save_tools import (
    SavePapersRawTool,
    SavePapersDedupTool,
    _transform_to_papers_raw,
    _normalize_authors,
)


class TestNormalizeAuthors:
    """测试 authors 字段标准化。"""

    def test_string_list(self):
        """字符串列表应保持不变。"""
        authors = ["John Doe", "Jane Smith"]
        result = _normalize_authors(authors)
        assert result == ["John Doe", "Jane Smith"]

    def test_object_list(self):
        """对象列表应提取 name 字段。"""
        authors = [{"name": "John Doe"}, {"display_name": "Jane Smith"}]
        result = _normalize_authors(authors)
        assert result == ["John Doe", "Jane Smith"]

    def test_mixed_list(self):
        """混合格式应统一处理。"""
        authors = ["John Doe", {"name": "Jane Smith"}]
        result = _normalize_authors(authors)
        assert result == ["John Doe", "Jane Smith"]

    def test_empty_list(self):
        """空列表应返回空列表。"""
        result = _normalize_authors([])
        assert result == []

    def test_none(self):
        """None 应返回空列表。"""
        result = _normalize_authors(None)
        assert result == []


class TestTransformToPapersRaw:
    """测试论文数据格式转换。"""

    def test_semantic_scholar_format(self):
        """测试 Semantic Scholar 格式转换。"""
        paper = {
            "id": "test123",
            "source": "semantic_scholar",
            "title": "Test Paper",
            "authors": [{"name": "John Doe"}, {"name": "Jane Smith"}],
            "year": 2024,
            "abstract": "This is an abstract.",
            "venue": "NeurIPS",
            "citationCount": 100,
            "doi": "10.1234/test",
        }
        result = _transform_to_papers_raw(paper)
        assert result["id"] == "test123"
        assert result["source"] == "semantic_scholar"
        assert result["title"] == "Test Paper"
        assert result["authors"] == ["John Doe", "Jane Smith"]
        assert result["year"] == 2024
        assert result["abstract"] == "This is an abstract."
        assert result["venue"] == "NeurIPS"
        assert result["citation_count"] == 100

    def test_arxiv_format(self):
        """测试 arXiv 格式转换。"""
        paper = {
            "id": "arxiv:1234.5678",
            "source": "arxiv",
            "title": "arXiv Paper",
            "authors": [{"name": "Author One"}],
            "year": 2023,
            "abstract": "arXiv abstract.",
            "externalIds": {"ArXiv": "1234.5678"},
        }
        result = _transform_to_papers_raw(paper)
        assert result["id"] == "arxiv:1234.5678"
        assert result["source"] == "arxiv"
        assert result["citation_count"] == 0  # arXiv 没有引用数

    def test_openalex_format(self):
        """测试 OpenAlex 格式转换。"""
        paper = {
            "id": "W123456",
            "source": "openalex",
            "title": "OpenAlex Paper",
            "authors": ["Author One", "Author Two"],
            "year": 2022,
            "abstract": "OpenAlex abstract.",
            "venue": "ICML",
            "citation_count": 50,
        }
        result = _transform_to_papers_raw(paper)
        assert result["id"] == "W123456"
        assert result["source"] == "openalex"
        assert result["authors"] == ["Author One", "Author Two"]
        assert result["citation_count"] == 50

    def test_minimal_paper(self):
        """测试最小数据格式转换。"""
        paper = {
            "title": "Minimal Paper",
        }
        result = _transform_to_papers_raw(paper)
        assert result["title"] == "Minimal Paper"
        assert result["id"] == ""
        assert result["authors"] == []
        assert result["year"] is None


class TestSavePapersRawTool:
    """测试 save_papers_raw 工具。"""

    @pytest.fixture
    def workspace(self, tmp_workspace: Path):
        return tmp_workspace

    @pytest.fixture
    def policy(self, workspace: Path):
        from researchos.tools.workspace_policy import WorkspaceAccessPolicy
        # 允许 workspace 根目录和子目录读写
        return WorkspaceAccessPolicy(workspace, ["", "literature/"], ["", "literature/"])

    @pytest.fixture
    def tool(self, policy):
        return SavePapersRawTool(policy)

    @pytest.mark.asyncio
    async def test_save_papers(self, tool, workspace):
        """测试保存论文数据。"""
        papers = [
            {
                "id": "test1",
                "source": "semantic_scholar",
                "title": "Paper 1",
                "authors": [{"name": "Author 1"}],
                "year": 2024,
                "abstract": "Abstract 1",
                "venue": "NeurIPS",
                "citationCount": 10,
            },
            {
                "id": "test2",
                "source": "arxiv",
                "title": "Paper 2",
                "authors": ["Author 2"],
                "year": 2023,
                "abstract": "Abstract 2",
            },
        ]
        result = await tool.execute(papers=papers)

        assert result.ok is True
        assert "成功保存" in result.content

        # 验证文件内容
        file_path = workspace / "literature" / "papers_raw.jsonl"
        assert file_path.exists()

        content = file_path.read_text(encoding="utf-8")
        assert "Paper 1" in content
        assert "Paper 2" in content
        assert "Author 1" in content  # authors 应该被标准化为字符串

    @pytest.mark.asyncio
    async def test_save_with_append(self, tool, workspace):
        """测试追加模式。"""
        papers1 = [
            {
                "id": "test1",
                "source": "test",
                "title": "Paper 1",
                "authors": [],
                "year": 2024,
            }
        ]
        result1 = await tool.execute(papers=papers1)
        assert result1.ok is True

        papers2 = [
            {
                "id": "test2",
                "source": "test",
                "title": "Paper 2",
                "authors": [],
                "year": 2023,
            }
        ]
        result2 = await tool.execute(papers=papers2, append=True)
        assert result2.ok is True

        # 验证两个论文都在文件中
        file_path = workspace / "literature" / "papers_raw.jsonl"
        content = file_path.read_text(encoding="utf-8")
        assert "Paper 1" in content
        assert "Paper 2" in content

    @pytest.mark.asyncio
    async def test_append_skips_duplicates(self, tool, workspace):
        """测试追加模式跳过重复 id。"""
        papers1 = [
            {
                "id": "test1",
                "source": "test",
                "title": "Paper 1",
                "authors": [],
                "year": 2024,
            }
        ]
        await tool.execute(papers=papers1)

        papers2 = [
            {
                "id": "test1",  # 重复 id
                "source": "test",
                "title": "Paper 1 Updated",
                "authors": [],
                "year": 2025,
            }
        ]
        result = await tool.execute(papers=papers2, append=True)
        assert result.ok is True

        # 验证只有一篇论文
        file_path = workspace / "literature" / "papers_raw.jsonl"
        content = file_path.read_text(encoding="utf-8")
        lines = [l for l in content.splitlines() if l.strip()]
        assert len(lines) == 1


class TestSavePapersDedupTool:
    """测试 save_papers_dedup 工具。"""

    @pytest.fixture
    def workspace(self, tmp_workspace: Path):
        return tmp_workspace

    @pytest.fixture
    def policy(self, workspace: Path):
        from researchos.tools.workspace_policy import WorkspaceAccessPolicy
        # 允许 workspace 根目录和子目录读写
        return WorkspaceAccessPolicy(workspace, ["", "literature/"], ["", "literature/"])

    @pytest.fixture
    def tool(self, policy):
        return SavePapersDedupTool(policy)

    @pytest.mark.asyncio
    async def test_save_dedup_papers(self, tool, workspace):
        """测试保存去重后的论文。"""
        papers = [
            {
                "id": "test1",
                "source": "semantic_scholar",
                "title": "Paper 1",
                "authors": [{"name": "Author 1"}],
                "year": 2024,
                "abstract": "Abstract 1",
                "venue": "NeurIPS",
                "relevance_score": 0.9,
                "why_relevant": "Directly relevant",
                "source_type": "top_conference",
            },
            {
                "id": "test2",
                "source": "arxiv",
                "title": "Paper 2",
                "authors": ["Author 2"],
                "year": 2023,
                "abstract": "Abstract 2",
                "relevance_score": 0.7,
                "why_relevant": "Related work",
                "source_type": "preprint",
            },
        ]
        result = await tool.execute(papers=papers)

        assert result.ok is True
        assert "成功保存" in result.content

        # 验证文件内容
        file_path = workspace / "literature" / "papers_dedup.jsonl"
        assert file_path.exists()

        content = file_path.read_text(encoding="utf-8")
        assert "Paper 1" in content
        assert "Paper 2" in content


# ============================================================================
# 流式写入工具测试
# ============================================================================


class TestAppendPapersRawTool:
    """流式追加工具测试。"""

    @pytest.fixture
    def workspace(self, tmp_path: Path):
        literature_dir = tmp_path / "literature"
        literature_dir.mkdir(parents=True)
        return tmp_path

    @pytest.fixture
    def policy(self, workspace: Path):
        from researchos.tools.workspace_policy import WorkspaceAccessPolicy
        return WorkspaceAccessPolicy(workspace, ["", "literature/"], ["", "literature/"])

    @pytest.fixture
    def tool(self, policy):
        from researchos.tools.paper_save_tools import AppendPapersRawTool
        return AppendPapersRawTool(policy)

    @pytest.mark.asyncio
    async def test_append_single_batch(self, tool, workspace):
        """测试追加单批论文。"""
        papers = [
            {"id": "p1", "title": "Paper 1", "authors": [{"name": "Author 1"}]},
            {"id": "p2", "title": "Paper 2", "authors": ["Author 2"]},
        ]
        result = await tool.execute(papers=papers)

        assert result.ok is True
        assert "追加 2 篇" in result.content

        # 验证文件内容（原始格式，不过滤）
        file_path = workspace / "literature" / "papers_raw.jsonl"
        assert file_path.exists()
        content = file_path.read_text(encoding="utf-8")
        assert "Paper 1" in content
        assert "Paper 2" in content
        assert '{"name": "Author 1"}' in content  # 原始格式保留

    @pytest.mark.asyncio
    async def test_append_multiple_batches(self, tool, workspace):
        """测试多次追加（模拟多次检索）。"""
        # 第一批
        papers1 = [{"id": "p1", "title": "Paper 1"}]
        result1 = await tool.execute(papers=papers1)
        assert result1.ok is True

        # 第二批
        papers2 = [{"id": "p2", "title": "Paper 2"}]
        result2 = await tool.execute(papers=papers2)
        assert result2.ok is True

        # 验证文件包含两批
        file_path = workspace / "literature" / "papers_raw.jsonl"
        content = file_path.read_text(encoding="utf-8")
        lines = [l for l in content.strip().split("\n") if l]
        assert len(lines) == 2
        assert "Paper 1" in lines[0]
        assert "Paper 2" in lines[1]


class TestProcessPapersRawTool:
    """批量处理工具测试。"""

    @pytest.fixture
    def workspace(self, tmp_path: Path):
        literature_dir = tmp_path / "literature"
        literature_dir.mkdir(parents=True)
        return tmp_path

    @pytest.fixture
    def policy(self, workspace: Path):
        from researchos.tools.workspace_policy import WorkspaceAccessPolicy
        return WorkspaceAccessPolicy(workspace, ["", "literature/"], ["", "literature/"])

    @pytest.fixture
    def tool(self, policy):
        from researchos.tools.paper_save_tools import ProcessPapersRawTool
        return ProcessPapersRawTool(policy)

    @pytest.mark.asyncio
    async def test_process_valid_papers(self, tool, workspace):
        """测试处理有效数据。"""
        # 先用 AppendPapersRawTool 写入原始数据
        from researchos.tools.paper_save_tools import AppendPapersRawTool

        append_tool = AppendPapersRawTool(tool.policy)
        raw_papers = [
            {"id": "p1", "title": "Paper 1", "authors": [{"name": "Author 1"}], "year": 2024},
            {"id": "p2", "title": "Paper 2", "authors": ["Author 2"], "year": "2023"},
        ]
        await append_tool.execute(papers=raw_papers)

        # 处理
        result = await tool.execute()

        assert result.ok is True
        assert "成功处理 2 篇" in result.content

        # 验证转换后的格式
        file_path = workspace / "literature" / "papers_raw.jsonl"
        content = file_path.read_text(encoding="utf-8")
        assert "Author 1" in content  # authors 已转换为字符串
