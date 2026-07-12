from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..runtime.errors import ToolAccessDenied, WorkspaceError


@dataclass
class WorkspaceAccessPolicy:
    workspace_dir: Path
    allowed_read_prefixes: list[str]
    allowed_write_prefixes: list[str]
    allow_read_references: bool = True
    # Most tasks are scoped by directory.  A survey section task is narrower:
    # it may update the shared state but must not mark or rewrite another
    # section.  Keep that constraint on the policy object so deterministic
    # tools enforce the same boundary as filesystem tools.
    task_id: str | None = None
    allowed_survey_section_ids: frozenset[str] | None = None

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

    def require_survey_section(self, section_id: str) -> None:
        """Reject a section-state mutation outside the current task scope."""

        if self.allowed_survey_section_ids is None:
            return
        if section_id not in self.allowed_survey_section_ids:
            allowed = ", ".join(sorted(self.allowed_survey_section_ids)) or "none"
            raise ToolAccessDenied(
                f"Survey section '{section_id}' is outside task scope for "
                f"{self.task_id or 'current task'}. Allowed: {allowed}"
            )

    @staticmethod
    def path_allowed(rel_path: str, prefixes: Iterable[str]) -> bool:
        """Public prefix matcher used when deriving a narrower task policy."""

        return WorkspaceAccessPolicy._match_prefix(rel_path, list(prefixes))

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
