from __future__ import annotations

import pytest

from researchos.tools.human_gate import CLIHumanInterface, HumanInputUnavailable


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


@pytest.mark.asyncio
async def test_cli_gate_eof_pauses_instead_of_defaulting(monkeypatch):
    async def _run_gate():
        human = CLIHumanInterface()
        await human.present_gate(
            gate_id="custom_gate",
            presentation={},
            options=[{"id": "go", "label": "继续"}],
        )

    def raise_eof(_prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    with pytest.raises(HumanInputUnavailable):
        await _run_gate()


@pytest.mark.asyncio
async def test_t5_executor_gate_empty_input_defaults_to_mock(monkeypatch):
    answers = iter([""])

    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    human = CLIHumanInterface()
    result = await human.present_gate(
        gate_id="t5_executor_gate",
        presentation={},
        options=[
            {"id": "mock_dry_run", "label": "mock dry-run"},
            {"id": "claude_code_window", "label": "Claude Code"},
        ],
    )

    assert result["option_id"] == "mock_dry_run"


@pytest.mark.asyncio
async def test_t5_codex_cli_requires_yes_confirmation(monkeypatch):
    answers = iter(["3", "no"])

    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    human = CLIHumanInterface()
    result = await human.present_gate(
        gate_id="t5_executor_gate",
        presentation={},
        options=[
            {"id": "mock_dry_run", "label": "mock dry-run"},
            {"id": "claude_code_window", "label": "Claude Code"},
            {"id": "codex_cli", "label": "Codex CLI 真实执行"},
        ],
    )

    assert result["option_id"] == "claude_code_window"
    assert result["captured"]["downgraded_from"] == "codex_cli"
