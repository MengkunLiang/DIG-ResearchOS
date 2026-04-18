from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy
from ..runtime.errors import ToolAccessDenied, ToolRuntimeError


class ReadFileParams(BaseModel):
    path: str = Field(..., description="相对 workspace 的路径")


class ReadFileTool(Tool):
    name = "read_file"
    description = "读取 workspace 中的 UTF-8 文本文件"
    parameters_schema = ReadFileParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        path = kwargs["path"]
        try:
            abs_path = self.policy.resolve_read(path)
            content = abs_path.read_text(encoding="utf-8")
            return ToolResult(ok=True, content=content, data={"path": path, "size": len(content)})
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except FileNotFoundError:
            return ToolResult(ok=False, content=f"File not found: {path}", error="not_found")
        except UnicodeDecodeError:
            return ToolResult(ok=False, content=f"File is not UTF-8 text: {path}", error="not_text")


class WriteFileParams(BaseModel):
    path: str = Field(..., description="相对 workspace 的路径")
    content: str = Field(..., description="要写入的文本内容")


class WriteFileTool(Tool):
    name = "write_file"
    description = "写入 UTF-8 文本文件到 workspace"
    parameters_schema = WriteFileParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        path = kwargs["path"]
        content = kwargs["content"]
        try:
            abs_path = self.policy.resolve_write(path)
            abs_path.write_text(content, encoding="utf-8")
            return ToolResult(
                ok=True,
                content=f"Wrote {len(content)} chars to {path}",
                data={"path": path, "bytes": len(content.encode('utf-8'))},
            )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except OSError as exc:
            raise ToolRuntimeError("write_file", exc) from exc


class ListFilesParams(BaseModel):
    path: str = Field(".", description="相对 workspace 的目录路径")
    recursive: bool = Field(False, description="是否递归列出子目录")


class ListFilesTool(Tool):
    name = "list_files"
    description = "列出 workspace 中的文件"
    parameters_schema = ListFilesParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        path = kwargs["path"]
        recursive = kwargs["recursive"]
        try:
            rel_path = "" if path == "." else path
            abs_path = self.policy.resolve_read(rel_path) if rel_path else self.policy.workspace_dir
            if not abs_path.exists():
                return ToolResult(ok=False, content=f"Path not found: {path}", error="not_found")
            pattern = "**/*" if recursive else "*"
            items = sorted(
                p.relative_to(self.policy.workspace_dir).as_posix()
                for p in abs_path.glob(pattern)
                if p != abs_path
            )
            return ToolResult(ok=True, content="\n".join(items), data={"items": items})
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")

