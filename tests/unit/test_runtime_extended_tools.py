from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from researchos.runtime.config import RuntimeSettings, WebFetchSettings
from researchos.testing.mocks import MockHumanInterface
from researchos.tools.bash_run import BashRunTool
from researchos.tools.glob_files import GlobFilesTool
from researchos.tools.grep_search import GrepSearchTool
from researchos.tools.registry import ToolBuildContext, ToolRegistry
from researchos.tools.web_fetch import WebFetchAllowlist, WebFetchTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


class _TestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/hello":
            body = b"hello from server"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/hello")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


@pytest.fixture
def local_http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_bash_run_supports_workspace_env_and_truncation(tmp_workspace: Path):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tool = BashRunTool(policy, max_output_bytes=32)

    result = await tool.execute(
        command="printf '%s %s' \"$MY_VALUE\" \"$(printf 'x%.0s' {1..64})\"",
        env={"MY_VALUE": "hello"},
        timeout_seconds=5,
    )

    assert result.ok
    assert "STDOUT:\nhello " in result.content
    assert "[output truncated at 32 bytes]" in result.content
    assert result.data["cwd"] == str(tmp_workspace)
    assert result.data["truncated"] is True


@pytest.mark.asyncio
async def test_bash_run_uses_skill_dir_as_cwd_candidate(tmp_path: Path, tmp_workspace: Path):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    skill_dir = tmp_path / "skill_bundle"
    skill_scripts = skill_dir / "scripts"
    skill_scripts.mkdir(parents=True)
    tool = BashRunTool(policy, skill_dir=skill_dir)

    result = await tool.execute(command="pwd", cwd="scripts", timeout_seconds=5)

    assert result.ok
    assert str(skill_scripts) in result.content
    assert result.data["cwd"] == str(skill_scripts)


@pytest.mark.asyncio
async def test_bash_run_blocks_cwd_escape_and_handles_timeout(tmp_workspace: Path):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tool = BashRunTool(policy)

    denied = await tool.execute(command="pwd", cwd="/tmp", timeout_seconds=5)
    assert not denied.ok
    assert denied.error == "access_denied"

    timed_out = await tool.execute(
        command="python -c 'import time; time.sleep(2)'",
        timeout_seconds=1,
    )
    assert not timed_out.ok
    assert timed_out.error == "timeout"


@pytest.mark.asyncio
async def test_grep_search_python_fallback_finds_matches(monkeypatch, tmp_workspace: Path):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    (tmp_workspace / "src").mkdir()
    (tmp_workspace / "src" / "a.txt").write_text("Alpha\nbeta needle\n", encoding="utf-8")
    (tmp_workspace / "src" / "b.md").write_text("nothing\nNeedle again\n", encoding="utf-8")

    monkeypatch.setattr("researchos.tools.grep_search.shutil.which", lambda _: None)
    tool = GrepSearchTool(policy)
    result = await tool.execute(pattern="needle", path="src", glob="**/*", max_results=10)

    assert result.ok
    assert result.data["engine"] == "python"
    assert result.data["count"] == 2
    assert "src/a.txt:2:beta needle" in result.content
    assert "src/b.md:2:Needle again" in result.content


@pytest.mark.asyncio
async def test_glob_files_lists_matches_and_respects_limit(tmp_workspace: Path):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    (tmp_workspace / "pkg" / "sub").mkdir(parents=True)
    (tmp_workspace / "a.txt").write_text("a", encoding="utf-8")
    (tmp_workspace / "pkg" / "b.txt").write_text("b", encoding="utf-8")
    (tmp_workspace / "pkg" / "sub" / "c.txt").write_text("c", encoding="utf-8")
    tool = GlobFilesTool(policy)

    result = await tool.execute(pattern="**/*.txt", limit=2)

    assert result.ok
    assert result.data["count"] == 2
    assert result.data["truncated"] is True
    assert "a.txt" in result.content


@pytest.mark.asyncio
async def test_web_fetch_fetches_text_and_follows_allowed_redirects(local_http_server: str):
    tool = WebFetchTool()

    result = await tool.execute(url=f"{local_http_server}/redirect", timeout_seconds=5, max_bytes=1024)

    assert result.ok
    assert result.content == "hello from server"
    assert result.data["status_code"] == 200
    assert result.data["redirect_chain"]


@pytest.mark.asyncio
async def test_web_fetch_enforces_allowlist(local_http_server: str):
    settings = RuntimeSettings(
        web_fetch=WebFetchSettings(
            allowed_schemes=("http",),
            allowed_hosts=("example.com",),
        )
    )
    tool = WebFetchTool(allowlist=WebFetchAllowlist.from_runtime_settings(settings))

    result = await tool.execute(url=f"{local_http_server}/hello", timeout_seconds=5, max_bytes=1024)

    assert not result.ok
    assert result.error == "access_denied"


def test_builtin_registry_registers_extended_tools(tmp_workspace: Path):
    from researchos.tools.builtin import register_builtin_tools

    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    registry = ToolRegistry()
    register_builtin_tools(registry, RuntimeSettings())
    built = registry.build(
        ["bash_run", "grep_search", "glob_files", "web_fetch", "extract_paper_sections"],
        ToolBuildContext(policy=policy, human=MockHumanInterface()),
    )

    assert sorted(built) == [
        "bash_run",
        "extract_paper_sections",
        "glob_files",
        "grep_search",
        "web_fetch",
    ]
