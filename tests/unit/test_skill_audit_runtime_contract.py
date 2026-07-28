"""Regression tests for runtime-aware Skill suite auditing."""

from __future__ import annotations

from pathlib import Path

from researchos.skills.audit import _audit_skill, _script_paths


def test_audit_rejects_a_skill_tool_that_is_not_registered_at_runtime(tmp_path: Path) -> None:
    """A syntactically valid Skill must not defer an unknown tool failure to an Agent run."""

    skill_dir = tmp_path / "broken-tool-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: broken-tool-skill\n"
        "description: Verify runtime tool binding.\n"
        "tools:\n"
        "  - unknown_tool\n"
        "---\n"
        "# Broken Tool Skill\n",
        encoding="utf-8",
    )

    record = _audit_skill(
        skill_dir=skill_dir,
        kind="public",
        check_script_help=False,
        runtime_tool_names={"read_file", "finish_task"},
    )

    assert record["status"] == "fail"
    assert {issue["code"] for issue in record["errors"]} == {"unregistered_declared_tool"}
    assert "unknown_tool" in {issue["message"] for issue in record["errors"]}


def test_private_script_modules_are_not_treated_as_public_cli_entrypoints(tmp_path: Path) -> None:
    """Only scripts a researcher can invoke should be forced through ``--help`` smoke."""

    scripts = tmp_path / "skill" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "command.py").write_text("\"\"\"Public command.\"\"\"\n", encoding="utf-8")
    (scripts / "_support.py").write_text("\"\"\"Private support module.\"\"\"\n", encoding="utf-8")

    assert [path.name for path in _script_paths(scripts.parent)] == ["command.py"]
