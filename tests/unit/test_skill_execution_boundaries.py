"""Regression coverage for public, pipeline-owned, and executor Skill routes."""

from __future__ import annotations

import asyncio
from pathlib import Path

from researchos.cli import build_parser, run_skill_command
from researchos.schemas.state import StateYaml
from researchos.skills.audit import audit_skill_suite
from researchos.skills.loader import discover_skills
from researchos.skills.routing import managed_skill_route


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_repository_skill_layers_are_explicit_and_auditable() -> None:
    """No internal package may masquerade as a public or executor CLI Skill."""

    repo_root = _repo_root()
    top_level = discover_skills(repo_root / "skills")
    executor_templates = discover_skills(repo_root / "skills" / "external_executor_skills")

    assert len(top_level) == 42
    assert sum(skill.execution_scope == "standalone" for skill in top_level.values()) == 40
    assert {
        skill.name
        for skill in top_level.values()
        if skill.execution_scope == "state_machine"
    } == {"research-reboost", "project-skill-specialization"}
    assert "method-builder" not in top_level

    assert len(executor_templates) == 13
    assert {skill.execution_scope for skill in executor_templates.values()} == {"executor_template"}
    assert {skill.execution_owner for skill in executor_templates.values()} == {"T5-SPECIALIZE-EXECUTOR-SKILLS"}

    report = audit_skill_suite(repo_root)
    assert report["status"] == "pass"
    assert report["summary"]["standalone_skills"] == 40
    assert report["summary"]["pipeline_owned_skills"] == 2
    assert report["summary"]["executor_templates"] == 13


def test_pipeline_owned_route_uses_existing_state_without_unsafe_stage_jump(tmp_path: Path) -> None:
    """A direct route must advise ordinary resume, never a T5 bypass command."""

    StateYaml(
        project_id="routing-test",
        current_task="T4.5-REVIEW",
        status="PAUSED",
    ).dump_yaml(tmp_path / "state.yaml")

    route = managed_skill_route(
        skill_name="research-reboost",
        execution_scope="state_machine",
        execution_owner="T5-REBOOST-GATE",
        workspace=tmp_path,
    )

    assert "T4.5-REVIEW" in route.next_action
    assert route.command == f"python -m researchos.cli resume --workspace {tmp_path}"
    assert "--from-task" not in route.render()


def test_run_skill_refuses_pipeline_module_with_a_concrete_safe_route(tmp_path: Path, capsys: object) -> None:
    """The direct CLI must stop before runtime preparation and tell the user what to run."""

    parser = build_parser()
    args = parser.parse_args(
        [
            "run-skill",
            "research-reboost",
            "audit",
            "--workspace",
            str(tmp_path),
            "--non-interactive",
            "--no-banner",
            "--no-color",
        ]
    )

    assert asyncio.run(run_skill_command(args)) == 2
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "不支持独立运行" in output
    assert "建议命令：python -m researchos.cli run --workspace" in output
    assert "启动模型和创建工作区前安全停止" in output
