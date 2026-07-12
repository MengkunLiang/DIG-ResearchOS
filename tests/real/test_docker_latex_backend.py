from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.runtime.config import LatexSettings
from researchos.tools.docker_exec import DockerExecTool, check_docker_environment
from researchos.tools.latex_compile import LatexCompileTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


def _require_docker_tex() -> None:
    ok, error, _details = check_docker_environment(image="researchos/system:latest")
    if not ok:
        pytest.skip(error or "Docker TeX image is not available in this environment")


@pytest.mark.asyncio
async def test_docker_latex_backend_compiles_bibliography_and_chinese_tex(tmp_path: Path):
    """Exercise the same Docker path T3.6 and T9 use on a real TeX image."""

    _require_docker_tex()
    workspace = tmp_path / "workspace"
    survey_dir = workspace / "drafts" / "survey"
    bundle_dir = workspace / "submission" / "bundle"
    survey_dir.mkdir(parents=True)
    bundle_dir.mkdir(parents=True)

    (survey_dir / "survey.tex").write_text(
        "\\documentclass{ctexart}\n"
        "\\begin{document}\n"
        "ResearchOS 中文编译验证。\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    (bundle_dir / "main.tex").write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "A real Docker bibliography check cites~\\cite{smith2024}.\n"
        "\\bibliographystyle{plain}\n"
        "\\bibliography{references}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    (bundle_dir / "references.bib").write_text(
        "@article{smith2024, author={Smith, Ada}, title={A Test Article}, "
        "journal={Test Journal}, year={2024}}\n",
        encoding="utf-8",
    )

    policy = WorkspaceAccessPolicy(workspace, ["", "drafts/", "submission/"], ["", "drafts/", "submission/"])
    tool = LatexCompileTool(
        DockerExecTool(policy),
        LatexSettings(
            default_backend="auto",
            allow_docker_fallback=True,
            docker_image="researchos/system:latest",
        ),
    )

    survey_result = await tool.execute(
        tex_path="drafts/survey/survey.tex",
        engine="xelatex",
        bibtex=False,
        backend="docker",
    )
    bundle_result = await tool.execute(
        tex_path="submission/bundle/main.tex",
        engine="pdflatex",
        bibtex=True,
        backend="docker",
    )

    assert survey_result.ok, survey_result.content
    assert bundle_result.ok, bundle_result.content
    assert (survey_dir / "survey.pdf").read_bytes().startswith(b"%PDF")
    assert (bundle_dir / "main.pdf").read_bytes().startswith(b"%PDF")
    survey_report = json.loads((survey_dir / "survey_compile_report.json").read_text(encoding="utf-8"))
    bundle_report = json.loads((workspace / "submission" / "compile_report.json").read_text(encoding="utf-8"))
    assert survey_report["selected_backend"] == "docker"
    assert survey_report["engine"] == "docker"
    assert bundle_report["selected_backend"] == "docker"
    assert bundle_report["success"] is True
