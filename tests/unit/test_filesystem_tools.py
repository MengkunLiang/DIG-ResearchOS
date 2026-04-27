from __future__ import annotations

from researchos.tools.filesystem import ReadFileTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


async def test_read_file_directory_returns_actionable_error(tmp_workspace):
    (tmp_workspace / "experiments").mkdir()
    policy = WorkspaceAccessPolicy(
        workspace_dir=tmp_workspace,
        allowed_read_prefixes=[""],
        allowed_write_prefixes=[""],
    )
    tool = ReadFileTool(policy)

    result = await tool.execute(path="experiments")

    assert result.ok is False
    assert result.error == "is_directory"
    assert "list_files" in result.content
