from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from researchos.runtime.config import LatexSettings
from researchos.tools.docker_exec import DockerExecTool
from researchos.tools.latex_compile import LatexCompileTool
from researchos.tools.workspace_policy import WorkspaceAccessPolicy


def _require_native_tex() -> None:
    required = ("latexmk", "pdflatex", "xelatex", "bibtex")
    missing = [command for command in required if shutil.which(command) is None]
    if missing:
        pytest.skip(f"native TeX toolchain unavailable: {', '.join(missing)}")


@pytest.mark.asyncio
async def test_native_latex_backend_compiles_chinese_and_bibliography(tmp_path: Path):
    """Exercise the native-first path used by host T3.6 and T9 runs."""

    _require_native_tex()
    workspace = tmp_path / "workspace"
    survey_dir = workspace / "drafts" / "survey"
    bundle_dir = workspace / "submission" / "bundle"
    survey_dir.mkdir(parents=True)
    bundle_dir.mkdir(parents=True)

    (survey_dir / "survey.tex").write_text(
        "\\documentclass{ctexart}\n"
        "\\begin{document}\n"
        "ResearchOS 中文 XeLaTeX 编译验证。\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    (bundle_dir / "main.tex").write_text(
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "A native bibliography check cites~\\cite{smith2024}.\n"
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
        LatexSettings(default_backend="auto", allow_docker_fallback=False),
    )

    survey_result = await tool.execute(
        tex_path="drafts/survey/survey.tex",
        engine="xelatex",
        bibtex=False,
    )
    bundle_result = await tool.execute(
        tex_path="submission/bundle/main.tex",
        engine="pdflatex",
        bibtex=True,
    )

    assert survey_result.ok, survey_result.content
    assert bundle_result.ok, bundle_result.content
    assert (survey_dir / "survey.pdf").read_bytes().startswith(b"%PDF")
    assert (bundle_dir / "main.pdf").read_bytes().startswith(b"%PDF")
    survey_report = json.loads((survey_dir / "survey_compile_report.json").read_text(encoding="utf-8"))
    bundle_report = json.loads((workspace / "submission" / "compile_report.json").read_text(encoding="utf-8"))
    assert survey_report["selected_backend"] == "latexmk"
    assert survey_report["engine"] == "native"
    assert bundle_report["selected_backend"] == "latexmk"
    assert bundle_report["success"] is True
