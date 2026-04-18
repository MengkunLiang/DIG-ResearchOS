from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..runtime.errors import ToolAccessDenied, WorkspaceError


@dataclass
class WorkspaceAccessPolicy:
    workspace_dir: Path
    allowed_read_prefixes: list[str]
    allowed_write_prefixes: list[str]
    allow_read_references: bool = True

    def __post_init__(self) -> None:
        self.workspace_dir = self.workspace_dir.resolve()
        if not self.workspace_dir.exists():
            raise WorkspaceError(f"Workspace not found: {self.workspace_dir}")
        if not self.workspace_dir.is_dir():
            raise WorkspaceError(f"Workspace is not a directory: {self.workspace_dir}")

    def resolve_read(self, rel_path: str) -> Path:
        abs_path = self._resolve_within_workspace(rel_path)
        rel = abs_path.relative_to(self.workspace_dir).as_posix()
        if not self._match_prefix(rel, self.allowed_read_prefixes):
            raise ToolAccessDenied(
                f"Read access denied for '{rel_path}'. Allowed: {self.allowed_read_prefixes}"
            )
        return abs_path

    def resolve_write(self, rel_path: str) -> Path:
        abs_path = self._resolve_within_workspace(rel_path)
        rel = abs_path.relative_to(self.workspace_dir).as_posix()
        if not self._match_prefix(rel, self.allowed_write_prefixes):
            raise ToolAccessDenied(
                f"Write access denied for '{rel_path}'. Allowed: {self.allowed_write_prefixes}"
            )
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        return abs_path

    def _resolve_within_workspace(self, rel_path: str) -> Path:
        if rel_path.startswith("/"):
            raise ToolAccessDenied(f"Absolute paths not allowed: '{rel_path}'")
        candidate = (self.workspace_dir / rel_path).resolve()
        try:
            candidate.relative_to(self.workspace_dir)
        except ValueError as exc:
            raise ToolAccessDenied(f"Path escapes workspace: '{rel_path}' -> '{candidate}'") from exc
        return candidate

    @staticmethod
    def _match_prefix(rel: str, prefixes: list[str]) -> bool:
        for prefix in prefixes:
            if prefix == "":
                if "/" not in rel:
                    return True
            elif rel == prefix.rstrip("/") or rel.startswith(prefix.rstrip("/") + "/"):
                return True
        return False

