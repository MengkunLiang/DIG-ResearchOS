from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from researchos.agents.writer import WriterAgent
from researchos.runtime.config import RuntimeSettings, WebFetchSettings
from researchos.testing.mocks import MockHumanInterface
from researchos.tools.bash_run import BashRunTool
from researchos.tools.citation_graph import build_domain_map
from researchos.tools.glob_files import GlobFilesTool
from researchos.tools.grep_search import GrepSearchTool
from researchos.tools.ideation_tools import analyze_idea_concentration, compute_idea_novelty_signal
from researchos.tools.literature_synthesis import BuildSynthesisWorkbenchTool
from researchos.tools.manuscript import (
    AssembleManuscriptTool,
    AuditManuscriptClaimsTool,
    AuditWritingCraftTool,
    BuildAlignmentMatrixTool,
    BuildManuscriptRegistriesTool,
    BuildManuscriptRevisionPatchesTool,
    BuildManuscriptResourceIndexTool,
    InitializeManuscriptStateTool,
    PlanManuscriptEvidenceTool,
    PlanManuscriptSectionsTool,
    UpdateManuscriptSectionStateTool,
    audit_writing_craft,
)
from researchos.tools.survey_tools import (
    AssembleSurveyTool,
    AuditSurveyCoverageTool,
    BuildSurveyStateTool,
    ExportSurveyForIdeationTool,
    UpdateSurveySectionStateTool,
)
from researchos.tools.registry import ToolBuildContext, ToolRegistry
from researchos.tools.web_fetch import WebFetchAllowlist, WebFetchTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


class _TestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/hello":
            body = b"hello from server"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/hello")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


class _WriterContext:
    def __init__(self, workspace_dir: Path, mode: str, extra: dict | None = None):
        self.mode = mode
        self.workspace_dir = workspace_dir
        self.extra = {"phase": mode}
        if extra:
            self.extra.update(extra)


@pytest.fixture
def local_http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_bash_run_supports_workspace_env_and_truncation(tmp_workspace: Path):
    policy = WorkspaceAccessPolicy(tmp_workspace, ["", "drafts/", "literature/", "ideation/"], ["drafts/"])
    tool = BashRunTool(policy, max_output_bytes=32)

    result = await tool.execute(
        command="printf '%s %s' \"$MY_VALUE\" \"$(printf 'x%.0s' {1..64})\"",
        env={"MY_VALUE": "hello"},
        timeout_seconds=5,
    )

    assert result.ok
    assert "STDOUT:\nhello " in result.content
    assert "[output truncated at 32 bytes]" in result.content
    assert result.data["cwd"] == str(tmp_workspace)
    assert result.data["truncated"] is True


@pytest.mark.asyncio
async def test_bash_run_uses_skill_dir_as_cwd_candidate(tmp_path: Path, tmp_workspace: Path):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    skill_dir = tmp_path / "skill_bundle"
    skill_scripts = skill_dir / "scripts"
    skill_scripts.mkdir(parents=True)
    tool = BashRunTool(policy, skill_dir=skill_dir)

    result = await tool.execute(command="pwd", cwd="scripts", timeout_seconds=5)

    assert result.ok
    assert str(skill_scripts) in result.content
    assert result.data["cwd"] == str(skill_scripts)


@pytest.mark.asyncio
async def test_bash_run_blocks_cwd_escape_and_handles_timeout(tmp_workspace: Path):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    tool = BashRunTool(policy)

    denied = await tool.execute(command="pwd", cwd="/tmp", timeout_seconds=5)
    assert not denied.ok
    assert denied.error == "access_denied"

    timed_out = await tool.execute(
        command="python -c 'import time; time.sleep(2)'",
        timeout_seconds=1,
    )
    assert not timed_out.ok
    assert timed_out.error == "timeout"


@pytest.mark.asyncio
async def test_grep_search_python_fallback_finds_matches(monkeypatch, tmp_workspace: Path):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    (tmp_workspace / "src").mkdir()
    (tmp_workspace / "src" / "a.txt").write_text("Alpha\nbeta needle\n", encoding="utf-8")
    (tmp_workspace / "src" / "b.md").write_text("nothing\nNeedle again\n", encoding="utf-8")

    monkeypatch.setattr("researchos.tools.grep_search.shutil.which", lambda _: None)
    tool = GrepSearchTool(policy)
    result = await tool.execute(pattern="needle", path="src", glob="**/*", max_results=10)

    assert result.ok
    assert result.data["engine"] == "python"
    assert result.data["count"] == 2
    assert "src/a.txt:2:beta needle" in result.content
    assert "src/b.md:2:Needle again" in result.content


def test_build_domain_map_buckets_core_adjacent_and_boundary():
    papers = [
        {"id": "W_core_a", "title": "Core A", "source_bucket": "core"},
        {"id": "W_core_b", "title": "Core B", "source_bucket": "core"},
        {"id": "W_adj", "title": "Adjacent Mechanism", "search_bucket": "adjacent_field"},
        {"id": "W_boundary", "title": "Sparse Boundary"},
    ]

    domain_map = build_domain_map(
        papers_verified=papers,
        citation_edges=[
            {"source_id": "W_core_a", "referenced_works": ["W_core_b"], "related_works": ["W_adj"]},
        ],
    )

    assert domain_map["semantics"] == "domain_map_for_synthesis_and_ideation_not_final_gaps"
    assert ["W_core_a", "W_core_b"] in domain_map["citation_edges"]
    assert domain_map["bucket_assignments"]["W_core_a"] == "core"
    assert domain_map["bucket_assignments"]["W_adj"] == "adjacent"
    assert domain_map["bucket_assignments"]["W_boundary"] == "boundary"
    adjacent = {item["id"]: item for item in domain_map["adjacent"]}
    assert adjacent["W_adj"]["bridges_to_core"] == ["W_core_a"]


def test_build_domain_map_seed_bucket_does_not_force_core():
    papers = [
        {"id": "W_seed_adj", "title": "Seed Adjacent Theory", "source_bucket": "adjacent_field"},
        {"id": "W_seed_boundary", "title": "Seed Boundary Probe", "source_bucket": "seed"},
        {"id": "W_core", "title": "Core Method", "source_bucket": "core"},
    ]

    domain_map = build_domain_map(
        papers_verified=papers,
        citation_edges=[["W_core", "W_seed_adj"]],
    )

    core_ids = {item["id"] for item in domain_map["core"]}
    adjacent_ids = {item["id"] for item in domain_map["adjacent"]}
    boundary_ids = {item["id"] for item in domain_map["boundary"]}
    assert "W_seed_adj" not in core_ids
    assert "W_seed_adj" in adjacent_ids
    assert "W_seed_boundary" not in core_ids
    assert "W_seed_boundary" in adjacent_ids or "W_seed_boundary" in boundary_ids
    assert domain_map["bucket_assignments"]["W_seed_boundary"] == "seed"


def test_audit_writing_craft_warns_when_related_work_ignores_pre_t5_signals():
    rows = [
        {
            "cid": "C1",
            "related_gap": {
                "tension": "uniformity assumptions conflict with subgroup robustness",
                "nearest_prior_work": {"work": "Smith2024 subgroup robustness", "distance": "moderate"},
            },
            "experiment": {"rq": "RQ1", "table": "tab:main", "result_metric": "Recall@20"},
        },
        {
            "cid": "C2",
            "related_gap": {
                "tension": "noise schedules ignore adaptive activity",
                "nearest_prior_work": {"work": "Jones2025 adaptive noise", "distance": "distant"},
            },
            "experiment": {"rq": "RQ2", "table": "tab:abl", "result_metric": "NDCG@20"},
        },
        {
            "cid": "C3",
            "related_gap": {
                "tension": "contrastive training lacks boundary analysis",
                "nearest_prior_work": {"work": "Lee2023 contrastive boundary", "distance": "very_close"},
            },
            "experiment": {"rq": "RQ3", "table": "tab:fail", "result_metric": "Coverage"},
        },
    ]
    section_texts = {
        "abstract": "Problem gap approach result contribution. " * 25,
        "introduction": "\\paragraph{Contribution 1} % [C1]\nA.\n\\paragraph{Contribution 2} % [C2]\nB.\n\\paragraph{Contribution 3} % [C3]\nC.",
        "related_work": "\\subsection{Prior Work}\n% [C1]\nPrior work is discussed.\n% [C2]\nMore work is discussed.\n% [C3]\nMore work is discussed.",
        "experiments": "% [C1]\nRQ1 tab:main Recall@20\n% [C2]\nRQ2 tab:abl NDCG@20\n% [C3]\nRQ3 tab:fail Coverage",
        "analysis": "% [C1]\nAnalysis.\n% [C2]\nAnalysis.\n% [C3]\nAnalysis.",
        "conclusion": "\\subsection{Limitations}\nNo new claims.",
    }
    audit = audit_writing_craft(
        paper="\n".join(section_texts.values()),
        section_texts=section_texts,
        paper_state={"shared_facts": {"result_metrics": ["Recall@20", "NDCG@20", "Coverage"], "alignment_matrix": rows}},
        alignment_matrix={"rows": rows},
        cdr_ledger={"contribution_chains": rows},
        venue_style="ccf_a",
    )

    check = next(item for item in audit["json"]["checks"] if item["name"] == "related_work_pre_t5_signal_consumption")
    assert check["level"] == "WARN"

    section_texts["related_work"] += "\nThis subsection compares the nearest prior work Smith2024 subgroup robustness and the cross-paper tension."
    audit = audit_writing_craft(
        paper="\n".join(section_texts.values()),
        section_texts=section_texts,
        paper_state={"shared_facts": {"result_metrics": ["Recall@20", "NDCG@20", "Coverage"], "alignment_matrix": rows}},
        alignment_matrix={"rows": rows},
        cdr_ledger={"contribution_chains": rows},
        venue_style="ccf_a",
    )
    check = next(item for item in audit["json"]["checks"] if item["name"] == "related_work_pre_t5_signal_consumption")
    assert check["level"] == "PASS"


def test_writing_craft_counts_itemize_contributions_and_allows_abstract_cites():
    rows = [
        {"cid": f"C{idx}", "experiment": {"rq": f"RQ{idx}", "table": "tab:main", "result_metric": "accuracy"}}
        for idx in range(1, 4)
    ]
    section_texts = {
        "abstract": "We position the setting against prior work \\cite{smith2024}. " * 20,
        "introduction": (
            "\\paragraph{Contributions}\n"
            "\\begin{itemize}\n"
            "\\item We identify a concrete gap.\n"
            "\\item We introduce a method.\n"
            "\\item We validate the mechanism.\n"
            "\\end{itemize}\n"
        ),
        "related_work": "\\subsection{Prior Work}\nPrior work discusses the nearest prior work and cross-paper tension.",
        "experiments": "RQ1 tab:main accuracy\nRQ2 tab:main accuracy\nRQ3 tab:main accuracy",
        "analysis": "Analysis.",
        "conclusion": "\\subsection{Limitations}\nNo new claims.",
    }
    audit = audit_writing_craft(
        paper="\n".join(section_texts.values()),
        section_texts=section_texts,
        paper_state={"shared_facts": {"result_metrics": ["accuracy"], "alignment_matrix": rows}},
        alignment_matrix={"rows": rows},
        cdr_ledger={"contribution_chains": rows},
        venue_style="ccf_a",
    )

    checks = {item["name"]: item for item in audit["json"]["checks"]}
    assert checks["intro_contribution_count"]["level"] == "PASS"
    assert "abstract_no_cite" not in checks


def test_ideation_soft_signal_tools_are_diagnostic_not_gates():
    scorecard = {
        "ideas": [
            {
                "idea": {"id": "D1", "idea_origin": "evidence_driven"},
                "nearest_prior_work": {"work": "Smith2024", "distance": "very_close"},
            },
            {
                "idea": {"id": "D2", "idea_origin": "problem_reframing"},
                "nearest_prior_work": {"work": "Smith2024", "distance": "moderate"},
            },
            {
                "idea": {"id": "D3", "idea_origin": "cross_domain_analogy"},
                "nearest_prior_work": {"work": "Jones2025", "distance": "distant"},
            },
        ]
    }
    concentration = analyze_idea_concentration(scorecard)
    assert concentration["semantics"] == "idea_concentration_soft_telemetry_not_gate"
    assert concentration["concentration_flags"]
    assert "集中度提示" in concentration["human_hint"]

    domain_map = {
        "core": [{"id": "W1", "title": "Graph contrastive recommendation perturbation"}],
        "adjacent": [{"id": "W2", "title": "Control feedback stabilization"}],
        "boundary": [],
    }
    marginal = compute_idea_novelty_signal(
        {"title": "Graph contrastive recommendation perturbation", "cdr_tuple": {}},
        domain_map,
    )
    adjacent = compute_idea_novelty_signal(
        {"title": "Feedback stabilization for sparse recommenders", "cdr_tuple": {}},
        domain_map,
    )
    distant = compute_idea_novelty_signal({"title": "Unseen mechanism", "cdr_tuple": {}}, domain_map)
    assert marginal["signal"] == "marginal_zone"
    assert adjacent["signal"] == "adjacent_zone"
    assert distant["signal"] == "no_nearby_cluster"
    assert marginal["semantics"].endswith("_not_gate")


def _prepare_manuscript_workspace(workspace: Path) -> None:
    (workspace / "literature").mkdir(parents=True, exist_ok=True)
    (workspace / "ideation").mkdir(parents=True, exist_ok=True)
    (workspace / "experiments").mkdir(parents=True, exist_ok=True)
    (workspace / "drafts" / "sections").mkdir(parents=True, exist_ok=True)
    (workspace / "project.yaml").write_text("project_id: p\nresearch_direction: Test\n", encoding="utf-8")
    (workspace / "literature" / "synthesis.md").write_text("# Synthesis\nPrior work shows a gap.\n", encoding="utf-8")
    (workspace / "literature" / "synthesis_workbench.json").write_text(
        json.dumps(
            {
                "adjacent_transfers": [
                    {
                        "mechanism": "feedback stabilization",
                        "source_papers": ["W_adjacent"],
                        "transfer_hypothesis_hint": "stabilize sparse feedback",
                    }
                ],
                "cross_paper_tensions": [],
            }
        ),
        encoding="utf-8",
    )
    (workspace / "literature" / "domain_map.json").write_text(
        json.dumps(
            {
                "semantics": "domain_map_for_synthesis_and_ideation_not_final_gaps",
                "core": [{"id": "W_core", "title": "Core graph recommendation", "degree": 3}],
                "adjacent": [
                    {
                        "id": "W_adjacent",
                        "title": "Control feedback stabilization",
                        "degree": 1,
                        "bridges_to_core": ["W_core"],
                        "why_adjacent": "feedback mechanism may transfer",
                    }
                ],
                "boundary": [],
                "citation_edges": [["W_core", "W_adjacent"]],
                "bucket_assignments": {"W_core": "core", "W_adjacent": "adjacent"},
            }
        ),
        encoding="utf-8",
    )
    (workspace / "literature" / "related_work.bib").write_text(
        "@article{smith2024,\n title={A Paper},\n year={2024}\n}\n",
        encoding="utf-8",
    )
    (workspace / "literature" / "comparison_table.csv").write_text("paper,metric\nA,0.7\n", encoding="utf-8")
    (workspace / "ideation" / "hypotheses.md").write_text("## H1\nHypothesis text.\n", encoding="utf-8")
    (workspace / "ideation" / "exp_plan.yaml").write_text("experiments:\n- name: exp1\n", encoding="utf-8")
    (workspace / "ideation" / "novelty_audit.md").write_text("# Novelty\nLevel 2\n", encoding="utf-8")
    (workspace / "ideation" / "idea_scorecard.yaml").write_text(
        "ideas:\n"
        "- idea:\n"
        "    id: D1\n"
        "    title: Adaptive feedback stabilization\n"
        "    cdr_tuple:\n"
        "      contribution_type: improvement\n"
        "      design_rationale: stabilize sparse feedback\n"
        "      artifact: feedback controller\n"
        "  decision: {status: selected}\n"
        "  hypothesis_refs: [H1]\n"
        "  counterfactual_check: survives_weakened\n"
        "  counterfactual_note: rationale remains without the nearest paper\n"
        "  nearest_prior_work: {work: smith2024, distance: moderate}\n"
        "  novelty_signal: adjacent_zone\n",
        encoding="utf-8",
    )
    (workspace / "experiments" / "results_summary.json").write_text(
        '{"experiments":[{"experiment_id":"exp1","metrics":{"accuracy":0.82}}]}\n',
        encoding="utf-8",
    )
    (workspace / "experiments" / "ablations.csv").write_text(
        "experiment_id,hypothesis_ref,ablation_type,metric,value,baseline_value,delta\n"
        "exp1,H1,remove_x,accuracy,0.80,0.82,-0.02\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_manuscript_resource_index_plan_assemble_and_audit(tmp_workspace: Path):
    _prepare_manuscript_workspace(tmp_workspace)
    policy = WorkspaceAccessPolicy(tmp_workspace, ["", "drafts/", "literature/", "ideation/"], ["drafts/"])

    index_tool = BuildManuscriptResourceIndexTool(policy)
    index_result = await index_tool.execute()
    assert index_result.ok
    index_path = tmp_workspace / "drafts" / "manuscript_resource_index.json"
    assert index_path.exists()
    resource_index = json.loads(index_path.read_text(encoding="utf-8"))
    assert "smith2024" in resource_index["bib_keys"]
    assert any(item["path"] == "experiments/results_summary.json" for item in resource_index["artifacts"])

    plan_tool = PlanManuscriptSectionsTool(policy)
    plan_result = await plan_tool.execute(target_venue="neurips")
    assert plan_result.ok
    section_plan_path = tmp_workspace / "drafts" / "section_plan.json"
    assert section_plan_path.exists()
    section_plan = json.loads(section_plan_path.read_text(encoding="utf-8"))
    assert any(section["id"] == "introduction" for section in section_plan["sections"])

    evidence_tool = PlanManuscriptEvidenceTool(policy)
    evidence_result = await evidence_tool.execute(target_venue="neurips")
    assert evidence_result.ok
    evidence_path = tmp_workspace / "drafts" / "evidence_plan.json"
    figure_table_path = tmp_workspace / "drafts" / "figure_table_plan.json"
    assert evidence_path.exists()
    assert figure_table_path.exists()
    evidence_plan = json.loads(evidence_path.read_text(encoding="utf-8"))
    figure_table_plan = json.loads(figure_table_path.read_text(encoding="utf-8"))
    assert any(
        slot["slot_id"] == "experiments_main_result"
        for slot in evidence_plan["claim_slots"]
    )
    assert any(
        visual["figure_id"] == "fig:main_results"
        for visual in figure_table_plan["planned_visuals"]
        if "figure_id" in visual
    )
    assert any(
        visual["table_id"] == "tab:main_results"
        for visual in figure_table_plan["planned_visuals"]
        if "table_id" in visual
    )

    registry_tool = BuildManuscriptRegistriesTool(policy)
    registry_result = await registry_tool.execute()
    assert registry_result.ok, registry_result.content
    assert (tmp_workspace / "drafts" / "cdr_claim_ledger.json").exists()
    assert (tmp_workspace / "drafts" / "claim_ledger.json").exists()
    assert (tmp_workspace / "drafts" / "figure_registry.json").exists()

    alignment_tool = BuildAlignmentMatrixTool(policy)
    alignment_result = await alignment_tool.execute()
    assert alignment_result.ok, alignment_result.content
    alignment = json.loads((tmp_workspace / "drafts" / "alignment_matrix.json").read_text(encoding="utf-8"))
    assert alignment["semantics"] == "alignment_matrix_seed_not_final_scientific_judgment"
    assert alignment["rows"]
    assert len(alignment["rows"]) == len(json.loads((tmp_workspace / "drafts" / "cdr_claim_ledger.json").read_text(encoding="utf-8"))["contribution_chains"])
    assert any(row["counterfactual"] == "survives_weakened" for row in alignment["rows"])
    assert any(row["nearest_prior_work"].get("work") == "smith2024" for row in alignment["rows"])
    assert any(row["novelty_signal"] == "adjacent_zone" for row in alignment["rows"])

    validator = WriterAgent()
    ok, err = validator.validate_outputs(_WriterContext(tmp_workspace, "resource_index"))
    assert ok, err

    (tmp_workspace / "drafts" / "outline.md").write_text(
        "# Outline\n"
        "## Title: Test Paper\n"
        "## Method\nProposed method details.\n"
        "## Experiments\nReport accuracy 0.82.\n"
        "## Introduction\nFrame the gap.\n",
        encoding="utf-8",
    )
    state_tool = InitializeManuscriptStateTool(policy)
    state_result = await state_tool.execute(target_venue="neurips")
    assert state_result.ok
    paper_state = json.loads((tmp_workspace / "drafts" / "paper_state.json").read_text(encoding="utf-8"))
    assert paper_state["semantics"] == "shared_state_for_section_by_section_writing_not_final_claims"
    assert paper_state["sections"]["methodology"]["file"] == "drafts/sections/methodology.tex"
    assert "limitations" not in paper_state["sections"]
    assert paper_state["shared_facts"]["bib_keys"] == ["smith2024"]
    assert paper_state["shared_facts"]["alignment_matrix"]
    assert (tmp_workspace / "drafts" / "section_outlines" / "methodology.md").exists()
    method_outline = (tmp_workspace / "drafts" / "section_outlines" / "methodology.md").read_text(encoding="utf-8")
    assert "## Responsible CIDs" in method_outline
    ok, err = validator.validate_outputs(_WriterContext(tmp_workspace, "section_plan"))
    assert ok, err

    for name in [
        "abstract",
        "introduction",
        "related_work",
        "methodology",
        "experiments",
        "analysis",
        "conclusion",
    ]:
        cid_lines = "\n".join(
            f"% [{row['cid']}]\nContent for {name} linked to {row['experiment']['rq']} "
            f"and {row['experiment']['table']} with accuracy 0.82."
            for row in alignment["rows"]
        )
        body = (cid_lines + "\n") * 3
        if name == "abstract":
            body = "This paper studies a gap. We propose a method. It achieves 0.82 accuracy. " * 25
        if name == "introduction":
            body = (
                "Gap 1 motivates C1. Gap 2 motivates C2. Gap 3 motivates C3.\n"
                "Contributions\n"
                "- % [C1] We improve the first alignment lane.\n"
                "- % [C2] We improve the second alignment lane.\n"
                "- % [C3] We improve the third alignment lane.\n"
            )
        if name == "related_work":
            body = "\n".join(
                f"\\subsection{{Rationale {idx}}}\n% [{row['cid']}]\nPrior work \\cite{{smith2024}} leaves a tension for {row['cid']}."
                for idx, row in enumerate(alignment["rows"], start=1)
            )
        if name == "conclusion":
            body += "\n\\subsection{Limitations}\nDirect-full evidence remains bounded.\n"
        (tmp_workspace / "drafts" / "sections" / f"{name}.tex").write_text(
            f"\\section{{{name.replace('_', ' ').title()}}}\n" + body + "\n",
            encoding="utf-8",
        )
    update_tool = UpdateManuscriptSectionStateTool(policy)
    updated = await update_tool.execute(section_id="methodology")
    assert updated.ok
    paper_state = json.loads((tmp_workspace / "drafts" / "paper_state.json").read_text(encoding="utf-8"))
    assert paper_state["sections"]["methodology"]["status"] == "written"
    ok, err = validator.validate_outputs(_WriterContext(tmp_workspace, "section_draft", {"section_id": "methodology"}))
    assert ok, err

    assemble_tool = AssembleManuscriptTool(policy)
    (tmp_workspace / "drafts" / "writing_style.json").write_text('{"venue_style":"both"}\n', encoding="utf-8")
    assembled = await assemble_tool.execute(target_venue="neurips", venue_style="both")
    assert assembled.ok
    assert (tmp_workspace / "drafts" / "is" / "paper.tex").exists()
    assert (tmp_workspace / "drafts" / "ccf_a" / "paper.tex").exists()
    paper = (tmp_workspace / "drafts" / "paper.tex").read_text(encoding="utf-8")
    assert "\\documentclass" in paper
    assert "\\begin{document}" in paper
    assert "\\bibliography{related_work}" in paper
    assert "\\section{Introduction}" in paper
    assert "\\section{Limitations}" not in paper

    audit_tool = AuditManuscriptClaimsTool(policy)
    audit = await audit_tool.execute()
    assert audit.ok
    assert audit.data["path"] == "drafts/manuscript_audit.md"
    audit_text = (tmp_workspace / "drafts" / "manuscript_audit.md").read_text(encoding="utf-8")
    assert audit_text.startswith("# Manuscript Mechanical Audit")
    assert "Citation Keys" in audit_text

    craft_tool = AuditWritingCraftTool(policy)
    craft = await craft_tool.execute(venue_style="both")
    assert craft.ok
    craft_text = (tmp_workspace / "drafts" / "craft_audit.md").read_text(encoding="utf-8")
    assert "Writing Craft And Alignment Audit" in craft_text
    craft_json = json.loads((tmp_workspace / "drafts" / "craft_audit.json").read_text(encoding="utf-8"))
    assert craft_json["semantics"] == "deterministic_writing_craft_audit_not_scientific_judgment"
    assert {item["name"] for item in craft_json["checks"]} >= {"matrix_row_count", "number_traceability"}
    assert (tmp_workspace / "drafts" / "is" / "craft_audit.json").exists()
    assert (tmp_workspace / "drafts" / "ccf_a" / "craft_audit.json").exists()
    ccf_variant = (tmp_workspace / "drafts" / "ccf_a" / "paper.tex").read_text(encoding="utf-8")
    assert "\\begin{abstract}" in ccf_variant
    (tmp_workspace / "drafts" / "ccf_a" / "paper.tex").write_text(
        ccf_variant.replace(
            "\\begin{abstract}",
            "\\begin{abstract}\nVariant abstract cites \\cite{smith2024}.",
            1,
        ),
        encoding="utf-8",
    )
    assert "\\cite{smith2024}" in (tmp_workspace / "drafts" / "ccf_a" / "paper.tex").read_text(encoding="utf-8")
    refreshed_craft = await craft_tool.execute(venue_style="both")
    assert refreshed_craft.ok
    ccf_craft = json.loads((tmp_workspace / "drafts" / "ccf_a" / "craft_audit.json").read_text(encoding="utf-8"))
    assert all(item["name"] != "abstract_no_cite" for item in ccf_craft["checks"])
    assert not any(
        item["level"] == "FAIL" and item["passed"] is False and "abstract" in item["name"]
        for item in ccf_craft["checks"]
    )

    review_dir = tmp_workspace / "drafts" / "review_rounds" / "round_1_sections"
    review_dir.mkdir(parents=True)
    (review_dir / "experiments.md").write_text(
        "# Section Review: experiments\n\n"
        "## Actionable Fixes\n"
        "- [High] Experiments reports accuracy without tying it to the result artifact.\n",
        encoding="utf-8",
    )
    (tmp_workspace / "drafts" / "review_rounds" / "round_1.md").write_text(
        "# Review\n\n## 主要问题\n- [Medium] Introduction overclaims the headline result.\n",
        encoding="utf-8",
    )
    patch_tool = BuildManuscriptRevisionPatchesTool(policy)
    patches = await patch_tool.execute(round_num=1)
    assert patches.ok
    patch_path = tmp_workspace / "drafts" / "patches" / "round_1_patches.json"
    patch_doc = json.loads(patch_path.read_text(encoding="utf-8"))
    assert patch_doc["semantics"] == "mechanical_review_issue_locations_not_final_revision_decisions"
    assert any(item["target_section"] == "experiments" for item in patch_doc["patches"])
    assert any(item["target_section"] == "introduction" for item in patch_doc["patches"])


@pytest.mark.asyncio
async def test_manuscript_audit_builds_index_fallback_and_accepts_real_citekeys(tmp_workspace: Path):
    _prepare_manuscript_workspace(tmp_workspace)
    policy = WorkspaceAccessPolicy(tmp_workspace, ["", "drafts/"], ["drafts/"])
    bib = tmp_workspace / "literature" / "related_work.bib"
    bib.write_text(
        "@inproceedings{arxiv:2301.12345,\n title={A Paper},\n year={2024}\n}\n"
        "@article{smith-2024.test,\n title={Another Paper},\n year={2024}\n}\n",
        encoding="utf-8",
    )
    (tmp_workspace / "drafts" / "paper.tex").write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{Introduction}\nText \\citep{arxiv:2301.12345}.\n"
        "\\section*{Literature Review}\nMore \\citet{smith-2024.test}.\n"
        "\\section{Methodology}\nMethod.\n"
        "\\section{Evaluation}\nMetric 0.82.\n"
        "\\section{Conclusions}\nDone.\n"
        "\\end{document}\n",
        encoding="utf-8",
    )

    audit_tool = AuditManuscriptClaimsTool(policy)
    audit = await audit_tool.execute()

    assert audit.ok
    audit_text = (tmp_workspace / "drafts" / "manuscript_audit.md").read_text(encoding="utf-8")
    assert "Missing BibTeX key" not in audit_text
    assert "Missing or nonstandard section" not in audit_text


@pytest.mark.asyncio
async def test_writing_craft_audit_reports_alignment_and_traceability_failures(tmp_workspace: Path):
    _prepare_manuscript_workspace(tmp_workspace)
    policy = WorkspaceAccessPolicy(tmp_workspace, ["", "drafts/", "literature/", "ideation/"], ["drafts/"])
    (tmp_workspace / "drafts" / "paper_state.json").write_text(
        json.dumps(
            {
                "version": "1.0",
                "semantics": "shared_state_for_section_by_section_writing_not_final_claims",
                "sections": {},
                "shared_facts": {
                    "bib_keys": ["smith2024"],
                    "result_metrics": [{"name": "accuracy", "value": 0.82}],
                    "alignment_matrix": [
                        {
                            "cid": "C1",
                            "motivation": "gap",
                            "contribution": "claim",
                            "related_gap": {"papers": ["smith2024"]},
                            "design_choice": "choice",
                            "experiment": {"rq": "RQ1", "result_metric": "accuracy", "table": "tab:main_results"},
                            "analysis": "analysis",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_workspace / "drafts" / "alignment_matrix.json").write_text(
        json.dumps(
            {
                "semantics": "alignment_matrix_seed_not_final_scientific_judgment",
                "rows": [
                    {
                        "cid": "C1",
                        "motivation": "gap",
                        "contribution": "claim",
                        "related_gap": {"papers": ["smith2024"]},
                        "design_choice": "choice",
                        "experiment": {"rq": "RQ1", "result_metric": "accuracy", "table": "tab:main_results"},
                        "analysis": "analysis",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_workspace / "drafts" / "cdr_claim_ledger.json").write_text(
        json.dumps(
            {
                "semantics": "cdr_claim_ledger_seed_not_final_scientific_judgment",
                "cdr_tuple": {"contribution_type": "improvement"},
                "contribution_chains": [{"cid": "C1"}],
                "contribution_claims": [],
            }
        ),
        encoding="utf-8",
    )
    sections = {
        "abstract": "This paper cites \\cite{smith2024}.",
        "introduction": "No contribution list here and number 0.77 is unsupported.",
        "related_work": "\\subsection{Prior}\nPrior work without cid.",
        "methodology": "% [C1]\nMethod text.",
        "experiments": "% [C1]\nExperiments omit the required table reference and report 0.77.",
        "analysis": "No cid anchor.",
        "conclusion": "\\section{Limitations}\nStandalone limitations.",
    }
    for name, text in sections.items():
        (tmp_workspace / "drafts" / "sections" / f"{name}.tex").write_text(text, encoding="utf-8")
    (tmp_workspace / "drafts" / "paper.tex").write_text(
        "\\documentclass{article}\\begin{document}\n" + "\n".join(sections.values()) + "\n\\end{document}",
        encoding="utf-8",
    )

    result = await AuditWritingCraftTool(policy).execute(venue_style="ccf_a")
    assert result.ok
    audit = json.loads((tmp_workspace / "drafts" / "craft_audit.json").read_text(encoding="utf-8"))
    failed = {item["name"] for item in audit["checks"] if item["level"] == "FAIL" and not item["passed"]}
    intro_check = next(item for item in audit["checks"] if item["name"] == "intro_contribution_count")
    assert intro_check["level"] == "WARN"
    assert all(item["name"] != "abstract_no_cite" for item in audit["checks"])
    assert "no_standalone_limitations" in failed
    assert "conclusion_has_limitations_subsection" in failed
    assert "number_traceability" in failed
    assert "cid_C1_experiment_artifact" in failed


@pytest.mark.asyncio
async def test_glob_files_lists_matches_and_respects_limit(tmp_workspace: Path):
    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    (tmp_workspace / "pkg" / "sub").mkdir(parents=True)
    (tmp_workspace / "a.txt").write_text("a", encoding="utf-8")
    (tmp_workspace / "pkg" / "b.txt").write_text("b", encoding="utf-8")
    (tmp_workspace / "pkg" / "sub" / "c.txt").write_text("c", encoding="utf-8")
    tool = GlobFilesTool(policy)

    result = await tool.execute(pattern="**/*.txt", limit=2)

    assert result.ok
    assert result.data["count"] == 2
    assert result.data["truncated"] is True
    assert "a.txt" in result.content


@pytest.mark.asyncio
async def test_web_fetch_fetches_text_and_follows_allowed_redirects(local_http_server: str):
    tool = WebFetchTool()

    result = await tool.execute(url=f"{local_http_server}/redirect", timeout_seconds=5, max_bytes=1024)

    assert result.ok
    assert result.content == "hello from server"
    assert result.data["status_code"] == 200
    assert result.data["redirect_chain"]


@pytest.mark.asyncio
async def test_web_fetch_enforces_allowlist(local_http_server: str):
    settings = RuntimeSettings(
        web_fetch=WebFetchSettings(
            allowed_schemes=("http",),
            allowed_hosts=("example.com",),
        )
    )
    tool = WebFetchTool(allowlist=WebFetchAllowlist.from_runtime_settings(settings))

    result = await tool.execute(url=f"{local_http_server}/hello", timeout_seconds=5, max_bytes=1024)

    assert not result.ok
    assert result.error == "access_denied"


def test_builtin_registry_registers_extended_tools(tmp_workspace: Path):
    from researchos.tools.builtin import register_builtin_tools

    policy = WorkspaceAccessPolicy(tmp_workspace, [""], [""])
    registry = ToolRegistry()
    register_builtin_tools(registry, RuntimeSettings())
    built = registry.build(
        [
            "bash_run",
            "grep_search",
            "glob_files",
            "web_fetch",
            "extract_paper_sections",
            "lookup_paper_record",
            "build_synthesis_workbench",
            "build_manuscript_revision_patches",
            "build_survey_state",
            "assemble_survey",
            "audit_survey_coverage",
        ],
        ToolBuildContext(policy=policy, human=MockHumanInterface()),
    )

    assert sorted(built) == [
        "assemble_survey",
        "audit_survey_coverage",
        "bash_run",
        "build_manuscript_revision_patches",
        "build_survey_state",
        "build_synthesis_workbench",
        "extract_paper_sections",
        "glob_files",
        "grep_search",
        "lookup_paper_record",
        "web_fetch",
    ]


def _note(paper_id: str, *, family_hint: str) -> str:
    return f"""# {family_hint} Paper {paper_id}

- **ID**: {paper_id}
- **Authors**: Ada, Bob
- **Venue**: TestConf (2025)
- **Status**: [FULL-TEXT]

## 2. Method Overview
This paper studies {family_hint} with a concrete mechanism for robust representation learning.

## 3. Key Results
- Accuracy: 88.1 [Evidence: p.4]

## 5. Limitations
- Limited sparse-data evaluation.

## 6. Relevance to Our Research
- Useful baseline for robustness and efficiency.

## 7. Technical Details Worth Noting
- Lightweight training objective.

## 9. Weaknesses / Gaps
- Missing deployment-oriented ablations.

## 11. My Questions
- Can the mechanism work under sparse feedback?
"""


@pytest.mark.asyncio
async def test_build_synthesis_workbench_writes_staged_outputs(tmp_workspace: Path):
    literature = tmp_workspace / "literature"
    notes_dir = literature / "paper_notes"
    notes_dir.mkdir(parents=True)
    for index in range(6):
        (notes_dir / f"paper_{index}.md").write_text(
            _note(f"paper_{index}", family_hint="LightGCN graph contrastive"),
            encoding="utf-8",
        )
    (literature / "comparison_table.csv").write_text(
        "id,title,year,venue,method_family,dataset,key_metric,metric_value\n"
        "paper_0,Paper 0,2025,TestConf,Graph,Dataset,Accuracy,88.1\n",
        encoding="utf-8",
    )
    (literature / "missing_areas.md").write_text("# 缺口\n稀疏数据鲁棒性覆盖不足。\n", encoding="utf-8")
    policy = WorkspaceAccessPolicy(tmp_workspace, ["", "literature/"], ["", "literature/"])
    tool = BuildSynthesisWorkbenchTool(policy)

    result = await tool.execute(write_final=False)

    assert result.ok
    assert (literature / "synthesis_workbench.json").exists()
    assert (literature / "synthesis_outline.md").exists()
    assert (literature / "synthesis_draft.md").exists()
    assert not (literature / "synthesis.md").exists()
    draft = (literature / "synthesis_draft.md").read_text(encoding="utf-8")
    assert "This is not a final literature synthesis" in draft
    assert "[paper_0]" in draft


@pytest.mark.asyncio
async def test_build_synthesis_workbench_uses_domain_map_for_adjacent_transfers(tmp_workspace: Path):
    literature = tmp_workspace / "literature"
    notes_dir = literature / "paper_notes"
    notes_dir.mkdir(parents=True)
    (notes_dir / "W_adj.md").write_text(
        _note("W_adj", family_hint="Control feedback")
        + "\n## A. 核心做法/视角\nFeedback stabilization under noisy control.\n"
        + "\n## B. 桥接点\nCan transfer to sparse recommender feedback loops.\n",
        encoding="utf-8",
    )
    (literature / "comparison_table.csv").write_text("id,title\nW_adj,Adjacent\n", encoding="utf-8")
    (literature / "domain_map.json").write_text(
        json.dumps(
            {
                "semantics": "domain_map_for_synthesis_and_ideation_not_final_gaps",
                "core": [{"id": "W_core", "title": "Core", "degree": 2}],
                "adjacent": [
                    {
                        "id": "W_adj",
                        "title": "Control feedback stabilization",
                        "degree": 1,
                        "bridges_to_core": ["W_core"],
                        "why_adjacent": "control feedback bridge",
                    }
                ],
                "boundary": [],
                "citation_edges": [["W_core", "W_adj"]],
                "bucket_assignments": {"W_core": "core", "W_adj": "adjacent"},
            }
        ),
        encoding="utf-8",
    )
    policy = WorkspaceAccessPolicy(tmp_workspace, ["", "literature/"], ["", "literature/"])
    tool = BuildSynthesisWorkbenchTool(policy)

    result = await tool.execute(write_final=False)

    assert result.ok, result.content
    workbench = json.loads((literature / "synthesis_workbench.json").read_text(encoding="utf-8"))
    assert workbench["citation_graph_context"]["citation_edges"] == [["W_core", "W_adj"]]
    assert workbench["domain_map_bucket_summary"]["adjacent"] == 1
    assert workbench["adjacent_transfers"]
    transfer = workbench["adjacent_transfers"][0]
    assert transfer["source_papers"] == ["W_adj"]
    assert "Feedback stabilization" in transfer["mechanism"]
    assert "sparse recommender" in transfer["transfer_hypothesis_hint"]
