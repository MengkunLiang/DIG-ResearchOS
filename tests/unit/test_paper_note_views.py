from __future__ import annotations

from researchos.agents.reader import _validate_current_note_extensions
from researchos.paper_notes import compact_paper_note_view
from researchos.runtime.progress import summarize_reader_note_progress


def test_compact_paper_note_view_keeps_only_researcher_facing_fields(tmp_path):
    note = tmp_path / "paper.md"
    note.write_text(
        """# A Paper\n\n- **Status**: [PARTIAL-TEXT]\n\n## 1. Problem & Motivation\nA bounded problem.\n\n## 3. Key Results\nA bounded finding.\n\n## 13. Mechanism Claim\n- **Stated mechanism**: A testable mechanism.\n\n## 20. Implications & Field-level Provenance\n- **Scientific implication**: It changes a scientific explanation.\n- **Scientific basis**: author_stated\n- **Scientific evidence**: Section 5\n- **Engineering / deployment implication**: It may improve reliability.\n- **Engineering basis**: system_inference\n- **Engineering evidence**: Section 6\n- **Practical / managerial / business implication**: not applicable\n- **Practical basis**: not_applicable\n- **Practical evidence**: none\n- **Field-level provenance**:\n| Field group | Provenance | Evidence location |\n|-------------|------------|-------------------|\n| Problem | author_stated | Section 1 |\n""",
        encoding="utf-8",
    )

    view = compact_paper_note_view(note, workspace_dir=tmp_path)

    assert view.problem == "A bounded problem."
    assert view.mechanism == "A testable mechanism."
    assert view.finding == "A bounded finding."
    assert view.engineering_implication == "It may improve reliability."
    assert view.implication_provenance["engineering"] == "system_inference"


def test_current_note_extension_requires_provenance_and_all_implication_statuses(tmp_path):
    note = tmp_path / "paper.md"
    note.write_text("- **Note schema version**: 2\n\n## 20. Implications & Field-level Provenance\n", encoding="utf-8")

    valid, error = _validate_current_note_extensions(note, note.read_text(encoding="utf-8"))

    assert valid is False
    assert "Scientific implication" in str(error)


def test_reader_progress_uses_the_compact_note_view_without_dumping_the_note():
    summary = summarize_reader_note_progress(
        {
            "paper_title": "A Bounded Paper",
            "note_status": "FULL-TEXT",
            "status": "complete",
            "progress": "1/2 target notes complete",
            "compact_note_view": {
                "problem": "A detailed problem that belongs in the saved note.",
                "mechanism": "A bounded mechanism is tested with an active and disabling comparison.",
                "finding": "A detailed finding that is not needed when the mechanism is available.",
                "scientific_implication": "The result would sharpen the scientific explanation of the target condition.",
                "engineering_implication": "not applicable",
                "practical_implication": "not applicable",
            },
        }
    )

    assert "机制：A bounded mechanism" in summary
    assert "含义：The result would sharpen" in summary
    assert "detailed problem" not in summary
    assert "detailed finding" not in summary
