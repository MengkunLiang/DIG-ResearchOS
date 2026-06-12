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


def test_t2_literature_gate_parses_inline_active_pool_customization():
    options = [
        {"id": "standard_research", "label": "标准研究论文覆盖", "is_default": True},
        {"id": "survey_balanced", "label": "综述均衡覆盖"},
        {"id": "custom", "label": "自定义关键数字"},
    ]

    result = CLIHumanInterface._parse_inline_gate_customization(
        "t2_literature_param_gate",
        "把 active pool 改成 300",
        options,
    )

    assert result == {
        "option_id": "custom",
        "captured": {"active_pool_max": "300", "base_option": "standard_research"},
    }


def test_t2_literature_gate_parses_multiple_inline_customizations():
    options = [
        {"id": "standard_research", "label": "标准研究论文覆盖"},
        {"id": "survey_balanced", "label": "综述均衡覆盖", "is_default": True},
        {"id": "custom", "label": "自定义关键数字"},
    ]

    result = CLIHumanInterface._parse_inline_gate_customization(
        "t2_literature_param_gate",
        "候选数300，精读80，摘要轻读all_readable，require=false",
        options,
    )

    assert result["option_id"] == "custom"
    assert result["captured"]["active_pool_max"] == "300"
    assert result["captured"]["deep_read_target"] == "80"
    assert result["captured"]["abstract_sweep_target"] == "all_readable"
    assert result["captured"]["require_deep_read_target"] == "false"
    assert result["captured"]["base_option"] == "survey_balanced"


def test_t2_literature_gate_parses_user_custom_inline_sentence():
    options = [
        {"id": "standard_research", "label": "标准研究论文覆盖"},
        {"id": "survey_balanced", "label": "综述均衡覆盖", "is_default": True},
        {"id": "survey_exhaustive", "label": "综述强覆盖"},
        {"id": "custom", "label": "自定义关键数字"},
    ]

    result = CLIHumanInterface._parse_inline_gate_customization(
        "t2_literature_param_gate",
        "4；active_pool_max=80；deep_read=35/35/45；require_target=True；abstract_sweep=80；英文",
        options,
    )

    assert result["option_id"] == "custom"
    captured = result["captured"]
    assert captured["active_pool_max"] == "80"
    assert captured["deep_read_min"] == "35"
    assert captured["deep_read_target"] == "35"
    assert captured["deep_read_max"] == "45"
    assert captured["abstract_sweep_target"] == "80"
    assert captured["require_deep_read_target"] == "True"
    assert captured["manuscript_language"] == "英文"
    assert captured["base_option"] == "survey_balanced"


def test_t2_literature_gate_parses_language_and_chinese_policy():
    options = [
        {"id": "standard_research", "label": "标准研究论文覆盖", "is_default": True},
        {"id": "custom", "label": "自定义关键数字"},
    ]

    result = CLIHumanInterface._parse_inline_gate_customization(
        "t2_literature_param_gate",
        "英文稿，不要中文论文，候选数300",
        options,
    )

    assert result["option_id"] == "custom"
    assert result["captured"]["manuscript_language"] == "英文"
    assert result["captured"]["include_chinese_literature"] == "false"
    assert result["captured"]["active_pool_max"] == "300"


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
async def test_ask_clarification_confirms_after_end_submission(monkeypatch, capsys):
    answers = iter(["这是我的回答", "END"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    human = CLIHumanInterface()
    answer = await human.ask_clarification(question="请回答")

    assert answer == "这是我的回答"
    out = capsys.readouterr().out
    assert "已收到输入，继续处理" in out
    assert "-" * 80 in out


@pytest.mark.asyncio
async def test_ask_clarification_confirms_after_ctrl_d_submission(monkeypatch, capsys):
    answers = iter(["这是我的回答"])

    def fake_input(_prompt: str = "") -> str:
        try:
            return next(answers)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr("builtins.input", fake_input)

    human = CLIHumanInterface()
    answer = await human.ask_clarification(question="请回答")

    assert answer == "这是我的回答"
    out = capsys.readouterr().out
    assert "已收到输入，继续处理" in out
    assert "-" * 80 in out


@pytest.mark.asyncio
async def test_ask_clarification_reprompts_after_empty_submission(monkeypatch, capsys):
    answers = iter([EOFError, "补充后的有效回答", "END"])

    def fake_input(_prompt: str = "") -> str:
        value = next(answers)
        if value is EOFError:
            raise EOFError
        return value

    monkeypatch.setattr("builtins.input", fake_input)

    human = CLIHumanInterface()
    answer = await human.ask_clarification(question="请回答")

    assert answer == "补充后的有效回答"
    out = capsys.readouterr().out
    assert "未收到有效输入，请重新输入" in out
    assert "已收到输入，继续处理" in out
    assert "-" * 80 in out


@pytest.mark.asyncio
async def test_ask_clarification_pauses_after_repeated_empty_submissions(monkeypatch, capsys):
    def fake_input(_prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", fake_input)

    human = CLIHumanInterface()
    with pytest.raises(HumanInputUnavailable):
        await human.ask_clarification(question="请回答")

    out = capsys.readouterr().out
    assert out.count("未收到有效输入，请重新输入") == human.CLARIFICATION_EMPTY_RETRIES - 1
    assert "连续多次未收到有效输入" in out


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
