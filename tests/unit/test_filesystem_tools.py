from __future__ import annotations

from researchos.tools.filesystem import ReadFileTool, WriteFileTool
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


async def test_write_file_rejects_root_bridge_domain_plan_and_points_to_literature(tmp_workspace):
    policy = WorkspaceAccessPolicy(
        workspace_dir=tmp_workspace,
        allowed_read_prefixes=[""],
        allowed_write_prefixes=["", "literature/"],
    )
    tool = WriteFileTool(policy)

    result = await tool.execute(
        path="bridge_domain_plan.json",
        content={
            "semantics": "bridge_domain_plan",
            "source": "auto",
            "bridge_domains": [],
        },
    )

    assert result.ok is False
    assert result.error == "structured_output_requires_write_structured_file"
    assert "literature/bridge_domain_plan.json" in result.content
    assert not (tmp_workspace / "bridge_domain_plan.json").exists()
