from __future__ import annotations

from pathlib import Path

import pytest

from researchos.runtime.config import RuntimeSettings
from researchos.skills.catalog import search_skills, skills_in_category
from researchos.skills.contracts import check_skill_readiness, prepare_skill_intake_packet
from researchos.skills.loader import discover_skills, load_skill
from researchos.skills.runner import run_skill
from researchos.skills.session import (
    load_session,
    record_readiness,
    record_run_result,
    render_skill_completion_panel,
    render_skill_description,
    render_skill_status_panel,
)
from researchos.testing.mocks import FakeLLMMessage, FakeRawCompletion, FakeToolCall, MockHumanInterface, MockLLMClient
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.registry import ToolRegistry


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / "skills"


@pytest.mark.asyncio
async def test_skill_run_persists_observable_progress_and_completion(tmp_path: Path):
    skill_dir = tmp_path / "hello-progress"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: hello-progress
description: progress test
tools: [write_file, finish_task]
strict_tools: true
interaction:
  mode: guided
  language: zh-CN
  summary: 写入一个测试产物并展示可恢复的运行进度。
  request_required: true
  request_prompt: 说明测试任务。
  required_inputs: []
  optional_inputs: []
  outputs:
    - id: output
      label: 测试产物
      path: output.md
      description: 用于确认运行状态和完成摘要。
outputs_expected:
  output: output.md
---
Write output.md and finish.
""",
        encoding="utf-8",
    )
    skill = load_skill(skill_dir)
    readiness = check_skill_readiness(skill_name=skill.name, metadata=skill.metadata, workspace=tmp_path, request="run")
    assert readiness.ready
    record_readiness(
        workspace=tmp_path,
        session_id="progress-demo",
        skill_name=skill.name,
        skill_path=skill_dir,
        readiness=readiness,
        resume=False,
    )
    registry = ToolRegistry()
    register_builtin_tools(registry, RuntimeSettings())
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(message=FakeLLMMessage(tool_calls=[FakeToolCall(name="write_file", arguments={"path": "output.md", "content": "ok"}, id="tc1")])),
            FakeRawCompletion(message=FakeLLMMessage(tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": "done"}, id="tc2")])),
        ]
    )
    result = await run_skill(
        skill=skill,
        user_request="run",
        workspace=tmp_path,
        tool_registry=registry,
        llm_client=llm,
        human_interface=MockHumanInterface(),
        outputs_expected={"output": tmp_path / "output.md"},
        skill_session_id="progress-demo",
    )
    assert result.ok
    mid_session = load_session(tmp_path, "progress-demo")
    assert mid_session is not None
    assert mid_session["progress"]["phase"] in {"tool_completed", "awaiting_llm", "llm_response_received"}
    assert any(turn.get("event") == "runtime_progress" for turn in mid_session["turns"])
    record_run_result(workspace=tmp_path, session_id="progress-demo", result=result, outputs_expected={"output": tmp_path / "output.md"})
    panel = render_skill_completion_panel(workspace=tmp_path, session_id="progress-demo")
    assert "SKILL 执行总结" in panel
    assert "测试产物" in panel
    assert "output.md" in panel


@pytest.mark.asyncio
async def test_guided_skill_can_request_human_followup_then_resume_same_run(tmp_path: Path):
    skill_dir = tmp_path / "followup-demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: followup-demo
description: follow-up protocol test
tools: [write_file, finish_task]
strict_tools: true
interaction:
  mode: guided
  language: zh-CN
  summary: 验证同一运行内的资料补充交互。
  request_required: true
  request_prompt: 说明任务。
  required_inputs: []
  optional_inputs: []
  outputs:
    - id: output
      label: 结果
      path: output.md
      description: 人工补充后生成的结果。
outputs_expected:
  output: output.md
---
When evidence is missing, write the follow-up request, ask the human, then write output.md.
""",
        encoding="utf-8",
    )
    skill = load_skill(skill_dir)
    readiness = check_skill_readiness(skill_name=skill.name, metadata=skill.metadata, workspace=tmp_path, request="run")
    intake = prepare_skill_intake_packet(readiness)
    record_readiness(
        workspace=tmp_path,
        session_id="followup-demo",
        skill_name=skill.name,
        skill_path=skill_dir,
        readiness=readiness,
        resume=False,
        intake_packet_path=intake,
    )
    registry = ToolRegistry()
    register_builtin_tools(registry, RuntimeSettings())
    llm = MockLLMClient(
        responses=[
            FakeRawCompletion(message=FakeLLMMessage(tool_calls=[FakeToolCall(name="write_file", arguments={"path": "user_inputs/followup-demo/_followup_request.md", "content": "# Need\n\nPlease provide one verified result."}, id="followup")])),
            FakeRawCompletion(message=FakeLLMMessage(tool_calls=[FakeToolCall(name="ask_human", arguments={"question": "Please provide the verified result."}, id="human")])),
            FakeRawCompletion(message=FakeLLMMessage(tool_calls=[FakeToolCall(name="write_file", arguments={"path": "output.md", "content": "Result uses the human-provided material."}, id="output")])),
            FakeRawCompletion(message=FakeLLMMessage(tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": "completed after human follow-up"}, id="finish")])),
        ]
    )
    human = MockHumanInterface(clarification_answer="Verified result is available in results.csv.")

    result = await run_skill(
        skill=skill,
        user_request="run",
        workspace=tmp_path,
        tool_registry=registry,
        llm_client=llm,
        human_interface=human,
        outputs_expected={"output": tmp_path / "output.md"},
        skill_session_id="followup-demo",
        skill_session_path="_runtime/skill_sessions/followup-demo.json",
        workspace_mode="standalone",
        intake_packet_path=str(intake.relative_to(tmp_path)) if intake else "",
    )

    assert result.ok
    assert (tmp_path / "user_inputs" / "followup-demo" / "_followup_request.md").exists()
    assert (tmp_path / "output.md").exists()
    assert any(kind == "clarification" for kind, _ in human.calls)


def test_skill_description_prefers_chinese_interaction_summary(tmp_path: Path):
    skill_dir = tmp_path / "catalog-summary"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        """---
name: catalog-summary
description: English metadata used by discovery.
interaction:
  mode: guided
  language: zh-CN
  summary: 中文优先的用户用途说明。
  request_required: false
  required_inputs: []
  optional_inputs: []
  outputs: []
---
No-op.
""",
        encoding="utf-8",
    )
    skill = load_skill(skill_dir)
    panel = render_skill_description(
        skill_name=skill.name,
        skill_path=skill.skill_dir,
        description=skill.description,
        interaction=check_skill_readiness(
            skill_name=skill.name,
            metadata=skill.metadata,
            workspace=tmp_path,
            request="",
        ).interaction,
    )
    assert "用途：中文优先的用户用途说明。" in panel
    assert "技术范围：English metadata used by discovery." in panel


def test_skill_catalog_search_and_category_filter_follow_workflow_metadata():
    skills = discover_skills(SKILLS_ROOT).values()

    citation_matches = search_skills(skills, "citation")
    writing_matches = skills_in_category(skills, "论文写作")

    assert any(skill.name == "citation-provenance-audit" for skill in citation_matches)
    assert {skill.name for skill in writing_matches} >= {"paper-outline", "paper-write"}


def test_skill_status_panel_shows_observable_phase_and_recovery(tmp_path: Path):
    running_path = tmp_path / "_runtime" / "skill_sessions" / "running.json"
    paused_path = tmp_path / "_runtime" / "skill_sessions" / "paused.json"
    running_path.parent.mkdir(parents=True)
    running = {
        "session_id": "running",
        "skill_name": "paper-write",
        "status": "RUNNING",
        "request": "起草英文论文。",
        "progress": {
            "step": 4,
            "step_limit": 36,
            "phase": "tool_running",
            "tool_name": "build_section_evidence_supplement",
            "detail": "正在回查 introduction 所需笔记 section。",
        },
    }
    paused = {
        "session_id": "paused",
        "skill_name": "survey-visuals",
        "status": "WAITING_RUNTIME",
        "progress": {"phase": "waiting_runtime", "detail": "matplotlib unavailable"},
    }

    panel = render_skill_status_panel(
        workspace=tmp_path,
        entries=[(running_path, running), (paused_path, paused)],
    )

    assert "状态：运行中" in panel
    assert "步骤 4/36 · 正在执行工具" in panel
    assert "当前工具：build_section_evidence_supplement" in panel
    assert "状态：等待运行环境恢复" in panel
    assert "--session-id paused --resume" in panel
