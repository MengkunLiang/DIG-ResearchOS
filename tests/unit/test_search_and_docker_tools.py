from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest

from researchos.tools.base import ToolResult
from researchos.tools.docker_exec import DockerExecTool, check_docker_environment
from researchos.tools.latex_compile import LatexCompileTool
from researchos.tools.latex_compile import _compile_dependency_fingerprint
from researchos.tools.search_papers import FetchPaperMetadataTool, SearchPapersTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture
def container_mode():
    """检测是否在容器内运行测试。

    用于根据环境调整测试预期：
    - 容器内：docker_exec 直接执行 bash 命令
    - 宿主机：docker_exec 构建 docker run 命令

    使用共享的容器检测工具。
    """
    from researchos.runtime.container_detection import is_running_in_container

    return is_running_in_container()


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
async def test_search_papers_rejects_blank_query_with_tool_result():
    tool = SearchPapersTool()

    result = await tool.execute(query="   ", source="auto", max_results=5)

    assert not result.ok
    assert result.error == "empty_query"
    assert "query 不能为空" in result.content


def test_search_papers_normalizers_preserve_doi_and_arxiv_pdf_url():
    s2 = SearchPapersTool._normalize_s2_paper(
        {
            "paperId": "S2",
            "title": "S2 Paper",
            "externalIds": {"DOI": "10.1234/s2"},
        }
    )
    assert s2["doi"] == "10.1234/s2"

    import xml.etree.ElementTree as ET

    xml = """<entry xmlns=\"http://www.w3.org/2005/Atom\">
      <id>https://arxiv.org/abs/2401.12345v2</id>
      <title>Arxiv Paper</title>
      <summary>Summary</summary>
      <published>2024-01-01T00:00:00Z</published>
      <author><name>Alice</name></author>
    </entry>"""
    entry = ET.fromstring(xml)
    arxiv = SearchPapersTool._normalize_arxiv_entry(entry, {"atom": "http://www.w3.org/2005/Atom"})
    assert arxiv["pdf_url"] == "https://arxiv.org/pdf/2401.12345v2.pdf"


def test_search_papers_normalizers_accept_string_authors():
    s2 = SearchPapersTool._normalize_s2_paper(
        {
            "paperId": "S2-string-authors",
            "title": "S2 String Authors",
            "authors": ["Ada Lovelace", {"name": "Grace Hopper"}],
            "externalIds": {"DOI": "10.1234/string-authors"},
        }
    )
    content = SearchPapersTool._format_papers([s2])

    assert s2["authors"] == ["Ada Lovelace", "Grace Hopper"]
    assert "Ada Lovelace, Grace Hopper" in content


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
    if not container_mode:
        monkeypatch.setattr(
            "researchos.tools.docker_exec.check_docker_environment",
            lambda **_kwargs: (True, None, {"mode": "host_docker"}),
        )

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
        assert "--entrypoint" in captured["args"]
        entrypoint_idx = captured["args"].index("--entrypoint")
        assert captured["args"][entrypoint_idx + 1] == "bash"
        image_idx = captured["args"].index("researchos/python:3.11-ml")
        assert captured["args"][image_idx + 1 : image_idx + 3] == ("-lc", "python run.py")


def test_check_docker_environment_reports_missing_command(monkeypatch):
    monkeypatch.setattr("researchos.tools.docker_exec.shutil.which", lambda _name: None)

    ok, err, details = check_docker_environment(image="researchos/system:latest")

    assert not ok
    assert "WAITING_ENVIRONMENT" in err
    assert details["error"] == "docker_command_not_found"


@pytest.mark.asyncio
async def test_docker_exec_rejects_workspace_prefix_trick(tmp_workspace: Path):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tool = DockerExecTool(policy, project_config={"compute_budget": {"gpu_enabled": False}})

    result = await tool.execute(
        image="researchos/system:latest",
        command="pwd",
        cwd="/workspace2",
        timeout_seconds=30,
        allow_network=False,
        gpu=False,
        env={},
        extra_mounts=[],
    )

    assert not result.ok
    assert result.error == "invalid_cwd"


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


@pytest.mark.asyncio
async def test_latex_compile_writes_survey_compile_report(tmp_workspace: Path):
    """T3.6 survey compile should not rely on the LLM copying tool data by hand."""

    survey_dir = tmp_workspace / "drafts" / "survey"
    survey_dir.mkdir(parents=True)
    tex_path = survey_dir / "survey.tex"
    tex_path.write_text("\\documentclass{article}\\begin{document}Survey\\end{document}", encoding="utf-8")
    (survey_dir / "survey.pdf").write_text("pdf", encoding="utf-8")
    (survey_dir / "survey.log").write_text("log", encoding="utf-8")

    class _FakeDockerTool:
        policy = WorkspaceAccessPolicy(tmp_workspace, ["", "drafts/"], ["", "drafts/"])

        async def execute(self, **kwargs):
            return ToolResult(ok=True, content="compiled", data={"exit_code": 0})

    tool = LatexCompileTool(_FakeDockerTool())
    result = await tool.execute(tex_path="drafts/survey/survey.tex")

    assert result.ok
    report_path = survey_dir / "survey_compile_report.json"
    assert report_path.exists()
    assert '"tex_path": "drafts/survey/survey.tex"' in report_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_latex_compile_skips_repeated_failure_for_same_tex(tmp_workspace: Path, monkeypatch):
    bundle = tmp_workspace / "submission" / "bundle"
    bundle.mkdir(parents=True)
    tex_path = bundle / "main.tex"
    tex_path.write_text("\\documentclass{article}\\begin{document}Broken", encoding="utf-8")

    class _FailingDockerTool:
        policy = WorkspaceAccessPolicy(tmp_workspace, ["", "submission/"], ["", "submission/"])
        calls = 0

        async def execute(self, **kwargs):
            self.calls += 1
            return ToolResult(ok=False, content="latex failed", error="nonzero_exit", data={"exit_code": 1})

    docker = _FailingDockerTool()
    tool = LatexCompileTool(docker)
    monkeypatch.setattr(tool, "_is_running_in_container", lambda: False)
    monkeypatch.setattr("researchos.tools.latex_compile.shutil.which", lambda _name: None)

    first = await tool.execute(tex_path="submission/bundle/main.tex")
    second = await tool.execute(tex_path="submission/bundle/main.tex")

    assert not first.ok
    assert not second.ok
    assert second.error == "cached_compile_failure_same_tex"
    assert docker.calls == 1
    report = json.loads((tmp_workspace / "submission" / "compile_report.json").read_text(encoding="utf-8"))
    assert report["attempt_count"] == 1


@pytest.mark.asyncio
async def test_latex_compile_retries_failed_report_when_pdf_exists(tmp_workspace: Path, monkeypatch):
    bundle = tmp_workspace / "submission" / "bundle"
    bundle.mkdir(parents=True)
    tex_path = bundle / "main.tex"
    pdf_path = bundle / "main.pdf"
    log_path = bundle / "main.log"
    tex_path.write_text("\\documentclass{article}\\begin{document}OK\\end{document}", encoding="utf-8")
    pdf_path.write_bytes(b"%PDF-1.4\nmock pdf body\n%%EOF")
    log_path.write_text("clean log", encoding="utf-8")
    dependency_fingerprint = _compile_dependency_fingerprint(tmp_workspace, tex_path)
    report = {
        "version": "1.0",
        "semantics": "latex_compile_attempt_report",
        "tex_path": "submission/bundle/main.tex",
        "requested_engine": "pdflatex",
        "bibtex": True,
        "output_dir": None,
        "success": False,
        "error": "nonzero_exit",
        "main_tex_sha256": _sha256_file(tex_path),
        "main_tex_mtime": tex_path.stat().st_mtime,
        "dependency_fingerprint": dependency_fingerprint,
        "pdf_path": "",
        "log_path": "submission/bundle/main.log",
        "attempts": [
            {
                "success": False,
                "exit_code": 2,
                "main_tex_sha256": _sha256_file(tex_path),
                "dependency_fingerprint_hash": dependency_fingerprint["hash"],
                "error": "nonzero_exit",
            }
        ],
    }
    (tmp_workspace / "submission" / "compile_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    class _DockerTool:
        policy = WorkspaceAccessPolicy(tmp_workspace, ["", "submission/"], ["", "submission/"])
        calls = 0

        async def execute(self, **kwargs):
            self.calls += 1
            return ToolResult(ok=True, content="compiled", data={"exit_code": 0})

    docker = _DockerTool()
    tool = LatexCompileTool(docker)
    monkeypatch.setattr(tool, "_is_running_in_container", lambda: False)
    monkeypatch.setattr("researchos.tools.latex_compile.shutil.which", lambda _name: None)

    result = await tool.execute(tex_path="submission/bundle/main.tex")

    assert result.ok
    assert docker.calls == 1


@pytest.mark.asyncio
async def test_latex_compile_treats_docker_entrypoint_error_as_environment(tmp_workspace: Path, monkeypatch):
    bundle = tmp_workspace / "submission" / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "main.tex").write_text("\\documentclass{article}\\begin{document}OK\\end{document}", encoding="utf-8")

    class _DockerTool:
        policy = WorkspaceAccessPolicy(tmp_workspace, ["", "submission/"], ["", "submission/"])

        async def execute(self, **kwargs):
            return ToolResult(
                ok=False,
                content="researchos: error: argument command: invalid choice: 'bash'",
                error="nonzero_exit",
                data={"exit_code": 2},
            )

    tool = LatexCompileTool(_DockerTool())
    monkeypatch.setattr(tool, "_is_running_in_container", lambda: False)
    monkeypatch.setattr("researchos.tools.latex_compile.shutil.which", lambda _name: None)

    result = await tool.execute(tex_path="submission/bundle/main.tex")

    assert not result.ok
    assert result.error == "waiting_environment_docker_entrypoint_misconfigured"
    report = json.loads((tmp_workspace / "submission" / "compile_report.json").read_text(encoding="utf-8"))
    assert report["error"] == "docker_entrypoint_misconfigured"


@pytest.mark.asyncio
async def test_latex_compile_attempt_history_appends_after_tex_changes(tmp_workspace: Path, monkeypatch):
    bundle = tmp_workspace / "submission" / "bundle"
    bundle.mkdir(parents=True)
    tex_path = bundle / "main.tex"
    tex_path.write_text("\\documentclass{article}\\begin{document}Broken 1", encoding="utf-8")

    class _FailingDockerTool:
        policy = WorkspaceAccessPolicy(tmp_workspace, ["", "submission/"], ["", "submission/"])

        async def execute(self, **kwargs):
            return ToolResult(ok=False, content="latex failed", error="nonzero_exit", data={"exit_code": 1})

    tool = LatexCompileTool(_FailingDockerTool())
    monkeypatch.setattr(tool, "_is_running_in_container", lambda: False)
    monkeypatch.setattr("researchos.tools.latex_compile.shutil.which", lambda _name: None)

    await tool.execute(tex_path="submission/bundle/main.tex")
    tex_path.write_text("\\documentclass{article}\\begin{document}Broken 2", encoding="utf-8")
    await tool.execute(tex_path="submission/bundle/main.tex")

    report = json.loads((tmp_workspace / "submission" / "compile_report.json").read_text(encoding="utf-8"))
    assert report["attempt_count"] == 2
    hashes = {attempt["main_tex_sha256"] for attempt in report["attempts"]}
    assert len(hashes) == 2


@pytest.mark.asyncio
async def test_latex_compile_cached_success_requires_matching_log_and_hashes(tmp_workspace: Path, monkeypatch):
    bundle = tmp_workspace / "submission" / "bundle"
    bundle.mkdir(parents=True)
    tex_path = bundle / "main.tex"
    pdf_path = bundle / "main.pdf"
    log_path = bundle / "main.log"
    tex_path.write_text("\\documentclass{article}\\begin{document}OK\\end{document}", encoding="utf-8")
    (bundle / "references.bib").write_text("@article{a,title={A}}\n", encoding="utf-8")
    pdf_path.write_bytes(b"%PDF-1.4\nmock pdf body\n%%EOF")
    log_path.write_text("clean log", encoding="utf-8")
    dependency_fingerprint = _compile_dependency_fingerprint(tmp_workspace, tex_path)
    report = {
        "version": "1.0",
        "semantics": "latex_compile_attempt_report",
        "tex_path": "submission/bundle/main.tex",
        "requested_engine": "pdflatex",
        "bibtex": True,
        "output_dir": None,
        "success": True,
        "error": None,
        "main_tex_sha256": _sha256_file(tex_path),
        "main_tex_mtime": tex_path.stat().st_mtime,
        "dependency_fingerprint": dependency_fingerprint,
        "pdf_path": "submission/bundle/main.pdf",
        "pdf_sha256": _sha256_file(pdf_path),
        "pdf_size": pdf_path.stat().st_size,
        "pdf_mtime": pdf_path.stat().st_mtime,
        "log_path": "submission/bundle/main.log",
        "log_sha256": "stale-log-hash",
        "log_size": log_path.stat().st_size,
        "log_mtime": log_path.stat().st_mtime,
        "attempts": [
            {
                "success": True,
                "exit_code": 0,
                "main_tex_sha256": _sha256_file(tex_path),
                "dependency_fingerprint_hash": dependency_fingerprint["hash"],
            }
        ],
    }
    (tmp_workspace / "submission" / "compile_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    class _DockerTool:
        policy = WorkspaceAccessPolicy(tmp_workspace, ["", "submission/"], ["", "submission/"])
        calls = 0

        async def execute(self, **kwargs):
            self.calls += 1
            return ToolResult(ok=True, content="compiled", data={"exit_code": 0})

    docker = _DockerTool()
    tool = LatexCompileTool(docker)
    monkeypatch.setattr(tool, "_is_running_in_container", lambda: False)
    monkeypatch.setattr("researchos.tools.latex_compile.shutil.which", lambda _name: None)

    result = await tool.execute(tex_path="submission/bundle/main.tex")

    assert result.ok
    assert docker.calls == 1


@pytest.mark.asyncio
async def test_latex_compile_cached_success_invalidated_by_bib_dependency(tmp_workspace: Path, monkeypatch):
    bundle = tmp_workspace / "submission" / "bundle"
    bundle.mkdir(parents=True)
    tex_path = bundle / "main.tex"
    pdf_path = bundle / "main.pdf"
    log_path = bundle / "main.log"
    bib_path = bundle / "references.bib"
    tex_path.write_text(
        "\\documentclass{article}\\begin{document}OK\\\\cite{a}\\bibliography{references}\\end{document}",
        encoding="utf-8",
    )
    bib_path.write_text("@article{a,title={A}}\n", encoding="utf-8")
    pdf_path.write_bytes(b"%PDF-1.4\nmock pdf body\n%%EOF")
    log_path.write_text("clean log", encoding="utf-8")
    old_dependency_fingerprint = _compile_dependency_fingerprint(tmp_workspace, tex_path)
    report = {
        "version": "1.0",
        "semantics": "latex_compile_attempt_report",
        "tex_path": "submission/bundle/main.tex",
        "requested_engine": "pdflatex",
        "bibtex": True,
        "output_dir": None,
        "success": True,
        "error": None,
        "main_tex_sha256": _sha256_file(tex_path),
        "main_tex_mtime": tex_path.stat().st_mtime,
        "dependency_fingerprint": old_dependency_fingerprint,
        "pdf_path": "submission/bundle/main.pdf",
        "pdf_sha256": _sha256_file(pdf_path),
        "pdf_size": pdf_path.stat().st_size,
        "pdf_mtime": pdf_path.stat().st_mtime,
        "log_path": "submission/bundle/main.log",
        "log_sha256": _sha256_file(log_path),
        "log_size": log_path.stat().st_size,
        "log_mtime": log_path.stat().st_mtime,
        "attempts": [
            {
                "success": True,
                "exit_code": 0,
                "main_tex_sha256": _sha256_file(tex_path),
                "dependency_fingerprint_hash": old_dependency_fingerprint["hash"],
            }
        ],
    }
    (tmp_workspace / "submission" / "compile_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    bib_path.write_text("@article{a,title={Changed}}\n", encoding="utf-8")

    class _DockerTool:
        policy = WorkspaceAccessPolicy(tmp_workspace, ["", "submission/"], ["", "submission/"])
        calls = 0

        async def execute(self, **kwargs):
            self.calls += 1
            return ToolResult(ok=True, content="compiled", data={"exit_code": 0})

    docker = _DockerTool()
    tool = LatexCompileTool(docker)
    monkeypatch.setattr(tool, "_is_running_in_container", lambda: False)
    monkeypatch.setattr("researchos.tools.latex_compile.shutil.which", lambda _name: None)

    result = await tool.execute(tex_path="submission/bundle/main.tex")

    assert result.ok
    assert docker.calls == 1
