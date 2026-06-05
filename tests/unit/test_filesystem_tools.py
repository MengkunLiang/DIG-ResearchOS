from __future__ import annotations

from researchos.tools.filesystem import InspectUserSeedsTool, ReadFileTool, WriteFileTool
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


async def test_inspect_user_seeds_ignores_guides_templates_and_placeholders(tmp_workspace):
    user_seeds = tmp_workspace / "user_seeds"
    pdf_dir = user_seeds / "pdfs"
    pdf_dir.mkdir(parents=True)
    (user_seeds / "_DIR_GUIDE.md").write_text("# Guide\n\nThis is a generated guide.\n", encoding="utf-8")
    (user_seeds / "README.md").write_text("# README\n", encoding="utf-8")
    (user_seeds / "seed_ideas.md").write_text("# Seed Ideas\n\n暂无\n", encoding="utf-8")
    (user_seeds / "seed_constraints.md").write_text("", encoding="utf-8")
    (user_seeds / "seed_papers.jsonl.example").write_text("{}", encoding="utf-8")
    (pdf_dir / "_DIR_GUIDE.md").write_text("# PDF Guide\n", encoding="utf-8")
    (pdf_dir / "Actual Seed Paper.pdf").write_bytes(b"%PDF-1.4\n")

    policy = WorkspaceAccessPolicy(
        workspace_dir=tmp_workspace,
        allowed_read_prefixes=[""],
        allowed_write_prefixes=[""],
    )
    tool = InspectUserSeedsTool(policy)

    result = await tool.execute(path="user_seeds")

    assert result.ok is True
    assert result.data["actual_material_count"] == 1
    assert result.data["actual_material_paths"] == ["user_seeds/pdfs/Actual Seed Paper.pdf"]
    assert result.data["placeholder_count"] >= 2
    assert result.data["guide_or_template_count"] >= 3
    assert "_DIR_GUIDE.md" in result.content
    assert "不算 seed" in result.content
