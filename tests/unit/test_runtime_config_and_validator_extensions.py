from __future__ import annotations

import json
from pathlib import Path
import textwrap

import yaml

from researchos.cli_runners.single_task import SingleTaskRunner
from researchos.runtime.config_audit import build_config_audit_summary
from researchos.runtime.config import (
    DebugSettings,
    LoggingSettings,
    RuntimeSettings,
    UISettings,
    WebFetchSettings,
    WorkspaceSettings,
    load_runtime_settings,
)
from researchos.schemas import validator
from researchos.testing.mocks import (
    FakeLLMMessage,
    FakeRawCompletion,
    FakeToolCall,
    MockLLMClient,
)
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.registry import ToolRegistry


def _hello_llm() -> MockLLMClient:
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


def test_load_runtime_settings_reads_shared_runtime_options(tmp_path: Path):
    config_path = tmp_path / "runtime.yaml"
    config_path.write_text(
        textwrap.dedent(
            """
            workspace:
              default_root: "./shared-workspace"
              runtime_dir: ".runtime"
            logging:
              level: "DEBUG"
              json: false
            human_interface:
              backend: "cli"
            debug:
              enable_trace: false
            ui:
              no_banner: true
              quiet: true
              verbose: false
            web_fetch:
              allowed_schemes: ["https"]
              allowed_hosts: ["example.com", "openalex.org"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    settings = load_runtime_settings(config_path)

    assert settings.workspace.default_root == "./shared-workspace"
    assert settings.workspace.runtime_dir == ".runtime"
    assert settings.logging == LoggingSettings(level="DEBUG", json=False)
    assert settings.debug == DebugSettings(enable_trace=False)
    assert settings.ui == UISettings(no_banner=True, quiet=True, verbose=False)
    assert settings.web_fetch == WebFetchSettings(
        allowed_schemes=("https",),
        allowed_hosts=("example.com", "openalex.org"),
    )


def test_t7_direct_full_prerequisites_do_not_require_t5_t6_outputs(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "project.yaml").parent.mkdir(parents=True, exist_ok=True)
    (ws / "project.yaml").write_text("project_id: p\nresearch_direction: Test\n", encoding="utf-8")
    (ws / "ideation").mkdir()
    (ws / "literature").mkdir()
    (ws / "ideation" / "hypotheses.md").write_text("## H1\nHypothesis\n", encoding="utf-8")
    (ws / "ideation" / "exp_plan.yaml").write_text("experiments:\n- name: exp1\n", encoding="utf-8")
    (ws / "ideation" / "novelty_audit.md").write_text("# Audit\nLevel 2\n", encoding="utf-8")
    (ws / "literature" / "synthesis.md").write_text("# Synthesis\n", encoding="utf-8")

    ok, err = validator.validate_prerequisites(ws, "T7")

    assert ok, err


async def test_single_task_runner_respects_custom_runtime_dir(tmp_workspace: Path):
    settings = RuntimeSettings(
        workspace=WorkspaceSettings(default_root="./workspace", runtime_dir=".runtime"),
    )
    runner = SingleTaskRunner(
        workspace=tmp_workspace,
        task_id="HELLO",
        llm_client=_hello_llm(),
        tool_registry=_registry(),
        runtime_settings=settings,
    )

    exit_code = await runner.run()

    assert exit_code == 0
    trace_dir = tmp_workspace / ".runtime" / "traces"
    assert trace_dir.exists()
    assert any(trace_dir.glob("*.jsonl"))


async def test_single_task_runner_can_disable_trace_output(tmp_workspace: Path):
    settings = RuntimeSettings(
        debug=DebugSettings(enable_trace=False),
    )
    runner = SingleTaskRunner(
        workspace=tmp_workspace,
        task_id="HELLO",
        llm_client=_hello_llm(),
        tool_registry=_registry(),
        runtime_settings=settings,
    )

    exit_code = await runner.run()

    assert exit_code == 0
    trace_dir = tmp_workspace / "_runtime" / "traces"
    assert not any(trace_dir.glob("*.jsonl"))


def test_validate_t2_artifacts_with_builtin_checker(tmp_path: Path):
    validator.register_builtin_task_checkers()
    workspace = tmp_path / "workspace"
    (workspace / "literature").mkdir(parents=True)

    # papers_raw使用字符串格式的authors（与schema一致）
    paper_raw = {
        "id": "paper-1",
        "source": "semantic_scholar",
        "title": "A Runtime Paper",
        "authors": ["Ada", "Bob"],
        "year": 2025,
        "abstract": "demo",
        "venue": "Conf",
        "citation_count": 1,
        "url": "https://example.com/paper-1",
    }
    (workspace / "literature" / "papers_raw.jsonl").write_text(
        json.dumps(paper_raw, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # papers_dedup使用字符串数组格式的authors（处理后）
    paper_dedup = {
        "id": "paper-1",
        "source": "semantic_scholar",
        "title": "A Runtime Paper",
        "authors": ["Ada", "Bob"],
        "year": 2025,
        "abstract": "demo",
        "venue": "Conf",
        "source_type": "conference",
        "relevance_score": 0.95,
        "why_relevant": "Directly related to the research topic",
        "citation_count": 1,
        "url": "https://example.com/paper-1",
    }
    (workspace / "literature" / "papers_dedup.jsonl").write_text(
        json.dumps(paper_dedup, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    verified_paper = {
        **paper_dedup,
        "canonical_id": "paper-1",
        "preferred_id_source": "doi",
        "verification_status": "metadata_verified",
        "verification_method": "crossref",
        "verification_source": "crossref",
        "verification_confidence": 0.95,
        "verification_title_similarity": 0.99,
        "verification_year_match": True,
        "semantic_screen": {
            "relation_to_project": "baseline_or_dataset_relevance",
            "role": "core",
            "confidence": "high",
            "bridge_id": None,
            "can_enter_core": True,
            "can_enter_deep_read": True,
            "rationale": "LLM screening allows this paper into the core review bucket.",
            "evidence_fields_used": ["title", "abstract"],
        },
    }
    (workspace / "literature" / "papers_verified.jsonl").write_text(
        json.dumps(verified_paper, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (workspace / "literature" / "verification_failures.jsonl").write_text(
        "",
        encoding="utf-8",
    )
    deep_read_item = {
        "paper_id": "paper-1",
        "title": "A Runtime Paper",
        "source": "semantic_scholar",
        "year": 2025,
        "venue": "Conf",
        "relevance_score": 0.95,
        "access_score_estimate": 0.6,
        "access_score": 0.6,
        "evidence_level": "ABSTRACT_ONLY",
        "seed_priority": False,
        "has_local_pdf": False,
        "why_relevant": "Directly related to the research topic",
        "queue_reason": "high relevance",
        "normalized_id": "paper-1",
        "url": "https://example.com/paper-1",
        "verification_status": "metadata_verified",
        "verification_confidence": 0.95,
        "read_priority": 0.83,
        "queue_rank": 1,
        "target_bucket": "target",
        "semantic_screen": verified_paper["semantic_screen"],
        "semantic_role": "core",
        "relation_to_project": "baseline_or_dataset_relevance",
    }
    (workspace / "literature" / "deep_read_queue.jsonl").write_text(
        json.dumps(deep_read_item, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (workspace / "literature" / "citation_edges.json").write_text("[]\n", encoding="utf-8")
    (workspace / "literature" / "domain_map.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "domain_map_for_synthesis_and_ideation_not_final_gaps",
                "core": [
                    {
                        "id": "paper-1",
                        "title": "A Runtime Paper",
                        "degree": 0,
                        "relation_to_project": "baseline_or_dataset_relevance",
                        "semantic_role": "core",
                        "key_rationale_hint": "LLM screening allows this paper into the core review bucket.",
                    }
                ],
                "theory_bridge": [],
                "adjacent": [],
                "boundary": [],
                "citation_edges": [],
                "bucket_assignments": {"paper-1": "core"},
                "warnings": ["citation_edges_empty_or_unavailable"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (workspace / "literature" / "access_audit.md").write_text(
        "# Access Audit\n",
        encoding="utf-8",
    )

    (workspace / "literature" / "search_log.md").write_text("# Search Log\n", encoding="utf-8")
    (workspace / "literature" / "missing_areas.md").write_text("- none\n", encoding="utf-8")

    ok, errors = validator.validate_task_artifacts(workspace, "T2")

    assert ok
    # validate_task_artifacts返回(ok, error_message)，成功时error_message为None
    assert errors is None


def test_validate_t4_artifacts_reports_bad_hypothesis_ref(tmp_path: Path):
    validator.register_builtin_task_checkers()
    workspace = tmp_path / "workspace"
    (workspace / "ideation").mkdir(parents=True)
    (workspace / "ideation" / "hypotheses.md").write_text(
        "# H1 First Hypothesis\n\n" + ("x" * 600),
        encoding="utf-8",
    )
    (workspace / "ideation" / "exp_plan.yaml").write_text(
        yaml.safe_dump(
            {
                "experiments": [
                    {
                        "id": "exp-1",
                        "hypothesis_ref": "H2",
                    }
                ]
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "risks.md").write_text("risk\n", encoding="utf-8")
    (workspace / "ideation" / "idea_rationales.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "ideas": [
                    {
                        "idea_id": "D1",
                        "hypothesis_refs": ["H1"],
                        "title": "First Hypothesis",
                        "idea_summary": "Traceable idea for H1.",
                        "basis": {
                            "source_questions": ["Q1"],
                            "literature_observations": [
                                {
                                    "claim": "The synthesis identifies a testable gap.",
                                    "source": "synthesis.md: Q1 / [p1]",
                                    "strength": "direct",
                                }
                            ],
                        },
                        "reasoning": "The observed gap supports H1 as a candidate hypothesis.",
                        "confidence": "medium",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "idea_scorecard.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "1.0",
                "ideas": [
                    {
                        "idea": {
                            "id": "D1",
                            "title": "First Hypothesis",
                            "pitch": "Traceable idea for H1.",
                            "core_claim": "A measurable mechanism improves the task.",
                            "target_problem": "The synthesis identifies a testable gap.",
                            "mechanism": "gradient regularization improves sparse user embeddings",
                            "prediction": "Recall@20 improves by 5% on sparse users",
                            "counterfactual": "if mechanism fails, no improvement observed",
                            "mechanism_family": "selective noise application",
                        },
                        "hypothesis_refs": ["H1"],
                        "source": {
                            "from_synthesis_section": "synthesis.md: Q1",
                            "from_missing_area": "missing_areas.md: gap",
                            "from_seed_idea": False,
                            "idea_origin": "free_reasoning",
                            "constraint_status": "mainline",
                            "supporting_papers": [
                                {
                                    "title": "Paper 1",
                                    "claim_used": "The synthesis identifies a testable gap.",
                                }
                            ],
                            "trigger_observation": "Existing methods leave the gap open.",
                        },
                        "selection_rationale": {
                            "novelty_reason": "The mechanism is underexplored.",
                            "feasibility_reason": "A small pilot is enough.",
                            "impact_reason": "The problem matters.",
                            "evaluability_reason": "Metrics are clear.",
                            "paper_story": "Problem, method, and experiment align.",
                        },
                        "closest_baselines": [
                            {
                                "name": "Baseline",
                                "similarity": "Same problem.",
                                "difference": "Different mechanism.",
                            }
                        ],
                        "scores": {
                            "novelty": 4,
                            "feasibility": 4,
                            "impact": 4,
                            "evaluability": 5,
                            "differentiation": 3,
                            "cost": 5,
                            "contribution_strength": 4,
                        },
                        "decision": {
                            "status": "selected",
                            "selected_reason": ["clear story"],
                            "selected_by": "user",
                            "user_feedback": "select D1",
                        },
                        "risks": [
                            {
                                "risk": "No gain",
                                "early_signal": "Pilot fails",
                                "mitigation": "Run ablation",
                                "kill_criteria": "No improvement",
                            }
                        ],
                        "minimum_experiment": {
                            "dataset": "small data",
                            "baseline": "Baseline",
                            "metric": ["accuracy"],
                            "expected_signal": "improvement",
                            "estimated_cost_usd": 5,
                        },
                        "counterfactual_check": "independent",
                        "counterfactual_note": "The design rationale remains valid without Paper 1; the paper only supplies one motivating example.",
                        "nearest_prior_work": {"work": "Paper 1", "distance": "moderate"},
                        "novelty_signal": "adjacent_zone",
                    },
                    {
                        "idea": {
                            "id": "D2",
                            "title": "Rejected idea",
                            "pitch": "Too close to prior work.",
                            "core_claim": "A weak transfer may work.",
                            "target_problem": "Weak gap.",
                            "mechanism": "direct transfer changes target-domain representations through inherited source bias",
                            "prediction": "if transfer bias helps, target accuracy improves over baseline",
                            "counterfactual": "if transfer bias is irrelevant, replacing it with baseline does not reduce accuracy",
                            "mechanism_family": "direct transfer",
                        },
                        "hypothesis_refs": [],
                        "source": {
                            "from_synthesis_section": "synthesis.md: Q2",
                            "from_missing_area": "missing_areas.md: weak gap",
                            "from_seed_idea": False,
                            "idea_origin": "seed_refinement",
                            "constraint_status": "mainline",
                            "supporting_papers": [
                                {
                                    "title": "Paper 2",
                                    "claim_used": "Prior work already covers it.",
                                }
                            ],
                            "trigger_observation": "Direct transfer idea.",
                        },
                        "selection_rationale": {
                            "novelty_reason": "Weak novelty.",
                            "feasibility_reason": "Feasible.",
                            "impact_reason": "Limited impact.",
                            "evaluability_reason": "Metrics unclear.",
                            "paper_story": "Story too thin.",
                        },
                        "closest_baselines": [
                            {
                                "name": "Paper 2",
                                "similarity": "Very similar.",
                                "difference": "Mostly scenario change.",
                            }
                        ],
                        "scores": {
                            "novelty": 2,
                            "feasibility": 4,
                            "impact": 2,
                            "evaluability": 2,
                            "differentiation": 2,
                            "cost": 4,
                            "contribution_strength": 2,
                        },
                        "decision": {
                            "status": "rejected",
                            "rejection_reason": ["too close to prior work"],
                            "can_revisit_if": "Find a stronger mechanism.",
                        },
                        "risks": [
                            {
                                "risk": "Low novelty",
                                "early_signal": "High overlap",
                                "mitigation": "Find differentiation",
                                "kill_criteria": "Only scenario change",
                            }
                        ],
                        "minimum_experiment": {
                            "dataset": "small data",
                            "baseline": "Paper 2",
                            "metric": ["accuracy"],
                            "expected_signal": "large improvement",
                            "estimated_cost_usd": 5,
                        },
                        "counterfactual_check": "collapses",
                        "counterfactual_note": "Without Paper 2 the remaining idea is mostly a direct scenario transfer.",
                        "nearest_prior_work": {"work": "Paper 2", "distance": "very_close"},
                        "novelty_signal": "marginal_zone",
                    },
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "rejected_ideas.md").write_text(
        "# Rejected / Deferred Ideas\n\n"
        "## D2: Rejected idea\n\n"
        "- **Status**: rejected\n"
        "- **Why rejected**:\n"
        "  - too close to prior work\n"
        "- **Closest existing work**: Paper 2.\n"
        "- **Missing evidence / metric**: stronger mechanism.\n"
        "- **Can revisit if**: Find a stronger mechanism.\n"
        "- **Cheap pilot that was not chosen**: small data is not enough.\n",
        encoding="utf-8",
    )
    (workspace / "ideation" / "_family_distribution.md").write_text(
        "## Mechanism Family Distribution\n\n"
        "### Family: selective noise application\n"
        "- Candidates: D1\n\n"
        "### Family: direct transfer\n"
        "- Candidates: D2\n\n"
        "## Summary\n\n"
        "- Total candidates: 2\n"
        "- Distinct families: 2\n",
        encoding="utf-8",
    )
    (workspace / "ideation" / "gate_decisions.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "decisions": [
                    {
                        "gate_id": "T4-DECIDE-1",
                        "action": "select_direction",
                        "selected_idea_ids": ["D1"],
                        "rejected_idea_ids": ["D2"],
                        "selected_by": "user",
                        "rationale": ["D1 clearer", "D2 too close"],
                    },
                    {
                        "gate_id": "T4-DECIDE-2",
                        "action": "confirm_plan",
                        "selected_idea_ids": ["D1"],
                        "rejected_idea_ids": [],
                        "selected_by": "user",
                        "rationale": ["plan ok"],
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "_candidate_directions.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "candidates": [
                    {
                        "idea_id": "D1",
                        "idea_origin": "free_reasoning",
                        "constraint_status": "mainline",
                        "basis_summary": "LLM mainline reasoning from synthesis and comparison table supports this hypothesis.",
                    },
                    {
                        "idea_id": "D1b",
                        "idea_origin": "evidence_driven",
                        "constraint_status": "mainline",
                        "basis_summary": "Evidence-driven candidate from paper notes and experiment feasibility.",
                    },
                    {
                        "idea_id": "D2",
                        "idea_origin": "seed_refinement",
                        "constraint_status": "mainline",
                        "basis_summary": "Seed-refinement candidate rejected because novelty and metrics are weak.",
                    },
                    {
                        "idea_id": "S1",
                        "idea_origin": "reverse_operation",
                        "constraint_status": "supplement",
                        "basis_summary": "Supplemental reverse-operation check for coverage of alternative mechanisms.",
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "_pass1_forward_candidates.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "raw_forward_generation_candidates_visible_to_gate",
                "candidates": [
                    {
                        "id": "D1",
                        "idea_origin": "free_reasoning",
                        "constraint_status": "mainline",
                        "basis_summary": "LLM mainline reasoning from synthesis and comparison table supports this hypothesis.",
                    },
                    {
                        "id": "D1b",
                        "idea_origin": "evidence_driven",
                        "constraint_status": "mainline",
                        "basis_summary": "Evidence-driven candidate from paper notes and experiment feasibility.",
                    },
                    {
                        "id": "D2",
                        "idea_origin": "seed_refinement",
                        "constraint_status": "mainline",
                        "basis_summary": "Seed-refinement candidate rejected because novelty and metrics are weak.",
                    },
                    {
                        "id": "S1",
                        "idea_origin": "reverse_operation",
                        "constraint_status": "supplement",
                        "basis_summary": "Supplemental reverse-operation check for coverage of alternative mechanisms.",
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "_pass2_grounding_review.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "grounding_review_flags_not_deletion_or_final_quality_gate",
                "reviews": [
                    {
                        "idea_id": "D1",
                        "screening_recommendation": "proceed",
                        "visible_to_gate": True,
                        "counterfactual_check": "independent",
                        "counterfactual_note": "Independent rationale remains without the nearest paper.",
                        "nearest_prior_work": {"work": "Smith2024", "distance": "moderate"},
                        "novelty_signal": "adjacent_zone",
                    },
                    {
                        "idea_id": "D1b",
                        "screening_recommendation": "defer_recommended",
                        "visible_to_gate": True,
                        "counterfactual_check": "survives_weakened",
                        "counterfactual_note": "Rationale survives but with weaker evidence.",
                        "nearest_prior_work": {"work": "Nearby Paper", "distance": "distant"},
                        "novelty_signal": "no_nearby_cluster",
                    },
                    {
                        "idea_id": "D2",
                        "screening_recommendation": "reject_recommended",
                        "visible_to_gate": True,
                        "counterfactual_check": "collapses",
                        "counterfactual_note": "Without the nearest prior work this is mostly transfer.",
                        "nearest_prior_work": {"work": "Nearby Paper", "distance": "very_close"},
                        "novelty_signal": "marginal_zone",
                    },
                    {
                        "idea_id": "S1",
                        "screening_recommendation": "revise_before_selection",
                        "visible_to_gate": True,
                        "counterfactual_check": "survives_weakened",
                        "counterfactual_note": "Reverse operation remains useful as a diagnostic.",
                        "nearest_prior_work": {"work": "none", "distance": "none_found"},
                        "novelty_signal": "no_nearby_cluster",
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (workspace / "ideation" / "_gate1_selection_brief.md").write_text(
        "# Gate1 Selection Brief\n\n"
        "- D1: Pass2 proceed.\n"
        "- D1b: Pass2 defer_recommended.\n"
        "- D2: Pass2 reject_recommended.\n"
        "- S1: Pass2 revise_before_selection.\n\n"
        "Merge options: 合并 D1+D1b or 合并 D1+S1.\n",
        encoding="utf-8",
    )

    ok, errors = validator.validate_task_artifacts(workspace, "T4")

    assert not ok
    assert errors is not None
    assert "hypothesis_ref 'H2' 不存在" in errors


def test_validate_t7_artifacts_happy_path(tmp_path: Path):
    validator.register_builtin_task_checkers()
    workspace = tmp_path / "workspace"
    (workspace / "experiments" / "runs" / "run_001").mkdir(parents=True)
    (workspace / "experiments" / "configs").mkdir(parents=True)
    (workspace / "project.yaml").write_text(
        yaml.safe_dump(
            {"project_id": "demo", "compute_budget": {"max_gpu_hours": 10}},
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (workspace / "experiments" / "results_summary.json").write_text(
        json.dumps({"summary": "ok", "total_gpu_hours": 2.5}, ensure_ascii=False),
        encoding="utf-8",
    )
    (workspace / "experiments" / "runs" / "run_001" / "record.json").write_text(
        json.dumps({"run_id": "run_001", "status": "done"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (workspace / "experiments" / "iteration_log.md").write_text(
        "# Iteration Log\n" + "run completed with stable metrics and checked artifacts. " * 5,
        encoding="utf-8",
    )
    (workspace / "experiments" / "ablations.csv").write_text(
        "experiment_id,accuracy\nexp1,0.85\nexp2,0.86\nexp3,0.87\n",
        encoding="utf-8",
    )
    (workspace / "experiments" / "docker_digests.txt").write_text(
        "researchos/system@sha256:abc123",
        encoding="utf-8",
    )
    (workspace / "experiments" / "iteration_diversity_check.md").write_text("diverse iterations\n", encoding="utf-8")
    (workspace / "experiments" / "seed_ensemble_summary.json").write_text("{}", encoding="utf-8")
    (workspace / "experiments" / "results_summary.json").write_text(
        json.dumps(
            {
                "experiments": [
                    {
                        "experiment_id": "headline_exp1",
                        "tier": "headline",
                        "seed_runs": [{"seed": 42}, {"seed": 43}, {"seed": 44}],
                        "status": "success",
                        "quality_status": "ok",
                    },
                    {
                        "experiment_id": "final_exp1",
                        "tier": "final_method",
                        "seed_runs": [{"seed": 42}, {"seed": 43}],
                        "status": "success",
                        "quality_status": "ok",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    ok, errors = validator.validate_task_artifacts(workspace, "T7")

    assert ok
    # validate_task_artifacts返回(ok, error_message)，成功时error_message为None
    assert errors is None


def test_build_config_audit_summary_reports_direct_llm_bindings(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "agent_params.yaml").write_text(
        textwrap.dedent(
            """
            agents:
              scout:
                llm:
                  profile: scout_resilient
                  tier: medium
              writer:
                llm:
                  model: openrouter/openai/gpt-4o
                  endpoint: openrouter_main
                modes:
                  revise:
                    llm:
                      model: openrouter/openai/gpt-4o-mini
                      endpoint: openrouter_main
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    summary = build_config_audit_summary(config_dir)

    assert "writer (base)" in summary["agents_disabling_profile_fallback"]
    assert "writer.revise" in summary["agents_disabling_profile_fallback"]
    assert "scout (base)" not in summary["agents_disabling_profile_fallback"]
