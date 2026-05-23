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
async def test_write_file_rejects_structured_idea_rationales(tmp_workspace):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], ["ideation/"])
    tool = WriteFileTool(policy)
    result = await tool.execute(path="ideation/idea_rationales.json", content={"ideas": []})

    assert not result.ok
    assert result.error == "structured_output_requires_write_structured_file"
    assert "schema_name='idea_rationales'" in result.content
    assert "format='json'" in result.content


@pytest.mark.asyncio
async def test_write_file_rejects_structured_idea_scorecard_and_gate_decisions(tmp_workspace):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], ["ideation/"])
    tool = WriteFileTool(policy)

    scorecard = await tool.execute(path="ideation/idea_scorecard.yaml", content={"ideas": []})
    gates = await tool.execute(path="ideation/gate_decisions.json", content={"decisions": []})

    assert not scorecard.ok
    assert scorecard.error == "structured_output_requires_write_structured_file"
    assert "schema_name='idea_scorecard'" in scorecard.content
    assert "format='yaml'" in scorecard.content
    assert not gates.ok
    assert gates.error == "structured_output_requires_write_structured_file"
    assert "schema_name='gate_decisions'" in gates.content
    assert "format='json'" in gates.content


@pytest.mark.asyncio
async def test_write_file_rejects_structured_pilot_outputs(tmp_workspace):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], ["pilot/"])
    tool = WriteFileTool(policy)

    plan_result = await tool.execute(path="pilot/pilot_plan.yaml", content="experiments: []\n")
    results_result = await tool.execute(path="pilot/pilot_results.json", content={"experiments": []})

    assert not plan_result.ok
    assert plan_result.error == "structured_output_requires_write_structured_file"
    assert "schema_name='pilot_plan'" in plan_result.content
    assert not results_result.ok
    assert results_result.error == "structured_output_requires_write_structured_file"
    assert "schema_name='pilot_results'" in results_result.content
    assert "format='json'" in results_result.content


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
