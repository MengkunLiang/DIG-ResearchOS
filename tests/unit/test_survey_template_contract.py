from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from researchos.latex_templates import ccf_template_entries
from researchos.orchestration.state_machine import StateMachine
from researchos.runtime.orchestrator import AgentRunner
from researchos.runtime.abstract_sweep import _append_bib_entries
from researchos.tools.survey_tools import (
    _extract_survey_abstract,
    _latex_has_meaningful_content,
    _reconcile_survey_bibliography_authors,
    _render_survey_document,
    _survey_citation_coverage_contract,
    _survey_citation_diversity_diagnostic,
    _survey_citation_diversity_issues,
    _survey_traceable_citation_inventory,
    _survey_abstract_interface_source_issues,
    survey_audit_release_ready,
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


def test_traceable_citation_coverage_is_hard_and_evidence_bounded() -> None:
    inventory = {
        f"key_{index}": {
            "bib_key": f"key_{index}",
            "title": f"Paper {index}",
            "evidence_level": "ABSTRACT_ONLY" if index == 9 else "FULL_TEXT",
            "source_file": f"literature/deep_read_notes/paper_{index}.md",
            "aliases": [f"paper_{index}"],
            "citation_role": (
                "background_trend_or_boundary_only"
                if index == 9
                else "claim_evidence_after_note_verification"
            ),
        }
        for index in range(10)
    }
    state = {
        "sections": {
            "introduction": {"status": "written"},
            "background": {"status": "written"},
            "taxonomy": {"status": "written"},
            "comparison": {"status": "written"},
        }
    }
    contract = _survey_citation_coverage_contract(
        cited={"key_0"},
        bib_keys=set(inventory),
        inventory=inventory,
        state=state,
    )

    assert contract["eligible_traceable_keys"] == 10
    # The 50% global target is combined with the active-section floor.
    assert contract["required_unique_citations"] == 7
    assert contract["cited_traceable_keys"] == 1
    assert contract["missing_unique_citations"] == 6
    assert contract["minimum_coverage_ratio"] >= 0.50

    tex = "\\section{Taxonomy} A supported observation \\cite{key_0}."
    guidance = _survey_citation_diversity_diagnostic(
        tex,
        {"taxonomy": tex},
        coverage_contract=contract,
        inventory=inventory,
        survey_plan={},
    )
    abstract_candidate = next(item for item in guidance["unrepresented_candidates"] if item["bib_key"] == "key_9")
    assert abstract_candidate["citation_role"] == "background_trend_or_boundary_only"
    assert abstract_candidate["source_file"] == "literature/deep_read_notes/paper_9.md"
    assert "aliases" not in abstract_candidate
    queue_entry = guidance["section_review_queue"][0]
    assert queue_entry["candidate_notes_to_verify"]
    assert queue_entry["candidate_notes_to_verify"][0]["source_file"].startswith("literature/")

    issues = _survey_citation_diversity_issues(tex, {"key_0"}, contract)
    assert any("required=7" in issue and "missing=6" in issue for issue in issues)


def test_survey_release_rejects_legacy_or_failed_traceable_coverage_contract() -> None:
    legacy_audit = {
        "passed": True,
        "checks": [{"name": "citation_diversity", "passed": True, "level": "PASS", "detail": "legacy"}],
        "repair_guidance": {},
    }
    ready, failures, warnings = survey_audit_release_ready(legacy_audit)
    assert not ready
    assert failures and "traceable citation-coverage contract" in failures[0]
    assert warnings == []

    failed_audit = {
        "passed": False,
        "checks": [
            {
                "name": "citation_diversity",
                "passed": False,
                # A legacy audit could retain this old level; release policy
                # must still treat the named coverage check as hard.
                "level": "WARN",
                "detail": "required=6, current=1",
            }
        ],
        "repair_guidance": {
            "citation_diversity": {
                "coverage_contract": {"version": "traceable_bibliography_coverage.v1"},
            }
        },
    }
    ready, failures, warnings = survey_audit_release_ready(failed_audit)
    assert not ready
    assert failures == ["citation_diversity: required=6, current=1"]
    assert warnings == []


def test_traceable_coverage_scope_excludes_retrieval_noise(tmp_path: Path) -> None:
    literature = tmp_path / "literature"
    notes = literature / "deep_read_notes"
    notes.mkdir(parents=True)
    bibtex = (
        "@article{relevant,\n"
        "  title = {Generative AI Advice and Human Capability},\n"
        "  year = {2026},\n"
        "}\n\n"
        "@article{noise,\n"
        "  title = {Search for Stable Hadronising Squarks and Gluinos},\n"
        "  year = {2026},\n"
        "}\n"
    )
    (literature / "related_work.bib").write_text(bibtex, encoding="utf-8")
    (notes / "relevant.md").write_text(
        "# Generative AI Advice and Human Capability\n"
        "- **ID**: relevant\n"
        "- **Status**: FULL\n",
        encoding="utf-8",
    )
    (notes / "noise.md").write_text(
        "# Search for Stable Hadronising Squarks and Gluinos\n"
        "- **ID**: noise\n"
        "- **Status**: FULL\n",
        encoding="utf-8",
    )
    plan = {
        "survey_title": "Generative AI Advice and Human Capability",
        "central_question": "When does AI advice build human capability rather than dependency?",
        "scope_boundaries": {"included": ["AI advice, learning, and cognitive capability"]},
        "taxonomy": {"dimension": "Advice design", "tree": []},
    }

    inventory, diagnostics = _survey_traceable_citation_inventory(
        tmp_path,
        bibtex,
        {"relevant", "noise"},
        survey_plan=plan,
    )

    assert set(inventory) == {"relevant"}
    assert inventory["relevant"]["source_file"] == "literature/deep_read_notes/relevant.md"
    assert inventory["relevant"]["scope_relevance"]
    assert diagnostics["traceability_qualified_keys"] == 2
    assert diagnostics["scope_excluded_entries"] == [
        {
            "bib_key": "noise",
            "title": "Search for Stable Hadronising Squarks and Gluinos",
            "evidence_level": "FULL_TEXT",
            "reason": "no_deterministic_link_to_survey_plan",
        }
    ]


def test_t36_repair_feedback_injects_section_notes_and_evidence_limits(tmp_path: Path) -> None:
    audit_path = tmp_path / "drafts" / "survey" / "survey_audit.json"
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text(
        json.dumps(
            {
                "repair_guidance": {
                    "citation_diversity": {
                        "coverage_contract": {
                            "required_unique_citations": 12,
                            "eligible_traceable_keys": 24,
                            "minimum_coverage_ratio": 0.5,
                            "cited_traceable_keys": 9,
                            "missing_unique_citations": 3,
                        },
                        "section_review_queue": [
                            {
                                "section_id": "taxonomy",
                                "candidate_notes_to_verify": [
                                    {
                                        "bib_key": "scaffold2026",
                                        "evidence_level": "ABSTRACT_ONLY",
                                        "source_file": "literature/shallow_read_notes/scaffold2026.md",
                                    }
                                ],
                            }
                        ],
                    },
                    "quality_warnings": [
                        {
                            "check": "survey_section_depth",
                            "action": "Rewrite the section as synthesis.",
                        }
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ctx = SimpleNamespace(task_id="T3.6-ASSEMBLE", workspace_dir=tmp_path)

    feedback = AgentRunner._validation_repair_feedback(
        ctx=ctx,
        error="survey_audit.json 存在硬失败: citation_diversity",
    )

    assert "硬目标：12 个可追溯不同引用 / 24 个可用条目（50%）；当前 9，还差 3" in feedback
    assert "scaffold2026 [ABSTRACT_ONLY; literature/shallow_read_notes/scaffold2026.md]" in feedback
    assert "ABSTRACT-ONLY 仅可用于背景、趋势、范围或证据边界" in feedback
    assert "Rewrite the section as synthesis." in feedback
    assert "citation padding" in feedback
