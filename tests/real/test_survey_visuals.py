from __future__ import annotations

import json
from pathlib import Path
import shutil
import struct

import pytest

from researchos.runtime.config import LatexSettings
from researchos.tools.docker_exec import DockerExecTool
from researchos.tools.latex_compile import LatexCompileTool
from researchos.tools.survey_tools import BuildSurveyFiguresTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


@pytest.mark.asyncio
async def test_real_survey_visual_generation_and_latex_embedding(tmp_path: Path):
    if shutil.which("latexmk") is None or shutil.which("pdflatex") is None:
        pytest.skip("native LaTeX toolchain unavailable")
    workspace = tmp_path / "workspace"
    literature = workspace / "literature"
    survey_dir = workspace / "drafts" / "survey"
    literature.mkdir(parents=True)
    survey_dir.mkdir(parents=True)
    (literature / "comparison_table.csv").write_text(
        "id,title,year,method_family,evidence_level\n"
        "p1,One,2022,Prompting,FULL_TEXT\n"
        "p2,Two,2023,Prompting,FULL_TEXT\n"
        "p3,Three,2024,Memory,PARTIAL_TEXT\n"
        "p4,Four,2024,Planning,FULL_TEXT\n"
        "p5,Five,2020,Memory,FULL_TEXT\n"
        "p6,Six,2021,Planning,FULL_TEXT\n"
        "p7,Seven,2022,Prompting,FULL_TEXT\n"
        "p8,Eight,2023,Memory,PARTIAL_TEXT\n"
        "p9,Nine,2024,Planning,FULL_TEXT\n"
        "p10,Ten,2024,Prompting,FULL_TEXT\n",
        encoding="utf-8",
    )
    policy = WorkspaceAccessPolicy(
        workspace,
        ["literature/", "drafts/survey/"],
        ["drafts/survey/"],
    )
    visual_result = await BuildSurveyFiguresTool(policy).execute()
    assert visual_result.ok, visual_result.content
    manifest = json.loads((survey_dir / "figures" / "survey_visual_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "generated"
    assert len(manifest["figures"]) == 2
    assert manifest["generation_policy"]["min_rows_per_figure"] == 8
    assert manifest["source"]["year_coverage"]["valid_rows"] == 10
    assert manifest["source"]["method_family_coverage"]["valid_rows"] == 10
    figure_path = survey_dir / "figures" / "survey_method_taxonomy.png"
    png = figure_path.read_bytes()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width, height = struct.unpack(">II", png[16:24])
    assert width >= 900
    assert height >= 500

    tex_path = survey_dir / "survey.tex"
    tex_path.write_text(
        "\\documentclass{article}\n\\usepackage{graphicx}\n\\begin{document}\n"
        "\\begin{figure}[h]\\centering\\includegraphics[width=\\textwidth]{figures/survey_method_taxonomy.png}\\caption{Method distribution.}\\end{figure}\n"
        "\\begin{table}[h]\\centering\\begin{tabular}{llllll}\n"
        "A & B & C & D & E & F \\\\ \n1 & 2 & 3 & 4 & 5 & 6 \\\\ \n"
        "\\end{tabular}\\caption{Wide table.}\\end{table}\n\\end{document}\n",
        encoding="utf-8",
    )
    compile_result = await LatexCompileTool(
        DockerExecTool(policy),
        LatexSettings(default_backend="auto", allow_docker_fallback=False),
    ).execute(tex_path="drafts/survey/survey.tex", engine="pdflatex", bibtex=False)
    assert compile_result.ok, compile_result.content
    assert (survey_dir / "survey.pdf").read_bytes().startswith(b"%PDF")
    assert "\\resizebox{\\textwidth}{!}{%" in tex_path.read_text(encoding="utf-8")
    report = json.loads((survey_dir / "survey_compile_report.json").read_text(encoding="utf-8"))
    assert report["table_layout"]["resizebox_inserted"] == 1


@pytest.mark.asyncio
async def test_sparse_survey_corpus_writes_skipped_manifest_without_decorative_images(tmp_path: Path):
    workspace = tmp_path / "workspace"
    literature = workspace / "literature"
    survey_dir = workspace / "drafts" / "survey"
    literature.mkdir(parents=True)
    survey_dir.mkdir(parents=True)
    (literature / "comparison_table.csv").write_text(
        "id,title,year,method_family\n"
        "p1,One,2023,Prompting\n"
        "p2,Two,2024,Memory\n"
        "p3,Three,2024,Planning\n"
        "p4,Four,2024,Prompting\n",
        encoding="utf-8",
    )
    policy = WorkspaceAccessPolicy(
        workspace,
        ["literature/", "drafts/survey/"],
        ["drafts/survey/"],
    )

    result = await BuildSurveyFiguresTool(policy).execute()

    assert result.ok, result.content
    manifest_path = survey_dir / "figures" / "survey_visual_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "skipped"
    assert manifest["figures"] == []
    assert manifest["generation_policy"]["min_rows_per_figure"] == 8
    assert all("8" in item["reason"] for item in manifest["skipped"])
    assert not (survey_dir / "figures" / "survey_corpus_landscape.png").exists()
    assert not (survey_dir / "figures" / "survey_method_taxonomy.png").exists()
