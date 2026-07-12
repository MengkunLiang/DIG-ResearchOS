from __future__ import annotations

from researchos.agents.writer import _suggest_template_selection
from researchos.tools.manuscript import audit_writing_craft
from researchos.writing_profiles import resolve_venue_writing_profile, section_word_budget_ranges


def test_venue_profiles_keep_management_story_and_ccf_technical_modes_distinct():
    informs = resolve_venue_writing_profile("Information Systems Research")
    neurips = resolve_venue_writing_profile("NeurIPS 2026")
    icml = resolve_venue_writing_profile("ICML")
    iclr = resolve_venue_writing_profile("ICLR")
    kdd = resolve_venue_writing_profile("SIGKDD")

    assert informs["id"] == "informs_story"
    assert informs["narrative_mode"] == "rationale_mechanism_story"
    assert neurips["id"] == "neurips_concise"
    assert icml["id"] == "icml_concise"
    assert iclr["id"] == "iclr_concise"
    assert kdd["id"] == "kdd_technical"
    assert section_word_budget_ranges(informs)["introduction"][0] > section_word_budget_ranges(neurips)["introduction"][0]
    assert "official venue" in neurips["internal_budget_notice"].lower()


def test_writer_template_suggestion_preserves_specific_ccf_venue():
    assert _suggest_template_selection("KDD 2026")["template_id"] == "kdd"
    assert _suggest_template_selection("ICML 2026")["template_id"] == "icml"
    assert _suggest_template_selection("ICLR")["template_id"] == "iclr"
    assert _suggest_template_selection("NeurIPS")["template_id"] == "neurips"


def test_craft_audit_reports_profile_storyline_and_internal_section_diagnostics():
    profile = resolve_venue_writing_profile("ICLR")
    headings = profile["storyline_headings"]
    storyline = "\n".join(f"## {heading}\nEvidence-bounded note." for heading in headings)
    rows = [
        {"cid": f"C{idx}", "experiment": {"rq": f"RQ{idx}", "table": "tab:main", "result_metric": "accuracy"}}
        for idx in range(1, 4)
    ]
    sections = {
        "abstract": "We state a bounded technical contribution with evidence. " * 28,
        "introduction": "\\begin{itemize}\\item A\\item B\\item C\\end{itemize}",
        "related_work": "Nearest prior work and tension are discussed.",
        "methodology": "Method detail. " * 20,
        "experiments": "RQ1 tab:main accuracy RQ2 tab:main accuracy RQ3 tab:main accuracy.",
        "analysis": "Mechanism, sensitivity, and failure analysis.",
        "conclusion": "\\subsection{Limitations} Evidence boundaries remain explicit.",
    }

    audit = audit_writing_craft(
        paper="\n".join(sections.values()),
        section_texts=sections,
        paper_state={"shared_facts": {"result_metrics": ["accuracy"], "alignment_matrix": rows}},
        alignment_matrix={"rows": rows},
        cdr_ledger={"contribution_chains": rows},
        venue_style="ccf_a",
        writing_profile=profile,
        storyline_text=storyline,
    )

    checks = {item["name"]: item for item in audit["json"]["checks"]}
    assert audit["json"]["venue_writing_profile"]["id"] == "iclr_concise"
    assert audit["json"]["missing_storyline_headings"] == []
    assert checks["writing_storyline_coverage"]["level"] == "PASS"
    assert "section_budget_methodology" in checks
    assert "official venue limits" in audit["markdown"]
