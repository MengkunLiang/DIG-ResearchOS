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


@pytest.mark.asyncio
async def test_write_file_rejects_structured_exp_plan(tmp_workspace):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], ["ideation/"])
    tool = WriteFileTool(policy)
    result = await tool.execute(path="ideation/exp_plan.yaml", content="experiments: []\n")

    assert not result.ok
    assert result.error == "structured_output_requires_write_structured_file"
    assert "write_structured_file" in result.content


@pytest.mark.asyncio
async def test_write_file_serializes_json_object_content(tmp_workspace):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], ["ideation/"])
    tool = WriteFileTool(policy)
    result = await tool.execute(
        path="ideation/_lens_analysis.json",
        content={"directions": [{"id": "D1", "scores": {"novelty": 4}}]},
    )

    assert result.ok
    written = (tmp_workspace / "ideation" / "_lens_analysis.json").read_text(encoding="utf-8")
    assert '"directions"' in written
    assert '"D1"' in written


@pytest.mark.asyncio
async def test_write_file_rejects_object_content_for_plain_text(tmp_workspace):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], ["ideation/"])
    tool = WriteFileTool(policy)
    result = await tool.execute(path="ideation/notes.md", content={"bad": "shape"})

    assert not result.ok
    assert result.error == "invalid_content_type"


def test_workspace_policy_blocks_path_escape(tmp_workspace):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    with pytest.raises(ToolAccessDenied):
        policy.resolve_write("../../../etc/passwd")
