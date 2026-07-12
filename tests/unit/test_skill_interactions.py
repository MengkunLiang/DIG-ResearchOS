from pathlib import Path

import researchos.cli as cli
from researchos.cli import main
from researchos.runtime.config import RuntimeSettings
from researchos.runtime.agent import ExecutionContext
from researchos.skills.agent import SkillAgent
from researchos.skills.contracts import (
    check_skill_readiness,
    expected_outputs_from_metadata,
    prepare_skill_intake_packet,
)
from researchos.skills.loader import discover_skills, load_skill
from researchos.skills.session import load_session, record_readiness, record_runtime_pause
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.registry import ToolRegistry


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skills"


def test_guided_skill_readiness_persists_missing_input_then_recovers(tmp_path: Path):
    skill = load_skill(SKILLS_ROOT / "paper-outline")
    missing = check_skill_readiness(
        skill_name=skill.name,
        metadata=skill.metadata,
        workspace=tmp_path,
        request="Create an English empirical paper outline.",
    )

    assert not missing.ready
    intake = prepare_skill_intake_packet(missing)
    assert intake is not None
    assert intake.exists()
    assert "user_inputs/paper-outline/brief.md" in intake.read_text(encoding="utf-8")
    session_path, session = record_readiness(
        workspace=tmp_path,
        session_id="outline-demo",
        skill_name=skill.name,
        skill_path=skill.skill_dir,
        readiness=missing,
        resume=False,
        intake_packet_path=intake,
    )
    assert session_path.exists()
    assert session["status"] == "WAITING_INPUT"
    assert session["intake_packet"] == "user_inputs/paper-outline/_intake.md"
    assert load_session(tmp_path, "outline-demo")["readiness"]["ready"] is False

    brief = tmp_path / "user_inputs" / "paper-outline" / "brief.md"
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text("Problem, method, available evidence, target venue, and limitations.\n" * 3, encoding="utf-8")
    ready = check_skill_readiness(
        skill_name=skill.name,
        metadata=skill.metadata,
        workspace=tmp_path,
        request="Create an English empirical paper outline.",
    )
    assert ready.ready
    assert ready.selected_inputs["research_brief"] == brief
    resumed_path, resumed = record_readiness(
        workspace=tmp_path,
        session_id="outline-demo",
        skill_name=skill.name,
        skill_path=skill.skill_dir,
        readiness=ready,
        resume=True,
    )
    assert resumed_path == session_path
    assert resumed["status"] == "READY"
    assert resumed["request"] == "Create an English empirical paper outline."
    record_runtime_pause(
        workspace=tmp_path,
        session_id="outline-demo",
        error="provider temporarily unavailable",
    )
    paused = load_session(tmp_path, "outline-demo")
    assert paused["status"] == "WAITING_RUNTIME"
    assert "provider temporarily unavailable" in paused["last_runtime_error"]
    outputs = expected_outputs_from_metadata(skill.metadata, tmp_path)
    assert outputs["outline"] == tmp_path / "drafts" / "outline.md"


def test_public_guided_skills_have_valid_runtime_tools():
    registry = ToolRegistry()
    register_builtin_tools(registry, RuntimeSettings())
    skills = discover_skills(SKILLS_ROOT)
    guided = [
        skill
        for skill in skills.values()
        if skill.metadata.get("strict_tools")
    ]

    assert guided
    for skill in guided:
        agent = SkillAgent(skill=skill, available_tools=set(registry.available_names()))
        assert agent.translation_warnings == []
        assert "finish_task" in agent.spec.tool_names
        if skill.metadata.get("interaction", {}).get("mode") == "guided":
            assert "ask_human" in agent.spec.tool_names
            assert f"user_inputs/{skill.name}/" in agent.spec.allowed_write_prefixes


def test_project_mode_intake_keeps_existing_artifacts_as_candidates(tmp_path: Path):
    skill = load_skill(SKILLS_ROOT / "paper-outline")
    (tmp_path / "project.yaml").write_text("name: demo\ntarget_venue: NeurIPS\n", encoding="utf-8")
    hypotheses = tmp_path / "ideation" / "hypotheses.md"
    hypotheses.parent.mkdir(parents=True)
    hypotheses.write_text("Problem, method, evidence boundary, target reader, and limitations.\n" * 3, encoding="utf-8")

    readiness = check_skill_readiness(
        skill_name=skill.name,
        metadata=skill.metadata,
        workspace=tmp_path,
        request="Build an outline from this project.",
    )
    intake = prepare_skill_intake_packet(readiness)

    assert readiness.workspace_mode == "project"
    assert readiness.ready
    assert readiness.selected_inputs["research_brief"] == hypotheses
    assert intake is not None
    content = intake.read_text(encoding="utf-8")
    assert "Existing files are candidate inputs" in content
    assert "not automatic proof" in content
    assert "_followup_request.md" in content


def test_guided_skill_prompt_includes_semantic_followup_protocol(tmp_path: Path):
    skill = load_skill(SKILLS_ROOT / "paper-write")
    registry = ToolRegistry()
    register_builtin_tools(registry, RuntimeSettings())
    agent = SkillAgent(skill=skill, available_tools=set(registry.available_names()))
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="skill-run",
        task_id="SKILL_paper-write",
        run_id="test",
        extra={
            "skill_session_path": "_runtime/skill_sessions/paper-write.json",
            "skill_workspace_mode": "project",
            "skill_intake_packet_path": "user_inputs/paper-write/_intake.md",
        },
    )

    prompt = agent.system_prompt(ctx)
    assert "ask_human" in agent.spec.tool_names
    assert "user_inputs/paper-write/" in agent.spec.allowed_write_prefixes
    assert "Material Intake Protocol" in prompt
    assert "_followup_request.md" in prompt
    assert "Do not create final deliverables by guessing" in prompt


def test_cli_missing_input_gate_does_not_prepare_or_call_llm(tmp_path: Path, capsys):
    code = main(
        [
            "run-skill",
            "paper-outline",
            "Draft an English empirical paper.",
            "--workspace",
            str(tmp_path),
            "--skills-root",
            str(SKILLS_ROOT),
            "--no-banner",
        ]
    )

    output = capsys.readouterr().out
    assert code == 2
    assert "等待补齐输入" in output
    assert "尚未调用 LLM" in output
    assert (tmp_path / "_runtime" / "skill_sessions" / "paper-outline.json").exists()
    assert (tmp_path / "user_inputs" / "paper-outline" / "_intake.md").exists()


def test_cli_ready_skill_runtime_failure_persists_resumable_session(tmp_path: Path, capsys, monkeypatch):
    brief = tmp_path / "user_inputs" / "paper-outline" / "brief.md"
    brief.parent.mkdir(parents=True)
    brief.write_text(
        "Problem, method, available evidence, target venue, and limitations.\n" * 3,
        encoding="utf-8",
    )
    calls: list[Path] = []

    async def unavailable_runtime(args, workspace: Path):
        calls.append(workspace)
        raise RuntimeError("provider temporarily unavailable")

    monkeypatch.setattr(cli, "_prepare_runtime", unavailable_runtime)

    code = main(
        [
            "run-skill",
            "paper-outline",
            "Draft an English empirical paper.",
            "--workspace",
            str(tmp_path),
            "--skills-root",
            str(SKILLS_ROOT),
            "--session-id",
            "runtime-pause-demo",
            "--no-banner",
        ]
    )

    captured = capsys.readouterr()
    session = load_session(tmp_path, "runtime-pause-demo")
    assert code == 1
    assert calls == [tmp_path]
    assert session is not None
    assert session["status"] == "WAITING_RUNTIME"
    assert "provider temporarily unavailable" in session["last_runtime_error"]
    assert "已保留会话" in captured.err
    assert "SKILL 执行总结" in captured.out
    assert "--session-id runtime-pause-demo --resume" in captured.out
