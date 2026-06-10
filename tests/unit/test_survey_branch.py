from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from researchos.agents.registry import get_agent_by_id
from researchos.agents.survey_writer import SurveyWriterAgent
from researchos.orchestration.task_io_contract import TASK_IO_CONTRACTS
from researchos.orchestration.state_machine import StateMachine
from researchos.runtime.system_config import system_config_path
from researchos.runtime.agent import AgentResult, ExecutionContext
from researchos.schemas.validator import validate_task_artifacts
from researchos.schemas.state import StateYaml
from researchos.tools.survey_tools import (
    AssembleSurveyTool,
    AuditSurveyCoverageTool,
    BindSurveyReviewTool,
    BuildSurveyStateTool,
    ExportSurveyForIdeationTool,
    UpdateSurveySectionStateTool,
)
from researchos.tools.latex_compile import _compile_dependency_fingerprint
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _survey_plan() -> dict:
    return {
        "semantics": "llm_authored_taxonomy_driven_survey_plan",
        "taxonomy": {
            "dimension": "mechanism families",
            "rationale": "Readers need a mechanism-level map.",
            "tree": [
                {"class_id": "T1", "name": "Perturbation mechanisms", "parent": None, "paper_ids": ["P1", "P2"]},
                {"class_id": "T2", "name": "Routing mechanisms", "parent": None, "paper_ids": ["P3"]},
            ],
        },
        "evolution_narrative": "Foundational perturbation work led to routing-aware methods.",
        "sectioning_policy": {
            "mode": "compact",
            "max_theme_sections": 0,
            "rationale": "Taxonomy classes are written inside Taxonomy and compared in Comparative Analysis.",
        },
        "outline": [
            {"section_id": "background", "title": "Background and Scope", "covers": ["scope"]},
            {"section_id": "taxonomy", "title": "Taxonomy", "covers": ["T1", "T2"]},
            {"section_id": "comparison", "title": "Comparative Analysis", "covers": ["cross_paper_tensions"]},
            {"section_id": "challenges", "title": "Open Challenges", "covers": ["challenge_hints"]},
            {"section_id": "future", "title": "Future Directions", "covers": ["adjacent_transfers"]},
        ],
        "coverage_selfcheck": {
            "unclassified_papers": [],
            "empty_classes": [],
            "corpus_sufficiency": "sufficient",
            "classes_needing_more_lit": [],
        },
        "resource_upgrade_needs": [
            {
                "paper_or_topic": "abstract_note",
                "reason": "abstract_only",
                "suggested_action": "Acquire PDF before using it as mechanism evidence.",
            }
        ],
    }


def _policy(workspace: Path) -> WorkspaceAccessPolicy:
    return WorkspaceAccessPolicy(
        workspace,
        allowed_read_prefixes=["", "drafts/", "literature/", "ideation/"],
        allowed_write_prefixes=["drafts/", "ideation/"],
    )


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_survey_compile_report(ws: Path) -> None:
    survey_dir = ws / "drafts" / "survey"
    tex = survey_dir / "survey.tex"
    pdf = survey_dir / "survey.pdf"
    log = survey_dir / "survey.log"
    dependency = _compile_dependency_fingerprint(ws, tex)
    _write_json(
        survey_dir / "survey_compile_report.json",
        {
            "semantics": "latex_compile_attempt_report",
            "tex_path": "drafts/survey/survey.tex",
            "success": True,
            "pdf_path": "drafts/survey/survey.pdf",
            "log_path": "drafts/survey/survey.log",
            "main_tex_sha256": _sha256_file(tex),
            "dependency_fingerprint": dependency,
            "attempts": [
                {
                    "success": True,
                    "exit_code": 0,
                    "dependency_fingerprint_hash": dependency["hash"],
                }
            ],
            "pdf_sha256": _sha256_file(pdf),
            "log_sha256": _sha256_file(log),
            "pdf_mtime": pdf.stat().st_mtime,
        },
    )


def _survey_ctx(ws: Path, mode: str, **extra):
    return type("Ctx", (), {"workspace_dir": ws, "mode": mode, "extra": extra})()


def _valid_survey_section_body(section: str, cite: str = "\\citep{p1}") -> str:
    mechanism_phrase = ""
    if section == "Taxonomy":
        mechanism_phrase = "It specifically separates perturbation and routing mechanisms as reader-facing categories. "
    return (
        f"\\section{{{section}}}\n"
        f"This section defines its reader-facing role using verified literature {cite}. "
        f"{mechanism_phrase}"
        "It compares taxonomy classes by mechanism, evidence boundary, evaluation setting, and scope, "
        "therefore the prose is not a paper-by-paper list. "
        "The section explains why the distinction matters for interpreting the field, how the classes differ, "
        "where cross-paper tensions remain unresolved, and which claims should stay conservative when evidence is partial. "
        "This wording is intentionally substantive enough for validation while still remaining a compact survey section."
    )


async def _build_valid_survey_chain(ws: Path) -> None:
    _write_json(ws / "drafts" / "survey" / "survey_plan.json", _survey_plan())
    _write_json(ws / "drafts" / "survey" / "corpus_decision.json", {"scope": "conservative"})
    (ws / "literature").mkdir(parents=True, exist_ok=True)
    (ws / "literature" / "related_work.bib").write_text(
        "@article{p1,title={A}}\n@article{p2,title={B}}\n@article{p3,title={C}}\n",
        encoding="utf-8",
    )
    policy = _policy(ws)
    result = await BuildSurveyStateTool(policy).execute()
    assert result.ok, result.content
    sections_dir = ws / "drafts" / "survey" / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    section_text = {
        "background": _valid_survey_section_body("Background and Scope", "\\citep[see][]{p1}"),
        "taxonomy": _valid_survey_section_body("Taxonomy", "\\citep{p1,p2}"),
        "comparison": _valid_survey_section_body("Comparative Analysis", "\\citep{p1,p3}"),
        "challenges": _valid_survey_section_body("Open Challenges", "\\citep{p2}"),
        "future": _valid_survey_section_body("Future Directions", "\\citep{p3}"),
        "introduction": _valid_survey_section_body("Introduction", "\\citep{p1}"),
        "conclusion": _valid_survey_section_body("Conclusion", "\\citep{p2,p3}"),
        "abstract": "A taxonomy-driven survey of mechanisms that compares evidence boundaries and future research needs.",
    }
    for section_id, text in section_text.items():
        (sections_dir / f"{section_id}.tex").write_text(text, encoding="utf-8")
        result = await UpdateSurveySectionStateTool(policy).execute(section_id=section_id)
        assert result.ok, result.content
    result = await AssembleSurveyTool(policy).execute()
    assert result.ok, result.content
    result = await AuditSurveyCoverageTool(policy).execute()
    assert result.ok, result.content


@pytest.mark.asyncio
async def test_survey_tools_build_state_assemble_audit_and_export(tmp_path: Path):
    ws = tmp_path
    _write_json(ws / "drafts" / "survey" / "survey_plan.json", _survey_plan())
    _write_json(ws / "drafts" / "survey" / "corpus_decision.json", {"scope": "conservative"})
    (ws / "literature").mkdir()
    (ws / "literature" / "related_work.bib").write_text(
        "@article{p1,title={A}}\n@article{p2,title={B}}\n@article{p3,title={C}}\n",
        encoding="utf-8",
    )
    (ws / "literature" / "metadata_triage.md").write_text(
        """# Metadata-only Literature Triage

## Likely Useful To Upgrade

- **17** - Useful metadata-only paper (Information Fusion, 2021)

## Low Evidence / Defer

- **22** - Weak metadata-only paper

## Do Not Use As Evidence

- **1** - Suspect venue paper

<!-- metadata_triage_source: reader_llm; candidate_count: 3 -->
""",
        encoding="utf-8",
    )
    policy = _policy(ws)

    result = await BuildSurveyStateTool(policy).execute()
    assert result.ok
    state = json.loads((ws / "drafts" / "survey" / "survey_state.json").read_text(encoding="utf-8"))
    assert state["semantics"] == "survey_state_for_taxonomy_driven_section_writing_not_final_claims"
    assert state["shared_facts"]["sectioning_policy"].startswith("compact_survey")
    assert state["sections"]["theme_1"]["status"] == "skipped"
    assert state["sections"]["theme_3"]["status"] == "skipped"
    assert state["shared_facts"]["resource_upgrade_needs"][0]["allowed_use"] == "resource_upgrade_hint_not_survey_or_idea_evidence"
    assert any("Useful metadata-only paper" in item["paper_or_topic"] for item in state["shared_facts"]["resource_upgrade_needs"])
    assert state["shared_facts"]["metadata_triage_boundaries"]["do_not_use_as_evidence_count"] == 1
    background_outline = (ws / "drafts" / "survey" / "section_outlines" / "background.md").read_text(encoding="utf-8")
    taxonomy_outline = (ws / "drafts" / "survey" / "section_outlines" / "taxonomy.md").read_text(encoding="utf-8")
    abstract_outline = (ws / "drafts" / "survey" / "section_outlines" / "abstract.md").read_text(encoding="utf-8")
    theme_outline = (ws / "drafts" / "survey" / "section_outlines" / "theme_1.md").read_text(encoding="utf-8")
    assert "Define core concepts" in background_outline
    assert "Carry the main classification framework" in taxonomy_outline
    assert "no heading, no LaTeX abstract environment" in abstract_outline
    assert "optional standalone theme slot" in theme_outline
    assert background_outline != taxonomy_outline
    state["shared_facts"]["resource_upgrade_needs"].append(
        {
            "paper_or_topic": "state_added_need",
            "reason": "review_found_weak_evidence",
            "suggested_action": "Acquire full text before T4 selection.",
            "allowed_use": "resource_upgrade_hint_not_survey_or_idea_evidence",
        }
    )
    (ws / "drafts" / "survey" / "survey_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert (ws / "drafts" / "survey" / "section_outlines" / "theme_1.md").exists()

    sections_dir = ws / "drafts" / "survey" / "sections"
    sections_dir.mkdir(parents=True)
    section_text = {
        "background": _valid_survey_section_body("Background and Scope", "\\citep[see][]{p1}"),
        "taxonomy": _valid_survey_section_body("Taxonomy", "\\citep{p1,p2}"),
        "comparison": _valid_survey_section_body("Comparative Analysis", "\\citep{p1,p3}"),
        "challenges": _valid_survey_section_body("Open Challenges", "\\citep{p2}"),
        "future": _valid_survey_section_body("Future Directions", "\\citep{p3}"),
        "introduction": _valid_survey_section_body("Introduction", "\\citep{p1}"),
        "conclusion": _valid_survey_section_body("Conclusion", "\\citep{p2,p3}"),
        "abstract": "A taxonomy-driven survey of mechanisms that compares evidence boundaries and future research needs.",
    }
    for section_id, text in section_text.items():
        (sections_dir / f"{section_id}.tex").write_text(text, encoding="utf-8")
        result = await UpdateSurveySectionStateTool(policy).execute(section_id=section_id)
        assert result.ok

    result = await AssembleSurveyTool(policy).execute()
    assert result.ok
    tex = (ws / "drafts" / "survey" / "survey.tex").read_text(encoding="utf-8")
    assert "\\documentclass" in tex
    assert "perturbation and routing mechanisms" in tex
    assert "Theme 1" not in tex
    assert "\\begin{abstract}" in tex
    assert "\\section*{Abstract}" not in tex
    assert tex.index("\\begin{abstract}") < tex.index("\\section{Introduction}")
    assert (ws / "drafts" / "survey" / "references.bib").exists()

    result = await AuditSurveyCoverageTool(policy).execute()
    assert result.ok
    audit = json.loads((ws / "drafts" / "survey" / "survey_audit.json").read_text(encoding="utf-8"))
    assert audit["passed"] is True

    result = await ExportSurveyForIdeationTool(policy).execute()
    assert result.ok
    insights = json.loads((ws / "ideation" / "survey_insights.json").read_text(encoding="utf-8"))
    assert insights["semantics"] == "survey_insights_optional_ideation_fuel_not_gate"
    assert insights["taxonomy"]["dimension"] == "mechanism families"
    assert insights["resource_upgrade_needs"][0]["paper_or_topic"] == "abstract_note"
    assert any(item["paper_or_topic"] == "state_added_need" for item in insights["resource_upgrade_needs"])
    summary = (ws / "drafts" / "survey" / "survey_summary.md").read_text(encoding="utf-8")
    assert "Resource Upgrade Needs" in summary


@pytest.mark.asyncio
async def test_survey_assemble_strips_legacy_abstract_wrappers(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    abstract_path = ws / "drafts" / "survey" / "sections" / "abstract.tex"
    abstract_path.write_text(
        "\\section*{Abstract}\n\\begin{abstract}\nA legacy abstract source with nested wrapper markup.\\end{abstract}",
        encoding="utf-8",
    )

    result = await AssembleSurveyTool(_policy(ws)).execute()

    assert result.ok, result.content
    tex = (ws / "drafts" / "survey" / "survey.tex").read_text(encoding="utf-8")
    assert tex.count("\\begin{abstract}") == 1
    assert tex.count("\\end{abstract}") == 1
    assert "\\section*{Abstract}" not in tex
    assert "A legacy abstract source with nested wrapper markup." in tex


def test_survey_writer_registry_and_phase():
    agent = get_agent_by_id("survey_writer", mode="survey_section")
    assert isinstance(agent, SurveyWriterAgent)
    assert agent._mode == "survey_section"


def test_survey_writer_prompt_includes_seed_outline_profile_as_taxonomy_prior(tmp_path: Path):
    ws = tmp_path
    (ws / "user_seeds").mkdir(parents=True)
    (ws / "literature").mkdir()
    (ws / "project.yaml").write_text(
        "project_id: p\nresearch_direction: 智能算法风险综述\ntarget_venue: 中文综述\n",
        encoding="utf-8",
    )
    _write_json(
        ws / "user_seeds" / "seed_outline_profile.json",
        {
            "semantics": "user_seed_outline_profile",
            "manuscript_type": "survey",
            "framework": {
                "taxonomy_hint": "理论 / 技术 / 管理 / 治理 × 场景 -> 数据 -> 模型 -> 决策 -> 反馈",
                "risk_generation_chain": ["场景", "数据", "模型", "决策", "反馈"],
                "perspectives": ["理论", "技术", "管理", "治理"],
            },
            "representative_literature_directions": [
                {"direction": "bounded rationality", "use_as": "query_direction_not_verified_citation"}
            ],
            "literature_seed_policy": {"directions_are_verified_citations": False},
        },
    )
    (ws / "user_seeds" / "seed_external_resources.jsonl").write_text(
        json.dumps(
            {
                "type": "regulation",
                "name": "EU AI Act",
                "source": "official_source_lookup_required",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    ctx = ExecutionContext(
        workspace_dir=ws,
        project_id="p",
        task_id="T3.6-PLAN",
        run_id="r1",
        mode="survey_plan",
    )

    prompt = SurveyWriterAgent(mode="survey_plan").system_prompt(ctx)

    assert "seed_outline_profile.json" in prompt
    assert "taxonomy hint" in prompt or "taxonomy" in prompt
    assert "理论 / 技术 / 管理 / 治理" in prompt
    assert "不是已验证 citation" in prompt
    assert "不得直接引用" in prompt
    assert "EU AI Act" in prompt


def test_survey_writer_plan_validation_requires_compact_sectioning_policy(tmp_path: Path):
    ws = tmp_path
    (ws / "drafts" / "survey").mkdir(parents=True)
    plan = _survey_plan()
    del plan["sectioning_policy"]
    _write_json(ws / "drafts" / "survey" / "survey_plan.json", plan)
    agent = SurveyWriterAgent(mode="survey_plan")
    ctx = type("Ctx", (), {"workspace_dir": ws, "mode": "survey_plan", "extra": {}})()

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "sectioning_policy" in (err or "")

    plan = _survey_plan()
    plan["outline"].insert(
        2,
        {"section_id": "theme_T1", "title": "Perturbation Mechanisms", "covers": ["T1"]},
    )
    _write_json(ws / "drafts" / "survey" / "survey_plan.json", plan)

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "compact sectioning_policy" in (err or "")


@pytest.mark.asyncio
async def test_build_survey_state_can_enable_limited_standalone_theme_sections(tmp_path: Path):
    ws = tmp_path
    plan = _survey_plan()
    plan["sectioning_policy"] = {
        "mode": "standalone_theme_sections",
        "max_theme_sections": 1,
        "rationale": "One unusually large mechanism family needs its own survey section.",
    }
    plan["outline"].insert(
        2,
        {"section_id": "theme_T1", "title": "Perturbation Mechanisms", "covers": ["T1"], "paper_ids": ["P1", "P2"]},
    )
    _write_json(ws / "drafts" / "survey" / "survey_plan.json", plan)
    _write_json(ws / "drafts" / "survey" / "corpus_decision.json", {"scope": "conservative"})
    result = await BuildSurveyStateTool(_policy(ws)).execute()

    assert result.ok, result.content
    state = json.loads((ws / "drafts" / "survey" / "survey_state.json").read_text(encoding="utf-8"))
    assert state["shared_facts"]["sectioning_policy"] == "standalone_theme_sections_enabled"
    assert state["sections"]["theme_1"]["status"] == "pending"
    assert state["sections"]["theme_1"]["title"] == "Perturbation Mechanisms"
    assert state["sections"]["theme_2"]["status"] == "skipped"


def test_t36_contract_exposes_seed_outline_inputs_to_non_compile_nodes():
    seed_keys = {"seed_outline_profile", "seed_ideas", "seed_constraints", "seed_external_resources"}
    for task_id, contract in TASK_IO_CONTRACTS.items():
        if not task_id.startswith("T3.6-") or task_id == "T3.6-COMPILE":
            continue
        inputs = set((contract.get("inputs") or {}).keys())
        assert seed_keys <= inputs, task_id


@pytest.mark.asyncio
async def test_survey_writer_compile_validation_accepts_success_report(tmp_path: Path):
    ws = tmp_path
    survey_dir = ws / "drafts" / "survey"
    survey_dir.mkdir(parents=True)
    (ws / "literature").mkdir(parents=True)
    (survey_dir / "survey_plan.json").write_text(json.dumps(_survey_plan()), encoding="utf-8")
    (survey_dir / "survey_state.json").write_text(
        json.dumps(
            {
                "semantics": "survey_state_for_taxonomy_driven_section_writing_not_final_claims",
                "sections": {
                    "taxonomy": {"status": "written"},
                    "comparison": {"status": "written"},
                    "challenges": {"status": "written"},
                    "future": {"status": "written"},
                },
            }
        ),
        encoding="utf-8",
    )
    (ws / "literature" / "related_work.bib").write_text(
        "@article{p1,title={A}}\n@article{p2,title={B}}\n@article{p3,title={C}}\n",
        encoding="utf-8",
    )
    (ws / "drafts" / "survey" / "survey.tex").write_text(
        (
            "\\documentclass{article}\\begin{document}"
            "\\begin{abstract}A taxonomy-driven survey of mechanisms.\\end{abstract}"
            "\\section{Introduction} Introduction \\citep{p1}."
            "\\section{Taxonomy} Taxonomy \\citep{p1}."
            "\\section{Comparative Analysis} Comparative analysis \\citep{p2}."
            "\\section{Open Challenges} Open challenges."
            "\\section{Future Directions} Future directions \\citep{p3}."
            "\\end{document}\n"
        ),
        encoding="utf-8",
    )
    result = await AuditSurveyCoverageTool(_policy(ws)).execute()
    assert result.ok, result.content
    (ws / "drafts" / "survey" / "survey.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 128)
    (ws / "drafts" / "survey" / "survey.log").write_text("ok", encoding="utf-8")
    _write_survey_compile_report(ws)
    agent = SurveyWriterAgent(mode="survey_compile")
    ctx = type("Ctx", (), {"workspace_dir": ws, "mode": "survey_compile", "extra": {}})()
    ok, err = agent.validate_outputs(ctx)
    assert ok, err


@pytest.mark.asyncio
async def test_t36_state_refuses_stale_plan_fingerprint(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    agent = SurveyWriterAgent(mode="survey_state")
    ctx = _survey_ctx(ws, "survey_state")
    ok, err = agent.validate_outputs(ctx)
    assert ok, err

    plan = json.loads((ws / "drafts" / "survey" / "survey_plan.json").read_text(encoding="utf-8"))
    plan["taxonomy"]["dimension"] = "changed taxonomy"
    _write_json(ws / "drafts" / "survey" / "survey_plan.json", plan)

    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "已过期" in (err or "")


@pytest.mark.asyncio
async def test_t36_section_refuses_stale_section_outline_and_file(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    agent = SurveyWriterAgent(mode="survey_section")
    ctx = _survey_ctx(ws, "survey_section", section_id="taxonomy")
    ok, err = agent.validate_outputs(ctx)
    assert ok, err

    (ws / "drafts" / "survey" / "section_outlines" / "taxonomy.md").write_text(
        "# Taxonomy\n\nChanged outline after section was marked written.\n",
        encoding="utf-8",
    )
    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "已过期" in (err or "")

    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="taxonomy")
    ok, err = agent.validate_outputs(ctx)
    assert ok, err

    (ws / "drafts" / "survey" / "sections" / "taxonomy.tex").write_text(
        (
            "\\section{Taxonomy}\n"
            "Changed section content after state fingerprint while still remaining long enough "
            "to pass the section length guard. The validator should therefore detect the stale "
            "fingerprint rather than reporting a short-section error. "
            "The edited section still compares taxonomy classes by mechanism, evidence boundary, "
            "evaluation setting, scope, and unresolved cross-paper tension so that craft checks "
            "do not mask the fingerprint freshness assertion in this test fixture."
        ),
        encoding="utf-8",
    )
    ok, err = agent.validate_outputs(ctx)
    assert ok, err
    state = json.loads((ws / "drafts" / "survey" / "survey_state.json").read_text(encoding="utf-8"))
    assert state["sections"]["taxonomy"]["input_fingerprints"]["section_file"]["sha256"] == _sha256_file(
        ws / "drafts" / "survey" / "sections" / "taxonomy.tex"
    )


@pytest.mark.asyncio
async def test_t36_section_repairs_unique_near_miss_citation_key(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    bib_key = "102316journal203201032034219"
    wrong_key = "102316journal201203032034219"
    (ws / "literature" / "related_work.bib").write_text(
        f"@article{{{bib_key},title={{Correct Key}}}}\n",
        encoding="utf-8",
    )
    section_path = ws / "drafts" / "survey" / "sections" / "background.tex"
    section_path.write_text(
        _valid_survey_section_body("Background and Scope", f"\\citep{{{wrong_key}}}"),
        encoding="utf-8",
    )
    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="background")

    ok, err = SurveyWriterAgent(mode="survey_section").validate_outputs(
        _survey_ctx(ws, "survey_section", section_id="background")
    )

    assert ok, err
    assert wrong_key not in section_path.read_text(encoding="utf-8")
    assert bib_key in section_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_t36_theme_section_skipped_is_optional_for_runtime_validation(tmp_path: Path):
    ws = tmp_path
    _write_json(ws / "drafts" / "survey" / "survey_plan.json", _survey_plan())
    _write_json(ws / "drafts" / "survey" / "corpus_decision.json", {"scope": "conservative"})
    result = await BuildSurveyStateTool(_policy(ws)).execute()
    assert result.ok, result.content
    result = await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="theme_1", status="skipped")
    assert result.ok, result.content
    state = json.loads((ws / "drafts" / "survey" / "survey_state.json").read_text(encoding="utf-8"))
    assert set(state["sections"]["theme_1"]["input_fingerprints"]) == {"section_outline"}

    ok, err = validate_task_artifacts(ws, "T3.6-SEC-THEME-1", declared_outputs={"section": "drafts/survey/sections/theme_1.tex"})

    assert ok, err
    sm = StateMachine(system_config_path("state_machine.yaml"))
    state = StateYaml(project_id="p", current_task="T3.6-SEC-THEME-1")
    ctx = sm.build_execution_context(ws, state)
    assert ctx.outputs_expected == {}


@pytest.mark.asyncio
async def test_t36_section_validation_rejects_dirty_abstract_and_bad_cites(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    agent = SurveyWriterAgent(mode="survey_section")

    abstract_path = ws / "drafts" / "survey" / "sections" / "abstract.tex"
    abstract_path.write_text(
        "This survey cites prior work \\citep{p1} in the abstract while otherwise summarizing a taxonomy-driven "
        "map of mechanisms, evidence boundaries, open challenges, and future research needs.",
        encoding="utf-8",
    )
    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="abstract")
    ok, err = agent.validate_outputs(_survey_ctx(ws, "survey_section", section_id="abstract"))
    assert not ok
    assert "abstract" in (err or "") and "引用" in (err or "")

    abstract_path.write_text(
        "\\section*{Abstract}\nThis is a long enough abstract without formal citation commands but with an invalid section heading.",
        encoding="utf-8",
    )
    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="abstract")
    ok, err = agent.validate_outputs(_survey_ctx(ws, "survey_section", section_id="abstract"))
    assert not ok
    assert "section" in (err or "") or "标题" in (err or "")

    abstract_path.write_text(
        "\\begin{abstract}\nThis is long enough prose but it incorrectly includes a LaTeX abstract environment.\\end{abstract}",
        encoding="utf-8",
    )
    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="abstract")
    ok, err = agent.validate_outputs(_survey_ctx(ws, "survey_section", section_id="abstract"))
    assert not ok
    assert "begin{abstract}" in (err or "") or "摘要正文" in (err or "")

    section_path = ws / "drafts" / "survey" / "sections" / "taxonomy.tex"
    section_path.write_text(
        "\\section{Taxonomy}\n"
        "TODO replace this placeholder with evidence after reviewing the full section context. "
        "The rest of this deliberately long sentence exists only to pass the length guard so "
        "the validator reaches the placeholder-specific check. "
        "The taxonomy narrative should normally compare mechanisms, evidence boundaries, and scope, "
        "but this fixture intentionally keeps the placeholder token visible for validation.",
        encoding="utf-8",
    )
    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="taxonomy")
    ok, err = agent.validate_outputs(_survey_ctx(ws, "survey_section", section_id="taxonomy"))
    assert not ok
    assert "placeholder" in (err or "")

    section_path.write_text(
        "\\section{Taxonomy}\n"
        "C1 is an internal ResearchOS alignment label and should not appear in polished survey prose. "
        "This deliberately long section text lets the validator reach the internal-label check. "
        "A valid taxonomy section would organize classes around mechanisms, evidence boundaries, "
        "and reader-facing scope rather than internal runtime identifiers.",
        encoding="utf-8",
    )
    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="taxonomy")
    ok, err = agent.validate_outputs(_survey_ctx(ws, "survey_section", section_id="taxonomy"))
    assert not ok
    assert "CID" in (err or "") or "内部" in (err or "")

    section_path.write_text(
        "\\section{Taxonomy}\n"
        "This section has enough substantive wording to pass the length guard while citing "
        "an unavailable source \\citep{missingKey2026} that is not present in the bibliography. "
        "The rest of the section compares taxonomy scope, evidence boundaries, and mechanism differences "
        "only to ensure the validator reaches the missing-citation check.",
        encoding="utf-8",
    )
    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="taxonomy")
    ok, err = agent.validate_outputs(_survey_ctx(ws, "survey_section", section_id="taxonomy"))
    assert not ok
    assert "missingKey2026" in (err or "")

    section_path.write_text(
        "\\section{Taxonomy}\n"
        "This section reviews prior work. Smith et al. studied one dataset. "
        "Jones et al. proposed a related model. Lee et al. reported another benchmark. "
        "Garcia et al. explored a fourth direction. Kumar et al. added a fifth variant. "
        "This section reviews additional papers without comparing taxonomy classes, mechanisms, "
        "boundaries, or evidence quality. The text is deliberately long enough to reach the craft check.",
        encoding="utf-8",
    )
    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="taxonomy")
    ok, err = agent.validate_outputs(_survey_ctx(ws, "survey_section", section_id="taxonomy"))
    assert not ok
    assert "流水账" in (err or "") or "写作结构" in (err or "")


@pytest.mark.asyncio
async def test_t36_assemble_refuses_stale_assembly_or_audit_inputs(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    agent = SurveyWriterAgent(mode="survey_assemble")
    ctx = _survey_ctx(ws, "survey_assemble")
    ok, err = agent.validate_outputs(ctx)
    assert ok, err

    (ws / "drafts" / "survey" / "sections" / "comparison.tex").write_text(
        "\\section{Comparative Analysis}\nChanged comparison after assembly and audit fingerprints.\n",
        encoding="utf-8",
    )
    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "已过期" in (err or "") or "已变化" in (err or "")

    result = await AssembleSurveyTool(_policy(ws)).execute()
    assert result.ok, result.content
    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "survey_audit.json" in (err or "") and ("已过期" in (err or "") or "已变化" in (err or ""))


def test_survey_writer_compile_validation_rejects_stale_tex_hash(tmp_path: Path):
    ws = tmp_path
    survey_dir = ws / "drafts" / "survey"
    survey_dir.mkdir(parents=True)
    tex = survey_dir / "survey.tex"
    pdf = survey_dir / "survey.pdf"
    log = survey_dir / "survey.log"
    tex.write_text("\\documentclass{article}\\begin{document}Survey\\end{document}\n", encoding="utf-8")
    pdf.write_bytes(b"%PDF-1.4\n")
    log.write_text("ok", encoding="utf-8")
    _write_survey_compile_report(ws)
    tex.write_text("\\documentclass{article}\\begin{document}Changed\\end{document}\n", encoding="utf-8")

    agent = SurveyWriterAgent(mode="survey_compile")
    ctx = _survey_ctx(ws, "survey_compile")
    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "main_tex_sha256" in (err or "")


@pytest.mark.asyncio
async def test_survey_writer_compile_validation_rejects_stale_dependency_fingerprint(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    survey_dir = ws / "drafts" / "survey"
    pdf = survey_dir / "survey.pdf"
    log = survey_dir / "survey.log"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 128)
    log.write_text("ok", encoding="utf-8")
    _write_survey_compile_report(ws)
    (survey_dir / "references.bib").write_text(
        (survey_dir / "references.bib").read_text(encoding="utf-8") + "\n@article{new,title={New}}\n",
        encoding="utf-8",
    )

    ok, err = SurveyWriterAgent(mode="survey_compile").validate_outputs(_survey_ctx(ws, "survey_compile"))

    assert not ok
    assert "dependency_fingerprint" in (err or "")


@pytest.mark.asyncio
async def test_survey_writer_compile_validation_rejects_stale_audit_after_compile_fix(tmp_path: Path):
    ws = tmp_path
    survey_dir = ws / "drafts" / "survey"
    survey_dir.mkdir(parents=True)
    (ws / "literature").mkdir(parents=True)
    _write_json(survey_dir / "survey_plan.json", _survey_plan())
    _write_json(
        survey_dir / "survey_state.json",
        {
            "semantics": "survey_state_for_taxonomy_driven_section_writing_not_final_claims",
            "sections": {
                "taxonomy": {"status": "written"},
                "comparison": {"status": "written"},
                "challenges": {"status": "written"},
                "future": {"status": "written"},
            },
        },
    )
    (ws / "literature" / "related_work.bib").write_text(
        "@article{p1,title={A}}\n@article{p2,title={B}}\n@article{p3,title={C}}\n",
        encoding="utf-8",
    )
    tex = survey_dir / "survey.tex"
    tex.write_text(
        (
            "\\documentclass{article}\\begin{document}"
            "\\begin{abstract}A taxonomy-driven survey of mechanisms.\\end{abstract}"
            "\\section{Introduction} Introduction \\citep{p1}."
            "\\section{Taxonomy} Taxonomy \\citep{p1}."
            "\\section{Comparative Analysis} Comparative analysis \\citep{p2}."
            "\\section{Open Challenges} Open challenges."
            "\\section{Future Directions} Future directions \\citep{p3}."
            "\\end{document}\n"
        ),
        encoding="utf-8",
    )
    result = await AuditSurveyCoverageTool(_policy(ws)).execute()
    assert result.ok, result.content
    tex.write_text(tex.read_text(encoding="utf-8").replace("Taxonomy", "Taxonomy revised", 1), encoding="utf-8")
    pdf = survey_dir / "survey.pdf"
    log = survey_dir / "survey.log"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 128)
    log.write_text("ok", encoding="utf-8")
    _write_survey_compile_report(ws)

    ok, err = SurveyWriterAgent(mode="survey_compile").validate_outputs(_survey_ctx(ws, "survey_compile"))

    assert not ok
    assert "survey_audit.json 已过期" in (err or "")


@pytest.mark.asyncio
async def test_survey_writer_compile_validation_rejects_undefined_citation_log(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    survey_dir = ws / "drafts" / "survey"
    pdf = survey_dir / "survey.pdf"
    log = survey_dir / "survey.log"
    pdf.write_bytes(b"%PDF-1.4\n" + b"x" * 128)
    log.write_text("LaTeX Warning: Citation `missing' undefined", encoding="utf-8")
    _write_survey_compile_report(ws)

    ok, err = SurveyWriterAgent(mode="survey_compile").validate_outputs(_survey_ctx(ws, "survey_compile"))

    assert not ok
    assert "survey.log" in (err or "")


def test_survey_writer_plan_validation_rejects_weak_evidence_as_core_paper(tmp_path: Path):
    ws = tmp_path
    (ws / "drafts" / "survey").mkdir(parents=True)
    (ws / "literature" / "paper_notes_abstract").mkdir(parents=True)
    (ws / "literature" / "paper_notes_abstract" / "weak_note.md").write_text(
        "# Weak Note\n\n- **ID**: weak_note\n- **Status**: [ABSTRACT-ONLY]\n",
        encoding="utf-8",
    )
    plan = _survey_plan()
    plan["taxonomy"]["tree"][0]["paper_ids"].append("weak_note")
    _write_json(ws / "drafts" / "survey" / "survey_plan.json", plan)
    agent = SurveyWriterAgent(mode="survey_plan")
    ctx = type("Ctx", (), {"workspace_dir": ws, "mode": "survey_plan", "extra": {}})()

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "abstract-only/metadata-only" in err

    plan["resource_upgrade_needs"].append(
        {
            "paper_or_topic": "weak_note",
            "reason": "abstract_only",
            "suggested_action": "Acquire full text before using as survey evidence.",
        }
    )
    _write_json(ws / "drafts" / "survey" / "survey_plan.json", plan)

    ok, err = agent.validate_outputs(ctx)

    assert ok, err


def test_t36_compile_artifact_checker_requires_compile_report(tmp_path: Path):
    ws = tmp_path
    (ws / "drafts" / "survey").mkdir(parents=True)
    (ws / "drafts" / "survey" / "survey.pdf").write_bytes(b"%PDF-1.4\n")
    (ws / "drafts" / "survey" / "survey.log").write_text("ok", encoding="utf-8")

    ok, err = validate_task_artifacts(ws, "T3.6-COMPILE")

    assert not ok
    assert err is not None
    assert "survey_compile_report" in err


@pytest.mark.asyncio
async def test_survey_writer_review_validation_accepts_complete_review(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    (ws / "drafts" / "survey" / "survey_review.md").write_text(
        "\n".join(
            [
                "## Taxonomy Review",
                "The taxonomy dimension is coherent and separates the main mechanism families clearly.",
                "## Coverage Review",
                "Coverage includes the planned core classes and flags abstract-only evidence boundaries.",
                "## Comparative Fairness Review",
                "Comparative analysis distinguishes settings before comparing evidence strength.",
                "## Challenges Review",
                "Challenges are tied to documented cross-paper tensions.",
                "## Future Directions Review",
                "Future directions use adjacent transfers without overstating them.",
                "## Scope And Craft Review",
                "Scope is stated honestly and craft issues have been checked.",
                "## Remaining Risks",
                "No blocking issue remains; minor citation polish may still be useful.",
            ]
        ),
        encoding="utf-8",
    )
    _write_json(
        ws / "drafts" / "survey" / "survey_review_actions.json",
        {
            "semantics": "llm_survey_review_and_section_revision_plan",
            "review_target": "taxonomy_driven_survey",
            "blocking_issues_remaining": False,
            "section_actions": [
                {
                    "section_id": "comparison",
                    "severity": "medium",
                    "issue": "Clarify incomparable settings.",
                    "action_taken": "revised",
                    "evidence": "comparison.tex revised",
                }
            ],
            "audit_after_review": {"survey_audit_passed": True},
        },
    )
    result = await BindSurveyReviewTool(_policy(ws)).execute()
    assert result.ok, result.content
    agent = SurveyWriterAgent(mode="survey_review")
    ctx = type("Ctx", (), {"workspace_dir": ws, "mode": "survey_review", "extra": {}})()
    ok, err = agent.validate_outputs(ctx)
    assert ok, err

    (ws / "drafts" / "survey" / "sections" / "comparison.tex").write_text(
        "\\section{Comparative Analysis}\nChanged section after review while file count remains stable.\n",
        encoding="utf-8",
    )
    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "目录内容已变化" in (err or "") or "已过期" in (err or "")

    result = await AssembleSurveyTool(_policy(ws)).execute()
    assert result.ok, result.content
    result = await AuditSurveyCoverageTool(_policy(ws)).execute()
    assert result.ok, result.content
    result = await BindSurveyReviewTool(_policy(ws)).execute()
    assert result.ok, result.content
    ok, err = agent.validate_outputs(ctx)
    assert ok, err

    (ws / "drafts" / "survey" / "survey.tex").write_text(
        "\\documentclass{article}\\begin{document}Changed after review\\end{document}\n",
        encoding="utf-8",
    )
    ok, err = agent.validate_outputs(ctx)
    assert not ok
    assert "已过期" in (err or "")


def test_t36_state_machine_routes_survey_yes_no_and_corpus_scope(tmp_path: Path):
    sm_config = {
        "initial_state": "T3.6-GATE-SURVEY",
        "states": {
            "T3.6-GATE-SURVEY": {
                "agent": "survey_writer",
                "mode": "survey_gate",
                "outputs": {"survey_decision": "drafts/survey/decision.json"},
                "next_on_success": "__parse_from_output__",
            },
            "T3.6-PLAN": {"agent": "survey_writer", "mode": "survey_plan", "outputs": {"survey_plan": "drafts/survey/survey_plan.json"}, "next_on_success": "done"},
            "T3.6-GATE-CORPUS": {
                "agent": "survey_writer",
                "mode": "corpus_gate",
                "outputs": {"corpus_decision": "drafts/survey/corpus_decision.json"},
                "next_on_success": "__parse_from_output__",
            },
            "T3.6-EXPAND": {"agent": "survey_writer", "mode": "survey_expand", "outputs": {"survey_expansion": "drafts/survey/survey_expansion.json"}, "next_on_success": "done"},
            "T3.6-STATE": {"agent": "survey_writer", "mode": "survey_state", "outputs": {"survey_state": "drafts/survey/survey_state.json"}, "next_on_success": "done"},
            "T3.6-COMPILE": {"agent": "survey_writer", "mode": "survey_compile", "outputs": {"survey_pdf": "drafts/survey/survey.pdf"}, "next_on_success": "done"},
            "T4": {"agent": "ideation", "outputs": {"hypotheses": "ideation/hypotheses.md"}, "next_on_success": "done"},
            "done": {"terminal": True},
            "failed": {"terminal": True},
        },
    }
    config = tmp_path / "state_machine.yaml"
    config.write_text(yaml.safe_dump(sm_config), encoding="utf-8")
    sm = StateMachine(config)
    (tmp_path / "drafts" / "survey").mkdir(parents=True)

    _write_json(tmp_path / "drafts" / "survey" / "decision.json", {"write_survey": True})
    assert sm._parse_t36_survey_decision(tmp_path) == "T3.6-PLAN"
    _write_json(tmp_path / "drafts" / "survey" / "decision.json", {"write_survey": False})
    assert sm._parse_t36_survey_decision(tmp_path) == "T4"
    _write_json(tmp_path / "drafts" / "survey" / "corpus_decision.json", {"scope": "complete"})
    assert sm._parse_t36_corpus_decision(tmp_path) == "T3.6-EXPAND"
    _write_json(tmp_path / "drafts" / "survey" / "corpus_decision.json", {"scope": "conservative"})
    assert sm._parse_t36_corpus_decision(tmp_path) == "T3.6-STATE"


def test_t36_missing_gate_decisions_route_back_to_human_gates(tmp_path: Path):
    sm_config = {
        "initial_state": "T3.6-GATE-SURVEY",
        "states": {
            "T3.6-GATE-SURVEY": {
                "agent": "survey_writer",
                "mode": "survey_gate",
                "outputs": {"survey_decision": "drafts/survey/decision.json"},
                "next_on_success": "__parse_from_output__",
            },
            "T3.6-PLAN": {
                "agent": "survey_writer",
                "mode": "survey_plan",
                "outputs": {"survey_plan": "drafts/survey/survey_plan.json"},
                "next_on_success": "done",
            },
            "T3.6-GATE-CORPUS": {
                "agent": "survey_writer",
                "mode": "corpus_gate",
                "outputs": {"corpus_decision": "drafts/survey/corpus_decision.json"},
                "next_on_success": "__parse_from_output__",
            },
            "T3.6-EXPAND": {
                "agent": "survey_writer",
                "mode": "survey_expand",
                "outputs": {"survey_expansion": "drafts/survey/survey_expansion.json"},
                "next_on_success": "done",
            },
            "T3.6-STATE": {
                "agent": "survey_writer",
                "mode": "survey_state",
                "outputs": {"survey_state": "drafts/survey/survey_state.json"},
                "next_on_success": "done",
            },
            "T4": {"agent": "ideation", "outputs": {"hypotheses": "ideation/hypotheses.md"}, "next_on_success": "done"},
            "done": {"terminal": True},
            "failed": {"terminal": True},
        },
    }
    config = tmp_path / "state_machine.yaml"
    config.write_text(yaml.safe_dump(sm_config), encoding="utf-8")
    sm = StateMachine(config)

    assert sm._parse_t36_survey_decision(tmp_path) == "T3.6-GATE-SURVEY"
    assert sm._parse_t36_corpus_decision(tmp_path) == "T3.6-GATE-CORPUS"

    (tmp_path / "drafts" / "survey").mkdir(parents=True)
    (tmp_path / "drafts" / "survey" / "decision.json").write_text("{bad json", encoding="utf-8")
    (tmp_path / "drafts" / "survey" / "corpus_decision.json").write_text("[]", encoding="utf-8")

    assert sm._parse_t36_survey_decision(tmp_path) == "T3.6-GATE-SURVEY"
    assert sm._parse_t36_corpus_decision(tmp_path) == "T3.6-GATE-CORPUS"


def test_t36_immediate_gates_persist_decisions(tmp_path: Path):
    sm_config = {
        "initial_state": "T3.6-GATE-SURVEY",
        "states": {
            "T3.6-GATE-SURVEY": {
                "agent": "survey_writer",
                "mode": "survey_gate",
                "extra": {"immediate_gate": True},
                "outputs": {"survey_decision": "drafts/survey/decision.json"},
                "gate": "t36_survey_gate",
            },
            "T3.6-GATE-CORPUS": {
                "agent": "survey_writer",
                "mode": "corpus_gate",
                "extra": {"immediate_gate": True},
                "outputs": {"corpus_decision": "drafts/survey/corpus_decision.json"},
                "gate": "t36_corpus_gate",
            },
            "T3.6-PLAN": {"agent": "survey_writer", "next_on_success": "done"},
            "T3.6-EXPAND": {"agent": "survey_writer", "next_on_success": "done"},
            "T3.6-STATE": {"agent": "survey_writer", "next_on_success": "done"},
            "T4": {"agent": "ideation", "next_on_success": "done"},
            "done": {"terminal": True},
        },
    }
    gates = {
        "gates": {
            "t36_survey_gate": {
                "options": [
                    {"id": "yes", "label": "写", "next": "T3.6-PLAN"},
                    {"id": "no", "label": "跳过", "next": "T4"},
                ]
            },
            "t36_corpus_gate": {
                "options": [
                    {"id": "complete", "label": "补检", "next": "T3.6-EXPAND"},
                    {"id": "conservative", "label": "保守", "next": "T3.6-STATE"},
                ]
            },
        }
    }
    config = tmp_path / "state_machine.yaml"
    gates_path = tmp_path / "gates.yaml"
    config.write_text(yaml.safe_dump(sm_config), encoding="utf-8")
    gates_path.write_text(yaml.safe_dump(gates), encoding="utf-8")
    sm = StateMachine(config, gates_path)

    state = StateYaml(project_id="p1", current_task="T3.6-GATE-SURVEY", status="RUNNING")
    state = sm.pause_for_immediate_gate(state, workspace_dir=tmp_path)
    state = sm.resolve_pending_gate(state, {"option_id": "yes", "captured": {}}, workspace_dir=tmp_path)
    assert state.current_task == "T3.6-PLAN"
    survey_decision = json.loads((tmp_path / "drafts" / "survey" / "decision.json").read_text(encoding="utf-8"))
    assert survey_decision["write_survey"] is True
    assert isinstance(survey_decision.get("input_fingerprints"), dict)
    assert sm._parse_t36_survey_decision(tmp_path) == "T3.6-PLAN"
    (tmp_path / "literature").mkdir(parents=True, exist_ok=True)
    (tmp_path / "literature" / "synthesis.md").write_text("changed after survey gate\n", encoding="utf-8")
    assert sm._parse_t36_survey_decision(tmp_path) == "T3.6-GATE-SURVEY"

    _write_json(tmp_path / "drafts" / "survey" / "survey_plan.json", _survey_plan())
    state = StateYaml(project_id="p1", current_task="T3.6-GATE-CORPUS", status="RUNNING")
    state = sm.pause_for_immediate_gate(state, workspace_dir=tmp_path)
    state = sm.resolve_pending_gate(state, {"option_id": "complete", "captured": {}}, workspace_dir=tmp_path)
    assert state.current_task == "T3.6-EXPAND"
    corpus_decision = json.loads((tmp_path / "drafts" / "survey" / "corpus_decision.json").read_text(encoding="utf-8"))
    assert corpus_decision["scope"] == "complete"
    assert isinstance(corpus_decision.get("input_fingerprints"), dict)
    assert sm._parse_t36_corpus_decision(tmp_path) == "T3.6-EXPAND"
    plan = json.loads((tmp_path / "drafts" / "survey" / "survey_plan.json").read_text(encoding="utf-8"))
    plan["taxonomy"]["dimension"] = "changed after corpus gate"
    _write_json(tmp_path / "drafts" / "survey" / "survey_plan.json", plan)
    assert sm._parse_t36_corpus_decision(tmp_path) == "T3.6-GATE-CORPUS"


def test_t45_reframe_and_drop_pause_for_human_gate(tmp_path: Path):
    sm_config = {
        "initial_state": "T4.5",
        "states": {
            "T4": {"agent": "ideation", "outputs": {"hypotheses": "ideation/hypotheses.md"}, "next_on_success": "T4.5"},
            "T4.5": {
                "agent": "novelty_auditor",
                "outputs": {"novelty_audit": "ideation/novelty_audit.md"},
                "next_on_success": "__parse_from_output__",
            },
            "T4.5-HUMAN-REVIEW": {
                "agent": "novelty_auditor",
                "mode": "human_review",
                "extra": {"immediate_gate": True},
                "inputs": {"novelty_audit": "ideation/novelty_audit.md"},
                "outputs": {"novelty_human_review": "ideation/novelty_human_review.json"},
                "gate": {"type": "t45_human_review_gate"},
            },
            "T7": {"agent": "experimenter", "mode": "full", "outputs": {"results": "experiments/results_summary.json"}, "next_on_success": "done"},
            "done": {"terminal": True},
            "failed": {"terminal": True},
        },
    }
    gates = {
        "gates": {
            "t45_human_review_gate": {
                "title": "T4.5 review",
                "presentation": {"audit": {"from_file": "ideation/novelty_audit.md", "max_chars": 1000}},
                "options": [
                    {"id": "continue_to_t7", "label": "继续进入外部实验链", "next": "T5-HANDOFF"},
                    {"id": "return_to_t4", "label": "回到 T4", "next": "T4"},
                    {"id": "stop_project", "label": "结束", "next": "done"},
                ],
            }
        }
    }
    config = tmp_path / "state_machine.yaml"
    gates_path = tmp_path / "gates.yaml"
    config.write_text(yaml.safe_dump(sm_config), encoding="utf-8")
    gates_path.write_text(yaml.safe_dump(gates), encoding="utf-8")
    sm = StateMachine(config, gates_path)
    (tmp_path / "ideation").mkdir()
    (tmp_path / "ideation" / "novelty_audit.md").write_text(
        "Final Gate Verdict: return_to_T4_reframe\n",
        encoding="utf-8",
    )
    assert sm._parse_t45_verdict(tmp_path) == "T4.5-HUMAN-REVIEW"

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
    state = sm.advance(state, result, workspace_dir=tmp_path)
    assert state.current_task == "T4.5-HUMAN-REVIEW"
    assert sm.should_pause_for_immediate_gate(state) is True
    state = sm.pause_for_immediate_gate(state, workspace_dir=tmp_path)
    assert state.status == "WAITING_HUMAN"
    assert state.pending_gate is not None
    state = sm.resolve_pending_gate(state, {"option_id": "return_to_t4", "captured": {"note": "reframe"}}, workspace_dir=tmp_path)
    assert state.current_task == "T4"
    decision = json.loads((tmp_path / "ideation" / "novelty_human_review.json").read_text(encoding="utf-8"))
    assert decision["semantics"] == "human_decision_over_agent_recommendation"
    assert decision["selected_option"] == "return_to_t4"
