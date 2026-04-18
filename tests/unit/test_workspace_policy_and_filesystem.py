import pytest

from researchos.runtime.errors import ToolAccessDenied
from researchos.tools.filesystem import ReadFileTool, WriteFileTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


@pytest.mark.asyncio
async def test_workspace_policy_read_write(tmp_workspace):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tool = WriteFileTool(policy)
    result = await tool.execute(path="hello.txt", content="hi")
    assert result.ok
    reader = ReadFileTool(policy)
    read_result = await reader.execute(path="hello.txt")
    assert read_result.content == "hi"


def test_workspace_policy_blocks_path_escape(tmp_workspace):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    with pytest.raises(ToolAccessDenied):
        policy.resolve_write("../../../etc/passwd")

