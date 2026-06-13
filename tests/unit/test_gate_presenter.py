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


def test_gate_presenter_supports_regex_extraction_from_file(tmp_workspace):
    (tmp_workspace / "evaluation").mkdir()
    (tmp_workspace / "evaluation" / "evaluation_decision.md").write_text(
        "## Option 1\nnext_task: T7\n",
        encoding="utf-8",
    )

    gate_spec = {
        "presentation": {
            "recommended_next_task": {
                "from_file_regex": {
                    "path": "evaluation/evaluation_decision.md",
                    "pattern": r"next_task:\s*([A-Za-z0-9_.-]+)",
                    "group": 1,
                    "default": "T8-WRITE",
                }
            }
        }
    }

    presentation = build_presentation(gate_spec, {}, tmp_workspace)

    assert presentation["recommended_next_task"] == "T7"


def test_gate_presenter_can_show_file_path_summary_without_storing_full_file(tmp_workspace):
    (tmp_workspace / "ideation").mkdir()
    (tmp_workspace / "ideation" / "_gate1_candidate_cards.md").write_text(
        "# Cards\n\n" + "D1 details\n" * 100,
        encoding="utf-8",
    )

    presentation = build_presentation(
        {
            "presentation": {
                "cards": {
                    "from_file": "ideation/_gate1_candidate_cards.md",
                    "mode": "path_summary",
                    "summary_chars": 30,
                }
            }
        },
        {},
        tmp_workspace,
    )

    assert presentation["cards"]["path"] == "ideation/_gate1_candidate_cards.md"
    assert presentation["cards"]["size_chars"] > 30
    assert "D1 details" in presentation["cards"]["summary"]
    assert "truncated from" in presentation["cards"]["summary"]
