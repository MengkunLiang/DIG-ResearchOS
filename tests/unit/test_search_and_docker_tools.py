from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from researchos.tools.base import ToolResult
from researchos.tools.docker_exec import DockerExecTool
from researchos.tools.latex_compile import LatexCompileTool
from researchos.tools.search_papers import FetchPaperMetadataTool, SearchPapersTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


@pytest.fixture
def container_mode():
    """检测是否在容器内运行测试。

    用于根据环境调整测试预期：
    - 容器内：docker_exec 直接执行 bash 命令
    - 宿主机：docker_exec 构建 docker run 命令
    """
    return (
        Path("/.dockerenv").exists()
        or Path("/run/.containerenv").exists()
        or os.getenv("CONTAINER_ID") is not None
    )


@pytest.mark.asyncio
async def test_search_papers_auto_falls_back_to_arxiv(monkeypatch):
    tool = SearchPapersTool()

    async def fake_s2(_params):
        return []

    async def fake_arxiv(_params):
        return [
            {
                "id": "1234.5678",
                "source": "arxiv",
                "title": "A Paper",
                "authors": [{"name": "Alice"}],
                "year": 2024,
                "abstract": "summary",
                "venue": "arXiv",
                "citationCount": 0,
                "externalIds": {"ArXiv": "1234.5678"},
                "url": "https://arxiv.org/abs/1234.5678",
            }
        ]

    monkeypatch.setattr(tool, "_s2_search", fake_s2)
    monkeypatch.setattr(tool, "_arxiv_search", fake_arxiv)

    result = await tool.execute(query="test", source="auto", max_results=5)

    assert result.ok
    assert result.data["source"] == "arxiv"
    assert "A Paper" in result.content


@pytest.mark.asyncio
async def test_fetch_paper_metadata_auto_detects_arxiv(monkeypatch):
    tool = FetchPaperMetadataTool()

    async def fake_fetch(identifier: str):
        return {
            "id": identifier,
            "source": "arxiv",
            "title": "Metadata",
            "authors": [{"name": "Bob"}],
            "year": 2023,
            "abstract": "x",
            "venue": "arXiv",
            "citationCount": 0,
            "externalIds": {"ArXiv": identifier},
            "references": [],
            "citations": [],
        }

    monkeypatch.setattr(tool, "_fetch_arxiv", fake_fetch)

    result = await tool.execute(id="2401.12345", source="auto")

    assert result.ok
    assert result.data["paper"]["id"] == "2401.12345"


class _FakeProc:
    def __init__(self, *, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.killed = False

    async def communicate(self):
        return self._stdout, self._stderr

    async def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True


@pytest.mark.asyncio
async def test_docker_exec_success(monkeypatch, tmp_workspace: Path, container_mode: bool):
    """测试 docker_exec 成功执行。

    容器内模式：验证直接执行 bash 命令
    宿主机模式：验证构建 docker run 命令
    """
    (tmp_workspace / "project.yaml").write_text(
        """
docker:
  allowed_images:
    - researchos/python:3.11-ml
compute_budget:
  gpu_enabled: false
""".strip(),
        encoding="utf-8",
    )
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tool = DockerExecTool(policy)
    captured = {}

    async def fake_create(*args, **kwargs):
        captured["args"] = args
        return _FakeProc(stdout=b"ok", stderr=b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    result = await tool.execute(
        image="researchos/python:3.11-ml",
        command="python run.py",
        cwd="/workspace",
        timeout_seconds=30,
        allow_network=False,
        gpu=False,
        env={},
        extra_mounts=[],
    )

    assert result.ok
    assert result.data["exit_code"] == 0

    # 根据模式验证命令格式
    if container_mode:
        # 容器内模式：应该是 bash -lc 命令
        assert captured["args"][0] == "bash"
        assert captured["args"][1] == "-lc"
    else:
        # 宿主机模式：应该是 docker run 命令
        assert captured["args"][0] == "docker"
        assert "run" in captured["args"]


@pytest.mark.asyncio
async def test_docker_exec_rejects_gpu_when_project_disallows(tmp_workspace: Path):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tool = DockerExecTool(policy, project_config={"compute_budget": {"gpu_enabled": False}})

    result = await tool.execute(
        image="researchos/system:latest",
        command="python run.py",
        cwd="/workspace",
        timeout_seconds=30,
        allow_network=False,
        gpu=True,
        env={},
        extra_mounts=[],
    )

    assert not result.ok
    assert result.error == "gpu_not_allowed"


@pytest.mark.asyncio
async def test_latex_compile_reports_pdf_path(tmp_workspace: Path, monkeypatch, container_mode: bool):
    """测试 LaTeX 编译并验证 PDF 路径。

    容器内模式：直接调用 latexmk
    宿主机模式：通过 docker_exec
    """
    (tmp_workspace / "drafts").mkdir()
    tex_path = tmp_workspace / "drafts" / "paper.tex"
    tex_path.write_text("\\documentclass{article}", encoding="utf-8")
    pdf_path = tmp_workspace / "drafts" / "paper.pdf"

    if container_mode:
        # 容器内模式：mock latexmk 命令
        async def fake_create(*args, **kwargs):
            # 模拟 PDF 生成
            pdf_path.write_text("pdf", encoding="utf-8")
            return _FakeProc(stdout=b"compiled", stderr=b"")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

        policy = WorkspaceAccessPolicy(tmp_workspace, ["", "drafts/"], ["", "drafts/"])
        docker_tool = DockerExecTool(policy)
        tool = LatexCompileTool(docker_tool)
    else:
        # 宿主机模式：使用 fake docker tool
        class _FakeDockerTool:
            policy = WorkspaceAccessPolicy(tmp_workspace, ["", "drafts/"], ["", "drafts/"])

            async def execute(self, **kwargs):
                pdf_path.write_text("pdf", encoding="utf-8")
                return ToolResult(ok=True, content="compiled", data={})

        tool = LatexCompileTool(_FakeDockerTool())

    result = await tool.execute(tex_path="drafts/paper.tex")

    assert result.ok
    assert result.data["pdf_path"] == "drafts/paper.pdf"
