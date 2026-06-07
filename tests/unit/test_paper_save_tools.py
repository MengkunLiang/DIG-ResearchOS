"""论文保存工具测试。

测试 save_papers_raw 和 save_papers_dedup 工具。
这些工具自动处理数据格式转换和 schema 验证。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.testing.fixtures import tmp_workspace, tool_registry, workspace_policy
from researchos.tools.paper_save_tools import (
    SavePapersRawTool,
    SavePapersDedupTool,
    _transform_to_papers_raw,
    _normalize_authors,
)
from researchos.runtime.t2_recovery import _seed_to_recovery_paper


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
        assert result["canonical_id"] == "W123456"
        assert result["canonical_id_source"] == "openalex"
        assert result["no_openalex_id"] is False

    def test_minimal_paper(self):
        """测试最小数据格式转换。"""
        paper = {
            "title": "Minimal Paper",
        }
        result = _transform_to_papers_raw(paper)
        assert result["title"] == "Minimal Paper"
        assert result["id"].startswith("noopenalex::")
        assert result["canonical_id"].startswith("noopenalex::")
        assert result["canonical_id"] != "Minimal Paper"
        assert result["id"] == result["canonical_id"]
        assert result["canonical_id_source"] == "noopenalex_fallback"
        assert result["no_openalex_id"] is True
        assert result["authors"] == []
        assert result["year"] is None

    def test_arxiv_without_openalex_keeps_readable_id_but_not_title_canonical(self):
        """arXiv id can stay readable, but canonical fallback must never be a title."""
        paper = {
            "id": "2401.12345",
            "source": "arxiv",
            "title": "Readable arXiv Paper",
            "authors": ["Author"],
            "externalIds": {"ArXiv": "2401.12345"},
        }

        result = _transform_to_papers_raw(paper)

        assert result["id"] == "arxiv:2401.12345"
        assert result["canonical_id"] == "arxiv:2401.12345"
        assert result["canonical_id"] != "Readable arXiv Paper"
        assert result["canonical_id_source"] == "arxiv_noopenalex"
        assert result["no_openalex_id"] is True

    def test_doi_without_openalex_uses_doi_canonical(self):
        """DOI-only records should align across raw, citation edges, queue, and notes."""
        paper = {
            "id": "doi:10.1234/example",
            "source": "crossref",
            "title": "DOI Only Paper",
            "authors": ["Author"],
            "doi": "10.1234/example",
        }

        result = _transform_to_papers_raw(paper)

        assert result["id"] == "doi:10.1234/example"
        assert result["canonical_id"] == "doi:10.1234/example"
        assert result["canonical_id_source"] == "doi_noopenalex"
        assert result["no_openalex_id"] is True

    def test_external_ids_doi_is_promoted_to_top_level(self):
        paper = {
            "id": "S2-paper",
            "source": "semantic_scholar",
            "title": "External DOI Paper",
            "authors": ["Author"],
            "externalIds": {"DOI": "10.1234/external"},
        }

        result = _transform_to_papers_raw(paper)

        assert result["doi"] == "10.1234/external"
        assert result["canonical_id"] == "doi:10.1234/external"

    def test_preserves_query_bucket_annotations(self):
        """Runtime/Scout routing labels should survive raw normalization."""
        paper = {
            "id": "p-adjacent",
            "title": "Adjacent Field Paper",
            "authors": ["Ada"],
            "search_bucket": "adjacent_field",
            "adjacent_field": True,
            "source_query": "queueing congestion learning",
        }
        result = _transform_to_papers_raw(paper)
        assert result["search_bucket"] == "adjacent_field"
        assert result["adjacent_field"] is True
        assert result["source_query"] == "queueing congestion learning"

    def test_preserves_openalex_reference_payloads(self):
        """OpenAlex reference fields must survive raw normalization for citation edges."""
        paper = {
            "id": "https://openalex.org/W123",
            "source": "openalex",
            "title": "OpenAlex Paper With References",
            "authors": ["Ada"],
            "year": 2025,
            "referenced_works": ["W456", "W789"],
            "related_works": ["W999"],
            "refs_unavailable": False,
        }
        result = _transform_to_papers_raw(paper)
        assert result["canonical_id"] == "W123"
        assert result["referenced_works"] == ["W456", "W789"]
        assert result["related_works"] == ["W999"]

    def test_t2_recovery_seed_without_openalex_uses_stable_noopenalex_id(self):
        seed = {
            "title": "Seed Without External Identifier",
            "authors": ["Ada"],
            "year": 2026,
            "abstract": "Seed abstract",
        }

        result = _seed_to_recovery_paper(seed)

        assert result["id"].startswith("noopenalex::")
        assert result["canonical_id"].startswith("noopenalex::")
        assert result["canonical_id"] != "Seed Without External Identifier"
        assert result["canonical_id_source"] == "noopenalex_fallback"
        assert result["provenance"]["id_source"] == "noopenalex_fallback"


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

    @pytest.mark.asyncio
    async def test_save_skips_invalid_records_without_dropping_valid_batch(self, tool, workspace):
        """单条空壳 metadata 不应让整批 search results 持久化失败。"""
        papers = [
            {
                "id": "bad-empty-title",
                "source": "crossref",
                "title": "",
                "authors": [],
                "year": 2024,
            },
            {
                "id": "good-paper",
                "source": "openalex",
                "title": "Valid Metadata Paper",
                "authors": ["Ada"],
                "year": 2025,
                "abstract": "Useful abstract.",
            },
        ]

        result = await tool.execute(papers=papers)

        assert result.ok is True
        assert result.data["count"] == 1
        assert result.data["valid_input_count"] == 1
        assert result.data["skipped_count"] == 1
        content = (workspace / "literature" / "papers_raw.jsonl").read_text(encoding="utf-8")
        assert "Valid Metadata Paper" in content
        assert "bad-empty-title" not in content


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

    @pytest.mark.asyncio
    async def test_save_dedup_append_merges_duplicate_metadata(self, tool, workspace):
        """旧 append 路径也必须合并增强字段，不能只按 id 静默跳过。"""
        result1 = await tool.execute(
            papers=[
                {
                    "id": "doi:10.1234/shared",
                    "title": "Shared Dedup Paper",
                    "authors": ["Ada"],
                    "doi": "10.1234/shared",
                    "abstract": "Short.",
                    "references": [],
                }
            ],
            append=False,
        )
        assert result1.ok

        result2 = await tool.execute(
            papers=[
                {
                    "id": "W999",
                    "title": "Shared Dedup Paper",
                    "authors": ["Ada"],
                    "doi": "10.1234/shared",
                    "abstract": "A much richer backfilled abstract.",
                    "externalIds": {"OpenAlex": "W999", "DOI": "10.1234/shared"},
                    "references": [{"doi": "10.8888/ref"}],
                    "pdf_url": "https://example.org/shared.pdf",
                }
            ],
            append=True,
        )

        assert result2.ok
        assert result2.data["count"] == 0
        assert result2.data["merged_count"] == 1
        records = [
            json.loads(line)
            for line in (workspace / "literature" / "papers_dedup.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(records) == 1
        assert records[0]["abstract"] == "A much richer backfilled abstract."
        assert records[0]["references"] == [{"doi": "10.8888/ref"}]
        assert records[0]["pdf_urls"] == ["https://example.org/shared.pdf"]


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

    @pytest.mark.asyncio
    async def test_append_merges_duplicate_raw_provenance(self, tool, workspace):
        """重复论文追加时要合并 bridge/citation/PDF provenance，不能静默跳过。"""
        result1 = await tool.execute(
            papers=[
                {
                    "id": "doi:10.1234/test",
                    "source": "crossref",
                    "title": "Shared Paper",
                    "authors": ["Ada"],
                    "doi": "10.1234/test",
                    "source_query": "core query",
                    "search_bucket": "core",
                    "source_tool": "crossref_search",
                    "abstract": "",
                }
            ],
            append=True,
        )
        assert result1.ok

        result2 = await tool.execute(
            papers=[
                {
                    "id": "arxiv:2401.00001",
                    "source": "arxiv",
                    "title": "Shared Paper",
                    "authors": ["Ada"],
                    "doi": "10.1234/test",
                    "source_query": "bridge query",
                    "search_bucket": "theory_bridge",
                    "bridge_id": "b2",
                    "source_tool": "multi_source_search",
                    "abstract": "Richer abstract",
                    "pdf_url": "https://arxiv.org/pdf/2401.00001.pdf",
                    "references": [{"doi": "10.9999/ref"}],
                }
            ],
            append=True,
        )

        assert result2.ok
        assert result2.data["count"] == 0
        assert result2.data["merged_count"] == 1
        records = [
            json.loads(line)
            for line in (workspace / "literature" / "papers_raw.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(records) == 1
        record = records[0]
        assert record["abstract"] == "Richer abstract"
        assert record["recalled_by_bridges"] == ["b2"]
        assert "core query" in record["source_queries"]
        assert "bridge query" in record["source_queries"]
        assert record["pdf_urls"] == ["https://arxiv.org/pdf/2401.00001.pdf"]
        assert record["references"] == [{"doi": "10.9999/ref"}]


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
