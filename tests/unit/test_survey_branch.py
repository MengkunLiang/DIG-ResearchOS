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
    _replace_template_document_body,
)
from researchos.tools.latex_compile import _compile_dependency_fingerprint
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _survey_plan() -> dict:
    return {
        "semantics": "llm_authored_taxonomy_driven_survey_plan",
        "writing_language": "en",
        "central_question": (
            "How should this field be reorganized into a mechanism-level framework that explains what prior studies "
            "have clarified, where their evidence boundaries remain, and which research problems should come next?"
        ),
        "scope_boundaries": {
            "included": ["mechanism-level studies", "comparative evaluation papers", "representative adjacent work"],
            "excluded": ["purely speculative commentary", "metadata-only records without abstracts"],
            "evidence_rules": "FULL/PARTIAL notes support claims; abstract-only notes are context; metadata-only records are upgrade hints.",
        },
        "review_contribution": (
            "The survey contributes a taxonomy-driven research map that links mechanisms, evidence boundaries, "
            "cross-stream tensions, and future agenda items rather than summarizing papers individually."
        ),
        "quality_plan": {
            "organizing_framework": "Mechanism families with explicit boundaries and cross-stream relationships.",
            "comparison_strategy": "Compare streams by assumptions, mechanisms, settings, evidence strength, and limitations.",
            "theoretical_lift": "Explain why the mechanism map reveals unresolved relationships among streams.",
            "future_agenda_logic": "Turn evidence gaps and tensions into concrete research questions.",
        },
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
            {
                "section_id": "background",
                "title": "Concepts, Scope, and Search Strategy",
                "reader_question": "What concepts, boundaries, and evidence rules define the review corpus?",
                "section_argument": "Clear boundaries prevent the survey from becoming a general topic summary.",
                "covers": ["scope"],
            },
            {
                "section_id": "taxonomy",
                "title": "Analytical Framework",
                "reader_question": "What framework reorganizes the literature into explanatory mechanism families?",
                "section_argument": "A mechanism framework clarifies relationships that paper-by-paper summaries obscure.",
                "covers": ["T1", "T2"],
            },
            {
                "section_id": "comparison",
                "title": "Research Progress and Comparative Evaluation",
                "reader_question": "How do the main research streams differ in assumptions, evidence, and limitations?",
                "section_argument": "Comparing streams exposes the field's contributions and unresolved tensions.",
                "covers": ["cross_paper_tensions"],
            },
            {
                "section_id": "challenges",
                "title": "Critical Assessment and Open Challenges",
                "reader_question": "Which unresolved tensions prevent the framework from becoming a settled account?",
                "section_argument": "Open challenges should be derived from evidence gaps and stream-level disagreements.",
                "covers": ["challenge_hints"],
            },
            {
                "section_id": "future",
                "title": "Future Research Agenda",
                "reader_question": "Which concrete research directions follow from the framework and critique?",
                "section_argument": "Future directions should translate critique into actionable studies and mechanisms.",
                "covers": ["adjacent_transfers"],
            },
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
    role_key = section
    if "Scope" in section or "Search Strategy" in section:
        role_key = "Background and Scope"
    elif "Framework" in section or section == "Taxonomy":
        role_key = "Taxonomy"
    elif "Comparative" in section or "Research Progress" in section:
        role_key = "Comparative Analysis"
    elif "Challenges" in section or "Critical Assessment" in section:
        role_key = "Open Challenges"
    elif "Future" in section or "Agenda" in section:
        role_key = "Future Directions"
    role_bits = {
        "Introduction": (
            "This review problem is not merely whether a topic has many papers, but how the field should be "
            "reorganized around a central problem that existing studies have only partially explained. "
            "The central problem is that prior work clarifies individual mechanisms while leaving the "
            "relationship among mechanism families, evidence boundaries, and future research agenda items "
            "fragmented. This survey therefore contributes a framework-oriented research map and explains why "
            "the review is needed now rather than promising a new empirical model. "
        ),
        "Background and Scope": (
            "The scope boundary matters because a review without inclusion and exclusion rules becomes a broad "
            "topic essay. This section defines the core concepts, states which mechanism-level studies are "
            "included, excludes purely speculative bibliographic records without usable content from claim evidence, and describes "
            "the corpus search and analysis method. The definition also separates established evidence from "
            "summary-level context so that citation claims remain calibrated. "
        ),
        "Taxonomy": (
            "The analytical framework separates perturbation and routing mechanisms as reader-facing taxonomy "
            "classes. This taxonomy is useful because it explains relationships among studies: one stream "
            "treats variation as the central mechanism, whereas another stream treats routing and assignment as "
            "the primary mechanism. The boundary between classes is therefore conceptual rather than merely "
            "bibliographic, and adjacent classes remain connected through shared evaluation concerns. "
        ),
        "Comparative Analysis": (
            "Research progress is best understood as several streams that differ in assumptions, mechanism "
            "claims, evaluation settings, and evidence strength. One stream contributes precise perturbation "
            "tests, whereas another stream contributes routing-aware designs; however, both streams leave "
            "boundary conditions under-specified. This comparison evaluates the contribution and limitation of "
            "each stream instead of listing studies one by one, and it shows how cross-stream tensions shape the "
            "field's next questions. "
        ),
        "Open Challenges": (
            "The main challenge is not a generic lack of research but an unresolved tension between mechanism "
            "claims and evidence boundaries. Because studies often optimize within narrow settings, the field "
            "still lacks a clear account of when mechanisms transfer, when they fail, and how partial evidence "
            "should be interpreted. These gaps prevent the framework from becoming a settled account and require "
            "more precise boundary tests. "
        ),
        "Future Directions": (
            "The future research agenda should translate the framework and critique into concrete studies. "
            "Future work should measure mechanism transfer across scenarios, design longitudinal evaluations, "
            "test boundary conditions, and build governance or audit procedures that connect evidence to action. "
            "These directions are specific next steps rather than generic calls for more theory or more data. "
        ),
        "Conclusion": (
            "Overall, this survey answers the central problem by showing that a framework-oriented map clarifies "
            "mechanism relationships, evidence boundaries, and research agenda priorities. The contribution is "
            "not the number of papers summarized but the taxonomy, comparison, and implications that help "
            "readers interpret the field. The conclusion also keeps limitations visible and avoids introducing "
            "new evidence. "
        ),
    }
    role = role_bits.get(role_key, role_bits["Comparative Analysis"])
    common = (
        f"Verified literature {cite} anchors the claim evidence, but the prose treats papers as evidence for "
        "stream-level relationships rather than as the structure of the paragraph. The discussion follows a "
        "claim, evidence, comparison, and evaluation sequence: it states a judgment, uses representative work "
        "to ground the judgment, compares the focal stream with adjacent streams, and then evaluates what the "
        "stream explains and what it misses. This makes the section a synthesis rather than a literature list. "
    )
    elaboration = (
        "A professional survey section also needs enough argumentative depth to connect definitions, mechanisms, "
        "and limitations rather than merely naming categories. The discussion identifies boundary conditions, "
        "clarifies how assumptions differ across streams, and explains why those differences matter for readers. "
        "It distinguishes established findings from provisional signals, which prevents weak evidence from being "
        "inflated into consensus. It also uses comparison words such as whereas, however, limitation, boundary, "
        "mechanism, relationship, and tradeoff so that the relationship among research streams remains visible. "
    )
    if "Comparative" in section or "Research Progress" in section:
        elaboration = elaboration + elaboration
    return f"\\section{{{section}}}\n" + role + common + elaboration + elaboration


def _valid_survey_bib() -> str:
    return "\n\n".join(
        [
            "@article{p1, author={Alpha, Alice}, title={Evidence Boundary Design}, journal={Journal of Review Methods}, year={2021}}",
            "@article{p2, author={Beta, Bob}, title={Mechanism Families in Applied Systems}, journal={Journal of Mechanism Studies}, year={2022}}",
            "@article{p3, author={Gamma, Carol}, title={Routing Mechanisms and Evaluation}, journal={Information Systems Research}, year={2023}}",
            "@article{p4, author={Delta, Dan}, title={Perturbation Tests and Boundary Conditions}, journal={Management Science}, year={2024}}",
            "@article{p5, author={Epsilon, Eve}, title={Comparative Evaluation of Research Streams}, journal={MIS Quarterly}, year={2025}}",
            "@article{p6, author={Zeta, Zoe}, title={Future Agenda Construction}, journal={Journal of Management Information Systems}, year={2026}}",
            "@article{p7, author={Eta, Erin}, title={Evidence Transfer Across Settings}, journal={Academy of Management Review}, year={2020}}",
            "@article{p8, author={Theta, Theo}, title={Governance and Audit Boundaries}, journal={Information and Management}, year={2019}}",
            "@article{p9, author={Iota, Iris}, title={Longitudinal Evaluation of Mechanisms}, journal={Decision Support Systems}, year={2018}}",
        ]
    ) + "\n"


def _write_valid_survey_bib(ws: Path) -> None:
    (ws / "literature").mkdir(parents=True, exist_ok=True)
    (ws / "literature" / "related_work.bib").write_text(_valid_survey_bib(), encoding="utf-8")


def test_survey_template_body_bibliographystyle_is_preserved():
    template = (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{Sample}\n"
        "\\bibliographystyle{ACM-Reference-Format}\n"
        "\\bibliography{sample-base}\n"
        "\\end{document}\n"
    )

    rendered = _replace_template_document_body(
        template,
        "\\section{Introduction}\nBody \\citep{p1}.\n\\bibliography{references}\n",
        bib_stem="references",
    )

    assert "\\bibliographystyle{ACM-Reference-Format}" in rendered
    assert rendered.count("\\bibliographystyle") == 1
    assert "\\bibliography{references}" in rendered


def _valid_survey_tex_document() -> str:
    sections = [
        "\\begin{abstract}A taxonomy-driven survey of mechanisms compares evidence boundaries and future research needs in a concise but complete form. It states the problem, the taxonomy axis, the comparative insight, and the research agenda without using formal citations. The abstract also explains why the survey matters to readers, how the framework organizes prior work, and what kinds of open questions follow from the evidence gradient.\\end{abstract}",
        _valid_survey_section_body("Introduction", "\\citep{p1,p2}"),
        _valid_survey_section_body("Concepts, Scope, and Search Strategy", "\\citep{p1,p2,p3,p4}"),
        _valid_survey_section_body("Taxonomy", "\\citep{p3,p4,p5,p6}"),
        _valid_survey_section_body("Research Progress and Comparative Evaluation", "\\citep{p5,p6,p7,p8,p9}"),
        _valid_survey_section_body("Critical Assessment and Open Challenges", "\\citep{p7,p8}"),
        _valid_survey_section_body("Future Research Agenda", "\\citep{p8,p9}"),
        _valid_survey_section_body("Conclusion", "\\citep{p6,p9}"),
    ]
    return "\\documentclass{article}\\begin{document}\n" + "\n".join(sections) + "\n\\end{document}\n"


async def _build_valid_survey_chain(ws: Path, *, plan: dict | None = None) -> None:
    _write_json(ws / "drafts" / "survey" / "survey_plan.json", plan or _survey_plan())
    _write_json(ws / "drafts" / "survey" / "corpus_decision.json", {"scope": "conservative"})
    _write_valid_survey_bib(ws)
    policy = _policy(ws)
    result = await BuildSurveyStateTool(policy).execute()
    assert result.ok, result.content
    sections_dir = ws / "drafts" / "survey" / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    section_text = {
        "background": _valid_survey_section_body("Background and Scope", "\\citep[see][]{p1,p2,p3,p4}"),
        "taxonomy": _valid_survey_section_body("Taxonomy", "\\citep{p3,p4,p5,p6}"),
        "comparison": _valid_survey_section_body("Comparative Analysis", "\\citep{p5,p6,p7,p8,p9}"),
        "challenges": _valid_survey_section_body("Open Challenges", "\\citep{p7,p8}"),
        "future": _valid_survey_section_body("Future Directions", "\\citep{p8,p9}"),
        "introduction": _valid_survey_section_body("Introduction", "\\citep{p1,p2}"),
        "conclusion": _valid_survey_section_body("Conclusion", "\\citep{p6,p9}"),
            "abstract": (
                "A taxonomy-driven survey of mechanisms that compares evidence boundaries and future research needs. "
                "The abstract states the motivating problem, the taxonomy axis, the comparative insight, and the "
                "research agenda without formal citations, while keeping evidence claims at a level appropriate for "
                "a concise survey summary."
            ),
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
    _write_valid_survey_bib(ws)
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
    assert state["sections"]["taxonomy"]["absorbs_theme_content"] is True
    assert state["sections"]["comparison"]["absorbs_theme_content"] is True
    assert state["sections"]["taxonomy"]["reader_question"].startswith("What framework")
    assert state["sections"]["comparison"]["section_argument"].startswith("Comparing streams")
    assert state["sections"]["background"]["writing_contract"]["purpose"].startswith("Define the review object")
    assert state["shared_facts"]["theme_coverage_contract"]["mode"] == "compact_theme_slots_skipped_content_must_be_absorbed"
    assert state["shared_facts"]["resource_upgrade_needs"][0]["allowed_use"] == "resource_upgrade_hint_not_survey_or_idea_evidence"
    assert any("Useful metadata-only paper" in item["paper_or_topic"] for item in state["shared_facts"]["resource_upgrade_needs"])
    assert state["shared_facts"]["metadata_triage_boundaries"]["do_not_use_as_evidence_count"] == 1
    background_outline = (ws / "drafts" / "survey" / "section_outlines" / "background.md").read_text(encoding="utf-8")
    taxonomy_outline = (ws / "drafts" / "survey" / "section_outlines" / "taxonomy.md").read_text(encoding="utf-8")
    abstract_outline = (ws / "drafts" / "survey" / "section_outlines" / "abstract.md").read_text(encoding="utf-8")
    theme_outline = (ws / "drafts" / "survey" / "section_outlines" / "theme_1.md").read_text(encoding="utf-8")
    assert "Define core concepts" in background_outline
    assert "Carry the main explanatory framework" in taxonomy_outline
    assert "Section Writing Contract" in taxonomy_outline
    assert "Every taxonomy class" in taxonomy_outline
    assert "Compact Theme Coverage Contract" in taxonomy_outline
    assert "T1: Perturbation mechanisms" in taxonomy_outline
    assert "T2: Routing mechanisms" in taxonomy_outline
    assert "every listed class must appear in both Taxonomy and Comparative Analysis" in taxonomy_outline
    assert "no heading, no LaTeX abstract environment" in abstract_outline
    assert "optional standalone theme slot" in theme_outline
    assert "Section Writing Contract" in theme_outline
    assert "Compact Theme Coverage Contract" in theme_outline
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
        "background": _valid_survey_section_body("Background and Scope", "\\citep[see][]{p1,p2,p3,p4}"),
        "taxonomy": _valid_survey_section_body("Taxonomy", "\\citep{p3,p4,p5,p6}"),
        "comparison": _valid_survey_section_body("Comparative Analysis", "\\citep{p5,p6,p7,p8,p9}"),
        "challenges": _valid_survey_section_body("Open Challenges", "\\citep{p7,p8}"),
        "future": _valid_survey_section_body("Future Directions", "\\citep{p8,p9}"),
        "introduction": _valid_survey_section_body("Introduction", "\\citep{p1,p2}"),
        "conclusion": _valid_survey_section_body("Conclusion", "\\citep{p6,p9}"),
            "abstract": (
                "A taxonomy-driven survey of mechanisms that compares evidence boundaries and future research needs. "
                "The abstract states the motivating problem, the taxonomy axis, the comparative insight, and the "
                "research agenda without formal citations, while keeping evidence claims at a level appropriate for "
                "a concise survey summary."
            ),
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
    assert "compact_theme_content_absorbed" in {item["name"] for item in audit["checks"]}
    assert audit["stats"]["theme_coverage_contract"]["mode"] == "compact_theme_slots_skipped_content_must_be_absorbed"

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
async def test_survey_audit_rejects_compact_theme_content_not_absorbed(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    comparison_path = ws / "drafts" / "survey" / "sections" / "comparison.tex"
    comparison_body = (
        "\\section{Comparative Analysis}\n"
        "Research progress is organized around research streams, assumptions, mechanisms, evidence strength, "
        "and boundary conditions rather than around a list of individual papers. The first stream contributes "
        "perturbation mechanisms and precise perturbation tests; in contrast, adjacent implementation studies "
        "focus on deployment constraints without naming the omitted taxonomy class. This comparison evaluates "
        "the contribution and limitation of each stream by asking whether its evidence explains mechanisms, "
        "whereas weaker streams only describe settings. Verified literature \\citep{p1,p2,p3,p4,p5} anchors these claims, "
        "but the paragraph uses citations as evidence for stream-level relationships rather than as the structure "
        "of the review. The strength of the perturbation stream is conceptual precision, while its limitation is "
        "that boundary conditions and transfer across settings remain under-specified. "
    )
    comparison_path.write_text(comparison_body * 5, encoding="utf-8")
    result = await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="comparison", status="revised")
    assert result.ok, result.content
    result = await AssembleSurveyTool(_policy(ws)).execute()
    assert result.ok, result.content

    result = await AuditSurveyCoverageTool(_policy(ws)).execute()

    assert not result.ok
    audit = json.loads((ws / "drafts" / "survey" / "survey_audit.json").read_text(encoding="utf-8"))
    failed = {item["name"] for item in audit["checks"] if item["passed"] is False}
    assert "compact_theme_content_absorbed" in failed
    compact_check = next(item for item in audit["checks"] if item["name"] == "compact_theme_content_absorbed")
    assert "Routing mechanisms" in compact_check["detail"]


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


def test_survey_writer_template_gate_accepts_recorded_human_selection(tmp_path: Path):
    ws = tmp_path
    survey_dir = ws / "drafts" / "survey"
    survey_dir.mkdir(parents=True)
    runtime_dir = ws / "_runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "human_interactions.jsonl").write_text(
        json.dumps(
            {
                "semantics": "researchos_human_interaction_record",
                "interaction_id": "human_template123",
                "task_id": "T3.6-TEMPLATE-GATE",
                "answer": "basic_zh",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_json(
        survey_dir / "writing_template.json",
        {
            "template_family": "basic_zh",
            "template_id": "basic_zh",
            "writing_language": "zh",
            "human_interaction_id": "human_template123",
        },
    )

    ok, err = SurveyWriterAgent(mode="template_gate").validate_outputs(_survey_ctx(ws, "template_gate"))

    assert ok, err


def test_survey_writer_plan_rejects_template_selection_mismatch(tmp_path: Path):
    ws = tmp_path
    survey_dir = ws / "drafts" / "survey"
    survey_dir.mkdir(parents=True)
    _write_json(
        survey_dir / "writing_template.json",
        {
            "template_family": "ccf",
            "template_id": "neurips",
            "writing_language": "en",
            "human_interaction_id": "human_template123",
        },
    )
    plan = _survey_plan()
    plan["template_selection"] = {
        "template_family": "utd",
        "template_id": "informs",
        "writing_language": "en",
    }
    _write_json(survey_dir / "survey_plan.json", plan)

    ok, err = SurveyWriterAgent(mode="survey_plan").validate_outputs(_survey_ctx(ws, "survey_plan"))

    assert not ok
    assert "template_selection.template_family" in (err or "")


@pytest.mark.asyncio
async def test_t36_assemble_applies_selected_basic_zh_template(tmp_path: Path):
    ws = tmp_path
    plan = _survey_plan()
    plan["writing_language"] = "zh"
    plan["template_selection"] = {
        "template_family": "basic_zh",
        "template_id": "basic_zh",
        "writing_language": "zh",
    }
    _write_json(ws / "drafts" / "survey" / "survey_plan.json", plan)
    _write_json(ws / "drafts" / "survey" / "corpus_decision.json", {"scope": "conservative"})
    _write_valid_survey_bib(ws)
    policy = _policy(ws)
    result = await BuildSurveyStateTool(policy).execute()
    assert result.ok, result.content
    sections_dir = ws / "drafts" / "survey" / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    zh_sections = {
        "abstract": "中文摘要正文。",
        "introduction": "\\section{Introduction}\n中文引言正文 \\citep{p1,p2}。",
        "background": "\\section{Background and Scope}\n中文背景正文 \\citep{p1,p2,p3,p4}。",
        "taxonomy": "\\section{Taxonomy}\n中文分类正文 \\citep{p3,p4,p5,p6}。",
        "comparison": "\\section{Comparative Analysis}\n中文比较正文 \\citep{p5,p6,p7,p8,p9}。",
        "challenges": "\\section{Open Challenges}\n中文挑战正文 \\citep{p7,p8}。",
        "future": "\\section{Future Directions}\n中文未来正文 \\citep{p8,p9}。",
        "conclusion": "\\section{Conclusion}\n中文结论正文。",
    }
    for section_id, text in zh_sections.items():
        (sections_dir / f"{section_id}.tex").write_text(text, encoding="utf-8")
        result = await UpdateSurveySectionStateTool(policy).execute(section_id=section_id)
        assert result.ok, result.content
    result = await AssembleSurveyTool(policy).execute()
    assert result.ok, result.content

    tex = (ws / "drafts" / "survey" / "survey.tex").read_text(encoding="utf-8")

    assert "\\documentclass[UTF8,11pt]{ctexart}" in tex
    assert "\\ctexset" in tex
    assert "ResearchOS template_family" not in tex


@pytest.mark.asyncio
async def test_t36_assemble_applies_informs_template_and_support_files(tmp_path: Path):
    ws = tmp_path
    plan = _survey_plan()
    plan["template_selection"] = {
        "template_family": "utd",
        "template_id": "informs",
        "writing_language": "en",
    }
    _write_json(ws / "drafts" / "survey" / "survey_plan.json", plan)
    _write_json(ws / "drafts" / "survey" / "corpus_decision.json", {"scope": "conservative"})
    _write_valid_survey_bib(ws)
    policy = _policy(ws)
    result = await BuildSurveyStateTool(policy).execute()
    assert result.ok, result.content
    sections_dir = ws / "drafts" / "survey" / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    for section_id in [
        "abstract",
        "introduction",
        "background",
        "taxonomy",
        "comparison",
        "challenges",
        "future",
        "conclusion",
    ]:
        text = "Abstract text." if section_id == "abstract" else _valid_survey_section_body(section_id)
        (sections_dir / f"{section_id}.tex").write_text(text, encoding="utf-8")
        result = await UpdateSurveySectionStateTool(policy).execute(section_id=section_id)
        assert result.ok, result.content

    result = await AssembleSurveyTool(policy).execute()
    assert result.ok, result.content
    tex = (ws / "drafts" / "survey" / "survey.tex").read_text(encoding="utf-8")

    assert "ResearchOS template_source" not in tex
    assert "\\bibliographystyle{informs2014}" in tex
    assert tex.count("\\bibliographystyle") == 1
    assert (ws / "drafts" / "survey" / "informs2014.bst").exists()
    manifest = json.loads((ws / "drafts" / "survey" / "survey_assembly_manifest.json").read_text(encoding="utf-8"))
    assert manifest["template_selection"]["template_family"] == "utd"


@pytest.mark.asyncio
async def test_t36_assemble_blocks_cited_bib_entry_without_author(tmp_path: Path):
    ws = tmp_path
    _write_json(ws / "drafts" / "survey" / "survey_plan.json", _survey_plan())
    _write_json(ws / "drafts" / "survey" / "corpus_decision.json", {"scope": "conservative"})
    (ws / "literature").mkdir(parents=True, exist_ok=True)
    bad_bib = _valid_survey_bib() + (
        "\n@article{noauthor2026, title={No Author Record}, journal={Journal}, year={2026}}\n"
    )
    (ws / "literature" / "related_work.bib").write_text(bad_bib, encoding="utf-8")
    policy = _policy(ws)
    result = await BuildSurveyStateTool(policy).execute()
    assert result.ok, result.content
    sections_dir = ws / "drafts" / "survey" / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    section_texts = {
        "abstract": "A taxonomy-driven survey without formal citations.",
        "introduction": _valid_survey_section_body("Introduction", "\\citep{p1,p2}"),
        "background": _valid_survey_section_body("Background and Scope", "\\citep{p1,p2,p3,noauthor2026}"),
        "taxonomy": _valid_survey_section_body("Taxonomy", "\\citep{p3,p4,p5,p6}"),
        "comparison": _valid_survey_section_body("Comparative Analysis", "\\citep{p5,p6,p7,p8,p9}"),
        "challenges": _valid_survey_section_body("Open Challenges", "\\citep{p7,p8}"),
        "future": _valid_survey_section_body("Future Directions", "\\citep{p8,p9}"),
        "conclusion": _valid_survey_section_body("Conclusion"),
    }
    for section_id, text in section_texts.items():
        (sections_dir / f"{section_id}.tex").write_text(text, encoding="utf-8")
        result = await UpdateSurveySectionStateTool(policy).execute(section_id=section_id)
        assert result.ok, result.content

    result = await AssembleSurveyTool(policy).execute()

    assert not result.ok
    assert result.error == "invalid_bibliography_quality"
    assert "noauthor2026: missing_author_or_organization" in result.content


@pytest.mark.asyncio
async def test_t36_assemble_strips_internal_bib_notes_from_public_references(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    source = ws / "literature" / "related_work.bib"
    source.write_text(
        source.read_text(encoding="utf-8")
        + "\n@article{status2026, author={Status, Sam}, title={Status Record}, journal={Journal}, year={2026}, note={FULL-TEXT; internal status}}\n",
        encoding="utf-8",
    )

    result = await AssembleSurveyTool(_policy(ws)).execute()

    assert result.ok, result.content
    references = (ws / "drafts" / "survey" / "references.bib").read_text(encoding="utf-8")
    assert "FULL-TEXT" not in references
    assert "internal status" not in references
    assert "status2026" in references


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
    _write_valid_survey_bib(ws)
    (ws / "drafts" / "survey" / "survey.tex").write_text(_valid_survey_tex_document(), encoding="utf-8")
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
            + _valid_survey_section_body("Taxonomy", "\\citep{p1,p2,p3,p4}").split("\n", 1)[1]
            + " This changed sentence is appended after state fingerprint while the section remains a valid "
            "analytical framework with mechanism, boundary, classification, taxonomy, relationship, and evidence signals."
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
    bib_keys = [
        "102316journal203201032034219",
        "102316journal203201032034220",
        "102316journal203201032034221",
        "102316journal203201032034222",
    ]
    wrong_keys = [
        "102316journal201203032034219",
        "102316journal201203032034220",
        "102316journal201203032034221",
        "102316journal201203032034222",
    ]
    (ws / "literature" / "related_work.bib").write_text(
        "\n".join(
            f"@article{{{key}, author={{Repair, Test}}, title={{Correct Key {idx}}}, journal={{Repair Journal}}, year={{202{idx}}}}}"
            for idx, key in enumerate(bib_keys, start=1)
        )
        + "\n",
        encoding="utf-8",
    )
    section_path = ws / "drafts" / "survey" / "sections" / "background.tex"
    section_path.write_text(
        _valid_survey_section_body("Background and Scope", "\\citep{" + ",".join(wrong_keys) + "}"),
        encoding="utf-8",
    )
    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="background")

    ok, err = SurveyWriterAgent(mode="survey_section").validate_outputs(
        _survey_ctx(ws, "survey_section", section_id="background")
    )

    assert ok, err
    repaired_text = section_path.read_text(encoding="utf-8")
    assert not any(wrong_key in repaired_text for wrong_key in wrong_keys)
    assert all(bib_key in repaired_text for bib_key in bib_keys)


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
        _valid_survey_section_body("Taxonomy", "\\citep{missingKey2026}"),
        encoding="utf-8",
    )
    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="taxonomy")
    ok, err = agent.validate_outputs(_survey_ctx(ws, "survey_section", section_id="taxonomy"))
    assert not ok
    assert "missingKey2026" in (err or "")

    section_path.write_text(
        "\\section{Taxonomy}\n"
        + (
            "This section reviews prior work \\citep{p3,p4,p5,p6}. Smith et al. studied one dataset. "
            "Jones et al. proposed a related model. Lee et al. reported another benchmark. "
            "Garcia et al. explored a fourth direction. Kumar et al. added a fifth variant. "
            "Brown et al. proposed a taxonomy class. Martin et al. found another pattern. "
            "Chen et al. argued for an adjacent mechanism. Patel et al. studied a related setting. "
            "This taxonomy discussion names classification, mechanism, evidence boundary, framework, and relationship, "
            "but it deliberately avoids real comparison, limitation, tension, or tradeoff evaluation so the validator "
            "can detect a paper-by-paper summary rather than a genuine survey synthesis. "
        )
        * 4,
        encoding="utf-8",
    )
    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="taxonomy")
    ok, err = agent.validate_outputs(_survey_ctx(ws, "survey_section", section_id="taxonomy"))
    assert not ok
    assert "流水账" in (err or "") or "写作结构" in (err or "")


@pytest.mark.asyncio
async def test_t36_audit_rejects_runtime_process_prose(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    background_path = ws / "drafts" / "survey" / "sections" / "background.tex"
    background_path.write_text(
        _valid_survey_section_body("Background and Scope", "\\citep[see][]{p1,p2,p3,p4}")
        + " After deduplication and initial screening, 562 candidate papers were retained; "
        + "60 FULL-TEXT or PARTIAL-TEXT notes and 502 ABSTRACT-ONLY records formed the corpus, "
        + "with metadata triage used for additional backlog decisions.",
        encoding="utf-8",
    )
    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="background", status="revised")
    result = await AssembleSurveyTool(_policy(ws)).execute()
    assert result.ok, result.content

    result = await AuditSurveyCoverageTool(_policy(ws)).execute()

    assert not result.ok
    audit = json.loads((ws / "drafts" / "survey" / "survey_audit.json").read_text(encoding="utf-8"))
    process_check = next(item for item in audit["checks"] if item["name"] == "no_runtime_process_prose")
    assert process_check["passed"] is False
    assert "metadata triage" in process_check["detail"] or "FULL-TEXT" in process_check["detail"]


@pytest.mark.asyncio
async def test_t36_audit_rejects_low_citation_diversity_when_bib_is_large(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    bib_entries = [
        f"@article{{p{i}, author={{Author, A{i}}}, title={{Paper {i}}}, journal={{Journal}}, year={{202{i % 10}}}}}"
        for i in range(1, 81)
    ]
    (ws / "literature" / "related_work.bib").write_text("\n\n".join(bib_entries) + "\n", encoding="utf-8")
    result = await AuditSurveyCoverageTool(_policy(ws)).execute()

    assert not result.ok
    audit = json.loads((ws / "drafts" / "survey" / "survey_audit.json").read_text(encoding="utf-8"))
    diversity_check = next(item for item in audit["checks"] if item["name"] == "citation_diversity")
    assert diversity_check["passed"] is False
    assert "diversity minimum" in diversity_check["detail"]


@pytest.mark.asyncio
async def test_t36_audit_rejects_obvious_citation_claim_mismatch(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    (ws / "literature" / "related_work.bib").write_text(
        _valid_survey_bib()
        + "\n@article{curriculum2026, author={Course, Chen}, title={Curriculum Alignment in Higher Education}, journal={Education Review}, year={2026}}\n",
        encoding="utf-8",
    )
    background_path = ws / "drafts" / "survey" / "sections" / "background.tex"
    background_path.write_text(
        _valid_survey_section_body("Background and Scope", "\\citep[see][]{p1,p2,p3,p4}")
        + "\nMartial arts training significantly improves commercial entrepreneurship capability \\citep{curriculum2026}.",
        encoding="utf-8",
    )
    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="background", status="revised")
    result = await AssembleSurveyTool(_policy(ws)).execute()
    assert result.ok, result.content

    result = await AuditSurveyCoverageTool(_policy(ws)).execute()

    assert not result.ok
    audit = json.loads((ws / "drafts" / "survey" / "survey_audit.json").read_text(encoding="utf-8"))
    alignment_check = next(item for item in audit["checks"] if item["name"] == "citation_claim_alignment")
    assert alignment_check["passed"] is False
    assert "curriculum2026" in alignment_check["detail"]


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


@pytest.mark.asyncio
async def test_t36_assemble_refuses_stale_citation_support_inputs(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    agent = SurveyWriterAgent(mode="survey_assemble")
    ctx = _survey_ctx(ws, "survey_assemble")

    ok, err = agent.validate_outputs(ctx)
    assert ok, err

    notes_dir = ws / "literature" / "paper_notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "p1.md").write_text(
        "# Evidence Boundary Design\n\n## 1. Metadata\nThis note changes citation support after survey audit.\n",
        encoding="utf-8",
    )

    ok, err = agent.validate_outputs(ctx)

    assert not ok
    assert "survey_audit.json" in (err or "")
    assert "paper_notes" in (err or "")


@pytest.mark.asyncio
async def test_t36_assemble_validation_rejects_old_audit_missing_new_checks(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    audit_path = ws / "drafts" / "survey" / "survey_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["checks"] = [
        item
        for item in audit["checks"]
        if item.get("name")
        not in {"section_level_citation_density", "citation_claim_alignment", "no_runtime_process_prose", "bibliography_quality"}
    ]
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ok, err = SurveyWriterAgent(mode="survey_assemble").validate_outputs(_survey_ctx(ws, "survey_assemble"))

    assert not ok
    assert "缺少新增质量检查" in (err or "")


@pytest.mark.asyncio
async def test_t36_assemble_validation_rejects_old_audit_missing_citation_support_fingerprints(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    audit_path = ws / "drafts" / "survey" / "survey_audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    for key in ("citation_map", "paper_notes_dir", "abstract_notes_dir", "bridge_notes_dir"):
        audit["input_fingerprints"].pop(key, None)
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ok, err = SurveyWriterAgent(mode="survey_assemble").validate_outputs(_survey_ctx(ws, "survey_assemble"))

    assert not ok
    assert "缺少新增输入指纹" in (err or "")
    assert "paper_notes_dir" in (err or "")


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
        (survey_dir / "references.bib").read_text(encoding="utf-8")
        + "\n@article{new, author={New, Nora}, title={New Reference}, journal={New Journal}, year={2026}}\n",
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
        _valid_survey_bib(),
        encoding="utf-8",
    )
    tex = survey_dir / "survey.tex"
    tex.write_text(_valid_survey_tex_document(), encoding="utf-8")
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
                "## Review Contribution Review",
                "The survey contributes a framework-level research map rather than a paper-by-paper summary.",
                "## Language And Depth Review",
                "The survey uses one manuscript language consistently and section depth is adequate.",
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

    (ws / "drafts" / "survey" / "sections" / "comparison.tex").write_text(
        _valid_survey_section_body("Comparative Analysis", "\\citep{p5,p6,p7,p8,p9}")
        + " This revised comparison clarifies incomparable settings while preserving a framework-level research map.",
        encoding="utf-8",
    )
    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="comparison", status="revised")
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


@pytest.mark.asyncio
async def test_survey_audit_rejects_language_mixed_body_for_zh_survey(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    (ws / "project.yaml").write_text(
        "project_id: p\nresearch_direction: 智能算法风险综述\ntarget_venues:\n- 管理科学学报\n",
        encoding="utf-8",
    )
    state = json.loads((ws / "drafts" / "survey" / "survey_state.json").read_text(encoding="utf-8"))
    state.setdefault("shared_facts", {})["writing_language"] = "zh"
    (ws / "drafts" / "survey" / "survey_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result = await AssembleSurveyTool(_policy(ws)).execute()
    assert result.ok, result.content
    result = await AuditSurveyCoverageTool(_policy(ws)).execute()

    assert not result.ok
    audit = json.loads((ws / "drafts" / "survey" / "survey_audit.json").read_text(encoding="utf-8"))
    failed = [item["name"] for item in audit["checks"] if item["passed"] is False]
    assert "survey_language_consistency" in failed


@pytest.mark.asyncio
async def test_t36_section_rejects_short_introduction_and_language_mismatch(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    state = json.loads((ws / "drafts" / "survey" / "survey_state.json").read_text(encoding="utf-8"))
    state.setdefault("shared_facts", {})["writing_language"] = "zh"
    (ws / "drafts" / "survey" / "survey_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    intro_path = ws / "drafts" / "survey" / "sections" / "introduction.tex"
    intro_path.write_text(
        "\\section{Introduction}\nThis is a short English introduction for a Chinese survey.",
        encoding="utf-8",
    )
    await UpdateSurveySectionStateTool(_policy(ws)).execute(section_id="introduction")

    ok, err = SurveyWriterAgent(mode="survey_section").validate_outputs(
        _survey_ctx(ws, "survey_section", section_id="introduction")
    )

    assert not ok
    assert "篇幅不足" in (err or "") or "语言不一致" in (err or "")


@pytest.mark.asyncio
async def test_survey_review_rejects_low_language_consistency_risk(tmp_path: Path):
    ws = tmp_path
    await _build_valid_survey_chain(ws)
    (ws / "drafts" / "survey" / "survey_review.md").write_text(
        "\n".join(
            [
                "## Taxonomy Review\nok",
                "## Coverage Review\nok",
                "## Comparative Fairness Review\nok",
                "## Challenges Review\nok",
                "## Future Directions Review\nok",
                "## Scope And Craft Review\nok",
                "## Review Contribution Review\nok",
                "## Language And Depth Review\nBilingual consistency (LOW): abstract is Chinese while body is English.",
                "## Remaining Risks\nNo blocking issue remains.",
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
                    "section_id": "abstract",
                    "severity": "low",
                    "issue": "Bilingual consistency issue.",
                    "action_taken": "no_change_needed",
                    "evidence": "Chinese abstract with English body.",
                }
            ],
            "audit_after_review": {"survey_audit_passed": True},
        },
    )
    result = await BindSurveyReviewTool(_policy(ws)).execute()
    assert result.ok, result.content

    ok, err = SurveyWriterAgent(mode="survey_review").validate_outputs(
        _survey_ctx(ws, "survey_review")
    )

    assert not ok
    assert "语言一致性" in (err or "")


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
            "T3.6-TEMPLATE-GATE": {"agent": "survey_writer", "mode": "template_gate", "next_on_success": "T3.6-PLAN"},
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
                    {"id": "yes", "label": "写", "next": "T3.6-TEMPLATE-GATE"},
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
    assert state.current_task == "T3.6-TEMPLATE-GATE"
    survey_decision = json.loads((tmp_path / "drafts" / "survey" / "decision.json").read_text(encoding="utf-8"))
    assert survey_decision["write_survey"] is True
    assert isinstance(survey_decision.get("input_fingerprints"), dict)
    assert sm._parse_t36_survey_decision(tmp_path) == "T3.6-TEMPLATE-GATE"
    (tmp_path / "_runtime").mkdir(parents=True, exist_ok=True)
    (tmp_path / "_runtime" / "human_interactions.jsonl").write_text(
        json.dumps({"interaction_id": "human_template", "task_id": "T3.6-TEMPLATE-GATE"}) + "\n",
        encoding="utf-8",
    )
    _write_json(
        tmp_path / "drafts" / "survey" / "writing_template.json",
        {
            "template_family": "ccf",
            "template_id": "neurips",
            "writing_language": "en",
            "human_interaction_id": "human_template",
        },
    )
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


def test_t36_failure_routes_retry_repairable_survey_nodes():
    config = yaml.safe_load(system_config_path("state_machine.yaml").read_text(encoding="utf-8"))
    states = config["states"]
    expected = {
        "T3.6-SEC-BACKGROUND": "T3.6-SEC-BACKGROUND",
        "T3.6-SEC-TAXONOMY": "T3.6-SEC-TAXONOMY",
        "T3.6-SEC-THEME-1": "T3.6-SEC-THEME-1",
        "T3.6-SEC-THEME-2": "T3.6-SEC-THEME-2",
        "T3.6-SEC-THEME-3": "T3.6-SEC-THEME-3",
        "T3.6-SEC-THEME-4": "T3.6-SEC-THEME-4",
        "T3.6-SEC-COMPARISON": "T3.6-SEC-COMPARISON",
        "T3.6-SEC-CHALLENGES": "T3.6-SEC-CHALLENGES",
        "T3.6-SEC-FUTURE": "T3.6-SEC-FUTURE",
        "T3.6-SEC-INTRO": "T3.6-SEC-INTRO",
        "T3.6-SEC-CONCLUSION": "T3.6-SEC-CONCLUSION",
        "T3.6-SEC-ABSTRACT": "T3.6-SEC-ABSTRACT",
        "T3.6-ASSEMBLE": "T3.6-ASSEMBLE",
        "T3.6-REVIEW": "T3.6-REVIEW",
        "T3.6-COMPILE": "T3.6-ASSEMBLE",
    }
    for task_id, next_task in expected.items():
        assert states[task_id]["next_on_failure"] == next_task
