from __future__ import annotations

import json
from pathlib import Path

import pytest

from researchos.latex_templates import ccf_template_entries
from researchos.orchestration.state_machine import StateMachine
from researchos.runtime.abstract_sweep import _append_bib_entries
from researchos.tools.survey_tools import (
    _extract_survey_abstract,
    _latex_has_meaningful_content,
    _reconcile_survey_bibliography_authors,
    _render_survey_document,
    _survey_abstract_interface_source_issues,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_informs_audit_requires_its_native_abstract_macro() -> None:
    invalid_informs = (
        "\\documentclass[isre,dblanonrev]{informs4}\n"
        "\\begin{document}\n\\begin{abstract}\nWrong interface.\n\\end{abstract}\n\\end{document}\n"
    )
    valid_informs = (
        "\\documentclass[isre,dblanonrev]{informs4}\n"
        "\\begin{document}\n\\ABSTRACT{%\nNative interface with \\textit{nested} markup.\n}%\n\\end{document}\n"
    )

    assert _extract_survey_abstract(invalid_informs) == ""
    assert _extract_survey_abstract(valid_informs) == "Native interface with \\textit{nested} markup."


@pytest.mark.parametrize(
    ("template_family", "template_id", "writing_language"),
    [
        ("basic_en", "basic_en", "en"),
        ("basic_zh", "basic_zh", "zh"),
        ("utd", "informs", "en"),
        *[("ccf", entry.template_id, "en") for entry in ccf_template_entries()],
    ],
)
def test_every_selectable_survey_template_uses_its_supported_abstract_interface(
    template_family: str,
    template_id: str,
    writing_language: str,
) -> None:
    abstract = "A compact template-contract abstract with \\textit{nested} markup."
    rendered = _render_survey_document(
        title="Template contract smoke test",
        abstract=abstract,
        body_sections=["\\section{Introduction}\nA minimal introduction."],
        writing_language=writing_language,
        template_selection={
            "template_family": template_family,
            "template_id": template_id,
            "writing_language": writing_language,
        },
        repo_root=_repo_root(),
    )

    extracted = _extract_survey_abstract(rendered)
    assert extracted == abstract
    if template_family == "utd" and template_id == "informs":
        assert "\\ABSTRACT{%" in rendered
        assert "\\begin{abstract}" not in rendered
    else:
        assert "\\begin{abstract}" in rendered


def test_section_sources_reject_template_owned_abstract_wrappers() -> None:
    assert _survey_abstract_interface_source_issues("Only abstract prose.") == []
    assert _survey_abstract_interface_source_issues("\\begin{abstract}Prose\\end{abstract}")
    assert _survey_abstract_interface_source_issues("\\ABSTRACT{Prose}")
    assert not _latex_has_meaningful_content("% generated placeholder\n  % still a placeholder\n")


def test_compile_gate_offers_direct_retry_only_for_environment_recovery(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    report_path = workspace / "drafts" / "survey" / "survey_compile_report.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(json.dumps({"error": "nonzero_exit"}), encoding="utf-8")

    assert not StateMachine._t36_compile_direct_retry_supported(workspace, "LaTeX compile failed")

    report_path.write_text(json.dumps({"error": "waiting_environment_latexmk_missing"}), encoding="utf-8")
    assert StateMachine._t36_compile_direct_retry_supported(workspace, "LaTeX compile failed")

    report_path.write_text(json.dumps({"success": True, "error": None}), encoding="utf-8")
    assert StateMachine._t36_compile_direct_retry_supported(workspace, "An old compile Gate is still visible")


def test_bibliography_append_upgrades_stale_entry_when_metadata_backfill_adds_author(tmp_path: Path) -> None:
    path = tmp_path / "related_work.bib"
    path.write_text(
        "@article{doi_10_1_example,\n  title = {Example},\n  year = {2026},\n}\n",
        encoding="utf-8",
    )
    _append_bib_entries(
        path,
        [
            "@article{doi_10_1_example,\n  title = {Example},\n  year = {2026},\n"
            "  author = {Ada Lovelace and Grace Hopper},\n}\n"
        ],
    )

    refreshed = path.read_text(encoding="utf-8")
    assert refreshed.count("@article{doi_10_1_example") == 1
    assert "author = {Ada Lovelace and Grace Hopper}" in refreshed


def test_survey_bibliography_projects_only_verified_local_author_metadata(tmp_path: Path) -> None:
    literature = tmp_path / "literature"
    literature.mkdir()
    (literature / "papers_verified.jsonl").write_text(
        json.dumps(
            {
                "doi": "10.1/example",
                "authors": ["Ada Lovelace", "Grace Hopper"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source = "@article{p_10_1_example,\n  title = {Example},\n  year = {2026},\n}\n"

    projected, repairs = _reconcile_survey_bibliography_authors(tmp_path, source, {"p_10_1_example"})

    assert "author = {Ada Lovelace and Grace Hopper}" in projected
    assert repairs == [
        {
            "bib_key": "p_10_1_example",
            "doi": "10.1/example",
            "authors": ["Ada Lovelace", "Grace Hopper"],
            "source_record": "literature/papers_verified.jsonl",
        }
    ]

    unbacked, missing_repairs = _reconcile_survey_bibliography_authors(tmp_path, source, {"unmatched_key"})
    assert unbacked == source
    assert missing_repairs == []
