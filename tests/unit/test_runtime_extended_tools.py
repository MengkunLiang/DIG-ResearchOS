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
from researchos.tools.glob_files import GlobFilesTool
from researchos.tools.grep_search import GrepSearchTool
from researchos.tools.literature_synthesis import BuildSynthesisWorkbenchTool
from researchos.tools.manuscript import (
    AssembleManuscriptTool,
    AuditManuscriptClaimsTool,
    BuildManuscriptRevisionPatchesTool,
    BuildManuscriptResourceIndexTool,
    InitializeManuscriptStateTool,
    PlanManuscriptEvidenceTool,
    PlanManuscriptSectionsTool,
    UpdateManuscriptSectionStateTool,
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
    policy = WorkspaceAccessPolicy(tmp_workspace, ["", "drafts/"], ["drafts/"])
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


def _prepare_manuscript_workspace(workspace: Path) -> None:
    (workspace / "literature").mkdir(parents=True, exist_ok=True)
    (workspace / "ideation").mkdir(parents=True, exist_ok=True)
    (workspace / "experiments").mkdir(parents=True, exist_ok=True)
    (workspace / "drafts" / "sections").mkdir(parents=True, exist_ok=True)
    (workspace / "project.yaml").write_text("project_id: p\nresearch_direction: Test\n", encoding="utf-8")
    (workspace / "literature" / "synthesis.md").write_text("# Synthesis\nPrior work shows a gap.\n", encoding="utf-8")
    (workspace / "literature" / "related_work.bib").write_text(
        "@article{smith2024,\n title={A Paper},\n year={2024}\n}\n",
        encoding="utf-8",
    )
    (workspace / "literature" / "comparison_table.csv").write_text("paper,metric\nA,0.7\n", encoding="utf-8")
    (workspace / "ideation" / "hypotheses.md").write_text("## H1\nHypothesis text.\n", encoding="utf-8")
    (workspace / "ideation" / "exp_plan.yaml").write_text("experiments:\n- name: exp1\n", encoding="utf-8")
    (workspace / "ideation" / "novelty_audit.md").write_text("# Novelty\nLevel 2\n", encoding="utf-8")
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
    policy = WorkspaceAccessPolicy(tmp_workspace, ["", "drafts/"], ["drafts/"])

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
    assert paper_state["shared_facts"]["bib_keys"] == ["smith2024"]
    assert (tmp_workspace / "drafts" / "section_outlines" / "methodology.md").exists()
    ok, err = validator.validate_outputs(_WriterContext(tmp_workspace, "section_plan"))
    assert ok, err

    for name in [
        "abstract",
        "introduction",
        "related_work",
        "methodology",
        "experiments",
        "analysis",
        "limitations",
        "conclusion",
    ]:
        (tmp_workspace / "drafts" / "sections" / f"{name}.tex").write_text(
            f"\\section{{{name.replace('_', ' ').title()}}}\n"
            + (f"Content for {name} with 0.82 and \\cite{{smith2024}}. " * 5)
            + "\n",
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
    assembled = await assemble_tool.execute(target_venue="neurips")
    assert assembled.ok
    paper = (tmp_workspace / "drafts" / "paper.tex").read_text(encoding="utf-8")
    assert "\\documentclass" in paper
    assert "\\begin{document}" in paper
    assert "\\bibliography{related_work}" in paper
    assert "\\section{Introduction}" in paper

    audit_tool = AuditManuscriptClaimsTool(policy)
    audit = await audit_tool.execute()
    assert audit.ok
    assert audit.data["path"] == "drafts/manuscript_audit.md"
    audit_text = (tmp_workspace / "drafts" / "manuscript_audit.md").read_text(encoding="utf-8")
    assert audit_text.startswith("# Manuscript Mechanical Audit")
    assert "Citation Keys" in audit_text

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
        ],
        ToolBuildContext(policy=policy, human=MockHumanInterface()),
    )

    assert sorted(built) == [
        "bash_run",
        "build_manuscript_revision_patches",
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
