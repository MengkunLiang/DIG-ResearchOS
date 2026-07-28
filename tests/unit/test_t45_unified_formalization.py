from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import time

import yaml

from researchos.agents.research_formalizer import ResearchFormalizerAgent
from researchos.ideation.formalization import (
    T45_SELECTION_ISOLATION_REL_PATH,
    T45_REPAIRABLE_WARNING_PREFIX,
    collect_t45_semantic_errors,
    collect_t45_quality_diagnostics,
    ensure_current_t45_selection_isolation,
    format_t45_repairable_quality_warnings,
    reset_t45_artifacts_for_new_selection,
    legacy_t45_upgrade_reason,
    validate_claims_markdown,
    validate_blueprint_and_claim_registry,
    validate_t45_selection_isolation,
    validate_t45_formalization_core,
)
from researchos.ideation.proposal import validate_t45_research_proposal
from researchos.ideation.t45_semantic_adjudication import (
    accepted_t45_semantic_errors,
    persist_t45_semantic_adjudication,
    semantic_adjudication_scope,
)
from researchos.ideation.prompt_composer import compose_t4_role_prompt
from researchos.orchestration.state_machine import StateMachine
from researchos.orchestration.task_io_contract import task_import_paths
from researchos.runtime.agent import Agent, AgentSpec, ExecutionContext, resolve_effective_config
from researchos.runtime.orchestrator import AgentRunner
from researchos.runtime.config import RuntimeSettings
from researchos.runtime.message import Message, ToolCall
from researchos.runtime.observability.extractors import extract_stage_insights
from researchos.runtime.prompts import get_prompt_env
from researchos.schemas.state import GateState, StateYaml
from researchos.testing.mocks import FakeLLMMessage, FakeRawCompletion, FakeToolCall, MockHumanInterface, MockLLMClient
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.external_experiment import _build_reboost_pack
from researchos.tools.registry import ToolRegistry
from tests.unit.t45_unified_fixture import populate_valid_t45_workspace, write, write_yaml


def _proposal_path(workspace: Path) -> Path:
    return workspace / "ideation" / "proposal" / "research_proposal.md"


def test_t45_insights_treats_null_orientation_review_as_pending(tmp_path: Path) -> None:
    """An incomplete T4.5 review must not crash the post-Gate stage display."""

    ideation = tmp_path / "ideation"
    ideation.mkdir()
    write(ideation / "novelty_audit.md", "# Novelty Audit\n")
    write_yaml(
        ideation / "research_blueprint.yaml",
        {
            "orientation": {"profile_type": "hybrid"},
            "technical_problem": {"key_challenges": [{"id": "C1"}]},
            "research_claims": {"active_claim_ids": ["TC1"]},
        },
    )
    write_yaml(ideation / "claim_registry.yaml", {"claims": []})
    write(ideation / "orientation_review.json", "null\n")

    insights = extract_stage_insights("T4.5-FORMALIZE", tmp_path)

    formalization = next(item for item in insights if item["title"] == "Research Formalization")
    assert ("Review", "pending") in formalization["rows"]
    assert ("Orientation", "hybrid") in formalization["rows"]


def test_formalizer_uses_explicit_chinese_research_facing_language(tmp_path: Path) -> None:
    """T4.5 prose language is independent from the eventual manuscript language."""

    populate_valid_t45_workspace(tmp_path)
    project_path = tmp_path / "project.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project.setdefault("metadata", {})["formalization_language"] = "zh"
    project["research_direction"] = "From Copilot to Crutch: Dynamic Fading Script Scaffolding for AI advice"
    write_yaml(project_path, project)
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="formalizer-zh",
        task_id="T4.5-FORMALIZE",
        run_id="formalizer-zh-run",
        mode="formalize",
    )

    prompt = ResearchFormalizerAgent(mode="formalize").system_prompt(ctx)

    assert "formalization language: zh" in prompt
    assert "所有研究者可读的正文和结构化字段值均使用中文" in prompt
    assert "# 研究主张与假设" in prompt
    assert "From Copilot to Crutch" in prompt
    assert "English Canonical Title（简洁中文释义）" in prompt
    assert "Pilot" in prompt
    assert "From Copilot to Crutch（从辅助到依赖）" in prompt
    assert "Academic writing and terminology discipline" in prompt
    assert "Expand every non-obvious acronym at its first occurrence" in prompt
    assert "Full English Name（简洁中文释义，ABBR）" in prompt
    assert "never expect a reader to infer an acronym from a component ID" in prompt


def test_formalizer_defaults_to_chinese_when_the_project_brief_is_chinese(tmp_path: Path) -> None:
    """An unconfigured Chinese workspace should not silently produce English-only formalization."""

    populate_valid_t45_workspace(tmp_path)
    project_path = tmp_path / "project.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project["research_direction"] = "研究生成式 AI（Generative AI）建议如何影响长期能力。"
    project.pop("formalization_language", None)
    project["metadata"] = {}
    write_yaml(project_path, project)
    # The automatic Chinese default applies only before a new formalization
    # has researcher-facing prose; an accepted existing package keeps its
    # historic language unless the project explicitly overrides it.
    (tmp_path / "ideation" / "hypotheses.md").unlink()
    (tmp_path / "ideation" / "proposal" / "research_proposal.md").unlink()
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="formalizer-zh-default",
        task_id="T4.5-FORMALIZE",
        run_id="formalizer-zh-default-run",
        mode="formalize",
    )

    prompt = ResearchFormalizerAgent(mode="formalize").system_prompt(ctx)

    assert "formalization language: zh" in prompt
    assert "所有研究者可读的正文和结构化字段值均使用中文" in prompt


def test_t4_role_prompts_preserve_canonical_english_names_in_chinese_explanations() -> None:
    """All researcher-facing T4 roles share the bilingual naming guardrail."""

    system, _ = compose_t4_role_prompt(
        prompt_name="idea_interaction_reviewer.j2",
        role_contract="Return only the required JSON.",
        rendered_task="{}",
        payload={},
        target_profile=None,
    )
    assert "Research-Facing Chinese Naming" in system
    assert "English canonical term（中文释义）" in system
    assert "From Copilot to Crutch" in system
    assert "concise Chinese interpretation" in system
    assert "Dynamic Fading Script Scaffolding" in system

    compiler_template = get_prompt_env().get_template("idea_final_card_compiler.j2").render(payload_json="{}")
    repair_template = get_prompt_env().get_template("idea_final_card_semantic_repair.j2").render(payload_json="{}")
    assert "English Canonical Title（简洁中文释义）" in compiler_template
    assert "mechanically translate a metaphorical English title" in compiler_template
    assert "English Canonical Title（简洁中文释义）" in repair_template


def test_formalizer_does_not_rewrite_valid_structured_sources_before_prose(tmp_path: Path) -> None:
    """A resume with a valid contract should move forward to prose, not loop on YAML."""

    populate_valid_t45_workspace(tmp_path)
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="formalizer-prose-only",
        task_id="T4.5-FORMALIZE",
        run_id="formalizer-prose-only-run",
        mode="formalize",
    )

    message = ResearchFormalizerAgent(mode="formalize").initial_user_message(ctx)

    assert "最新 `valid` 结果是本轮唯一权威状态" in message
    assert "若 valid=true，不得再重写已通过的 research_blueprint、claim_registry 与 exp_plan" in message
    assert "runtime 会从通过验证的 source artifacts 确定性编译它们" in message
    assert "validate_t45_formalization_sources" in ResearchFormalizerAgent(mode="formalize").spec.tool_names


def test_formalizer_prompt_treats_live_structured_validation_as_authoritative(tmp_path: Path) -> None:
    """A startup diagnostic must not keep the model in a stale YAML-repair branch."""

    populate_valid_t45_workspace(tmp_path)
    blueprint_path = tmp_path / "ideation" / "research_blueprint.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    blueprint["proposed_approach"]["alternatives_considered"] = []
    write_yaml(blueprint_path, blueprint)
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="formalizer-live-checkpoint",
        task_id="T4.5-FORMALIZE",
        run_id="formalizer-live-checkpoint-run",
        mode="formalize",
    )

    prompt = ResearchFormalizerAgent(mode="formalize").system_prompt(ctx)

    assert "Initial formalization diagnostic" in prompt
    assert "opening snapshot, not a permanent instruction" in prompt
    assert "Live structured-source checkpoint and delivery protocol" in prompt
    assert "latest `validate_t45_formalization_sources` result supersedes it" in prompt
    assert "If the initial snapshot and the latest tool result disagree, trust the latest" in prompt


def test_formalizer_exposes_safe_edit_compatibility_for_prose_only(tmp_path: Path) -> None:
    """A familiar prose edit cannot become an unknown-tool retry in T4.5."""

    agent = ResearchFormalizerAgent(mode="review")
    assert agent.spec.allow_edit_file_compatibility is True

    registry = ToolRegistry()
    register_builtin_tools(registry)
    # The compatibility tool delegates to WriteFileTool, so its availability
    # cannot bypass the schema guard for YAML or JSON sources.
    registry.grant_dynamic_tools(["edit_file"], allowed_agents=["research_formalizer"])
    runner = AgentRunner(
        agent,
        registry,
        MockLLMClient([]),
        MockHumanInterface(),
        RuntimeSettings(),
    )
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="formalizer-tool-contract",
        task_id="T4.5-REVIEW",
        run_id="formalizer-tool-contract-run",
        mode="review",
    )

    tool_names = runner._resolve_run_tool_names(resolve_effective_config(agent.spec, ctx))

    assert "edit_file" in tool_names
    assert {"write_file", "write_structured_file", "validate_t45_formalization_sources"} <= set(tool_names)
    prompt = agent.system_prompt(ctx)
    assert "compatible\n`edit_file`" in prompt


def test_t45_llm_semantic_adjudication_breaks_a_keyword_false_negative(tmp_path: Path) -> None:
    """A quoted independent judgment can pass one ambiguous prose rule."""

    populate_valid_t45_workspace(tmp_path)
    proposal = _proposal_path(tmp_path)
    proposal.write_text(
        proposal.read_text(encoding="utf-8").replace("Central Insight:", "Key design premise:"),
        encoding="utf-8",
    )
    error = (
        "Proposal does not state a readable central insight in Proposed Approach and Design Rationale "
        "(use Central Insight, Core Insight, 核心洞见, or 核心洞察)"
    )
    quote = (
        "Key design premise: a representation-aware estimator and a diagnostic feedback component "
        "should jointly expose the source of transport error before an intervention is ranked."
    )
    llm = MockLLMClient(
        [
            _finish_response("request review validation"),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    content=json.dumps(
                        {
                            "verdict": "satisfied",
                            "validator_error": error,
                            "artifact": "ideation/proposal/research_proposal.md",
                            "evidence": [
                                {
                                    "quote": quote,
                                    "explanation": "The opening premise states the causal design insight before COMP1 and COMP2 are introduced.",
                                }
                            ],
                            "reason": "The Proposal uses a natural synonymous heading and immediately explains the central design premise.",
                        }
                    )
                )
            ),
        ]
    )
    registry = ToolRegistry()
    register_builtin_tools(registry)
    runner = AgentRunner(
        ResearchFormalizerAgent(mode="review"),
        registry,
        llm,
        MockHumanInterface(),
        RuntimeSettings(),
    )
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="semantic-adjudication",
        task_id="T4.5-REVIEW",
        run_id="semantic-adjudication-run",
        mode="review",
    )

    result = asyncio.run(runner.run(ctx))

    assert result.ok is True, result.error or result.message
    assert llm.call_count == 2
    assert error in accepted_t45_semantic_errors(tmp_path)


def test_t45_llm_semantic_adjudication_reviews_all_current_prose_candidates_once(tmp_path: Path) -> None:
    """One final review adjudicates a coherent set instead of serial repair loops."""

    populate_valid_t45_workspace(tmp_path)
    proposal = _proposal_path(tmp_path)
    text = proposal.read_text(encoding="utf-8")
    text = text.replace("Central Insight:", "Key design premise:")
    text = text.replace("A simpler alternative", "A lean comparator")
    proposal.write_text(text, encoding="utf-8")
    blueprint_path = tmp_path / "ideation" / "research_blueprint.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    blueprint["proposed_approach"]["alternatives_considered"][0]["alternative"] = "静态固定方案甲乙丙丁"
    write_yaml(blueprint_path, blueprint)
    central_error = (
        "Proposal does not state a readable central insight in Proposed Approach and Design Rationale "
        "(use Central Insight, Core Insight, 核心洞见, or 核心洞察)"
    )
    alternative_error = "Proposal does not explain the simpler alternative from research_blueprint.yaml and why it is insufficient"
    assert collect_t45_semantic_errors(tmp_path) == [central_error, alternative_error]
    llm = MockLLMClient(
        [
            _finish_response("request batch semantic validation"),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    content=json.dumps(
                        {
                            "decisions": [
                                {
                                    "verdict": "satisfied",
                                    "validator_error": central_error,
                                    "artifact": "ideation/proposal/research_proposal.md",
                                    "evidence": [
                                        {
                                            "quote": "Key design premise: a representation-aware estimator and a diagnostic feedback component should jointly expose the source of transport error before an intervention is ranked.",
                                            "explanation": "The opening premise states the design insight before the component descriptions.",
                                        }
                                    ],
                                    "reason": "The wording is a clear scholarly synonym for the central insight.",
                                },
                                {
                                    "verdict": "satisfied",
                                    "validator_error": alternative_error,
                                    "artifact": "ideation/proposal/research_proposal.md",
                                    "evidence": [
                                        {
                                            "quote": "A lean comparator that applies a static model without these components cannot distinguish contextual shift from a genuine treatment effect.",
                                            "explanation": "The Proposal identifies a simpler comparator and the limitation that motivates the proposed design.",
                                        }
                                    ],
                                    "reason": "The comparison is substantive even though it does not use the literal alternative label.",
                                },
                            ]
                        }
                    )
                )
            ),
        ]
    )
    registry = ToolRegistry()
    register_builtin_tools(registry)
    runner = AgentRunner(
        ResearchFormalizerAgent(mode="review"),
        registry,
        llm,
        MockHumanInterface(),
        RuntimeSettings(),
    )
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="semantic-adjudication-batch",
        task_id="T4.5-REVIEW",
        run_id="semantic-adjudication-batch-run",
        mode="review",
    )

    result = asyncio.run(runner.run(ctx))

    assert result.ok is True, result.error or result.message
    assert llm.call_count == 2
    accepted = accepted_t45_semantic_errors(tmp_path)
    assert {central_error, alternative_error} <= accepted


def test_t45_semantic_adjudication_expires_when_its_source_changes(tmp_path: Path) -> None:
    """A receipt cannot authorize a later edit to the Proposal or blueprint."""

    populate_valid_t45_workspace(tmp_path)
    error = "Proposal does not explain the simpler alternative from research_blueprint.yaml and why it is insufficient"
    proposal = _proposal_path(tmp_path)
    quote = "A simpler alternative that applies a static model without these components cannot distinguish contextual shift from a genuine treatment effect."
    persist_t45_semantic_adjudication(
        tmp_path,
        validator_error=error,
        artifact="ideation/proposal/research_proposal.md",
        requirement="proposal_argument_semantics",
        evidence=[{"quote": quote, "explanation": "The Proposal names the simpler static alternative and its insufficiency."}],
        adjudicator_reason="Quoted prose directly supplies the requested design comparison.",
        model="mock-model",
    )

    assert error in accepted_t45_semantic_errors(tmp_path)
    proposal.write_text(proposal.read_text(encoding="utf-8") + "\nChanged source.\n", encoding="utf-8")
    assert error not in accepted_t45_semantic_errors(tmp_path)


def test_t45_hard_validation_errors_are_never_semantically_adjudicable() -> None:
    """LLM fallback cannot waive structured, evidence, or anti-quality contracts."""

    for error in (
        "Missing required structured artifact: ideation/exp_plan.yaml",
        "T4.5 derivatives require a passing Final Gate Verdict in novelty_audit.md",
        "Experiment plan has no experiment mapped to active claims: TC1",
        "research_proposal.md repeats the same sentence or near-identical sentence blocks instead of developing the argument",
        "hypotheses.md leaks internal novelty-audit labels into researcher-facing claims",
    ):
        assert semantic_adjudication_scope(error) is None


def test_t45_depth_warning_is_internal_prompt_guidance(tmp_path: Path) -> None:
    """Depth heuristics should guide repair without masquerading as missing artifacts."""

    populate_valid_t45_workspace(tmp_path)
    write(tmp_path / "ideation/hypotheses.md", "# Research Claims and Hypotheses\n\nbrief draft\n")
    diagnostics = collect_t45_quality_diagnostics(tmp_path)
    warning = format_t45_repairable_quality_warnings(diagnostics)
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="formalizer-quality-guidance",
        task_id="T4.5-FORMALIZE",
        run_id="formalizer-quality-guidance-run",
        mode="formalize",
    )

    assert any(item["code"] == "claims_depth" and item["severity"] == "repair" for item in diagnostics)
    assert warning is not None and warning.startswith(T45_REPAIRABLE_WARNING_PREFIX)
    assert AgentRunner._is_t45_repairable_warning(warning) is True
    assert "内部质量修订目标" in AgentRunner._validation_repair_feedback(ctx=ctx, error=warning)
    assert "Internal quality refinement targets" in ResearchFormalizerAgent(mode="formalize").system_prompt(ctx)


class _RepeatedT45RepairAgent(Agent):
    """A scripted Formalizer used to exercise the runtime recovery loop."""

    def __init__(self) -> None:
        super().__init__(
            AgentSpec(
                name="research_formalizer",
                model_tier="heavy",
                tool_names=["write_file", "finish_task"],
                allowed_read_prefixes=[""],
                allowed_write_prefixes=["ideation/"],
                # Deliberately low: the T4.5-specific loop must not use it.
                max_validation_retries=1,
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        return "Test only: repair the declared T4.5 source artifact."

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        return "Run the scripted T4.5 quality-gate repair test."

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        proposal = ctx.workspace_dir / "ideation" / "proposal" / "research_proposal.md"
        if "repair iteration 6" in proposal.read_text(encoding="utf-8"):
            return True, None
        return False, "research_proposal.md is too short for hybrid proposal (120/1900 words)"


class _EditCompatibilityAgent(Agent):
    """Agent fixture that deliberately uses the common legacy edit name."""

    def __init__(self) -> None:
        super().__init__(
            AgentSpec(
                name="edit-compatibility-test",
                model_tier="light",
                tool_names=["write_file", "finish_task"],
                allowed_read_prefixes=["ideation/"],
                allowed_write_prefixes=["ideation/"],
            )
        )

    def system_prompt(self, ctx: ExecutionContext) -> str:
        return "Test the policy-bound edit_file compatibility tool."

    def initial_user_message(self, ctx: ExecutionContext) -> str:
        return "Replace exactly one known text fragment, then finish."

    def validate_outputs(self, ctx: ExecutionContext) -> tuple[bool, str | None]:
        path = ctx.workspace_dir / "ideation" / "compatibility.md"
        if path.read_text(encoding="utf-8") == "after\n":
            return True, None
        return False, "edit_file compatibility replacement was not applied"


def _finish_response(summary: str) -> FakeRawCompletion:
    return FakeRawCompletion(
        message=FakeLLMMessage(
            tool_calls=[FakeToolCall(name="finish_task", arguments={"summary": summary})]
        )
    )


def _proposal_write_response(iteration: int, *, content: str | None = None) -> FakeRawCompletion:
    return FakeRawCompletion(
        message=FakeLLMMessage(
            tool_calls=[
                FakeToolCall(
                    name="write_file",
                    arguments={
                        "path": "ideation/proposal/research_proposal.md",
                        "content": content if content is not None else f"repair iteration {iteration}",
                    },
                )
            ]
        )
    )


def test_write_capable_agent_can_use_safe_edit_file_compatibility(tmp_path: Path) -> None:
    """A model's familiar edit call must not become an unknown-tool retry."""

    target = tmp_path / "ideation" / "compatibility.md"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    llm = MockLLMClient(
        [
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="edit_file",
                            arguments={
                                "path": "ideation/compatibility.md",
                                "old_string": "before",
                                "new_string": "after",
                            },
                        )
                    ]
                )
            ),
            _finish_response("exact replacement complete"),
        ]
    )
    registry = ToolRegistry()
    register_builtin_tools(registry)
    runner = AgentRunner(
        _EditCompatibilityAgent(),
        registry,
        llm,
        MockHumanInterface(),
        RuntimeSettings(),
    )

    result = asyncio.run(
        runner.run(
            ExecutionContext(
                workspace_dir=tmp_path,
                project_id="edit-compatibility",
                task_id="T1",
                run_id="edit-compatibility-run",
            )
        )
    )

    assert result.ok is True, result.error or result.message
    assert target.read_text(encoding="utf-8") == "after\n"


def test_one_unified_template_accepts_all_three_orientations(tmp_path: Path) -> None:
    for orientation in ("ccf_a", "utd", "hybrid"):
        workspace = tmp_path / orientation
        populate_valid_t45_workspace(workspace, orientation=orientation)
        ok, error = validate_t45_research_proposal(workspace, workspace / "ideation" / "novelty_audit.md")
        assert ok is True, f"{orientation}: {error}"
        proposal = _proposal_path(workspace).read_text(encoding="utf-8")
        assert proposal.count("## ") == 7


def test_new_candidate_selection_archives_prior_t45_package(tmp_path: Path) -> None:
    """A second Gate1 choice must never leave the first plan active."""

    populate_valid_t45_workspace(tmp_path)
    prior_proposal = (tmp_path / "ideation" / "proposal" / "research_proposal.md").read_text(encoding="utf-8")

    context = reset_t45_artifacts_for_new_selection(
        tmp_path,
        candidate_id="D2",
        selection_fingerprint="selection-d2",
    )

    assert context["status"] == "pending_novelty_audit"
    assert not (tmp_path / "ideation" / "novelty_audit.md").exists()
    assert not (tmp_path / "ideation" / "hypotheses.md").exists()
    assert not (tmp_path / "ideation" / "research_blueprint.yaml").exists()
    assert not (tmp_path / "ideation" / "proposal" / "research_proposal.md").exists()
    archive_root = tmp_path / context["archive"]["root"]
    assert (archive_root / "ideation" / "proposal" / "research_proposal.md").read_text(encoding="utf-8") == prior_proposal
    assert (archive_root / "ideation" / "post_novelty_formalization.json").is_file()

    write(
        tmp_path / "ideation" / "selected" / "selected_candidate.json",
        json.dumps(
            {
                "candidate_id": "D2",
                "selection_fingerprint": "selection-d2",
                "candidate": {"id": "D2"},
            }
        ),
    )
    isolation = json.loads((tmp_path / T45_SELECTION_ISOLATION_REL_PATH).read_text(encoding="utf-8"))
    assert isolation["candidate_id"] == "D2"
    assert isolation["selection_fingerprint"] == "selection-d2"
    assert validate_t45_selection_isolation(tmp_path, require_accepted=False) == (True, None)
    assert validate_t45_selection_isolation(tmp_path, require_accepted=True)[0] is False


def test_resume_isolation_keeps_new_structured_sources_but_archives_old_prose(tmp_path: Path) -> None:
    """A paused current formalization must not lose its already-written sources."""

    populate_valid_t45_workspace(tmp_path)
    selected_path = tmp_path / "ideation" / "selected" / "selected_candidate.json"
    selected_path.write_text(
        json.dumps(
            {
                "candidate_id": "D2",
                "selection_fingerprint": "selection-d2",
                "candidate": {"id": "D2"},
            }
        ),
        encoding="utf-8",
    )
    selection_time = time.time() + 10
    os.utime(selected_path, (selection_time, selection_time))
    for rel_path in (
        "ideation/research_blueprint.yaml",
        "ideation/claim_registry.yaml",
        "ideation/exp_plan.yaml",
    ):
        path = tmp_path / rel_path
        source_time = selection_time + 10
        os.utime(path, (source_time, source_time))

    context = ensure_current_t45_selection_isolation(tmp_path)

    assert context is not None
    assert context["candidate_id"] == "D2"
    assert (tmp_path / "ideation/research_blueprint.yaml").is_file()
    assert (tmp_path / "ideation/claim_registry.yaml").is_file()
    assert (tmp_path / "ideation/exp_plan.yaml").is_file()
    assert not (tmp_path / "ideation/hypotheses.md").exists()
    assert not (tmp_path / "ideation/proposal" / "research_proposal.md").exists()
    assert not (tmp_path / "ideation/post_novelty_formalization.json").exists()


def test_formalizer_injects_the_current_structured_repair_target(tmp_path: Path) -> None:
    """A resumed Formalizer should repair the named source, not restart T4.5."""

    populate_valid_t45_workspace(tmp_path)
    blueprint_path = tmp_path / "ideation" / "research_blueprint.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    blueprint["proposed_approach"]["design_rationales"] = []
    write_yaml(blueprint_path, blueprint)
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="formalizer-repair-target",
        task_id="T4.5-FORMALIZE",
        run_id="formalizer-repair-target-run",
        mode="formalize",
    )

    agent = ResearchFormalizerAgent(mode="formalize")
    prompt = agent.system_prompt(ctx)
    message = agent.initial_user_message(ctx)

    assert "Initial formalization diagnostic" in prompt
    assert "research_blueprint.yaml" in prompt
    assert "design rationale" in prompt
    assert "latest `validate_t45_formalization_sources` result supersedes it" in prompt
    assert "最新 `valid` 结果是本轮唯一权威状态" in message
    assert "最小同步集合" in message


def test_short_heading_complete_proposal_fails_with_substantive_reason(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    write(
        _proposal_path(tmp_path),
        "# Research Proposal\n\n"
        "## Research Motivation and Core Problem\nshort\n\n"
        "## Prior Research, Gap and Key Challenges\nshort\n\n"
        "## Proposed Approach and Design Rationale\nshort\n\n"
        "## Research Questions, Claims and Hypotheses\nshort\n\n"
        "## Research Design and Evaluation\nshort\n\n"
        "## Expected Contributions and Implications\nshort\n\n"
        "## Risks, Limitations and Execution Plan\nshort\n",
    )
    ok, error = validate_t45_research_proposal(tmp_path, tmp_path / "ideation" / "novelty_audit.md")
    assert ok is False
    assert "too short" in (error or "") or "fragments" in (error or "")


def test_proposal_accepts_chinese_core_insight_before_components(tmp_path: Path) -> None:
    """Natural Chinese `核心洞察` must not loop as a missing central insight."""

    populate_valid_t45_workspace(tmp_path)
    proposal = _proposal_path(tmp_path)
    proposal.write_text(
        proposal.read_text(encoding="utf-8").replace("Central Insight:", "核心洞察："),
        encoding="utf-8",
    )

    ok, error = validate_t45_research_proposal(tmp_path, tmp_path / "ideation" / "novelty_audit.md")

    assert ok is True, error


def test_proposal_rejects_core_insight_after_component_detail(tmp_path: Path) -> None:
    """The ordering contract must be real, rather than a heading keyword check."""

    populate_valid_t45_workspace(tmp_path)
    proposal = _proposal_path(tmp_path)
    text = proposal.read_text(encoding="utf-8")
    text = text.replace("Central Insight: ", "", 1)
    text = text.replace(
        "COMP1 learns a task-conditioned representation for C1",
        "COMP1 learns a task-conditioned representation for C1. Central Insight: ",
        1,
    )
    proposal.write_text(text, encoding="utf-8")

    ok, error = validate_t45_research_proposal(tmp_path, tmp_path / "ideation" / "novelty_audit.md")

    assert ok is False
    assert "after the first technical component" in (error or "")


def test_evaluation_requires_each_named_evidence_check(tmp_path: Path) -> None:
    """One Chinese keyword cannot satisfy every evaluation requirement."""

    populate_valid_t45_workspace(tmp_path)
    proposal = _proposal_path(tmp_path)
    text = proposal.read_text(encoding="utf-8")
    text = text.replace(
        "Mechanism validation tests the COMP2 pathway; an ablation removes COMP1 and a separate ablation removes COMP2. Robustness evaluates distribution shift and policy variation.",
        "机制检验保留。",
    )
    proposal.write_text(text, encoding="utf-8")

    ok, error = validate_t45_research_proposal(tmp_path, tmp_path / "ideation" / "novelty_audit.md")

    assert ok is False
    assert "baselines" in (error or "") or "ablations" in (error or "")


def test_repetitive_or_audit_dominated_proposal_fails(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    path = _proposal_path(tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + "\nT4.5 Level 0 true_collision. T4.5 Level 0 true_collision.\n", encoding="utf-8")
    ok, error = validate_t45_research_proposal(tmp_path, tmp_path / "ideation" / "novelty_audit.md")
    assert ok is False
    assert "audit-dominated" in (error or "")


def test_removed_h1_cannot_satisfy_active_claim_contract(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    write(
        tmp_path / "ideation" / "hypotheses.md",
        "# Research Claims and Hypotheses\n\n### H1 [removed]\nThis claim was dropped after an internal audit and is not active.\n",
    )
    ok, error = validate_t45_formalization_core(tmp_path)
    assert ok is False
    assert "TC1" in (error or "") or "too short" in (error or "")


def test_claim_markdown_keeps_level_three_fields_inside_level_two_claim() -> None:
    """Chinese researcher-facing claim fields must not be parsed as a new claim."""

    registry = {"claims": [{"id": "DP1"}]}
    text = """# 研究主张与假设

## DP1：动态渐退式话术脚手架设计原则

### 主张
渐退式建议应当在计划性 AI 移除后提高主播的独立销售转化率，并且相对静态稀疏建议仍有增量收益。

### 理由
静态稀疏格式只能降低对完整脚本的依赖，却不能检验逐步撤除支持是否促进独立策略生成。

### 机制
完整脚本、目标理由和关键词提示依次减少外部认知卸载，促使主播检索并重组自身的情境知识。

### 预期观察
渐退组在无 AI 阶段优于静态稀疏组，且主动改写比率在 AI 辅助期末更高。

### 评测
以三臂随机实验比较渐退、持续完整脚本和静态稀疏条件，并预先注册主要结果和非劣效检验。

### 竞争解释
若高质量内容的被动暴露足以解释学习，静态稀疏组应与渐退组表现等价。

### 证伪条件
若渐退组在 AI 移除后的表现不高于静态稀疏组，或即时绩效显著下降，则该设计主张不成立。

## MC1：下一项主张

### 主张
此处是独立 claim，不应进入 DP1 区块。
"""

    assert validate_claims_markdown(text, registry=registry, orientation={}) is None


def test_claim_without_experiment_mapping_and_component_test_fails(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    plan_path = tmp_path / "ideation" / "exp_plan.yaml"
    plan = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    plan["experiments"] = [plan["experiments"][0]]
    write_yaml(plan_path, plan)
    ok, error = validate_t45_formalization_core(tmp_path)
    assert ok is False
    assert "MC1" in (error or "")

    populate_valid_t45_workspace(tmp_path)
    blueprint_path = tmp_path / "ideation" / "research_blueprint.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    blueprint["evaluation"]["ablations"] = [{"component_id": "COMP1", "planned_test": "Remove COMP1 only."}]
    blueprint["evaluation"]["mechanism_tests"] = [{"component_id": "COMP1", "planned_test": "Test COMP1 only."}]
    write_yaml(blueprint_path, blueprint)
    ok, error = validate_t45_formalization_core(tmp_path)
    assert ok is False
    assert "COMP2" in (error or "")


def test_orientation_specific_failure_modes_are_blocked(tmp_path: Path) -> None:
    ccf = tmp_path / "ccf"
    populate_valid_t45_workspace(ccf, orientation="ccf_a")
    proposal = _proposal_path(ccf)
    proposal.write_text(proposal.read_text(encoding="utf-8").replace("algorithmic model", "narrative description").replace("algorithm and diagnostic system artifact", "application paragraph"), encoding="utf-8")
    # The validator must still reject lack of explicit method rationale when
    # the component/alternative language is removed.
    proposal.write_text(proposal.read_text(encoding="utf-8").replace("A simpler alternative", "A conventional option").replace("cannot distinguish", "is not described"), encoding="utf-8")
    ok, error = validate_t45_research_proposal(ccf, ccf / "ideation" / "novelty_audit.md")
    assert ok is False
    assert "alternative" in (error or "") or "computational" in (error or "")

    utd = tmp_path / "utd"
    populate_valid_t45_workspace(utd, orientation="utd")
    text = _proposal_path(utd).read_text(encoding="utf-8")
    before, tail = text.split("## Proposed Approach and Design Rationale\n", 1)
    _old_approach, after = tail.split("## Research Questions, Claims and Hypotheses\n", 1)
    text = (
        before
        + "## Proposed Approach and Design Rationale\n"
        + "Central Insight: call an existing LLM API for the task. COMP1 and COMP2 are labels for the same API call. "
        + "A simpler alternative is not discussed beyond reusing the API. "
        + " ".join(
            f"API-only approach statement {index} deliberately omits learned structure, a training objective, optimization, inference design, and a new technical artifact."
            for index in range(1, 9)
        )
        + "\n\n## Research Questions, Claims and Hypotheses\n"
        + after
    )
    _proposal_path(utd).write_text(text, encoding="utf-8")
    ok, error = validate_t45_research_proposal(utd, utd / "ideation" / "novelty_audit.md")
    assert ok is False
    assert (
        "LLM/API" in (error or "")
        or "technical artifact" in (error or "")
        or "simpler alternative" in (error or "")
    )

    chinese_actor = tmp_path / "chinese-actor"
    populate_valid_t45_workspace(chinese_actor, orientation="hybrid")
    blueprint_path = chinese_actor / "ideation" / "research_blueprint.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    blueprint["core_problem"]["affected_actors"] = ["Live-streaming sales anchors"]
    write_yaml(blueprint_path, blueprint)
    proposal = _proposal_path(chinese_actor)
    proposal.write_text(
        proposal.read_text(encoding="utf-8").replace("platform analysts and product decision owners", "直播主播"),
        encoding="utf-8",
    )
    ok, error = validate_t45_research_proposal(chinese_actor, chinese_actor / "ideation" / "novelty_audit.md")
    assert ok is True, error

    hybrid = tmp_path / "hybrid"
    populate_valid_t45_workspace(hybrid, orientation="hybrid")
    blueprint_path = hybrid / "ideation" / "research_blueprint.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    blueprint["research_claims"]["cross_level_links"] = []
    write_yaml(blueprint_path, blueprint)
    ok, error = validate_blueprint_and_claim_registry(hybrid)
    assert ok is False
    assert "cross-level" in (error or "")


def test_t45_quality_repair_feedback_targets_the_failing_source_artifact(tmp_path: Path) -> None:
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="quality-gate-test",
        task_id="T4.5-REVIEW",
        run_id="quality-gate-feedback",
    )

    proposal_feedback = AgentRunner._validation_repair_feedback(
        ctx=ctx,
        error="research_proposal.md is too short for hybrid proposal (120/1900 words)",
    )
    plan_feedback = AgentRunner._validation_repair_feedback(
        ctx=ctx,
        error="Experiment plan has no experiment mapped to active claims: TC1",
    )
    review_feedback = AgentRunner._validation_repair_feedback(
        ctx=ctx,
        error="Orientation-aware review scores remain below threshold: evaluation_rigor",
    )

    assert "research_proposal.md" in proposal_feedback
    assert "不要直接写 `proposal_manifest.json`" in proposal_feedback
    assert "exp_plan.yaml" in plan_feedback
    assert "orientation_review.json" in review_feedback
    assert "不得只把 status 改为 accepted" in review_feedback


def test_t45_quality_repair_has_no_fixed_retry_limit_but_stops_without_source_progress(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="quality-gate-test",
        task_id="T4.5-REVIEW",
        run_id="quality-gate-fingerprint",
    )
    error = "research_proposal.md is too short for hybrid proposal (120/1900 words)"

    assert AgentRunner._record_t45_quality_repair_attempt(ctx=ctx, error=error) is False
    # A repeated finish_task with neither a source write nor a new diagnosis
    # cannot make progress, irrespective of any numeric retry setting.
    assert AgentRunner._record_t45_quality_repair_attempt(ctx=ctx, error=error) is True

    proposal = _proposal_path(tmp_path)
    proposal.write_text(proposal.read_text(encoding="utf-8") + "\nA source repair.\n", encoding="utf-8")
    assert AgentRunner._record_t45_quality_repair_attempt(ctx=ctx, error=error) is False


def test_t45_quality_repair_ignores_unrelated_source_writes(tmp_path: Path) -> None:
    """A repair loop cannot claim progress by changing an artifact outside its scope."""

    populate_valid_t45_workspace(tmp_path)
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="quality-gate-scope-test",
        task_id="T4.5-REVIEW",
        run_id="quality-gate-scope-run",
    )
    error = (
        "T45_REPAIRABLE_WARNING:\n"
        "Internal T4.5 quality refinements required:\n"
        "- [claims_depth] ideation/hypotheses.md: Research Claims and Hypotheses is below the orientation depth target.\n"
        "  Required repair: Develop missing research reasoning."
    )

    assert AgentRunner._record_t45_quality_repair_attempt(ctx=ctx, error=error) is False
    proposal = _proposal_path(tmp_path)
    proposal.write_text(proposal.read_text(encoding="utf-8") + "\nUnrelated proposal edit.\n", encoding="utf-8")
    assert AgentRunner._record_t45_quality_repair_attempt(ctx=ctx, error=error) is True

    hypotheses = tmp_path / "ideation" / "hypotheses.md"
    hypotheses.write_text(hypotheses.read_text(encoding="utf-8") + "\nA claim-source repair.\n", encoding="utf-8")
    assert AgentRunner._record_t45_quality_repair_attempt(ctx=ctx, error=error) is False


def test_t45_quality_gate_repairs_past_legacy_limit_without_human_extension(tmp_path: Path) -> None:
    """Exercise finish -> validation failure -> repair -> revalidate with mock LLM calls."""

    populate_valid_t45_workspace(tmp_path)
    original_proposal = _proposal_path(tmp_path).read_text(encoding="utf-8")
    responses = [_finish_response("initial validation")]
    for iteration in range(1, 7):
        responses.append(
            _proposal_write_response(
                iteration,
                content=original_proposal + f"\n\nrepair iteration {iteration} retains the complete research proposal.\n",
            )
        )
        responses.append(_finish_response(f"repair {iteration}"))
    llm = MockLLMClient(responses)
    human = MockHumanInterface()
    registry = ToolRegistry()
    register_builtin_tools(registry)
    runner = AgentRunner(
        _RepeatedT45RepairAgent(),
        registry,
        llm,
        human,
        RuntimeSettings(),
    )
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="quality-gate-test",
        task_id="T4.5-REVIEW",
        run_id="quality-gate-run",
        mode="review",
    )

    result = asyncio.run(runner.run(ctx))

    assert result.ok is True, result.error or result.message
    assert llm.call_count == len(responses)
    assert not any(
        call[0] == "gate" and call[1]["gate_id"] == "runtime_validation_retry_extension"
        for call in human.calls
    )
    user_messages = [
        str(message.get("content") or "")
        for call_messages in llm.last_messages
        for message in call_messages
        if message.get("role") == "user"
    ]
    assert any("research_proposal.md is too short" in message for message in user_messages)


def test_t45_quality_gate_pauses_if_the_model_retries_without_changing_a_source(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    llm = MockLLMClient([_finish_response("initial validation"), _finish_response("ignored repair")])
    human = MockHumanInterface()
    registry = ToolRegistry()
    register_builtin_tools(registry)
    runner = AgentRunner(
        _RepeatedT45RepairAgent(),
        registry,
        llm,
        human,
        RuntimeSettings(),
    )
    ctx = ExecutionContext(
        workspace_dir=tmp_path,
        project_id="quality-gate-test",
        task_id="T4.5-REVIEW",
        run_id="quality-gate-no-progress",
        mode="review",
    )

    result = asyncio.run(runner.run(ctx))

    assert result.ok is False
    assert result.stop_reason == "interrupted"
    assert "源产物没有变化" in (result.error or "")
    assert llm.call_count == 2
    assert not any(call[0] == "gate" for call in human.calls)


def test_t5_is_blocked_when_final_quality_gate_is_not_passed(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    review_path = tmp_path / "ideation" / "orientation_review.json"
    review = yaml.safe_load(review_path.read_text(encoding="utf-8"))
    review["status"] = "requires_repair"
    write(review_path, __import__("json").dumps(review, ensure_ascii=False, indent=2))
    ok, error = validate_t45_research_proposal(tmp_path, tmp_path / "ideation" / "novelty_audit.md")
    assert ok is False
    assert "requires targeted repair" in (error or "")
    pack, _report = _build_reboost_pack(tmp_path)
    assert pack["generation_status"] == "blocked"
    assert any("T4.5 formalization" in item["question"] for item in pack["unresolved_items"])


def test_legacy_passed_audit_requires_formalization_upgrade_not_silent_t5_use(tmp_path: Path) -> None:
    write(
        tmp_path / "ideation" / "novelty_audit.md",
        "# Old attempt\nFinal Gate Verdict: drop_due_to_collision\n\n# Final attempt\n"
        "**Final Gate Verdict**: `pass_to_experiment`\n",
    )
    write_yaml(tmp_path / "ideation" / "hypothesis_brief.yaml", {"draft_hypotheses": [{"id": "H1", "statement": "draft"}]})
    write(tmp_path / "ideation" / "selected" / "selected_candidate.json", "{}\n")
    write(tmp_path / "literature" / "synthesis.md", "# Synthesis\n")
    reason = legacy_t45_upgrade_reason(tmp_path)
    assert reason is not None
    assert "统一研究正式化质量 gate" in reason


def test_t4_gate1_workspace_import_includes_the_complete_reselection_closure() -> None:
    """A Gate1 import must not leave a cards-only workspace without project/T4 state."""

    paths = task_import_paths("T4-GATE1")

    assert "project.yaml" in paths
    assert "literature" in paths
    assert "ideation" in paths
    assert not any(path.startswith("ideation/") for path in paths)


def _state_machine() -> StateMachine:
    repo_root = Path(__file__).resolve().parents[2]
    return StateMachine(
        repo_root / "config/system_config/state_machine.yaml",
        repo_root / "config/system_config/gates.yaml",
    )


def test_incomplete_t5_contract_redirects_to_t45_formalization(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    (tmp_path / "ideation" / "orientation_review.json").unlink()
    state = StateYaml(project_id="test", current_task="T5-REBOOST-GATE", status="PAUSED")

    redirected = _state_machine()._redirect_incomplete_t5_to_t45_formalization(
        state,
        tmp_path,
        source="test",
    )

    assert redirected is state
    assert state.current_task == "T4.5-FORMALIZE"
    assert state.status == "RUNNING"
    assert state.pending_gate is None
    repair = state.task_context["t45_t5_handoff_repair"]
    assert repair["target_task"] == "T4.5-FORMALIZE"
    assert "orientation_review" in repair["error_summary"]


def test_t45_human_continue_cannot_bypass_missing_formalization(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    (tmp_path / "ideation" / "exp_plan.yaml").unlink()
    state = StateYaml(
        project_id="test",
        current_task="T4.5-HUMAN-REVIEW",
        status="WAITING_HUMAN",
        pending_gate=GateState(
            gate_id="t45_human_review_gate",
            presented_at="2026-07-27T00:00:00+00:00",
            presentation={},
            options=[],
        ),
    )

    resolved = _state_machine().resolve_pending_gate(
        state,
        {"option_id": "continue_to_t5", "captured": {}},
        workspace_dir=tmp_path,
    )

    assert resolved.current_task == "T4.5-FORMALIZE"
    assert resolved.status == "RUNNING"
    assert resolved.pending_gate is None
    receipt = json.loads((tmp_path / "ideation" / "novelty_human_review.json").read_text(encoding="utf-8"))
    assert receipt["selected_option"] == "continue_to_t5"
    assert receipt["next_task"] == "T4.5-FORMALIZE"


def test_pending_t5_recovery_is_upgraded_to_t45_formalization(tmp_path: Path) -> None:
    populate_valid_t45_workspace(tmp_path)
    (tmp_path / "ideation" / "research_blueprint.yaml").unlink()
    state = StateYaml(
        project_id="test",
        current_task="T5-REBOOST-GATE",
        status="WAITING_HUMAN",
        pending_gate=GateState(
            gate_id="runtime_recovery_gate",
            presented_at="2026-07-27T00:00:00+00:00",
            presentation={},
            options=[],
        ),
    )

    refreshed = _state_machine().refresh_pending_gate_presentation(state, workspace_dir=tmp_path)

    assert refreshed.current_task == "T4.5-FORMALIZE"
    assert refreshed.status == "RUNNING"
    assert refreshed.pending_gate is None


def test_nonpassing_audit_cannot_enter_t45_formalizer_from_stale_state(tmp_path: Path) -> None:
    """A stale T4.5-FORMALIZE state must not create an impossible Proposal loop."""

    populate_valid_t45_workspace(tmp_path)
    write(
        tmp_path / "ideation" / "novelty_audit.md",
        "# Novelty Audit\n\n## Final Gate Verdict\n\nFinal Gate Verdict: return_to_t4\n",
    )
    state = StateYaml(project_id="test", current_task="T4.5-FORMALIZE", status="PAUSED")

    redirected = _state_machine()._redirect_unapproved_t45_formalization_to_human_review(
        state,
        tmp_path,
        source="test",
    )

    assert redirected is state
    assert state.current_task == "T4.5-HUMAN-REVIEW"
    assert state.status == "RUNNING"
    blocker = state.task_context["t45_formalization_verdict_blocker"]
    assert blocker["normalized_verdict"] == "return_to_t4"
    assert "cannot repair or override" in blocker["reason"]


def test_t45_human_continue_does_not_reopen_formalizer_when_audit_is_nonpassing(tmp_path: Path) -> None:
    """Continue intent cannot send a rejected audit to T5 or an impossible repair loop."""

    populate_valid_t45_workspace(tmp_path)
    write(tmp_path / "ideation" / "novelty_audit.md", "Final Gate Verdict: drop_due_to_collision\n")
    state = StateYaml(
        project_id="test",
        current_task="T4.5-HUMAN-REVIEW",
        status="WAITING_HUMAN",
        pending_gate=GateState(
            gate_id="t45_human_review_gate",
            presented_at="2026-07-28T00:00:00+00:00",
            presentation={},
            options=[{"id": "continue_to_t5"}, {"id": "return_to_t4"}],
        ),
    )

    resolved = _state_machine().resolve_pending_gate(
        state,
        {"option_id": "continue_to_t5", "captured": {}},
        workspace_dir=tmp_path,
    )

    assert resolved.current_task == "T4.5-HUMAN-REVIEW"
    assert resolved.status == "WAITING_HUMAN"
    assert resolved.pending_gate is not None
    assert "continuation_blocker" in resolved.pending_gate.presentation
    assert "Proposal or hypothesis edits cannot change" in resolved.last_error


def test_t45_history_cap_discards_old_full_document_writes_but_keeps_latest_tool_turn(tmp_path: Path) -> None:
    """T4.5 repair loops must not carry every historic Proposal body forever."""

    llm = MockLLMClient([], context_window=524_288)
    registry = ToolRegistry()
    register_builtin_tools(registry)
    runner = AgentRunner(
        ResearchFormalizerAgent(mode="formalize"),
        registry,
        llm,
        MockHumanInterface(),
        RuntimeSettings(),
    )
    binding = llm.resolve(profile=None, tier="heavy")[0][0]
    messages = [Message.system("T4.5 system contract")]
    latest_call_id = ""
    for index in range(8):
        call = ToolCall.create(
            "write_file",
            {
                "path": "ideation/proposal/research_proposal.md",
                "content": f"proposal revision {index}: " + ("x" * 42_000),
            },
        )
        latest_call_id = call.id
        messages.append(Message.assistant(tool_calls=[call], step=index + 1))
        messages.append(
            Message.tool(
                tool_call_id=call.id,
                name="write_file",
                content="Proposal write accepted.",
                step=index + 1,
            )
        )

    compacted = runner._maybe_truncate(messages, binding, task_id="T4.5-FORMALIZE")
    compacted_tokens = llm.count_tokens([message.to_openai_dict() for message in compacted], binding)

    assert compacted_tokens < 60_000
    assert any("已省略较早的" in (message.content or "") for message in compacted)
    assert any(
        message.role.value == "assistant" and any(call.id == latest_call_id for call in message.tool_calls)
        for message in compacted
    )
