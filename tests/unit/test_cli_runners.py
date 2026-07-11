from __future__ import annotations

import json
from pathlib import Path
import textwrap

import pytest
import yaml

from researchos.agents.hello import HelloAgent
from researchos.cli import PreparedRuntime, build_parser, main
from researchos.cli_runners import CompletePipelineRunner, SingleTaskRunner
from researchos.orchestration.state_machine import StateMachine
from researchos.runtime.agent import AgentResult
from researchos.runtime.environment import workspace_host_hint
from researchos.schemas.state import StateYaml, TaskHistoryEntry
from researchos.testing.mocks import (
    FakeLLMMessage,
    FakeRawCompletion,
    FakeToolCall,
    MockLLMClient,
)
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.human_gate import HumanInputUnavailable, HumanInterface
from researchos.tools.registry import ToolRegistry
from tests.unit.test_runner_basic import _write_t4_stage_visibility_artifacts


def _write_t8_section_plan_inputs(workspace: Path) -> None:
    drafts = workspace / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    (workspace / "project.yaml").write_text("target_venue: neurips\n", encoding="utf-8")
    (drafts / "outline.md").write_text(
        "# Outline\n\n## Introduction\nFrame.\n\n## Method\nMethod.\n\n## Experiments\nResults.\n",
        encoding="utf-8",
    )
    (drafts / "manuscript_resource_index.json").write_text(
        '{"version":"1.0","bib_keys":["smith2024"],"result_metrics":[{"metric":"acc","value":0.8}]}\n',
        encoding="utf-8",
    )
    sections = [
        "abstract",
        "introduction",
        "related_work",
        "methodology",
        "experiments",
        "analysis",
        "conclusion",
    ]
    (drafts / "section_plan.json").write_text(
        "{"
        + '"version":"1.0","sections":['
        + ",".join(
            (
                '{"id":"'
                + section
                + '","required_inputs":[],"available_inputs":[],"missing_inputs":[],"expected_outputs":["section"]}'
            )
            for section in sections
        )
        + "]}\n",
        encoding="utf-8",
    )
    (drafts / "evidence_plan.json").write_text('{"version":"1.0","claim_slots":[]}\n', encoding="utf-8")
    (drafts / "figure_table_plan.json").write_text('{"version":"1.0","planned_visuals":[]}\n', encoding="utf-8")
    (drafts / "alignment_matrix.json").write_text(
        '{"version":"1.0","semantics":"alignment_matrix_seed_not_final_scientific_judgment","rows":[{"cid":"C1"}]}\n',
        encoding="utf-8",
    )
    (drafts / "paper_state.json").write_text('{"semantics":"old_invalid_state","sections":{}}\n', encoding="utf-8")


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _write_t2_source_workspace(source: Path) -> None:
    (source / "user_seeds" / "pdfs").mkdir(parents=True)
    (source / "literature").mkdir(parents=True)
    (source / "project.yaml").write_text("project_id: copied-project\n", encoding="utf-8")
    (source / "user_seeds" / "seed_papers.jsonl").write_text(
        '{"title":"Seed Paper"}\n',
        encoding="utf-8",
    )
    (source / "user_seeds" / "seed_constraints.md").write_text("constraints\n", encoding="utf-8")
    (source / "user_seeds" / "seed_ideas.md").write_text("ideas\n", encoding="utf-8")
    (source / "user_seeds" / "seed_external_resources.jsonl").write_text("", encoding="utf-8")
    (source / "user_seeds" / "seed_outline_profile.json").write_text(
        '{"semantics":"user_seed_outline_profile","manuscript_type":"survey"}\n',
        encoding="utf-8",
    )
    (source / "user_seeds" / "pdfs" / "seed.pdf").write_bytes(b"%PDF")
    (source / "literature" / "bridge_domain_plan.json").write_text(
        '{"source":"none","bridge_domains":[]}\n',
        encoding="utf-8",
    )
    StateYaml(
        project_id="copied-project",
        current_task="T2",
        status="PAUSED",
        history=[
            TaskHistoryEntry(
                task="T1",
                run_id="t1_done",
                status="DONE",
                started_at="2026-01-01T00:00:00+00:00",
                finished_at="2026-01-01T00:01:00+00:00",
            ),
            TaskHistoryEntry(
                task="T2",
                run_id="t2_old",
                status="INTERRUPTED",
                started_at="2026-01-01T00:02:00+00:00",
                finished_at="2026-01-01T00:03:00+00:00",
            ),
        ],
    ).dump_yaml(source / "state.yaml")


def _write_t3_source_workspace(source: Path) -> None:
    _write_t2_source_workspace(source)
    (source / "literature" / "papers_dedup.jsonl").write_text(
        '{"id":"paper1","title":"Paper 1"}\n',
        encoding="utf-8",
    )
    (source / "literature" / "papers_verified.jsonl").write_text(
        '{"id":"paper1","title":"Paper 1","verification_status":"metadata_verified"}\n',
        encoding="utf-8",
    )
    (source / "literature" / "papers_backlog.jsonl").write_text(
        '{"id":"paper2","title":"Paper 2"}\n',
        encoding="utf-8",
    )
    (source / "literature" / "deep_read_queue.jsonl").write_text(
        '{"paper_id":"paper1","title":"Paper 1","queue_rank":1}\n',
        encoding="utf-8",
    )
    (source / "literature" / "domain_map.json").write_text(
        '{"semantics":"domain_map_for_synthesis_and_ideation_not_final_gaps"}\n',
        encoding="utf-8",
    )
    (source / "literature" / "access_audit.md").write_text("# Access Audit\n", encoding="utf-8")
    (source / "literature" / "missing_areas.md").write_text("# Missing Areas\n", encoding="utf-8")
    (source / "literature" / "paper_notes").mkdir(parents=True)
    (source / "literature" / "paper_notes" / "old.md").write_text("# Old Note\n", encoding="utf-8")


def _hello_llm() -> MockLLMClient:
    """构造一组能驱动 HelloAgent 完成任务的 mock LLM 响应。"""

    return MockLLMClient(
        responses=[
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="write_file",
                            arguments={"path": "hello.txt", "content": "Hello, Runtime!"},
                            id="tc_write",
                        )
                    ]
                )
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="finish_task",
                            arguments={"summary": "hello finished"},
                            id="tc_finish",
                        )
                    ]
                )
            ),
        ]
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


class _UnavailableGateHuman(HumanInterface):
    async def ask_approval(self, *, tool_name: str, arguments: dict) -> bool:
        return False

    async def ask_clarification(self, *, question: str, suggestions: list[str] | None = None) -> str:
        raise HumanInputUnavailable("stdin closed")

    async def present_gate(self, *, gate_id: str, presentation: dict, options: list[dict]) -> dict:
        raise HumanInputUnavailable("stdin closed")


class _AutoGateHuman(HumanInterface):
    def __init__(self, option_id: str = "go") -> None:
        self.option_id = option_id
        self.gates: list[str] = []

    async def ask_approval(self, *, tool_name: str, arguments: dict) -> bool:
        return False

    async def ask_clarification(self, *, question: str, suggestions: list[str] | None = None) -> str:
        return ""

    async def present_gate(self, *, gate_id: str, presentation: dict, options: list[dict]) -> dict:
        self.gates.append(gate_id)
        return {"option_id": self.option_id, "captured": {}}


def test_single_task_runner_t36_alias_points_to_survey_gate():
    assert SingleTaskRunner._normalize_task_id("T3.6") == "T3.6-GATE-SURVEY"
    assert SingleTaskRunner._normalize_task_id("T3.6-SURVEY") == "T3.6-GATE-SURVEY"
    assert SingleTaskRunner._normalize_task_id("SURVEY") == "T3.6-GATE-SURVEY"


def test_single_task_runner_retires_plain_legacy_experiment_tasks():
    with pytest.raises(ValueError, match="retired"):
        SingleTaskRunner._normalize_task_id("T7")
    with pytest.raises(ValueError, match="requires --allow-legacy"):
        SingleTaskRunner._normalize_task_id("LEGACY-T7-FULL")
    assert SingleTaskRunner._normalize_task_id("LEGACY-T7-FULL", allow_legacy=True) == "T7"


@pytest.mark.asyncio
async def test_single_task_runner_runs_hello_happy_path(tmp_workspace: Path):
    runner = SingleTaskRunner(
        workspace=tmp_workspace,
        task_id="HELLO",
        llm_client=_hello_llm(),
        tool_registry=_registry(),
    )

    exit_code = await runner.run()

    assert exit_code == 0
    assert (tmp_workspace / "hello.txt").read_text(encoding="utf-8") == "Hello, Runtime!"
    state_text = (tmp_workspace / "state.yaml").read_text(encoding="utf-8")
    assert "COMPLETED" in state_text
    assert "DONE" in state_text


def test_hello_agent_can_read_runtime_resume_state():
    agent = HelloAgent()

    assert "_runtime/resume/" in agent.spec.allowed_read_prefixes


@pytest.mark.asyncio
async def test_complete_pipeline_runner_advances_until_completed(tmp_workspace: Path):
    config = tmp_workspace / "fsm.yaml"
    _write_yaml(
        config,
        """
        initial_state: HELLO
        states:
          HELLO:
            agent: hello
            outputs:
              hello_file: hello.txt
            next_on_success: done
          done:
            terminal: true
        """,
    )
    runner = CompletePipelineRunner(
        workspace=tmp_workspace,
        state_machine=StateMachine(config),
        llm_client=_hello_llm(),
        tool_registry=_registry(),
    )

    exit_code = await runner.run(project_id="demo-project")

    assert exit_code == 0
    assert (tmp_workspace / "state.yaml").exists()
    assert "COMPLETED" in (tmp_workspace / "state.yaml").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_complete_pipeline_pauses_when_pending_gate_input_unavailable(tmp_workspace: Path):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: GATE
        states:
          GATE:
            agent: hello
            extra:
              immediate_gate: true
            gate: gate1
          done:
            terminal: true
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          gate1:
            options:
              - id: go
                label: Go
                next: done
        """,
    )
    state = StateYaml(project_id="demo-project", current_task="GATE", status="WAITING_HUMAN")
    state.pending_gate = StateMachine(config, gates).pause_for_immediate_gate(state, workspace_dir=tmp_workspace).pending_gate
    state.dump_yaml(tmp_workspace / "state.yaml")
    runner = CompletePipelineRunner(
        workspace=tmp_workspace,
        state_machine=StateMachine(config, gates),
        llm_client=_hello_llm(),
        tool_registry=_registry(),
        human_interface=_UnavailableGateHuman(),
    )

    exit_code = await runner.run(project_id="demo-project", resume=True)

    assert exit_code == 130
    state_after = StateYaml.load_yaml(tmp_workspace / "state.yaml")
    assert state_after.status == "PAUSED"
    assert "stdin closed" in (state_after.last_error or "")


@pytest.mark.asyncio
async def test_complete_pipeline_fast_forwards_t4_gate1_when_selection_file_exists(tmp_workspace: Path):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: T4-GATE1
        states:
          T4-GATE1:
            agent: ideation
            extra:
              immediate_gate: true
            gate: t4_gate1_selection_gate
            outputs:
              gate1_user_selection: ideation/_gate1_user_selection.json
            next_on_success: T4
          T4:
            terminal: true
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          t4_gate1_selection_gate:
            presentation:
              cards:
                from_file: ideation/_gate1_candidate_cards.md
            options:
              - id: select_or_reframe
                label: Select
                next: T4
        """,
    )
    (tmp_workspace / "ideation").mkdir(parents=True, exist_ok=True)
    _write_t4_stage_visibility_artifacts(tmp_workspace / "ideation")
    sm = StateMachine(config, gates)
    state = sm.pause_for_immediate_gate(sm.create_initial_state("demo-project"), workspace_dir=tmp_workspace)
    stale_pending_gate = state.pending_gate
    state = sm.resolve_pending_gate(
        state,
        {"option_id": "select_or_reframe", "captured": {"selection": "D1"}},
        workspace_dir=tmp_workspace,
    )
    state.current_task = "T4-GATE1"
    state.status = "WAITING_HUMAN"
    state.pending_gate = stale_pending_gate
    state.dump_yaml(tmp_workspace / "state.yaml")
    human = _UnavailableGateHuman()
    runner = CompletePipelineRunner(
        workspace=tmp_workspace,
        state_machine=sm,
        llm_client=_hello_llm(),
        tool_registry=_registry(),
        human_interface=human,
    )

    state_after = await runner._run_one_step(StateYaml.load_yaml(tmp_workspace / "state.yaml"), tmp_workspace / "state.yaml")

    assert state_after.status == "COMPLETED"
    assert state_after.current_task == "T4"
    assert state_after.pending_gate is None
    persisted = StateYaml.load_yaml(tmp_workspace / "state.yaml")
    assert persisted.current_task == "T4"
    assert persisted.pending_gate is None
    assert not (tmp_workspace / "hello.txt").exists()


@pytest.mark.asyncio
async def test_complete_pipeline_presents_immediate_gate_without_prior_exit(tmp_workspace: Path):
    config = tmp_workspace / "fsm.yaml"
    gates = tmp_workspace / "gates.yaml"
    _write_yaml(
        config,
        """
        initial_state: GATE
        states:
          GATE:
            agent: hello
            extra:
              immediate_gate: true
            gate: gate1
          done:
            terminal: true
        """,
    )
    _write_yaml(
        gates,
        """
        gates:
          gate1:
            options:
              - id: go
                label: Go
                next: done
        """,
    )
    human = _AutoGateHuman("go")
    runner = CompletePipelineRunner(
        workspace=tmp_workspace,
        state_machine=StateMachine(config, gates),
        llm_client=_hello_llm(),
        tool_registry=_registry(),
        human_interface=human,
    )

    exit_code = await runner.run(project_id="demo-project")

    assert exit_code == 0
    assert human.gates == ["gate1"]
    state_after = StateYaml.load_yaml(tmp_workspace / "state.yaml")
    assert state_after.status == "COMPLETED"
    assert state_after.current_task == "done"


def test_cli_run_task_command_dispatches(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    observed: dict[str, object] = {}

    async def fake_prepare_runtime(args, workspace_dir):
        observed["workspace"] = workspace_dir
        return PreparedRuntime(
            skill_roots=[],
            registry=ToolRegistry(),
            llm_client=object(),
        )

    async def fake_run(self):
        observed["task_id"] = self.task_id
        observed["from_workspace"] = self.from_workspace
        observed["profile"] = self.override_profile
        return 0

    monkeypatch.setattr("researchos.cli.install_signal_handlers", lambda: None)
    monkeypatch.setattr("researchos.cli._prepare_runtime", fake_prepare_runtime)
    monkeypatch.setattr("researchos.cli.SingleTaskRunner.run", fake_run)

    exit_code = main(
        [
            "--no-banner",
            "--workspace",
            str(workspace),
            "run-task",
            "HELLO",
            "--profile",
            "audit",
        ]
    )

    assert exit_code == 0
    assert observed["workspace"] == workspace.resolve()
    assert observed["task_id"] == "HELLO"
    assert observed["from_workspace"] is None
    assert observed["profile"] == "audit"


def test_cli_run_from_start_task_initializes_pipeline_state(monkeypatch, tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    _write_t2_source_workspace(source)

    async def fake_prepare_runtime(args, workspace_dir):
        return PreparedRuntime(
            skill_roots=[],
            registry=ToolRegistry(),
            llm_client=object(),
        )

    async def fake_run(self, *, project_id: str, resume: bool = False):
        state = StateYaml.load_yaml(self.workspace / "state.yaml")
        assert state.current_task == "T2"
        assert state.status == "RUNNING"
        assert [entry.task for entry in state.history] == ["T1"]
        assert (self.workspace / "project.yaml").read_text(encoding="utf-8") == "project_id: copied-project\n"
        assert (self.workspace / "user_seeds" / "seed_papers.jsonl").read_text(encoding="utf-8") == '{"title":"Seed Paper"}\n'
        assert (self.workspace / "user_seeds" / "pdfs" / "seed.pdf").exists()
        assert (self.workspace / "literature" / "bridge_domain_plan.json").exists()
        return 0

    monkeypatch.setattr("researchos.cli.install_signal_handlers", lambda: None)
    monkeypatch.setattr("researchos.cli._prepare_runtime", fake_prepare_runtime)
    monkeypatch.setattr("researchos.cli.CompletePipelineRunner.run", fake_run)

    exit_code = main(
        [
            "--no-banner",
            "--workspace",
            str(workspace),
            "run",
            "--from",
            str(source),
            "--start-task",
            "T2",
        ]
    )

    assert exit_code == 0


def test_cli_run_from_defaults_to_t2(monkeypatch, tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    _write_t2_source_workspace(source)

    async def fake_prepare_runtime(args, workspace_dir):
        return PreparedRuntime(
            skill_roots=[],
            registry=ToolRegistry(),
            llm_client=object(),
        )

    async def fake_run(self, *, project_id: str, resume: bool = False):
        state = StateYaml.load_yaml(self.workspace / "state.yaml")
        assert state.current_task == "T2"
        assert (self.workspace / "user_seeds" / "seed_papers.jsonl").exists()
        assert not (self.workspace / "literature" / "papers_raw.jsonl").exists()
        return 0

    monkeypatch.setattr("researchos.cli.install_signal_handlers", lambda: None)
    monkeypatch.setattr("researchos.cli._prepare_runtime", fake_prepare_runtime)
    monkeypatch.setattr("researchos.cli.CompletePipelineRunner.run", fake_run)

    exit_code = main(
        [
            "--no-banner",
            "--workspace",
            str(workspace),
            "run",
            "--from",
            str(source),
        ]
    )

    assert exit_code == 0


def test_cli_run_smoke_writes_small_params_and_medium_overrides(monkeypatch, tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    _write_t2_source_workspace(source)

    observed: dict[str, object] = {}

    async def fake_prepare_runtime(args, workspace_dir):
        observed["prepare_workspace"] = workspace_dir
        return PreparedRuntime(
            skill_roots=[],
            registry=ToolRegistry(),
            llm_client=object(),
        )

    async def fake_run(self, *, project_id: str, resume: bool = False):
        state = StateYaml.load_yaml(self.workspace / "state.yaml")
        assert state.current_task == "T2"
        params = json.loads((self.workspace / "literature" / "literature_params.json").read_text(encoding="utf-8"))
        confirmation = json.loads(
            (self.workspace / "literature" / "literature_params_confirmation.json").read_text(encoding="utf-8")
        )
        assert params["selected_option"] == "smoke"
        assert params["t2_finalize"]["active_pool_max"] == 18
        assert params["reader"]["deep_read_target"] == 2
        assert params["reader"]["abstract_sweep"]["lite_paper_num"] == 4
        assert confirmation["confirmed_to_start_t2"] is True
        assert self.state_machine.nodes["T2"].llm["tier"] == "medium"
        assert self.state_machine.nodes["T3"].llm["tier"] == "medium"
        return 0

    monkeypatch.setattr("researchos.cli.install_signal_handlers", lambda: None)
    monkeypatch.setattr("researchos.cli._prepare_runtime", fake_prepare_runtime)
    monkeypatch.setattr("researchos.cli.CompletePipelineRunner.run", fake_run)

    exit_code = main(
        [
            "--no-banner",
            "--workspace",
            str(workspace),
            "run_smoke",
            "--from",
            str(source),
            "--active-pool-max",
            "18",
            "--deep-read-target",
            "2",
            "--abstract-sweep",
            "4",
            "--skip-startup-selftest",
        ]
    )

    assert exit_code == 0
    assert observed["prepare_workspace"] == workspace.resolve()


def test_cli_run_smoke_uses_topic_as_research_direction(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "project.yaml").write_text(
        "project_id: smoke-topic\n"
        "topic: graph uplift modeling smoke\n",
        encoding="utf-8",
    )

    async def fake_prepare_runtime(args, workspace_dir):
        return PreparedRuntime(
            skill_roots=[],
            registry=ToolRegistry(),
            llm_client=object(),
        )

    async def fake_run(self, *, project_id: str, resume: bool = False):
        project = yaml.safe_load((self.workspace / "project.yaml").read_text(encoding="utf-8"))
        assert project["research_direction"] == "graph uplift modeling smoke"
        return 0

    monkeypatch.setattr("researchos.cli.install_signal_handlers", lambda: None)
    monkeypatch.setattr("researchos.cli._prepare_runtime", fake_prepare_runtime)
    monkeypatch.setattr("researchos.cli.CompletePipelineRunner.run", fake_run)

    exit_code = main(
        [
            "--no-banner",
            "--workspace",
            str(workspace),
            "run_smoke",
            "--active-pool-max",
            "12",
            "--deep-read-target",
            "2",
            "--abstract-sweep",
            "2",
            "--skip-startup-selftest",
        ]
    )

    assert exit_code == 0


def test_cli_run_smoke_quiet_keeps_copy_and_state_output_silent(monkeypatch, tmp_path: Path, capsys):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    _write_t2_source_workspace(source)

    async def fake_prepare_runtime(args, workspace_dir):
        return PreparedRuntime(
            skill_roots=[],
            registry=ToolRegistry(),
            llm_client=object(),
        )

    async def fake_run(self, *, project_id: str, resume: bool = False):
        return 0

    monkeypatch.setattr("researchos.cli.install_signal_handlers", lambda: None)
    monkeypatch.setattr("researchos.cli._prepare_runtime", fake_prepare_runtime)
    monkeypatch.setattr("researchos.cli.CompletePipelineRunner.run", fake_run)

    exit_code = main(
        [
            "--no-banner",
            "--quiet",
            "--workspace",
            str(workspace),
            "run_smoke",
            "--from",
            str(source),
            "--active-pool-max",
            "18",
            "--deep-read-target",
            "2",
            "--abstract-sweep",
            "4",
            "--skip-startup-selftest",
        ]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "copied:" not in out
    assert "[进度] 已初始化 pipeline state" not in out
    assert "[Smoke] 已写入快速联调参数" not in out
    assert "[Smoke] start_task=T2, tier=medium" in out


def test_cli_run_from_start_task_t3_copies_t3_inputs_not_old_notes(monkeypatch, tmp_path: Path):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    _write_t3_source_workspace(source)

    async def fake_prepare_runtime(args, workspace_dir):
        return PreparedRuntime(
            skill_roots=[],
            registry=ToolRegistry(),
            llm_client=object(),
        )

    async def fake_run(self, *, project_id: str, resume: bool = False):
        state = StateYaml.load_yaml(self.workspace / "state.yaml")
        assert state.current_task == "T3"
        assert (self.workspace / "project.yaml").exists()
        assert (self.workspace / "literature" / "papers_dedup.jsonl").exists()
        assert (self.workspace / "literature" / "deep_read_queue.jsonl").exists()
        assert (self.workspace / "literature" / "domain_map.json").exists()
        assert (self.workspace / "user_seeds" / "seed_outline_profile.json").exists()
        assert not (self.workspace / "literature" / "paper_notes" / "old.md").exists()
        assert not (self.workspace / "literature" / "comparison_table.csv").exists()
        assert not (self.workspace / "literature" / "related_work.bib").exists()
        return 0

    monkeypatch.setattr("researchos.cli.install_signal_handlers", lambda: None)
    monkeypatch.setattr("researchos.cli._prepare_runtime", fake_prepare_runtime)
    monkeypatch.setattr("researchos.cli.CompletePipelineRunner.run", fake_run)

    exit_code = main(
        [
            "--no-banner",
            "--workspace",
            str(workspace),
            "run",
            "--from",
            str(source),
            "--start-task",
            "T3",
        ]
    )

    assert exit_code == 0


def test_cli_run_from_refuses_existing_target_state(tmp_path: Path, capsys):
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    _write_t2_source_workspace(source)
    workspace.mkdir()
    StateYaml(project_id="existing", current_task="T3", status="PAUSED").dump_yaml(workspace / "state.yaml")

    exit_code = main(
        [
            "--no-banner",
            "--workspace",
            str(workspace),
            "run",
            "--from",
            str(source),
            "--start-task",
            "T2",
            "--skip-startup-selftest",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "目标 workspace 已存在 state.yaml" in captured.out
    state = StateYaml.load_yaml(workspace / "state.yaml")
    assert state.current_task == "T3"


def test_cli_run_task_plain_t7_reports_retired(monkeypatch, tmp_path: Path, capsys):
    workspace = tmp_path / "workspace"

    async def fake_prepare_runtime(args, workspace_dir):
        return PreparedRuntime(
            skill_roots=[],
            registry=ToolRegistry(),
            llm_client=object(),
        )

    monkeypatch.setattr("researchos.cli.install_signal_handlers", lambda: None)
    monkeypatch.setattr("researchos.cli._prepare_runtime", fake_prepare_runtime)

    exit_code = main(
        [
            "--no-banner",
            "--workspace",
            str(workspace),
            "run-task",
            "T7",
        ]
    )

    assert exit_code == 2
    assert "retired" in capsys.readouterr().out


def test_cli_validate_repairs_t8_section_plan_state(tmp_path: Path):
    workspace = tmp_path / "workspace"
    _write_t8_section_plan_inputs(workspace)

    exit_code = main(
        [
            "--no-banner",
            "--workspace",
            str(workspace),
            "validate",
            "--task",
            "T8-SECTION-PLAN",
        ]
    )

    assert exit_code == 0
    state_text = (workspace / "drafts" / "paper_state.json").read_text(encoding="utf-8")
    assert "shared_state_for_section_by_section_writing_not_final_claims" in state_text


def test_cli_init_workspace_creates_standard_tree(tmp_path: Path):
    workspace = tmp_path / "workspace"

    exit_code = main(
        [
            "--no-banner",
            "--workspace",
            str(workspace),
            "--project-id",
            "demo-init",
            "init-workspace",
            "--topic",
            "runtime verification",
        ]
    )

    assert exit_code == 0
    assert (workspace / "_runtime" / "traces").exists()
    assert (workspace / "literature" / "paper_notes").exists()
    assert (workspace / "project.yaml").exists()


def test_cli_init_workspace_accepts_shared_options_after_subcommand(tmp_path: Path):
    workspace = tmp_path / "workspace"

    exit_code = main(
        [
            "--no-banner",
            "init-workspace",
            "--workspace",
            str(workspace),
            "--project-id",
            "demo-init",
            "--topic",
            "runtime verification",
        ]
    )

    assert exit_code == 0
    assert (workspace / "_runtime" / "traces").exists()
    assert (workspace / "project.yaml").exists()
    assert (workspace / "_runtime" / "runtime_environment.json").exists()


def test_cli_doctor_allows_missing_docker_and_tex(tmp_path: Path, monkeypatch, capsys):
    workspace = tmp_path / "doctor-ws"
    monkeypatch.setattr("researchos.runtime.environment.shutil.which", lambda _name: None)
    monkeypatch.setattr("researchos.cli.shutil.which", lambda _name: None)

    exit_code = main(["--no-banner", "doctor", "--workspace", str(workspace)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[OK" in captured.out
    assert "Docker: CLI not found" in captured.out
    assert "LaTeX backend" in captured.out
    assert (workspace / "_runtime" / "runtime_environment.json").exists()


def test_researchos_workspace_root_overrides_parser_default(tmp_path: Path, monkeypatch):
    root = tmp_path / "workspaces"
    monkeypatch.setenv("RESEARCHOS_WORKSPACE_ROOT", str(root))

    parser = build_parser()
    args = parser.parse_args(["doctor"])

    assert args.workspace == str(root)


def test_workspace_host_hint_maps_container_root_and_projects(monkeypatch):
    monkeypatch.setenv("RESEARCHOS_HOST_WORKSPACE_ROOT", "/host/researchos/workspaces")
    monkeypatch.setenv("RESEARCHOS_WORKSPACE_ROOT", "/app/workspaces")

    assert workspace_host_hint(Path("/app/workspaces")) == "/host/researchos/workspaces"
    assert (
        workspace_host_hint(Path("/app/workspaces/project-a"))
        == "/host/researchos/workspaces/project-a"
    )


def test_cli_trace_renders_human_readable_output(tmp_path: Path, capsys):
    workspace = tmp_path / "workspace"
    trace_dir = workspace / "_runtime" / "traces"
    trace_dir.mkdir(parents=True)
    (trace_dir / "demo.jsonl").write_text(
        "\n".join(
            [
                '{"seq":1,"ts":"2026-01-01T00:00:00+00:00","type":"run_start","payload":{"run_id":"demo","agent_name":"hello","project_id":"p1","task_id":"HELLO","workspace_dir":"/tmp/ws"}}',
                '{"seq":2,"ts":"2026-01-01T00:00:01+00:00","type":"message","payload":{"role":"assistant","content":"done","step":1,"metadata":{}}}',
                '{"seq":3,"ts":"2026-01-01T00:00:02+00:00","type":"run_end","payload":{"ok":true,"stop_reason":"finished","steps_used":1}}',
            ]
        ),
        encoding="utf-8",
    )

    exit_code = main(["--workspace", str(workspace), "trace", "demo"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "RUN START" in captured.out
    assert "RUN END" in captured.out


def test_single_task_runner_injects_resume_extra_from_failed_history(tmp_workspace: Path):
    runner = SingleTaskRunner(
        workspace=tmp_workspace,
        task_id="T3",
        llm_client=_hello_llm(),
        tool_registry=_registry(),
    )
    state = StateYaml(project_id="demo-project", current_task="T3")
    state.history.append(
        TaskHistoryEntry(
            task="T3",
            run_id="T3_single_prev",
            status="FAILED",
            started_at="2026-01-01T00:00:00Z",
        )
    )

    extra: dict[str, object] = {}
    runner._inject_resume_extra(extra, state)

    assert extra["is_resume"] is True
    assert extra["resumed_from_run_id"] == "T3_single_prev"
    assert extra["resume_reason"] == "retry_after_failure"


def test_single_task_record_finished_preserves_recoverable_stop_reason(tmp_workspace: Path):
    runner = SingleTaskRunner(
        workspace=tmp_workspace,
        task_id="T3",
        llm_client=_hello_llm(),
        tool_registry=_registry(),
    )
    state = StateYaml(project_id="demo-project", current_task="T3")
    state = runner._record_started(state, "T3_single_run")
    result = AgentResult(
        ok=False,
        message="max steps",
        outputs_produced={},
        steps_used=10,
        tokens_in=11,
        tokens_out=12,
        cost_usd=0.0,
        duration_seconds=1.0,
        stop_reason=AgentResult.STOP_MAX_STEPS,
        error="Reached maximum allowed steps; paused so you can resume.",
    )

    state = runner._record_finished(state, result)

    assert state.status == "PAUSED"
    assert state.history[-1].status == "INTERRUPTED"
    assert state.history[-1].stop_reason == AgentResult.STOP_MAX_STEPS
    assert state.history[-1].tokens == 23
    assert state.last_error == result.error
