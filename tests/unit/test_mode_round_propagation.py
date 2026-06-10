"""Mode/Round 传递完整性测试。

测试场景：
1. SingleTaskRunner 正确传递 mode 和 round 到 ExecutionContext
2. StateMachine 正确传递 mode 和 round 到 ExecutionContext
3. Agent 正确使用 ctx.mode 决定行为
4. WriterAgent 正确选择 phase
5. 多阶段状态机正确传递 extra 参数
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from researchos.agents.registry import get_agent_by_id
from researchos.agents.writer import WriterAgent
from researchos.cli_runners.single_task import SingleTaskRunner
from researchos.orchestration.state_machine import StateMachine, TaskNode
from researchos.orchestration.task_io_contract import (
    TASK_IO_CONTRACTS,
    required_input_names,
    resolve_inputs,
    resolve_outputs,
)
from researchos.runtime.agent import AgentResult, ExecutionContext
from researchos.runtime.artifact_fingerprints import write_t45_fingerprint_report
from researchos.runtime.system_config import system_config_path


def _write_t45_fingerprint_report(workspace: Path) -> None:
    """Mirror NoveltyAuditor's success hook for parse-only state-machine tests."""

    write_t45_fingerprint_report(workspace)


class TestAgentModeInitialization:
    """测试 Agent 使用不同 mode 初始化。"""

    def test_writer_agent_init_with_mode(self):
        """WriterAgent 应该接受 mode 参数。"""
        agent = WriterAgent(mode="outline")
        assert agent._mode == "outline"

    def test_writer_agent_init_without_mode(self):
        """WriterAgent 不传 mode 时应该使用默认值。"""
        agent = WriterAgent()
        assert agent._mode is None

    def test_get_agent_by_id_with_mode(self):
        """get_agent_by_id 应该支持 mode 参数。"""
        from researchos.agents.writer import WriterAgent

        agent = get_agent_by_id("writer", mode="draft")
        assert isinstance(agent, WriterAgent)
        assert agent._mode == "draft"

    def test_get_agent_by_id_without_mode(self):
        """get_agent_by_id 不传 mode 时应该使用默认值。"""
        from researchos.agents.writer import WriterAgent

        agent = get_agent_by_id("writer")
        assert isinstance(agent, WriterAgent)


class TestWriterPhaseSelection:
    """测试 WriterAgent 的 phase 选择逻辑。"""

    def test_phase_from_ctx_mode(self):
        """ctx.mode 应该被优先使用。"""
        agent = WriterAgent()

        # 创建带有 mode 的 ctx
        ctx = MagicMock(spec=ExecutionContext)
        ctx.mode = "outline"
        ctx.extra = {}

        phase = agent._phase(ctx)
        assert phase == "outline"

    def test_phase_from_extra_phase(self):
        """ctx.extra['phase'] 应该作为 fallback。"""
        agent = WriterAgent()

        ctx = MagicMock(spec=ExecutionContext)
        ctx.mode = None
        ctx.extra = {"phase": "revise"}

        phase = agent._phase(ctx)
        assert phase == "revise"

    def test_phase_from_agent_mode(self):
        """agent._mode 应该作为 fallback。"""
        agent = WriterAgent(mode="draft")

        ctx = MagicMock(spec=ExecutionContext)
        ctx.mode = None
        ctx.extra = {}

        phase = agent._phase(ctx)
        assert phase == "draft"

    def test_phase_default_fallback(self):
        """没有任何 mode 信息时应该使用默认值 'draft'。"""
        agent = WriterAgent()

        ctx = MagicMock(spec=ExecutionContext)
        ctx.mode = None
        ctx.extra = {}

        phase = agent._phase(ctx)
        assert phase == "draft"


class TestTaskNodeExtraPropagation:
    """测试 TaskNode 的 extra 字段传递。"""

    def test_node_with_mode_and_round(self):
        """节点应该正确包含 mode 和 round。"""
        node = TaskNode(
            task_id="T8-WRITE",
            agent="writer",
            mode="outline",
            round=1,
            extra={"key": "value"},
        )

        assert node.mode == "outline"
        assert node.round == 1
        assert node.extra == {"key": "value"}

    def test_extra_contains_phase_from_mode(self):
        """当节点有 mode 时，extra 应该包含 phase。"""
        node = TaskNode(
            task_id="T8-WRITE",
            agent="writer",
            mode="outline",
        )

        # 模拟 single_task._build_task_extra 的逻辑
        extra = dict(node.extra or {}) if node.extra else {}
        if node.mode is not None:
            extra.setdefault("phase", node.mode)

        assert extra.get("phase") == "outline"


class TestSingleTaskRunnerContext:
    """测试 SingleTaskRunner 的 context 构建。"""

    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def mock_llm_client(self):
        return MagicMock()

    @pytest.fixture
    def mock_tool_registry(self):
        return MagicMock()

    def test_single_task_passes_mode_to_context(self, temp_workspace, mock_llm_client, mock_tool_registry):
        """SingleTaskRunner 应该将 task_node.mode 传递给 ExecutionContext。"""
        # 创建 state_machine.yaml
        sm_config = {
            "initial_state": "T8-WRITE",
            "states": {
                "T8-WRITE": {
                    "agent": "writer",
                    "mode": "outline",
                    "round": 1,
                    "outputs": {"outline": "drafts/outline.md"},
                    "inputs": {},
                    "next_on_success": "done",
                },
            },
        }

        sm_path = temp_workspace / "state_machine.yaml"
        sm_path.write_text(yaml.dump(sm_config))

        # 创建 project.yaml
        project_path = temp_workspace / "project.yaml"
        project_data = {
            "project_id": "test-project",
            "research_direction": "Test research",
            "keywords": ["test"],
            "created_at": "2026-01-01T00:00:00Z",
            "seed_ensemble": {
                "tier1_seeds": [42],
                "tier2_seeds": [123],
                "tier3_seeds": [456],
            },
        }
        project_path.write_text(yaml.dump(project_data))

        # 创建 drafts 目录
        (temp_workspace / "drafts").mkdir(parents=True)

        # 加载 state_machine
        sm = StateMachine(sm_path)

        # 获取节点
        node = sm.nodes["T8-WRITE"]
        assert node.mode == "outline"

        # 构建 ExecutionContext（模拟 single_task 的逻辑）
        from researchos.schemas.state import StateYaml

        state = StateYaml(project_id="test-project", current_task="T8-WRITE")
        ctx = sm.build_execution_context(temp_workspace, state)

        assert ctx.mode == "outline"
        assert ctx.extra.get("phase") == "outline"
        assert ctx.extra.get("round") == 1


class TestStateMachineContextBuild:
    """测试 StateMachine.build_execution_context 的 mode/round 传递。"""

    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_build_execution_context_with_mode(self, temp_workspace):
        """build_execution_context 应该正确传递 mode。"""
        sm_config = {
            "initial_state": "T8-WRITE",
            "states": {
                "T8-WRITE": {
                    "agent": "writer",
                    "mode": "draft",
                    "round": 2,
                    "outputs": {"paper": "drafts/paper.tex"},
                    "inputs": {},
                    "next_on_success": "done",
                },
            },
        }

        sm_path = temp_workspace / "state_machine.yaml"
        sm_path.write_text(yaml.dump(sm_config))

        # 创建 project.yaml
        project_path = temp_workspace / "project.yaml"
        project_path.write_text(
            yaml.dump({
                "project_id": "test",
                "research_direction": "test",
                "keywords": ["test"],
                "created_at": "2026-01-01T00:00:00Z",
                "seed_ensemble": {
                    "tier1_seeds": [42],
                    "tier2_seeds": [123],
                    "tier3_seeds": [456],
                },
            })
        )

        # 创建 drafts 目录
        (temp_workspace / "drafts").mkdir(parents=True)

        sm = StateMachine(sm_path)
        node = sm.nodes["T8-WRITE"]

        from researchos.schemas.state import StateYaml

        state = StateYaml(project_id="test", current_task="T8-WRITE")
        ctx = sm.build_execution_context(temp_workspace, state)

        assert ctx.mode == "draft"
        assert ctx.extra.get("phase") == "draft"
        assert ctx.extra.get("round") == 2

    def test_build_execution_context_without_mode(self, temp_workspace):
        """没有 mode 时应该正常工作。"""
        sm_config = {
            "initial_state": "T1",
            "states": {
                "T1": {
                    "agent": "pi",
                    "mode": "init",
                    "outputs": {"project": "project.yaml"},
                    "inputs": {},
                    "next_on_success": "T2",
                },
                "T2": {
                    "agent": "scout",
                    "outputs": {"papers": "literature/papers_dedup.jsonl"},
                    "inputs": {},
                    "next_on_success": "done",
                },
            },
        }

        sm_path = temp_workspace / "state_machine.yaml"
        sm_path.write_text(yaml.dump(sm_config))

        sm = StateMachine(sm_path)
        node = sm.nodes["T2"]  # T2 没有 mode

        from researchos.schemas.state import StateYaml

        state = StateYaml(project_id="test", current_task="T2")
        ctx = sm.build_execution_context(temp_workspace, state)

        # T2 没有 mode，应该为 None
        assert ctx.mode is None


class TestTaskIOContractCompleteness:
    """测试 Task I/O Contract 的完整性。"""

    def test_all_states_have_inputs_outputs(self):
        """所有状态机节点应该定义 inputs 和 outputs。"""
        sm_path = system_config_path("state_machine.yaml")
        gates_path = system_config_path("gates.yaml")
        if not sm_path.exists():
            pytest.skip("state_machine.yaml not found")

        sm = StateMachine(sm_path, gates_config_path=gates_path)
        errors = []

        # HELLO 和 T1 是起始状态，可能不需要 inputs
        # 只有非 terminal 且不是起始状态的节点需要 inputs/outputs
        for task_id, node in sm.nodes.items():
            if node.terminal:
                continue
            # HELLO 和 T1 是起始状态，不需要 inputs
            if task_id in ("HELLO", "T1"):
                continue

            if not node.inputs:
                errors.append(f"{task_id}: missing inputs")
            if not node.outputs:
                errors.append(f"{task_id}: missing outputs")

        assert not errors, f"Contract validation failed:\n" + "\n".join(errors)

    def test_state_machine_contract_alignment(self):
        """状态机配置应该与 task_io_contract 对齐。"""
        sm_path = system_config_path("state_machine.yaml")
        if not sm_path.exists():
            pytest.skip("state_machine.yaml not found")

        sm = StateMachine(sm_path)

        # 检查所有有契约的 task
        for task_id in TASK_IO_CONTRACTS:
            if task_id not in sm.nodes:
                continue

            node = sm.nodes[task_id]
            contract = TASK_IO_CONTRACTS[task_id]

            # 验证 inputs 对齐
            declared_inputs = dict(node.inputs or {})
            contract_inputs = dict(contract.get("inputs", {}))

            if declared_inputs != contract_inputs:
                assert declared_inputs == contract_inputs, f"{task_id}: inputs do not match task_io_contract"

            declared_outputs = dict(node.outputs or {})
            declared_outputs.update(dict(node.optional_outputs or {}))
            contract_outputs = dict(contract.get("outputs", {}))
            assert declared_outputs == contract_outputs, f"{task_id}: outputs do not match task_io_contract"

            declared_optional = dict(node.optional_outputs or {})
            contract_optional = set(contract.get("optional_outputs", []))
            assert set(declared_optional) == contract_optional, (
                f"{task_id}: optional_outputs do not match task_io_contract"
            )

    def test_pre_t5_and_t8_required_contracts_cover_shared_artifacts(self):
        """Pre-T5/T8 single-task contracts must not silently drop shared artifacts."""

        assert "citation_edges" in TASK_IO_CONTRACTS["T2"]["outputs"]
        assert TASK_IO_CONTRACTS["T2"]["outputs"]["citation_edges"] == "literature/citation_edges.json"

        expected_required = {
            "T3.5": {"domain_map"},
            "T4": {"domain_map", "synthesis_workbench"},
            "T8": {"domain_map", "synthesis_workbench", "idea_scorecard", "writing_style"},
            "T8-RESOURCE": {"domain_map", "synthesis_workbench", "idea_scorecard", "writing_style"},
            "T8-WRITE": {"domain_map", "synthesis_workbench", "idea_scorecard", "writing_style"},
            "T8-SECTION-PLAN": {"domain_map", "synthesis_workbench", "idea_scorecard", "writing_style"},
            "T8-SEC-RELATED": {
                "domain_map",
                "synthesis_workbench",
                "idea_scorecard",
                "alignment_matrix",
            },
        }
        for task_id, names in expected_required.items():
            assert names.issubset(set(required_input_names(task_id)))


class TestMultiModeStateFlow:
    """测试多模式状态流转。"""

    @pytest.fixture
    def temp_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_t8_subtasks_all_have_modes(self, temp_workspace):
        """T8 的所有子任务应该都有正确的 mode。"""
        sm_config = {
            "initial_state": "T8-RESOURCE",
            "states": {
                "T8-RESOURCE": {
                    "agent": "writer",
                    "mode": "resource_index",
                    "outputs": {"index": "drafts/manuscript_resource_index.json"},
                    "inputs": {"project": "project.yaml"},
                    "next_on_success": "T8-WRITE",
                },
                "T8-WRITE": {
                    "agent": "writer",
                    "mode": "outline",
                    "round": 1,
                    "outputs": {"outline": "drafts/outline.md"},
                    "inputs": {"project": "project.yaml"},
                    "next_on_success": "T8-SECTION-PLAN",
                },
                "T8-SECTION-PLAN": {
                    "agent": "writer",
                    "mode": "section_plan",
                    "outputs": {"paper_state": "drafts/paper_state.json"},
                    "inputs": {"outline": "drafts/outline.md"},
                    "next_on_success": "T8-SEC-METHOD",
                },
                "T8-SEC-METHOD": {
                    "agent": "writer",
                    "mode": "section_draft",
                    "extra": {"section_id": "methodology"},
                    "outputs": {"section": "drafts/sections/methodology.tex"},
                    "inputs": {"paper_state": "drafts/paper_state.json"},
                    "next_on_success": "T8-DRAFT",
                },
                "T8-DRAFT": {
                    "agent": "writer",
                    "mode": "draft",
                    "round": 1,
                    "outputs": {"paper": "drafts/paper.tex"},
                    "inputs": {"project": "project.yaml", "outline": "drafts/outline.md"},
                    "next_on_success": "T8-REVIEW-1",
                },
                "T8-REVIEW-1": {
                    "agent": "reviewer",
                    "mode": "review",
                    "round": 1,
                    "outputs": {"review": "drafts/review_rounds/round_1.md"},
                    "inputs": {"paper": "drafts/paper.tex"},
                    "next_on_success": "T8-REVISE-1",
                },
                "T8-REVISE-1": {
                    "agent": "writer",
                    "mode": "revise",
                    "round": 1,
                    "outputs": {"paper": "drafts/paper.tex"},
                    "inputs": {"project": "project.yaml", "review": "drafts/review_rounds/round_1.md"},
                    "next_on_success": "done",
                },
            },
        }

        sm_path = temp_workspace / "state_machine.yaml"
        sm_path.write_text(yaml.dump(sm_config))

        sm = StateMachine(sm_path)

        # 验证所有 T8 子任务都有正确的 mode
        expected_modes = {
            "T8-RESOURCE": "resource_index",
            "T8-WRITE": "outline",
            "T8-SECTION-PLAN": "section_plan",
            "T8-SEC-METHOD": "section_draft",
            "T8-DRAFT": "draft",
            "T8-REVIEW-1": "review",
            "T8-REVISE-1": "revise",
        }

        for task_id, expected_mode in expected_modes.items():
            node = sm.nodes.get(task_id)
            assert node is not None, f"{task_id} not found"
            assert node.mode == expected_mode, f"{task_id} mode should be {expected_mode}, got {node.mode}"

    def test_real_t8_chain_uses_single_section_nodes(self, temp_workspace):
        """真实状态机中 T8 正文写作必须逐 section 执行，不能回退到 T8-SECTIONS。"""
        sm_path = system_config_path("state_machine.yaml")
        sm = StateMachine(sm_path)

        expected_chain = [
            ("T8-SECTION-PLAN", "T8-SEC-METHOD", None),
            ("T8-SEC-METHOD", "T8-SEC-EXPERIMENTS", "methodology"),
            ("T8-SEC-EXPERIMENTS", "T8-SEC-RELATED", "experiments"),
            ("T8-SEC-RELATED", "T8-SEC-ANALYSIS", "related_work"),
            ("T8-SEC-ANALYSIS", "T8-SEC-INTRO", "analysis"),
            ("T8-SEC-INTRO", "T8-SEC-CONCLUSION", "introduction"),
            ("T8-SEC-CONCLUSION", "T8-SEC-ABSTRACT", "conclusion"),
            ("T8-SEC-ABSTRACT", "T8-DRAFT", "abstract"),
        ]

        for task_id, next_task, section_id in expected_chain:
            node = sm.nodes[task_id]
            assert node.next_on_success == next_task
            if task_id == "T8-SECTION-PLAN":
                assert node.mode == "section_plan"
                continue
            assert node.mode == "section_draft"
            assert node.extra.get("section_id") == section_id
            assert set(node.outputs or {}) == {"section"}

        assert "T8-SECTIONS" not in [
            task_id for task_id, _, _ in expected_chain
        ]
        assert "T8-SEC-LIMITATIONS" not in sm.nodes

    def test_t75_parse_defaults_to_resource_stage(self, temp_workspace):
        """T7.5 parse fallback should enter the writing style gate first."""
        sm_config = {
            "initial_state": "T7.5",
            "states": {
                "T7.5": {
                    "agent": "pi",
                    "mode": "evaluate",
                    "inputs": {"results_summary": "experiments/results_summary.json"},
                    "outputs": {"evaluation_decision": "evaluation/evaluation_decision.md"},
                    "next_on_success": "__parse_from_output__",
                },
                "T8-STYLE-GATE": {
                    "agent": "writer",
                    "mode": "style_gate",
                    "inputs": {"project": "project.yaml"},
                    "outputs": {"style": "drafts/writing_style.json"},
                    "next_on_success": "T8-RESOURCE",
                },
                "T8-RESOURCE": {
                    "agent": "writer",
                    "mode": "resource_index",
                    "inputs": {"project": "project.yaml"},
                    "outputs": {"index": "drafts/manuscript_resource_index.json"},
                    "next_on_success": "done",
                },
                "done": {"terminal": True},
            },
        }

        sm_path = temp_workspace / "state_machine.yaml"
        sm_path.write_text(yaml.dump(sm_config))
        sm = StateMachine(sm_path)

        assert sm._parse_t75_decision(temp_workspace) == "T8-STYLE-GATE"

    def test_t75_parse_maps_legacy_t8_write_to_resource_when_available(self, temp_workspace):
        """旧评估报告写 next_task: T8-WRITE 时应先进入写作风格 gate。"""
        sm_config = {
            "initial_state": "T7.5",
            "states": {
                "T7.5": {
                    "agent": "pi",
                    "mode": "evaluate",
                    "outputs": {"evaluation_decision": "evaluation/evaluation_decision.md"},
                    "next_on_success": "__parse_from_output__",
                },
                "T8-RESOURCE": {
                    "agent": "writer",
                    "mode": "resource_index",
                    "outputs": {"index": "drafts/manuscript_resource_index.json"},
                    "next_on_success": "done",
                },
                "T8-STYLE-GATE": {
                    "agent": "writer",
                    "mode": "style_gate",
                    "outputs": {"style": "drafts/writing_style.json"},
                    "next_on_success": "T8-RESOURCE",
                },
                "T8-WRITE": {
                    "agent": "writer",
                    "mode": "outline",
                    "outputs": {"outline": "drafts/outline.md"},
                    "next_on_success": "done",
                },
                "done": {"terminal": True},
            },
        }
        (temp_workspace / "evaluation").mkdir()
        (temp_workspace / "evaluation" / "evaluation_decision.md").write_text(
            "next_task: T8-WRITE\n",
            encoding="utf-8",
        )
        sm_path = temp_workspace / "state_machine.yaml"
        sm_path.write_text(yaml.dump(sm_config))
        sm = StateMachine(sm_path)

        assert sm._parse_t75_decision(temp_workspace) == "T8-STYLE-GATE"

    def test_t75_parse_skips_style_gate_when_style_exists(self, temp_workspace):
        sm_config = {
            "initial_state": "T7.5",
            "states": {
                "T7.5": {
                    "agent": "pi",
                    "mode": "evaluate",
                    "outputs": {"evaluation_decision": "evaluation/evaluation_decision.md"},
                    "next_on_success": "__parse_from_output__",
                },
                "T8-STYLE-GATE": {
                    "agent": "writer",
                    "mode": "style_gate",
                    "outputs": {"style": "drafts/writing_style.json"},
                    "next_on_success": "T8-RESOURCE",
                },
                "T8-RESOURCE": {
                    "agent": "writer",
                    "mode": "resource_index",
                    "outputs": {"index": "drafts/manuscript_resource_index.json"},
                    "next_on_success": "done",
                },
                "done": {"terminal": True},
            },
        }
        (temp_workspace / "evaluation").mkdir()
        (temp_workspace / "evaluation" / "evaluation_decision.md").write_text("next_task: T8\n", encoding="utf-8")
        (temp_workspace / "drafts").mkdir()
        (temp_workspace / "drafts" / "writing_style.json").write_text(
            '{"venue_style":"ccf_a","template_family":"ccf","template_id":"neurips","writing_language":"en"}\n',
            encoding="utf-8",
        )
        sm_path = temp_workspace / "state_machine.yaml"
        sm_path.write_text(yaml.dump(sm_config))
        sm = StateMachine(sm_path)

        assert sm._parse_t75_decision(temp_workspace) == "T8-RESOURCE"

    def test_t75_parse_does_not_skip_style_gate_when_style_invalid(self, temp_workspace):
        sm_config = {
            "initial_state": "T7.5",
            "states": {
                "T7.5": {
                    "agent": "pi",
                    "mode": "evaluate",
                    "outputs": {"evaluation_decision": "evaluation/evaluation_decision.md"},
                    "next_on_success": "__parse_from_output__",
                },
                "T8-STYLE-GATE": {
                    "agent": "writer",
                    "mode": "style_gate",
                    "outputs": {"style": "drafts/writing_style.json"},
                    "next_on_success": "T8-RESOURCE",
                },
                "T8-RESOURCE": {
                    "agent": "writer",
                    "mode": "resource_index",
                    "outputs": {"index": "drafts/manuscript_resource_index.json"},
                    "next_on_success": "done",
                },
                "done": {"terminal": True},
            },
        }
        (temp_workspace / "evaluation").mkdir()
        (temp_workspace / "evaluation" / "evaluation_decision.md").write_text("next_task: T8\n", encoding="utf-8")
        (temp_workspace / "drafts").mkdir()
        sm_path = temp_workspace / "state_machine.yaml"
        sm_path.write_text(yaml.dump(sm_config))
        sm = StateMachine(sm_path)

        for invalid_style_text in [
            "not-json",
            '{"venue_style":"other","template_family":"ccf","template_id":"neurips","writing_language":"en"}\n',
            '{"suggested":"ccf_a"}\n',
            '{"venue_style":"ccf_a","template_family":"ccf","template_id":"auto","writing_language":"en"}\n',
        ]:
            (temp_workspace / "drafts" / "writing_style.json").write_text(invalid_style_text, encoding="utf-8")
            assert sm._parse_t75_decision(temp_workspace) == "T8-STYLE-GATE"

    def test_t45_parse_routes_final_gate_verdicts(self, temp_workspace):
        sm_config = {
            "initial_state": "T4.5",
            "states": {
                "T4": {"agent": "ideation", "outputs": {"hypotheses": "ideation/hypotheses.md"}, "next_on_success": "T4.5"},
                "T4.5": {
                    "agent": "novelty_auditor",
                    "outputs": {"novelty_audit": "ideation/novelty_audit.md"},
                    "next_on_success": "__parse_from_output__",
                    "next_on_failure": "failed",
                },
                "T4.5-HUMAN-REVIEW": {
                    "agent": "novelty_auditor",
                    "mode": "human_review",
                    "outputs": {"novelty_human_review": "ideation/novelty_human_review.json"},
                    "gate": {"type": "t45_human_review_gate"},
                },
                "T7": {"agent": "experimenter", "mode": "full", "outputs": {"results": "experiments/results_summary.json"}, "next_on_success": "done"},
                "done": {"terminal": True},
                "failed": {"terminal": True},
            },
        }
        (temp_workspace / "ideation").mkdir()
        sm_path = temp_workspace / "state_machine.yaml"
        sm_path.write_text(yaml.dump(sm_config))
        sm = StateMachine(sm_path)

        verdicts = {
            "Final Gate Verdict: pass_to_experiment\n": "T7",
            "Final Gate Verdict: pass_with_required_baselines\n": "T7",
            "Final Gate Verdict: return_to_T4_reframe\n": "T4.5-HUMAN-REVIEW",
            "Final Gate Verdict: drop_due_to_collision\n": "T4.5-HUMAN-REVIEW",
            "Final Gate Verdict: uncertain_needs_user_decision\n": "T4.5-HUMAN-REVIEW",
            "Final Gate Verdict: do_not_pass_to_experiment\n": "T4.5-HUMAN-REVIEW",
            "# Audit without explicit final verdict\n": "T4.5-HUMAN-REVIEW",
        }
        for text, expected in verdicts.items():
            (temp_workspace / "ideation" / "novelty_audit.md").write_text(text, encoding="utf-8")
            _write_t45_fingerprint_report(temp_workspace)
            assert sm._parse_t45_verdict(temp_workspace) == expected

    def test_t45_pass_routes_to_external_handoff_when_available(self, temp_workspace):
        sm_config = {
            "initial_state": "T4.5",
            "states": {
                "T4.5": {
                    "agent": "novelty_auditor",
                    "outputs": {"novelty_audit": "ideation/novelty_audit.md"},
                    "next_on_success": "__parse_from_output__",
                    "next_on_failure": "failed",
                },
                "T4.5-HUMAN-REVIEW": {
                    "agent": "novelty_auditor",
                    "mode": "human_review",
                    "outputs": {"novelty_human_review": "ideation/novelty_human_review.json"},
                    "gate": {"type": "t45_human_review_gate"},
                },
                "T5-HANDOFF": {
                    "agent": "experimenter",
                    "mode": "handoff",
                    "outputs": {"handoff_pack": "external_executor/handoff_pack.json"},
                    "next_on_success": "done",
                },
                "T7": {
                    "agent": "experimenter",
                    "mode": "full",
                    "outputs": {"results": "experiments/results_summary.json"},
                    "next_on_success": "done",
                },
                "done": {"terminal": True},
                "failed": {"terminal": True},
            },
        }
        (temp_workspace / "ideation").mkdir()
        (temp_workspace / "ideation" / "novelty_audit.md").write_text(
            "Final Gate Verdict: pass_to_experiment\n",
            encoding="utf-8",
        )
        _write_t45_fingerprint_report(temp_workspace)
        sm_path = temp_workspace / "state_machine.yaml"
        sm_path.write_text(yaml.dump(sm_config))
        sm = StateMachine(sm_path)

        assert sm._parse_t45_verdict(temp_workspace) == "T5-HANDOFF"

    def test_t45_advance_uses_final_gate_verdict(self, temp_workspace):
        sm_config = {
            "initial_state": "T4.5",
            "states": {
                "T4": {"agent": "ideation", "outputs": {"hypotheses": "ideation/hypotheses.md"}, "next_on_success": "T4.5"},
                "T4.5": {
                    "agent": "novelty_auditor",
                    "outputs": {"novelty_audit": "ideation/novelty_audit.md"},
                    "next_on_success": "__parse_from_output__",
                    "next_on_failure": "failed",
                },
                "T4.5-HUMAN-REVIEW": {
                    "agent": "novelty_auditor",
                    "mode": "human_review",
                    "outputs": {"novelty_human_review": "ideation/novelty_human_review.json"},
                    "gate": {"type": "t45_human_review_gate"},
                },
                "T7": {"agent": "experimenter", "mode": "full", "outputs": {"results": "experiments/results_summary.json"}, "next_on_success": "done"},
                "done": {"terminal": True},
                "failed": {"terminal": True},
            },
        }
        (temp_workspace / "ideation").mkdir()
        (temp_workspace / "ideation" / "novelty_audit.md").write_text(
            "# Audit\n\nFinal Gate Verdict: return_to_T4_reframe\n",
            encoding="utf-8",
        )
        sm_path = temp_workspace / "state_machine.yaml"
        sm_path.write_text(yaml.dump(sm_config))
        sm = StateMachine(sm_path)
        state = sm.start_task(sm.create_initial_state("p1"), "run_t45")
        result = AgentResult(
            ok=True,
            message="done",
            outputs_produced={},
            steps_used=1,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0,
            duration_seconds=0,
            stop_reason=AgentResult.STOP_FINISHED,
        )

        state = sm.advance(state, result, workspace_dir=temp_workspace)

        assert state.current_task == "T4.5-HUMAN-REVIEW"
        assert state.status == "RUNNING"

    def test_t75_parse_keeps_legacy_t8_write_when_resource_stage_absent(self, temp_workspace):
        """旧测试/旧配置没有 T8-RESOURCE 时仍应兼容 T8-WRITE。"""
        sm_config = {
            "initial_state": "T7.5",
            "states": {
                "T7.5": {
                    "agent": "pi",
                    "mode": "evaluate",
                    "outputs": {"evaluation_decision": "evaluation/evaluation_decision.md"},
                    "next_on_success": "__parse_from_output__",
                },
                "T8-WRITE": {
                    "agent": "writer",
                    "mode": "outline",
                    "outputs": {"outline": "drafts/outline.md"},
                    "next_on_success": "done",
                },
                "done": {"terminal": True},
            },
        }
        (temp_workspace / "evaluation").mkdir()
        (temp_workspace / "evaluation" / "evaluation_decision.md").write_text(
            "next_task: T8-WRITE\n",
            encoding="utf-8",
        )
        sm_path = temp_workspace / "state_machine.yaml"
        sm_path.write_text(yaml.dump(sm_config))
        sm = StateMachine(sm_path)

        assert sm._parse_t75_decision(temp_workspace) == "T8-WRITE"

    def test_t75_legacy_t7_recommendation_routes_to_external_handoff_when_available(self, temp_workspace):
        """旧 PI 报告写 next_task: T7 时，主链应回到外部实验 handoff，而不是 legacy 内部实验。"""
        sm_config = {
            "initial_state": "T7.5",
            "states": {
                "T7.5": {
                    "agent": "pi",
                    "mode": "evaluate",
                    "outputs": {"evaluation_decision": "evaluation/evaluation_decision.md"},
                    "next_on_success": "__parse_from_output__",
                },
                "T5-HANDOFF": {
                    "agent": "experimenter",
                    "mode": "handoff",
                    "outputs": {"handoff_pack": "external_executor/handoff_pack.json"},
                    "next_on_success": "done",
                },
                "T7": {
                    "agent": "experimenter",
                    "mode": "full",
                    "outputs": {"results": "experiments/results_summary.json"},
                    "next_on_success": "done",
                },
                "T8-STYLE-GATE": {
                    "agent": "writer",
                    "mode": "style_gate",
                    "outputs": {"writing_style": "drafts/writing_style.json"},
                    "next_on_success": "done",
                },
                "done": {"terminal": True},
            },
        }
        (temp_workspace / "evaluation").mkdir()
        (temp_workspace / "evaluation" / "evaluation_decision.md").write_text(
            "next_task: T7\n",
            encoding="utf-8",
        )
        sm_path = temp_workspace / "state_machine.yaml"
        sm_path.write_text(yaml.dump(sm_config))
        sm = StateMachine(sm_path)

        assert sm._parse_t75_decision(temp_workspace) == "T5-HANDOFF"

    def test_experimenter_modes(self, temp_workspace):
        """T5 和 T7 应该有不同的 mode。"""
        sm_config = {
            "initial_state": "T5",
            "states": {
                "T5": {
                    "agent": "experimenter",
                    "mode": "pilot",
                    "outputs": {"pilot_results": "pilot/pilot_results.json"},
                    "inputs": {},
                    "next_on_success": "T7",
                },
                "T7": {
                    "agent": "experimenter",
                    "mode": "full",
                    "outputs": {"results": "experiments/results_summary.json"},
                    "inputs": {},
                    "next_on_success": "done",
                },
            },
        }

        sm_path = temp_workspace / "state_machine.yaml"
        sm_path.write_text(yaml.dump(sm_config))

        sm = StateMachine(sm_path)

        assert sm.nodes["T5"].mode == "pilot"
        assert sm.nodes["T7"].mode == "full"


class TestStateMachineValidation:
    """测试状态机配置校验。"""

    def test_validate_definition_no_errors(self):
        """validate_definition 应该返回 0 错误。"""
        sm_path = system_config_path("state_machine.yaml")
        gates_path = system_config_path("gates.yaml")
        if not sm_path.exists():
            pytest.skip("state_machine.yaml not found")

        sm = StateMachine(sm_path, gates_config_path=gates_path)
        errors = sm.validate_definition()

        assert len(errors) == 0, f"State machine validation errors:\n" + "\n".join(errors)

    def test_t75_gate_go_write_enters_style_gate(self):
        """Manual write option must not bypass T8-STYLE-GATE."""
        gates_path = system_config_path("gates.yaml")
        data = yaml.safe_load(gates_path.read_text(encoding="utf-8")) or {}
        gate = (data.get("gates") or {}).get("t75_human_review_gate") or {}
        options = {
            str(option.get("id")): option
            for option in gate.get("options", [])
            if isinstance(option, dict)
        }
        assert options["go_write"]["next"] == "T8-STYLE-GATE"
        assert gate["presentation"]["recommended_next_task"]["from_file_regex"]["default"] == "T8-STYLE-GATE"

    def test_validate_definition_missing_initial_state(self):
        """缺少 initial_state 应该报错。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            sm_path = Path(tmpdir) / "sm.yaml"
            sm_path.write_text(yaml.dump({"states": {"T1": {}}}))

            # StateMachine 构造函数应该在这里失败
            with pytest.raises(KeyError):
                sm = StateMachine(sm_path)

    def test_validate_definition_terminal_node_no_next(self):
        """终端节点不应该报错。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            sm_path = Path(tmpdir) / "sm.yaml"
            sm_path.write_text(
                yaml.dump({
                    "initial_state": "done",
                    "states": {
                        "done": {"terminal": True},
                    },
                })
            )

            sm = StateMachine(sm_path)
            errors = sm.validate_definition()

            # 终端节点不应该有 next_on_success/next_on_failure 错误
            next_errors = [e for e in errors if "next_on_success" in e or "next_on_failure" in e]
            assert len(next_errors) == 0
