"""Compile every standalone source template bundled in ``latex_templete``.

The runtime's CCF catalog compiles generated manuscripts, but source examples
also need to remain independently usable for people who start from them.  This
matrix copies each source tree before compiling so auxiliary files never leak
between templates or into the repository checkout.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


SOURCE_TEMPLATES = {
    "ccf-latex-templates/AAAI/aaai2026_template.tex": "pdf",
    "ccf-latex-templates/ACL/acl_latex.tex": "pdf",
    "ccf-latex-templates/CVPR/main.tex": "pdf",
    "ccf-latex-templates/CVPR/rebuttal.tex": "pdf",
    "ccf-latex-templates/ECCV/main.tex": "pdf",
    "ccf-latex-templates/EMNLP/acl_latex.tex": "pdf",
    "ccf-latex-templates/ICCV/main.tex": "pdf",
    "ccf-latex-templates/ICCV/rebuttal.tex": "pdf",
    "ccf-latex-templates/ICLR/iclr2026_basic.tex": "pdf",
    "ccf-latex-templates/ICML/example_paper.tex": "pdf",
    "ccf-latex-templates/IJCAI/ijcai26.tex": "pdf",
    "ccf-latex-templates/NAACL/acl_latex.tex": "pdf",
    "ccf-latex-templates/NeurIPS/neurips_2026.tex": "pdf",
    "ccf-latex-templates/SIGKDD/kdd_basic.tex": "pdf",
    "ccf-latex-templates/VLDB/main.tex": "pdf",
    "normal/basic_en.tex": "pdf",
    "normal/basic_zh.tex": "xelatex",
    "utd/informs/INFORMS-ISRE-Template-6-10-2024/INFORMS-ISRE-Template.tex": "pdf",
    "utd/informs/informs_fallback.tex": "pdf",
    "utd/informs_basic.tex": "pdf",
}


pytestmark = pytest.mark.skipif(
    os.environ.get("RESEARCHOS_RUN_LATEX_MATRIX") != "1" or shutil.which("latexmk") is None,
    reason="set RESEARCHOS_RUN_LATEX_MATRIX=1 with latexmk installed to run the native source-template matrix",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _standalone_source_templates(template_root: Path) -> set[str]:
    return {
        path.relative_to(template_root).as_posix()
        for path in template_root.rglob("*.tex")
        if "\\documentclass" in path.read_text(encoding="utf-8", errors="ignore")
    }


def test_source_template_matrix_covers_every_standalone_tex_document() -> None:
    template_root = _repo_root() / "latex_templete"
    assert set(SOURCE_TEMPLATES) == _standalone_source_templates(template_root)


@pytest.mark.parametrize("relative_path, engine", SOURCE_TEMPLATES.items(), ids=lambda case: str(case))
def test_bundled_source_template_compiles_in_a_clean_directory(
    tmp_path: Path,
    relative_path: str,
    engine: str,
) -> None:
    template_root = _repo_root() / "latex_templete"
    source_path = template_root / relative_path
    output_dir = tmp_path / source_path.parent.name
    shutil.copytree(source_path.parent, output_dir)
    copied_tex = output_dir / source_path.name

    result = subprocess.run(
        ["latexmk", f"-{engine}", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", copied_tex.name],
        cwd=output_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=90,
        check=False,
    )
    pdf_path = copied_tex.with_suffix(".pdf")
    assert result.returncode == 0 and pdf_path.is_file() and pdf_path.read_bytes().startswith(b"%PDF"), (
        f"{relative_path} did not compile.\n{result.stdout[-5000:]}"
    )
