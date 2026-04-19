from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

from ..runtime.errors import ToolRuntimeError
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy


class GrepSearchParams(BaseModel):
    pattern: str = Field(..., description="要搜索的正则表达式")
    path: str = Field(default=".", description="相对 workspace 的搜索根目录或文件")
    glob: str | None = Field(
        default=None,
        description="可选文件 glob，例如 '**/*.py'",
    )
    case_sensitive: bool = Field(default=False, description="是否大小写敏感")
    max_results: int = Field(default=200, ge=1, le=2_000, description="最多返回多少条匹配")


class GrepSearchTool(Tool):
    name = "grep_search"
    description = "在 workspace 中按正则搜索文本；优先使用 rg，无 rg 时回退到 Python 实现"
    parameters_schema = GrepSearchParams
    timeout_seconds = 30.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        path = kwargs.get("path", ".")
        root = self.policy.workspace_dir if path in ("", ".") else self.policy.resolve_read(path)
        if not root.exists():
            return ToolResult(ok=False, content=f"Path not found: {path}", error="not_found")

        if shutil.which("rg"):
            return await self._search_with_rg(root=root, **kwargs)
        return self._search_with_python(root=root, **kwargs)

    async def _search_with_rg(self, *, root: Path, **kwargs) -> ToolResult:
        pattern = kwargs["pattern"]
        glob = kwargs.get("glob")
        case_sensitive = kwargs.get("case_sensitive", False)
        max_results = kwargs.get("max_results", 200)

        target = (
            "."
            if root == self.policy.workspace_dir
            else root.relative_to(self.policy.workspace_dir).as_posix()
        )
        command = [
            "rg",
            "--line-number",
            "--with-filename",
            "--color=never",
            "--max-count",
            str(max_results),
        ]
        if not case_sensitive:
            command.append("--ignore-case")
        if glob:
            command.extend(["--glob", glob])
        command.extend([pattern, target])

        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self.policy.workspace_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except OSError as exc:
            raise ToolRuntimeError(self.name, exc) from exc

        if proc.returncode == 1:
            return ToolResult(
                ok=True,
                content="No matches found.",
                data={"matches": [], "count": 0, "engine": "rg"},
            )
        if proc.returncode != 0:
            return ToolResult(
                ok=False,
                content=stderr.decode("utf-8", errors="replace") or "rg search failed",
                error="search_failed",
                data={"engine": "rg"},
            )

        matches: list[dict[str, str | int]] = []
        lines: list[str] = []
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            file_path, line_no, text = self._parse_rg_line(line)
            matches.append({"path": file_path, "line_number": line_no, "line_text": text})
            lines.append(f"{file_path}:{line_no}:{text}")

        return ToolResult(
            ok=True,
            content="\n".join(lines) if lines else "No matches found.",
            data={"matches": matches, "count": len(matches), "engine": "rg"},
        )

    def _search_with_python(self, *, root: Path, **kwargs) -> ToolResult:
        pattern = kwargs["pattern"]
        glob = kwargs.get("glob")
        case_sensitive = kwargs.get("case_sensitive", False)
        max_results = kwargs.get("max_results", 200)

        try:
            regex = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
        except re.error as exc:
            return ToolResult(ok=False, content=f"Invalid regex: {exc}", error="invalid_pattern")

        matches: list[dict[str, str | int]] = []
        lines: list[str] = []
        for file_path in self._iter_files(root, glob):
            if self._looks_binary(file_path):
                continue
            try:
                with file_path.open("r", encoding="utf-8", errors="ignore") as handle:
                    for index, line in enumerate(handle, start=1):
                        text = line.rstrip("\n")
                        if not regex.search(text):
                            continue
                        rel_path = file_path.relative_to(self.policy.workspace_dir).as_posix()
                        matches.append(
                            {"path": rel_path, "line_number": index, "line_text": text}
                        )
                        lines.append(f"{rel_path}:{index}:{text}")
                        if len(matches) >= max_results:
                            return ToolResult(
                                ok=True,
                                content="\n".join(lines),
                                data={
                                    "matches": matches,
                                    "count": len(matches),
                                    "engine": "python",
                                    "truncated": True,
                                },
                            )
            except OSError:
                continue

        return ToolResult(
            ok=True,
            content="\n".join(lines) if lines else "No matches found.",
            data={
                "matches": matches,
                "count": len(matches),
                "engine": "python",
                "truncated": False,
            },
        )

    @staticmethod
    def _parse_rg_line(line: str) -> tuple[str, int, str]:
        file_path, line_no, text = line.split(":", 2)
        return file_path, int(line_no), text

    @staticmethod
    def _looks_binary(path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                return b"\x00" in handle.read(4096)
        except OSError:
            return True

    @staticmethod
    def _iter_files(root: Path, pattern: str | None) -> list[Path]:
        if root.is_file():
            return [root]
        glob_pattern = pattern or "**/*"
        return sorted(path for path in root.glob(glob_pattern) if path.is_file())
