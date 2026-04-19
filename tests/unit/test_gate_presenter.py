from researchos.orchestration.gate_presenter import build_presentation


def test_gate_presenter_builds_file_dir_state_and_literal(tmp_workspace):
    (tmp_workspace / "drafts").mkdir()
    (tmp_workspace / "drafts" / "paper.tex").write_text("abcdef" * 20, encoding="utf-8")
    (tmp_workspace / "submission").mkdir()
    (tmp_workspace / "submission" / "a.pdf").write_text("x", encoding="utf-8")
    (tmp_workspace / "submission" / "b.tex").write_text("y", encoding="utf-8")

    gate_spec = {
        "title": "确认",
        "description": "描述",
        "presentation": {
            "paper_preview": {"from_file": "drafts/paper.tex", "max_chars": 10},
            "bundle": {"from_dir": "submission", "glob": "*", "max_items": 1},
            "cost": {"from_state": "budget_cumulative.cost_usd_total"},
            "hint": {"literal": "go"},
        },
    }
    state = {"budget_cumulative": {"cost_usd_total": 1.23}}

    presentation = build_presentation(gate_spec, state, tmp_workspace)

    assert presentation["_title"] == "确认"
    assert presentation["_description"] == "描述"
    assert "[... truncated" in presentation["paper_preview"]
    assert len(presentation["bundle"]) == 1
    assert presentation["cost"] == 1.23
    assert presentation["hint"] == "go"
