"""测试种子材料上传工具。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from researchos.tools.upload_seed_materials import (
    UploadSeedCodeTool,
    UploadSeedDataTool,
    UploadSeedPdfTool,
)
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


@pytest.mark.asyncio
async def test_upload_seed_pdf_success(tmp_workspace: Path):
    """测试成功上传 PDF 文件。"""
    # 创建测试 PDF 文件
    test_pdf = tmp_workspace.parent / "test.pdf"
    test_pdf.write_text("Fake PDF content", encoding="utf-8")

    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tool = UploadSeedPdfTool(policy)

    result = await tool.execute(
        source_path=str(test_pdf),
        paper_id="arxiv_2401.12345",
        metadata={"title": "Test Paper", "authors": ["Alice", "Bob"]},
    )

    assert result.ok
    assert "arxiv_2401.12345.pdf" in result.content
    assert (tmp_workspace / "user_seeds" / "pdfs" / "arxiv_2401.12345.pdf").exists()
    assert (tmp_workspace / "user_seeds" / "pdfs" / "arxiv_2401.12345.json").exists()


@pytest.mark.asyncio
async def test_upload_seed_pdf_file_not_found(tmp_workspace: Path):
    """测试上传不存在的文件。"""
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tool = UploadSeedPdfTool(policy)

    result = await tool.execute(
        source_path="/nonexistent/file.pdf",
        paper_id="test",
    )

    assert not result.ok
    assert result.error == "file_not_found"


@pytest.mark.asyncio
async def test_upload_seed_pdf_invalid_format(tmp_workspace: Path):
    """测试上传非 PDF 文件。"""
    test_file = tmp_workspace.parent / "test.txt"
    test_file.write_text("Not a PDF", encoding="utf-8")

    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tool = UploadSeedPdfTool(policy)

    result = await tool.execute(
        source_path=str(test_file),
        paper_id="test",
    )

    assert not result.ok
    assert result.error == "invalid_format"


@pytest.mark.asyncio
async def test_upload_seed_data_file(tmp_workspace: Path):
    """测试上传单个数据文件。"""
    test_data = tmp_workspace.parent / "data.csv"
    test_data.write_text("col1,col2\n1,2\n3,4", encoding="utf-8")

    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tool = UploadSeedDataTool(policy)

    result = await tool.execute(
        source_path=str(test_data),
        dataset_name="test_dataset",
        description="Test dataset for experiments",
    )

    assert result.ok
    assert "test_dataset" in result.content
    assert (tmp_workspace / "user_seeds" / "data" / "test_dataset" / "data.csv").exists()
    assert (tmp_workspace / "user_seeds" / "data" / "test_dataset" / "README.txt").exists()


@pytest.mark.asyncio
async def test_upload_seed_data_directory(tmp_workspace: Path):
    """测试上传数据目录。"""
    test_dir = tmp_workspace.parent / "test_data"
    test_dir.mkdir()
    (test_dir / "train.csv").write_text("train data", encoding="utf-8")
    (test_dir / "test.csv").write_text("test data", encoding="utf-8")
    (test_dir / "subdir").mkdir()
    (test_dir / "subdir" / "val.csv").write_text("val data", encoding="utf-8")

    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tool = UploadSeedDataTool(policy)

    result = await tool.execute(
        source_path=str(test_dir),
        dataset_name="multi_file_dataset",
    )

    assert result.ok
    assert result.data["file_count"] == 3
    target_dir = tmp_workspace / "user_seeds" / "data" / "multi_file_dataset"
    assert (target_dir / "train.csv").exists()
    assert (target_dir / "test.csv").exists()
    assert (target_dir / "subdir" / "val.csv").exists()


@pytest.mark.asyncio
async def test_upload_seed_code_file(tmp_workspace: Path):
    """测试上传单个代码文件。"""
    test_code = tmp_workspace.parent / "main.py"
    test_code.write_text("print('hello')", encoding="utf-8")

    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tool = UploadSeedCodeTool(policy)

    result = await tool.execute(
        source_path=str(test_code),
        repo_name="baseline_code",
        entry_point="main.py",
    )

    assert result.ok
    assert "baseline_code" in result.content
    assert (tmp_workspace / "user_seeds" / "code" / "baseline_code" / "main.py").exists()
    assert (tmp_workspace / "user_seeds" / "code" / "baseline_code" / "ENTRY_POINT.txt").exists()


@pytest.mark.asyncio
async def test_upload_seed_code_directory(tmp_workspace: Path):
    """测试上传代码目录（排除缓存文件）。"""
    test_dir = tmp_workspace.parent / "test_repo"
    test_dir.mkdir()
    (test_dir / "main.py").write_text("main code", encoding="utf-8")
    (test_dir / "utils.py").write_text("utils code", encoding="utf-8")
    (test_dir / "__pycache__").mkdir()
    (test_dir / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"bytecode")
    (test_dir / ".git").mkdir()
    (test_dir / ".git" / "config").write_text("git config", encoding="utf-8")

    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tool = UploadSeedCodeTool(policy)

    result = await tool.execute(
        source_path=str(test_dir),
        repo_name="test_repo",
    )

    assert result.ok
    assert result.data["file_count"] == 2  # 只有 main.py 和 utils.py
    target_dir = tmp_workspace / "user_seeds" / "code" / "test_repo"
    assert (target_dir / "main.py").exists()
    assert (target_dir / "utils.py").exists()
    assert not (target_dir / "__pycache__").exists()
    assert not (target_dir / ".git").exists()


@pytest.mark.asyncio
async def test_upload_seed_data_path_not_found(tmp_workspace: Path):
    """测试上传不存在的路径。"""
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tool = UploadSeedDataTool(policy)

    result = await tool.execute(
        source_path="/nonexistent/path",
        dataset_name="test",
    )

    assert not result.ok
    assert result.error == "path_not_found"
