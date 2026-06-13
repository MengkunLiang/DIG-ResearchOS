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


def test_template_gate_parses_inline_informs_and_ccf_choices():
    t36_options = [
        {"id": "basic_en", "label": "英文基础模板"},
        {"id": "ccf_neurips", "label": "CCF 默认 NeurIPS"},
        {"id": "utd_informs", "label": "UTD/INFORMS"},
        {"id": "custom", "label": "自定义模板"},
    ]
    t8_options = [
        {"id": "is_informs", "label": "IS + INFORMS"},
        {"id": "ccf_neurips", "label": "CCF-A + NeurIPS"},
        {"id": "basic_zh", "label": "中文基础模板"},
        {"id": "custom", "label": "自定义模板"},
    ]

    informs = CLIHumanInterface._parse_inline_gate_customization(
        "t36_template_gate",
        "英文 informs",
        t36_options,
    )
    assert informs["option_id"] == "utd_informs"
    assert informs["captured"]["template_family"] == "utd"
    assert informs["captured"]["template_id"] == "informs"

    cds = CLIHumanInterface._parse_inline_gate_customization(
        "t8_style_template_gate",
        "CDS / INFORMS Journal on Data Science",
        t8_options,
    )
    assert cds["option_id"] == "is_informs"
    assert cds["captured"]["venue_style"] == "is"
    assert cds["captured"]["template_family"] == "utd"
    assert cds["captured"]["template_id"] == "informs"

    ccf = CLIHumanInterface._parse_inline_gate_customization(
        "t8_style_template_gate",
        "ccf kdd",
        t8_options,
    )
    assert ccf["option_id"] == "ccf_neurips"
    assert ccf["captured"]["venue_style"] == "ccf_a"
    assert ccf["captured"]["template_family"] == "ccf"
    assert ccf["captured"]["template_id"] == "kdd"

    zh = CLIHumanInterface._parse_inline_gate_customization(
        "t8_style_template_gate",
        "中文基础模板",
        t8_options,
    )
    assert zh["option_id"] == "basic_zh"
    assert zh["captured"]["writing_language"] == "zh"


def test_t4_gate1_parses_direct_candidate_and_merge_inputs():
    options = [
        {"id": "select_or_reframe", "label": "按说明选择/重构"},
        {"id": "merge", "label": "合并多个候选"},
        {"id": "new_idea", "label": "补充新想法"},
        {"id": "reanalyze", "label": "重新分析候选池"},
    ]

    selected = CLIHumanInterface._parse_inline_gate_customization(
        "t4_gate1_selection_gate",
        "D1，按 D1 重构",
        options,
    )
    assert selected == {
        "option_id": "select_or_reframe",
        "captured": {"selection": "D1,按 D1 重构"},
    }

    merged = CLIHumanInterface._parse_inline_gate_customization(
        "t4_gate1_selection_gate",
        "merge D1+D3",
        options,
    )
    assert merged == {
        "option_id": "merge",
        "captured": {"merge_plan": "merge D1+D3"},
    }


def test_t4_gate1_parses_new_idea_and_reanalyze_inputs():
    options = [
        {"id": "select_or_reframe", "label": "按说明选择/重构"},
        {"id": "merge", "label": "合并多个候选"},
        {"id": "new_idea", "label": "补充新想法"},
        {"id": "reanalyze", "label": "重新分析候选池"},
    ]

    new_idea = CLIHumanInterface._parse_inline_gate_customization(
        "t4_gate1_selection_gate",
        "new: dataset-first benchmark idea",
        options,
    )
    assert new_idea == {
        "option_id": "new_idea",
        "captured": {"new_idea": "dataset-first benchmark idea"},
    }

    reanalyze = CLIHumanInterface._parse_inline_gate_customization(
        "t4_gate1_selection_gate",
        "reanalyze: 只保留一个 CCF 方法主线",
        options,
    )
    assert reanalyze == {
        "option_id": "reanalyze",
        "captured": {"feedback": "只保留一个 CCF 方法主线"},
    }


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
