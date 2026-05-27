from __future__ import annotations

from researchos.tools.human_gate import CLIHumanInterface


def test_cli_gate_parse_accepts_budget_extension_aliases():
    options = [
        {"id": "extend", "label": "继续，并增加 600 seconds"},
        {"id": "stop", "label": "停止本次运行"},
    ]

    assert CLIHumanInterface._parse_option_index("1", options) == 0
    assert CLIHumanInterface._parse_option_index("确认", options) == 0
    assert CLIHumanInterface._parse_option_index("继续", options) == 0
    assert CLIHumanInterface._parse_option_index("extend", options) == 0
    assert CLIHumanInterface._parse_option_index("停止", options) == 1
    assert CLIHumanInterface._parse_option_index("stop", options) == 1
    assert CLIHumanInterface._parse_option_index("2", options) == 1


def test_cli_gate_parse_accepts_label_and_custom_aliases():
    options = [
        {"id": "revise", "label": "修改计划", "aliases": ["调整"]},
        {"id": "accept", "label": "确认计划"},
    ]

    assert CLIHumanInterface._parse_option_index("调整", options) == 0
    assert CLIHumanInterface._parse_option_index("确认计划", options) == 1
