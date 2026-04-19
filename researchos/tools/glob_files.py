from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy


class GlobFilesParams(BaseModel):
    pattern: str = Field(..., description="glob 模式，例如 '**/*.py'")
    path: str = Field(default=".", description="相对 workspace 的搜索根目录或文件")
    include_dirs: bool = Field(default=False, description="是否包含目录")
    limit: int = Field(default=500, ge=1, le=5_000, description="最多返回多少个结果")


class GlobFilesTool(Tool):
    name = "glob_files"
    description = "使用 pathlib glob 列出 workspace 中匹配的文件"
    parameters_schema = GlobFilesParams
    timeout_seconds = 5.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        pattern = kwargs["pattern"]
        path = kwargs.get("path", ".")
        include_dirs = kwargs.get("include_dirs", False)
        limit = kwargs.get("limit", 500)

        root = self.policy.workspace_dir if path in ("", ".") else self.policy.resolve_read(path)
        if not root.exists():
            return ToolResult(ok=False, content=f"Path not found: {path}", error="not_found")

        items: list[str] = []
        candidates = self._iter_matches(root, pattern)
        for candidate in candidates:
            if not include_dirs and candidate.is_dir():
                continue
            items.append(candidate.relative_to(self.policy.workspace_dir).as_posix())
            if len(items) >= limit:
                return ToolResult(
                    ok=True,
                    content="\n".join(items),
                    data={"items": items, "count": len(items), "truncated": True},
                )

        return ToolResult(
            ok=True,
            content="\n".join(items) if items else "No files matched.",
            data={"items": items, "count": len(items), "truncated": False},
        )

    @staticmethod
    def _iter_matches(root: Path, pattern: str) -> list[Path]:
        if root.is_file():
            return [root] if root.match(pattern) or root.name == pattern else []
        return sorted(root.glob(pattern))
