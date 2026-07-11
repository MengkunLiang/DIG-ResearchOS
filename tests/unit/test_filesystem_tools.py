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


async def test_read_file_reads_whole_file_when_it_fits_model_context(tmp_workspace):
    content = "x" * 20_000
    (tmp_workspace / "large.txt").write_text(content, encoding="utf-8")
    policy = WorkspaceAccessPolicy(
        workspace_dir=tmp_workspace,
        allowed_read_prefixes=[""],
        allowed_write_prefixes=[""],
    )
    tool = ReadFileTool(policy, llm_max_context=16_000)

    result = await tool.execute(path="large.txt")

    assert result.ok is True
    assert result.content == content
    assert result.data["max_chars"] == 20_000
    assert result.data["max_chars_source"] == "model_context_full"
    assert result.data["llm_max_context"] == 16_000
    assert result.data["truncated"] is False


async def test_read_file_chunks_only_when_file_exceeds_model_context_budget(tmp_workspace):
    content = "x" * 50_000
    (tmp_workspace / "large.txt").write_text(content, encoding="utf-8")
    policy = WorkspaceAccessPolicy(
        workspace_dir=tmp_workspace,
        allowed_read_prefixes=[""],
        allowed_write_prefixes=[""],
    )
    tool = ReadFileTool(policy, llm_max_context=16_000)

    result = await tool.execute(path="large.txt")

    assert result.ok is True
    assert 8_000 < result.data["max_chars"] < len(content)
    assert result.data["max_chars_source"] == "model_context_chunk"
    assert result.data["estimated_text_tokens"] > result.data["usable_context_tokens"]
    assert result.data["truncated"] is True
    assert "模型上下文容量估算" in result.content


async def test_read_file_explicit_max_chars_overrides_model_context(tmp_workspace):
    content = "x" * 20_000
    (tmp_workspace / "large.txt").write_text(content, encoding="utf-8")
    policy = WorkspaceAccessPolicy(
        workspace_dir=tmp_workspace,
        allowed_read_prefixes=[""],
        allowed_write_prefixes=[""],
    )
    tool = ReadFileTool(policy, llm_max_context=16_000)

    result = await tool.execute(path="large.txt", max_chars=1_234)

    assert result.ok is True
    assert result.data["max_chars"] == 1_234
    assert result.data["max_chars_source"] == "explicit"
    assert result.data["truncated"] is True


async def test_read_file_without_model_context_keeps_legacy_fallback(tmp_workspace):
    content = "x" * 60_000
    (tmp_workspace / "large.txt").write_text(content, encoding="utf-8")
    policy = WorkspaceAccessPolicy(
        workspace_dir=tmp_workspace,
        allowed_read_prefixes=[""],
        allowed_write_prefixes=[""],
    )
    tool = ReadFileTool(policy)

    result = await tool.execute(path="large.txt")

    assert result.ok is True
    assert result.data["max_chars"] == 50_000
    assert result.data["max_chars_source"] == "fallback_default"
    assert result.data["truncated"] is True


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
