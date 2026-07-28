"""Compile every public paper-write skeleton in an isolated directory."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


TEMPLATE_NAMES = (
    "iclr2026.tex",
    "icml2025.tex",
    "ieee_conference.tex",
    "ieee_journal.tex",
    "neurips2025.tex",
)


pytestmark = pytest.mark.skipif(
    shutil.which("latexmk") is None,
    reason="latexmk is required to compile paper-write templates",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _write_minimal_inputs(template_dir: Path) -> None:
    sections = template_dir / "sections"
    sections.mkdir(exist_ok=True)
    for section in (
        "0_abstract",
        "1_introduction",
        "2_related_work",
        "3_method",
        "4_experiments",
        "5_conclusion",
        "A_appendix",
    ):
        citation = " See~\\cite{fixture}." if section == "1_introduction" else ""
        (sections / f"{section}.tex").write_text(
            f"Compilation smoke content for {section.replace('_', ' ')}.{citation}\n",
            encoding="utf-8",
        )
    (template_dir / "references.bib").write_text(
        "@article{fixture, author={Doe, Jane}, title={Fixture}, journal={Journal}, year={2026}}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("template_name", TEMPLATE_NAMES)
def test_paper_write_template_compiles_with_its_bundled_support_files(tmp_path: Path, template_name: str) -> None:
    source_dir = _repo_root() / "skills" / "paper-write" / "templates"
    output_dir = tmp_path / template_name.removesuffix(".tex")
    shutil.copytree(source_dir, output_dir)
    _write_minimal_inputs(output_dir)

    result = subprocess.run(
        ["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", template_name],
        cwd=output_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=90,
        check=False,
    )

    assert result.returncode == 0 and (output_dir / template_name.replace(".tex", ".pdf")).is_file(), (
        f"{template_name} did not compile.\n{result.stdout[-5000:]}"
    )
