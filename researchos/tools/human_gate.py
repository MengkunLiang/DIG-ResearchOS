from __future__ import annotations

"""人机交互抽象。"""

from abc import ABC, abstractmethod
import io
import json
import re
import shutil
import unicodedata
from typing import Any, Awaitable, Callable

from rich.console import Console
from rich.panel import Panel
from rich.text import Text


_READLINE_CONFIGURED = False


_T2_LLM_CAPTURE_FIELDS = {
    "coverage_total",
    "active_pool_max",
    "deep_read_min",
    "deep_read_target",
    "deep_read_max",
    "abstract_sweep_target",
    "require_deep_read_target",
    "manuscript_language",
    "include_chinese_literature",
    "base_option",
}


def _strip_terminal_control_sequences(value: str) -> str:
    """Remove terminal replies/paste artifacts before parsing a human answer.

    Some terminal emulators paste OSC colour queries such as
    ``]10;rgb:cccc/cccc/cccc\\`` into stdin.  They are neither user intent nor
    a parameter, so remove both well-formed ANSI/OSC sequences and the common
    visible fragment before the answer is echoed or interpreted.
    """

    text = str(value or "")
    text = re.sub(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\]?\d{1,3};rgb:[0-9A-Fa-f/]{3,64}\\?", "", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    return text


def _configure_readline_once() -> None:
    """Enable predictable line editing for CLI gates when readline is available."""

    global _READLINE_CONFIGURED
    if _READLINE_CONFIGURED:
        return
    _READLINE_CONFIGURED = True
    try:
        import readline  # type: ignore
    except Exception:
        return
    for binding in (
        '"\\C-h": backward-delete-char',
        '"\\C-?": backward-delete-char',
        '"\\e[3~": delete-char',
    ):
        try:
            readline.parse_and_bind(binding)
        except Exception:
            continue


def _read_cli_line(prompt: str) -> str:
    """Read one terminal line with best-effort readline editing support."""

    _configure_readline_once()
    return _strip_terminal_control_sequences(input(prompt))


def _parse_json_object_from_llm_content(value: Any) -> dict[str, Any] | None:
    """Extract one JSON object from a short structured LLM response."""

    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    start = text.find("{")
    if start < 0:
        return None
    try:
        decoded, _ = json.JSONDecoder().raw_decode(text[start:])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _sanitize_t2_llm_capture(value: Any) -> dict[str, str]:
    """Keep only the bounded T2 parameter contract from an LLM response."""

    if not isinstance(value, dict):
        return {}
    captured: dict[str, str] = {}
    for key in _T2_LLM_CAPTURE_FIELDS:
        item = value.get(key)
        if item in (None, ""):
            continue
        if isinstance(item, bool):
            captured[key] = "true" if item else "false"
        elif isinstance(item, (str, int, float)):
            captured[key] = _strip_terminal_control_sequences(str(item)).strip()
    return {key: item for key, item in captured.items() if item}


def build_t2_parameter_llm_interpreter(
    llm_client: Any,
) -> Callable[[str], Awaitable[dict[str, str]]]:
    """Build a narrow LLM adapter for one-line T2 parameter intent parsing.

    The model only proposes a small JSON object.  ``CLIHumanInterface`` applies
    deterministic parsing and StateMachine-level range/language validation
    afterwards, so a malformed or overreaching model response cannot change
    search boundaries directly.
    """

    async def interpret(raw_answer: str) -> dict[str, str]:
        prompt = """You parse one user's T2 literature-coverage request for a research CLI.
Return exactly one JSON object and no Markdown. Do not explain your reasoning.
Only include values explicitly stated or unambiguously requested by the user.
Allowed keys: coverage_total, active_pool_max, deep_read_min, deep_read_target,
deep_read_max, abstract_sweep_target, require_deep_read_target,
manuscript_language, include_chinese_literature, base_option.

Rules:
- Numeric values must be decimal strings. abstract_sweep_target may also be all_readable.
- manuscript_language must be one of auto, en, zh, mixed when stated.
- include_chinese_literature must be one of true, false, auto when stated.
- English manuscript means non-seed Chinese literature will be excluded by a later
  deterministic policy. Do not invent a language when the user did not state one.
- Do not infer omitted numeric fields.

User input:
""" + _strip_terminal_control_sequences(raw_answer)
        response = await llm_client.chat(
            messages=[
                {
                    "role": "system",
                    "content": "You are a strict JSON parser. Return only the requested object.",
                },
                {"role": "user", "content": prompt},
            ],
            tools=None,
            temperature=0.0,
            tier="light",
            profile=None,
            timeout=25,
            max_retries_per_model=1,
            retry_base_delay=0.0,
        )
        choice = response.raw.choices[0].message
        parsed = _parse_json_object_from_llm_content(getattr(choice, "content", ""))
        return _sanitize_t2_llm_capture(parsed)

    return interpret


def _compact_text(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _summarize_arguments(arguments: dict[str, Any]) -> str:
    fields = []
    for key in ("path", "query", "tool_name", "action", "mode", "summary", "question"):
        value = arguments.get(key)
        if value not in (None, ""):
            fields.append(f"{key}={_compact_text(value, 120)}")
    if fields:
        return "; ".join(fields)
    return f"{len(arguments)} 个参数（完整参数写入 trace，不在 CLI 展开）"


def _humanize_presentation_key(key: str) -> str:
    labels = {
        "current_parameter_preview": "当前参数",
        "selected_parameters": "已选参数",
        "gate1_candidate_cards": "候选 idea 卡片",
        "gate1_selection_brief": "选择建议",
        "candidate_overview": "候选方向（中文决策面板）",
        "candidate_pool_fingerprints": "候选池校验",
        "input_fingerprints": "输入校验",
        "survey_summary": "综述摘要",
        "synthesis_preview": "T3.5 综合摘要",
        "weak_evidence_preview": "弱证据提示",
        "survey_compile_report": "编译报告",
        "survey_insights": "综述洞察",
        "how_to_choose": "如何选择",
        "task_id": "任务",
        "run_id": "运行",
        "failures": "失败次数",
        "retry_limit": "自动修复上限",
        "last_error": "最近错误",
        "existing_outputs": "已有输出",
    }
    if key in labels:
        return labels[key]
    return key.replace("_", " ")


class HumanInputUnavailable(RuntimeError):
    """Raised when the CLI cannot obtain required human input."""


class HumanInterface(ABC):
    """runtime 与外部用户交互的统一接口。"""

    @abstractmethod
    async def ask_approval(self, *, tool_name: str, arguments: dict) -> bool:
        ...

    @abstractmethod
    async def ask_clarification(
        self, *, question: str, suggestions: list[str] | None = None
    ) -> str:
        ...

    @abstractmethod
    async def present_gate(
        self, *, gate_id: str, presentation: dict, options: list[dict]
    ) -> dict:
        ...


class CLIHumanInterface(HumanInterface):
    """最小命令行版本的人机接口实现。"""

    CLARIFICATION_EMPTY_RETRIES = 3
    SEPARATOR_WIDTH = 80

    def __init__(
        self,
        *,
        t2_parameter_interpreter: Callable[[str], Awaitable[dict[str, str]]] | None = None,
        no_color: bool = False,
    ) -> None:
        self._t2_parameter_interpreter = t2_parameter_interpreter
        self._no_color = bool(no_color)

    def _render_panel(self, *, title: str, lines: list[str], border_style: str) -> None:
        """Render gate context without turning interactive input into a TUI."""

        width = max(88, min(140, shutil.get_terminal_size(fallback=(120, 40)).columns))
        buffer = io.StringIO()
        console = Console(
            file=buffer,
            force_terminal=not self._no_color,
            color_system=None if self._no_color else "truecolor",
            no_color=self._no_color,
            width=width,
            highlight=False,
            _environ={"COLUMNS": str(width), "LINES": "40"},
        )
        console.print(Panel(Text("\n".join(line for line in lines if line)), title=title, border_style=border_style, expand=True))
        rendered = buffer.getvalue().rstrip()
        if rendered:
            print("\n" + rendered)

    def _render_section(self, title: str) -> None:
        if self._no_color:
            print(f"\n【{title}】")
            return
        buffer = io.StringIO()
        console = Console(
            file=buffer,
            force_terminal=True,
            color_system="truecolor",
            width=max(88, min(140, shutil.get_terminal_size(fallback=(120, 40)).columns)),
            highlight=False,
            _environ={
                "COLUMNS": str(max(88, min(140, shutil.get_terminal_size(fallback=(120, 40)).columns))),
                "LINES": "40",
            },
        )
        console.print(Text(f"【{title}】", style="bold magenta"))
        print("\n" + buffer.getvalue().rstrip())

    async def ask_approval(self, *, tool_name: str, arguments: dict) -> bool:
        self._render_panel(
            title=f"Approval Required · {tool_name}",
            border_style="bright_yellow",
            lines=[
                "该工具可能执行高风险或外部副作用操作，需要用户显式确认。",
                f"输入摘要：{_summarize_arguments(arguments)}",
            ],
        )
        try:
            answer = _read_cli_line("批准执行? [y/N]: ").strip().lower()
        except EOFError as exc:
            raise HumanInputUnavailable(f"{tool_name} 需要用户批准，但当前输入不可用。") from exc
        return answer in {"y", "yes"}

    async def ask_clarification(
        self, *, question: str, suggestions: list[str] | None = None
    ) -> str:
        lines = [question]
        if suggestions:
            lines.append("参考选项 / 建议：")
            for idx, item in enumerate(suggestions, start=1):
                lines.append(f"  [{idx}] {_compact_text(item, 180)}")
        self._render_panel(title="Human Input Required", border_style="bright_yellow", lines=lines)
        for attempt in range(1, self.CLARIFICATION_EMPTY_RETRIES + 1):
            print("请输入回答（输入完成后，在最后输入单独一行 END，或按 Ctrl+D 提交）:")

            lines: list[str] = []
            try:
                while True:
                    line = _read_cli_line("> ")
                    if line.strip() == "END":
                        break
                    lines.append(line)
            except EOFError:
                pass  # Ctrl+D 正常提交

            answer = "\n".join(lines).strip()
            if answer:
                print("已收到输入，继续处理...")
                print("-" * self.SEPARATOR_WIDTH)
                return answer

            if attempt < self.CLARIFICATION_EMPTY_RETRIES:
                print("未收到有效输入，请重新输入；如需主动中断请按 Ctrl+C。")

        print("连续多次未收到有效输入，任务将暂停等待明确输入。")
        raise HumanInputUnavailable("ask_human 连续收到空回答，任务已暂停等待明确输入。")

    async def present_gate(
        self, *, gate_id: str, presentation: dict, options: list[dict]
    ) -> dict:
        title = presentation.get("_title")
        description = presentation.get("_description")
        self._render_panel(
            title=f"GATE · {gate_id}",
            border_style="bright_yellow",
            lines=[
                str(title or "人工决策"),
                str(description or ""),
                "请选择后继续；ResearchOS 会把选择写入 workspace，并按该选择推进。",
            ],
        )
        for key, value in presentation.items():
            if key.startswith("_"):
                continue
            if not self._should_render_presentation_field(gate_id, key):
                continue
            rendered = self._format_presentation_value(key, value, gate_id=gate_id)
            if not rendered.strip():
                continue
            self._render_section(_humanize_presentation_key(key))
            print(rendered)
        if gate_id == "t4_gate1_selection_gate":
            print("\n输入一行即可选择、合并、重构或提出新想法；无需先选择菜单项。")
        else:
            for idx, option in enumerate(options, start=1):
                default_marker = " [默认]" if option.get("is_default") else ""
                print(f"[{idx}] {option['label']}{default_marker}")
                if option.get("parameter_preview"):
                    preview = str(option["parameter_preview"])
                    if "\n" in preview:
                        print("    参数:")
                        for line in preview.splitlines():
                            if line.strip():
                                print(f"      - {line.strip()}")
                    else:
                        print(f"    参数: {preview}")
                if option.get("description"):
                    print(f"    作用: {option['description']}")
        selected = None
        while selected is None:
            try:
                prompt = "选择或说明: " if gate_id == "t4_gate1_selection_gate" else "请选择: "
                raw_answer = _read_cli_line(prompt).strip()
            except EOFError:
                raise HumanInputUnavailable(f"Gate {gate_id} 需要用户选择，但当前输入不可用。") from None
            if gate_id == "t2_literature_param_gate":
                menu_answer = self._parse_option_index(raw_answer, options)
                if menu_answer is not None:
                    selected = options[menu_answer]
                    break
            inline_result = await self._parse_inline_gate_customization_async(
                gate_id,
                raw_answer,
                options,
            )
            if inline_result is not None:
                print(self._format_gate_selection_confirmation(gate_id, inline_result, options))
                return inline_result
            answer = self._parse_option_index(raw_answer, options)
            if answer is None:
                if not raw_answer:
                    default_id = self._default_option_id(gate_id, options)
                    if default_id:
                        selected = next(
                            (option for option in options if (option.get("id") or option.get("key")) == default_id),
                            None,
                        )
                    if selected is None:
                        print(f"请输入 1-{len(options)}，或输入选项别名。")
                        continue
                    break
                if gate_id == "t4_gate1_selection_gate":
                    print("未识别。示例：选 D1，强调上下文控制；合并 D1+D3；新想法：……；重新分析：……")
                else:
                    print(f"无效选择: {raw_answer!r}。请输入 1-{len(options)}，或直接输入一句参数修改要求。")
                continue
            selected = options[answer]
        captured: dict[str, str] = {}
        option_id = selected.get("id") or selected.get("key")
        if gate_id == "t2_literature_param_gate" and option_id == "custom":
            captured = await self._collect_t2_customization_line(options)
        else:
            for field_name in selected.get("collect_input", []):
                prompt = self._collect_input_prompt(selected, field_name)
                try:
                    captured[field_name] = _read_cli_line(f"{prompt}: ").strip()
                except EOFError as exc:
                    raise HumanInputUnavailable(f"Gate {gate_id} 需要输入 {field_name}，但当前输入不可用。") from exc
        defaults = selected.get("captured_defaults")
        if isinstance(defaults, dict):
            for key, value in defaults.items():
                captured.setdefault(str(key), str(value))
        if gate_id == "t5_executor_gate" and option_id == "codex_cli":
            print(
                "codex_cli 将允许在 external_executor/workdir 内运行真实实验，"
                "可能消耗较多算力/时间。"
            )
            try:
                confirm = _read_cli_line(
                    "确认允许真实实验？输入 yes 继续，其它任意输入降级为 Claude Code 窗口: "
                ).strip()
            except EOFError as exc:
                raise HumanInputUnavailable("codex_cli 真实执行需要二次确认，但当前输入不可用。") from exc
            if confirm.lower() != "yes":
                option_id = "claude_code_window"
                captured["downgraded_from"] = "codex_cli"
                captured["downgrade_reason"] = "codex_cli confirmation was not yes"
        result = {"option_id": option_id, "captured": captured}
        print(self._format_gate_selection_confirmation(gate_id, result, options))
        return result

    @staticmethod
    def _should_render_presentation_field(gate_id: str, key: str) -> bool:
        """Keep interactive gates focused on a single decision surface."""

        if gate_id == "t4_gate1_selection_gate":
            return key == "candidate_overview"
        if gate_id == "t2_literature_param_gate":
            return key == "current_parameter_preview"
        return True

    async def _collect_t2_customization_line(self, options: list[dict]) -> dict[str, str]:
        """Collect T2/T3 coverage changes once instead of field by field."""

        default_id = self._default_option_id("t2_literature_param_gate", options)
        print(
            "一次输入覆盖数字、写作语言和中文文献策略；未提到的字段保持当前推荐。\n"
            "系统会先用 LLM 解释意图，再用本地规则校验数值和检索边界。\n"
            "示例：英文稿，候选30篇，精读15篇，粗读15篇；或：中文稿，允许中文文献，精读30篇。"
        )
        for attempt in range(1, 4):
            try:
                raw = _read_cli_line("自定义参数: ").strip()
            except EOFError as exc:
                raise HumanInputUnavailable("T2 自定义参数需要一行输入，但当前输入不可用。") from exc
            captured = await self._interpret_t2_literature_param_text(raw)
            has_parameters = any(
                key not in {"parser_source", "parser_fallback_reason"}
                for key in captured
            )
            if has_parameters or not raw:
                if default_id and default_id != "custom":
                    captured.setdefault("base_option", str(default_id))
                return captured
            if attempt < 3:
                print("未识别可调整的参数。请直接写数字或语言，例如：精读10篇，粗读20篇。")
        if default_id and default_id != "custom":
            return {"base_option": str(default_id)}
        return {}

    async def _interpret_t2_literature_param_text(self, raw_answer: str) -> dict[str, str]:
        """Interpret free text through an LLM, then enforce deterministic parsing.

        Explicit local matches take precedence over an LLM proposal.  This is
        deliberate: the model makes natural-language input ergonomic, while
        exact numeric and language policy enforcement remains auditable.
        """

        cleaned = _strip_terminal_control_sequences(raw_answer).strip()
        deterministic = self._parse_t2_literature_param_text(cleaned)
        if not cleaned:
            return deterministic
        if self._t2_parameter_interpreter is None:
            deterministic["parser_source"] = "deterministic_fallback"
            print("[参数解析] 当前未配置 LLM 解释器，已使用本地规则解析并会在确认页展示最终策略。")
            return deterministic

        print("[参数解析] 正在用 LLM 解释本句参数意图，并执行本地规则校验...")
        try:
            llm_capture = _sanitize_t2_llm_capture(
                await self._t2_parameter_interpreter(cleaned)
            )
        except Exception as exc:
            deterministic["parser_source"] = "llm_fallback"
            deterministic["parser_fallback_reason"] = type(exc).__name__
            print("[参数解析] LLM 暂不可用，已降级为本地规则解析；最终参数仍需在下一页确认。")
            return deterministic

        llm_capture.update(deterministic)
        llm_capture["parser_source"] = "llm_validated"
        print("[参数解析] 已完成 LLM 意图解析；本地规则已校验显式数字、语言与中文文献策略。")
        return llm_capture

    @staticmethod
    def _format_presentation_value(key: str, value: Any, *, gate_id: str = "") -> str:
        """Render gate presentation values for humans instead of dumping JSON by default."""

        if gate_id == "t2_coverage_gate":
            rendered = _format_t2_coverage_gate_field(key, value)
            if rendered is not None:
                return rendered
        if gate_id == "t36_survey_gate":
            rendered = _format_t36_survey_gate_field(key, value)
            if rendered is not None:
                return rendered
        if gate_id == "t2_literature_param_confirm_gate" and key == "selected_parameters":
            if isinstance(value, str):
                return _format_t2_selected_parameters_summary(
                    {"path": "literature/literature_params.json", "summary": value}
                )
            if _is_path_summary(value):
                return _format_t2_selected_parameters_summary(value)
        if gate_id == "t4_gate1_selection_gate" and key == "candidate_overview":
            return _format_t4_candidate_overview(value)
        if isinstance(value, str):
            return value
        if _is_path_summary(value):
            path = str(value.get("path") or "")
            size_chars = value.get("size_chars")
            summary = str(value.get("summary") or "").rstrip()
            header = [f"文件: {path}"]
            if size_chars not in (None, ""):
                header.append(f"字符数: {size_chars}")
            if summary:
                return "\n".join(header + ["摘要:", summary])
            return "\n".join(header)
        if key in {"candidate_pool_fingerprints", "input_fingerprints"} and isinstance(value, dict):
            if not CLIHumanInterface._show_machine_gate_field(gate_id, key):
                existing_count = sum(
                    1
                    for item in value.values()
                    if isinstance(item, dict) and item.get("exists")
                )
                missing_count = sum(
                    1
                    for item in value.values()
                    if isinstance(item, dict) and not item.get("exists")
                )
                if missing_count:
                    return f"机器校验信息已记录；{existing_count} 个文件已锁定，{missing_count} 个可选文件缺失。"
                return f"机器校验信息已记录；{existing_count} 个文件已锁定。"
            existing = [
                str(item.get("path") or label)
                for label, item in value.items()
                if isinstance(item, dict) and item.get("exists")
            ]
            missing = [
                str(item.get("path") or label)
                for label, item in value.items()
                if isinstance(item, dict) and not item.get("exists")
            ]
            lines = ["机器校验信息已记录在 state.yaml；一般不需要手动阅读。"]
            if existing:
                lines.append("已锁定候选文件: " + ", ".join(existing[:8]))
            if missing:
                lines.append("缺失/可选文件: " + ", ".join(missing[:8]))
            return "\n".join(lines)
        if gate_id == "t2_literature_param_gate" and key == "current_parameter_preview" and isinstance(value, dict):
            return _format_t2_parameter_preview(value)
        if isinstance(value, list):
            if not value:
                return "(空)"
            return "\n".join(f"- {item}" for item in value)
        return json.dumps(value, indent=2, ensure_ascii=False)

    @staticmethod
    def _show_machine_gate_field(gate_id: str, key: str) -> bool:
        return gate_id not in {
            "t4_gate1_selection_gate",
            "t2_literature_param_gate",
            "t2_literature_param_confirm_gate",
            "t36_post_survey_gate",
        }

    @staticmethod
    def _format_gate_selection_confirmation(gate_id: str, result: dict[str, Any], options: list[dict]) -> str:
        option_id = str(result.get("option_id") or result.get("key") or "")
        option = next((item for item in options if str(item.get("id") or item.get("key") or "") == option_id), {})
        label = str(option.get("label") or option_id)
        captured = result.get("captured") if isinstance(result.get("captured"), dict) else {}
        lines = ["-" * CLIHumanInterface.SEPARATOR_WIDTH, f"已确认选择: {label} ({option_id})"]
        if captured:
            compact = "; ".join(f"{key}={value}" for key, value in captured.items() if value not in (None, ""))
            if compact:
                lines.append(f"记录的补充输入: {compact}")
        if gate_id == "t2_literature_param_gate":
            lines.append("将写入: literature/literature_params.json；下一步: 参数最终确认 Gate")
        elif gate_id == "t2_literature_param_confirm_gate":
            if option_id == "confirm_start_t2":
                lines.append("将写入: literature/literature_params_confirmation.json；下一步: T2 文献检索")
            elif option_id == "revise_params":
                lines.append("不会启动 T2；下一步: 返回 T2 参数选择 Gate")
            elif option_id == "stop_project":
                lines.append("将结束当前项目，不启动 T2")
        elif gate_id == "t4_gate1_selection_gate":
            lines.append("将写入: ideation/_gate1_user_selection.json 和 ideation/selected_idea_brief.md；下一步: T4 后半段")
        elif gate_id == "t36_post_survey_gate":
            lines.append("将写入: drafts/survey/post_survey_decision.json")
        elif gate_id in {"t36_template_gate", "t8_style_template_gate"}:
            target = "drafts/survey/writing_template.json" if gate_id == "t36_template_gate" else "drafts/writing_style.json"
            lines.append(f"将写入: {target}")
        lines.append("-" * CLIHumanInterface.SEPARATOR_WIDTH)
        return "\n".join(lines)

    @staticmethod
    def _parse_inline_gate_customization(gate_id: str, raw_answer: str, options: list[dict]) -> dict | None:
        if gate_id in {"t36_template_gate", "t8_style_template_gate"}:
            return CLIHumanInterface._parse_template_gate_text(gate_id, raw_answer, options)
        if gate_id == "t4_gate1_selection_gate":
            return CLIHumanInterface._parse_t4_gate1_text(raw_answer, options)
        if gate_id != "t2_literature_param_gate":
            return None
        captured = CLIHumanInterface._parse_t2_literature_param_text(raw_answer)
        if not captured:
            return None
        if not any((option.get("id") or option.get("key")) == "custom" for option in options):
            return None
        default_id = CLIHumanInterface._default_option_id(gate_id, options)
        if default_id and default_id != "custom":
            captured.setdefault("base_option", str(default_id))
        return {"option_id": "custom", "captured": captured}

    async def _parse_inline_gate_customization_async(
        self,
        gate_id: str,
        raw_answer: str,
        options: list[dict],
    ) -> dict | None:
        """Async gate parser used by CLI so T2 can call its LLM interpreter."""

        if gate_id != "t2_literature_param_gate":
            return self._parse_inline_gate_customization(gate_id, raw_answer, options)
        captured = await self._interpret_t2_literature_param_text(raw_answer)
        parameter_capture = {
            key: value
            for key, value in captured.items()
            if key not in {"parser_source", "parser_fallback_reason"}
        }
        if not parameter_capture:
            return None
        if not any((option.get("id") or option.get("key")) == "custom" for option in options):
            return None
        default_id = self._default_option_id(gate_id, options)
        if default_id and default_id != "custom":
            captured.setdefault("base_option", str(default_id))
        return {"option_id": "custom", "captured": captured}

    @staticmethod
    def _parse_t4_gate1_text(raw_answer: str, options: list[dict]) -> dict | None:
        text = str(raw_answer or "").strip()
        if not text:
            return None
        normalized = text.replace("，", ",").replace("＋", "+").strip()
        lowered = normalized.casefold()
        option_ids = {str(option.get("id") or option.get("key") or "") for option in options}
        # ``\b`` treats Chinese characters as word characters, so a normal
        # answer such as "选D1作为主线" used to miss the candidate code.
        candidate_pattern = r"(?<![A-Za-z0-9])[DS]\d+(?![A-Za-z0-9])"
        candidates = [item.upper() for item in re.findall(candidate_pattern, normalized, flags=re.IGNORECASE)]
        unique_candidates = list(dict.fromkeys(candidates))

        if "reanalyze" in lowered or "重新分析" in normalized or "重跑" in normalized:
            feedback = re.sub(r"(?i)\breanalyze\b\s*[:：-]?", "", normalized).strip()
            feedback = feedback.replace("重新分析", "").replace("重跑", "").strip(" ：:-")
            return {"option_id": "reanalyze", "captured": {"feedback": feedback or normalized}}

        if lowered.startswith(("new:", "new idea:", "idea:")) or normalized.startswith(("新想法", "补充")):
            new_idea = re.sub(r"(?i)^(new|new idea|idea)\s*[:：-]?", "", normalized).strip()
            new_idea = re.sub(r"^(新想法|补充)\s*[:：-]?", "", new_idea).strip()
            return {"option_id": "new_idea", "captured": {"new_idea": new_idea or normalized}}

        explicit_merge = "merge" in lowered or "合并" in normalized or "+" in normalized or len(unique_candidates) > 1
        if candidates and explicit_merge and "merge" in option_ids:
            return {"option_id": "merge", "captured": {"merge_plan": normalized}}
        if candidates and "select_or_reframe" in option_ids:
            return {"option_id": "select_or_reframe", "captured": {"selection": normalized}}
        return None

    @staticmethod
    def _parse_template_gate_text(gate_id: str, raw_answer: str, options: list[dict]) -> dict | None:
        text = str(raw_answer or "").strip()
        if not text:
            return None
        normalized = text.casefold().replace("，", ",").replace("；", ";").replace("：", ":")
        option_ids = {str(option.get("id") or option.get("key") or "") for option in options}
        captured: dict[str, str] = {}
        option_id = ""

        if any(token in normalized for token in ("中文", "chinese", "basic_zh", " zh")) or normalized == "zh":
            captured.update({"template_family": "basic_zh", "template_id": "basic_zh", "writing_language": "zh"})
            option_id = "basic_zh"
        elif any(token in normalized for token in (
            "informs",
            "utd",
            "management science",
            "information systems research",
            "isr",
            "misq",
            "cds",
            "commerce data science",
            "informs journal on data science",
        )):
            captured.update({"template_family": "utd", "template_id": "informs", "writing_language": "en"})
            option_id = "utd_informs" if gate_id == "t36_template_gate" else "is_informs"
        elif any(token in normalized for token in ("ccf", "neurips", "iclr", "icml", "kdd", "conference", "会议", "ccf-a", "ccf_a")):
            if "kdd" in normalized or "sigkdd" in normalized:
                template_id, option_id = "kdd", "ccf_kdd"
            elif "icml" in normalized:
                template_id, option_id = "icml", "ccf_icml"
            elif "iclr" in normalized:
                template_id, option_id = "iclr", "ccf_iclr"
            else:
                template_id, option_id = "neurips", "ccf_neurips"
            captured.update({"template_family": "ccf", "template_id": template_id, "writing_language": "en"})
        elif any(token in normalized for token in ("英文", "english", "basic_en", "不用模板", "不要模板", "no template")) or normalized == "en":
            captured.update({"template_family": "basic_en", "template_id": "basic_en", "writing_language": "en"})
            option_id = "basic_en"
        elif any(token in normalized for token in ("both", "两套", "双线")) and gate_id == "t8_style_template_gate":
            captured.update({"template_family": "basic_en", "template_id": "basic_en", "writing_language": "en", "venue_style": "both"})
            option_id = "both_basic_en"

        key_aliases = {
            "venue_style": "venue_style",
            "style": "venue_style",
            "template_family": "template_family",
            "family": "template_family",
            "template_type": "template_family",
            "template_id": "template_id",
            "template": "template_id",
            "writing_language": "writing_language",
            "language": "writing_language",
            "lang": "writing_language",
        }
        for key, value in re.findall(r"([A-Za-z_][A-Za-z0-9_\-\s]*?)\s*[=:]\s*([A-Za-z0-9_\-]+)", normalized):
            canonical = key_aliases.get(re.sub(r"[\s-]+", "_", key.strip().lower()))
            if canonical:
                captured[canonical] = value.strip()

        if gate_id == "t8_style_template_gate":
            if "venue_style" not in captured:
                if option_id == "is_informs":
                    captured["venue_style"] = "is"
                elif option_id == "both_basic_en":
                    captured["venue_style"] = "both"
                elif option_id:
                    captured["venue_style"] = "ccf_a"

        if not captured:
            return None
        # Older/custom gate definitions may expose only the generic CCF option.
        # Preserve the requested concrete template id, but route through that
        # generic option rather than incorrectly declaring the input invalid.
        if option_id not in option_ids and option_id in {"ccf_iclr", "ccf_icml", "ccf_kdd"} and "ccf_neurips" in option_ids:
            option_id = "ccf_neurips"
        if not option_id or option_id not in option_ids:
            option_id = "custom" if "custom" in option_ids else next(iter(option_ids), "")
        if not option_id:
            return None
        return {"option_id": option_id, "captured": captured}

    @staticmethod
    def _parse_t2_literature_param_text(raw_answer: str) -> dict[str, str]:
        text = _strip_terminal_control_sequences(str(raw_answer or "")).strip()
        if not text:
            return {}
        normalized = text.replace("，", ",").replace("；", ";").replace("：", ":")
        captured: dict[str, str] = {}
        if re.search(r"\b(en|english)\b", normalized, flags=re.IGNORECASE) or any(
            token in normalized for token in ("英文稿", "英文论文", "英文")
        ):
            captured["manuscript_language"] = "英文"
        elif (
            re.search(r"\b(zh|chinese)\b", normalized, flags=re.IGNORECASE)
            or any(token in normalized for token in ("中文稿", "中文论文", "中文"))
        ) and "不要中文论文" not in normalized:
            captured["manuscript_language"] = "中文"
        if any(token in normalized for token in ("不要中文论文", "不要中文文献", "不检索中文", "不引用中文", "排除中文")):
            captured["include_chinese_literature"] = "false"
        elif any(token in normalized for token in ("允许中文论文", "允许中文文献", "中文论文允许", "中文文献允许", "检索中文", "包含中文", "包括中文")):
            captured["include_chinese_literature"] = "true"
        if any(token in normalized for token in ("稿件中文", "论文中文", "写作中文", "中文写作")):
            captured["manuscript_language"] = "中文"
        if any(token in normalized for token in ("不必读满目标", "无需读满目标", "达到最低即可", "读到最低线即可")):
            captured["require_deep_read_target"] = "false"
        elif any(token in normalized for token in ("必须读满目标", "一定读满目标")):
            captured["require_deep_read_target"] = "true"
        if any(token in normalized for token in ("不粗读", "不要粗读", "不略读", "不要略读", "不做粗读", "不做摘要轻读")):
            captured["abstract_sweep_target"] = "0"

        deep_triplet = re.search(
            r"\bdeep[_\s-]*read\b\s*(?:=|:|为)?\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)",
            normalized,
            flags=re.IGNORECASE,
        )
        if not deep_triplet:
            deep_triplet = re.search(
                r"(?:精读|深读|深入阅读)\s*(?:=|:|为)?\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)",
                normalized,
                flags=re.IGNORECASE,
            )
        if deep_triplet:
            captured["deep_read_min"] = deep_triplet.group(1)
            captured["deep_read_target"] = deep_triplet.group(2)
            captured["deep_read_max"] = deep_triplet.group(3)

        patterns = {
            "coverage_total": [
                r"\b(?:total|coverage[_\s-]*total|total[_\s-]*coverage|reading[_\s-]*total)\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*(\d+)",
                r"(?:总共|一共|总计|总量|总覆盖|覆盖总数|阅读总数|总阅读量)\s*(?:=|:|改成|设为|设置为|到|为)?\s*(\d+)",
            ],
            "active_pool_max": [
                r"\bactive[_\s-]*pool(?:[_\s-]*max)?\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*(\d+)",
                r"(?:保留候选数|候选池|候选数|保留候选|候选|active\s*pool)\s*(?:=|:|改成|设为|设置为|到|为)?\s*(\d+)\s*(?:篇)?",
            ],
            "deep_read_target": [
                r"\bdeep[_\s-]*read(?:[_\s-]*target)?\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*(\d+)",
                r"(?:精读目标|精读|深读目标|深读|深入阅读)\s*(?:=|:|改成|设为|设置为|到|为)?\s*(\d+)\s*(?:篇)?",
            ],
            "abstract_sweep_target": [
                r"\babstract[_\s-]*sweep(?:[_\s-]*target)?\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*([A-Za-z0-9_\-]+|全部)",
                r"(?:摘要轻读|轻读|略读|粗读|粗略阅读|摘要阅读|浅读)\s*(?:=|:|改成|设为|设置为|到|为)?\s*([A-Za-z0-9_\-]+|全部)\s*(?:篇)?",
            ],
            "require_deep_read_target": [
                r"\brequire(?:[_\s-]*deep)?(?:[_\s-]*read)?(?:[_\s-]*target)?\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*(true|false|yes|no|y|n|1|0|是|否|需要|不需要)",
                r"(?:必须读满|读满目标|必须达到目标|达到最低即可)\s*(?:=|:|改成|设为|设置为|到|为)?\s*(true|false|yes|no|y|n|1|0|是|否|需要|不需要)?",
            ],
            "manuscript_language": [
                r"\b(?:manuscript|writing)?[_\s-]*language\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*(en|english|zh|chinese|mixed|bilingual|auto)",
                r"(?:写作语言|论文语言|稿件语言)\s*(?:=|:|改成|设为|设置为|到|为)?\s*(英文|中文|双语|en|english|zh|chinese|mixed|bilingual|auto)",
            ],
            "include_chinese_literature": [
                r"\b(?:include[_\s-]*chinese|include[_\s-]*zh|chinese[_\s-]*literature)\b\s*(?:=|:|改成|设为|设置为|到|为)?\s*(true|false|yes|no|y|n|1|0|auto|include|exclude)",
                r"(?:中文文献|中文论文|检索中文|包含中文|include\s*zh)\s*(?:=|:|改成|设为|设置为|到|为)?\s*(true|false|yes|no|y|n|1|0|auto|允许|不允许|不要|包括|不包括|是|否)?",
            ],
        }
        for field_name, field_patterns in patterns.items():
            if field_name in captured:
                continue
            for pattern in field_patterns:
                match = re.search(pattern, normalized, flags=re.IGNORECASE)
                if match:
                    value = (match.group(1) or "").strip()
                    if field_name == "require_deep_read_target" and not value:
                        value = "false" if "最低" in match.group(0) else "true"
                    if field_name == "include_chinese_literature" and not value:
                        value = "false" if any(token in match.group(0) for token in ("不", "不要", "exclude")) else "true"
                    if value:
                        captured[field_name] = value
                    break

        key_aliases = {
            "active_pool": "active_pool_max",
            "active_pool_max": "active_pool_max",
            "pool": "active_pool_max",
            "total": "coverage_total",
            "coverage_total": "coverage_total",
            "total_coverage": "coverage_total",
            "reading_total": "coverage_total",
            "deep": "deep_read_target",
            "deep_read": "deep_read_target",
            "deep_read_min": "deep_read_min",
            "deep_read_target": "deep_read_target",
            "deep_read_max": "deep_read_max",
            "abstract": "abstract_sweep_target",
            "abstract_sweep": "abstract_sweep_target",
            "abstract_sweep_target": "abstract_sweep_target",
            "rough": "abstract_sweep_target",
            "rough_read": "abstract_sweep_target",
            "lite": "abstract_sweep_target",
            "lite_read": "abstract_sweep_target",
            "shallow": "abstract_sweep_target",
            "shallow_read": "abstract_sweep_target",
            "require": "require_deep_read_target",
            "require_target": "require_deep_read_target",
            "require_deep_read_target": "require_deep_read_target",
            "language": "manuscript_language",
            "manuscript_language": "manuscript_language",
            "writing_language": "manuscript_language",
            "include_zh": "include_chinese_literature",
            "include_chinese": "include_chinese_literature",
            "include_chinese_literature": "include_chinese_literature",
            "chinese_literature": "include_chinese_literature",
        }
        for key, value in re.findall(r"([A-Za-z_][A-Za-z0-9_\-\s]*?)\s*[=:]\s*([A-Za-z0-9_\/\-]+|全部)", normalized):
            canonical = key_aliases.get(re.sub(r"[\s-]+", "_", key.strip().lower()))
            if canonical:
                value = value.strip()
                if canonical == "deep_read_target" and re.fullmatch(r"\d+/\d+/\d+", value):
                    min_v, target_v, max_v = value.split("/")
                    captured["deep_read_min"] = min_v
                    captured["deep_read_target"] = target_v
                    captured["deep_read_max"] = max_v
                    continue
                captured[canonical] = value

        return captured

    @staticmethod
    def _default_option_id(gate_id: str, options: list[dict] | None = None) -> str | None:
        for option in options or []:
            if option.get("is_default"):
                return option.get("id") or option.get("key")
        if gate_id == "t2_literature_param_gate":
            return "survey_balanced"
        if gate_id == "t2_literature_param_confirm_gate":
            return "confirm_start_t2"
        if gate_id == "t5_executor_gate":
            return "codex_cli"
        return None

    @staticmethod
    def _collect_input_prompt(option: dict, field_name: str) -> str:
        prompts = option.get("input_prompts") or {}
        if isinstance(prompts, dict) and prompts.get(field_name):
            return f"{field_name}（{prompts[field_name]}）"
        return field_name

    @staticmethod
    def _parse_option_index(raw_answer: str, options: list[dict] | int) -> int | None:
        """解析 CLI gate 选择。

        某些终端会把快捷键或 ANSI 控制字符混进 input，例如 `\x1ba\x1ba1`。
        优先提取数字，同时支持 option id/key、label 和常用中文/英文确认别名。
        """

        if isinstance(options, int):
            option_list: list[dict] = [{"id": str(index + 1), "label": ""} for index in range(options)]
        else:
            option_list = list(options)
        option_count = len(option_list)
        cleaned = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw_answer)
        cleaned = cleaned.replace("\x1b", "")
        match = re.search(r"\d+", cleaned)
        if not match:
            return CLIHumanInterface._parse_option_alias(cleaned, option_list)
        idx = int(match.group(0)) - 1
        if 0 <= idx < option_count:
            return idx
        return CLIHumanInterface._parse_option_alias(cleaned, option_list)

    @staticmethod
    def _parse_option_alias(raw_answer: str, options: list[dict]) -> int | None:
        normalized_answer = CLIHumanInterface._normalize_answer(raw_answer)
        if not normalized_answer:
            return None

        positive_aliases = {
            "确认",
            "确定",
            "同意",
            "继续",
            "扩限",
            "增加",
            "是",
            "好",
            "yes",
            "y",
            "ok",
            "okay",
            "confirm",
            "continue",
            "extend",
            "approve",
        }
        negative_aliases = {
            "停止",
            "停",
            "取消",
            "否",
            "不",
            "no",
            "n",
            "stop",
            "cancel",
            "reject",
            "abort",
        }

        for idx, option in enumerate(options):
            tokens = {
                str(option.get("id") or ""),
                str(option.get("key") or ""),
                str(option.get("label") or ""),
            }
            tokens.update(str(alias) for alias in option.get("aliases") or [])
            normalized_tokens = {CLIHumanInterface._normalize_answer(token) for token in tokens}
            normalized_tokens = {token for token in normalized_tokens if token}
            if normalized_answer in normalized_tokens:
                return idx
            if any(normalized_answer in token for token in normalized_tokens):
                return idx

        for idx, option in enumerate(options):
            option_id = str(option.get("id") or option.get("key") or "").lower()
            label = CLIHumanInterface._normalize_answer(str(option.get("label") or ""))
            if option_id == "extend" and normalized_answer in positive_aliases:
                return idx
            if option_id == "stop" and normalized_answer in negative_aliases:
                return idx
            if any(alias in normalized_answer for alias in positive_aliases) and (
                "继续" in label or "扩限" in label or "增加" in label or "continue" in label or "extend" in label
            ):
                return idx
            if any(alias in normalized_answer for alias in negative_aliases) and (
                "停止" in label or "取消" in label or "stop" in label or "cancel" in label
            ):
                return idx
        return None

    @staticmethod
    def _normalize_answer(value: str) -> str:
        return re.sub(r"[\s\[\]（）()。.!！,，:：;；\"'`]+", "", value.strip().lower())


def _is_path_summary(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("path"), str)
        and "summary" in value
        and "size_chars" in value
    )


def _path_summary_text(value: Any, *, default_path: str = "") -> tuple[str, str]:
    if isinstance(value, dict):
        return str(value.get("path") or default_path), str(value.get("summary") or "")
    return default_path, str(value or "")


def _path_summary_size(value: Any) -> int | None:
    if isinstance(value, dict):
        size = value.get("size_chars")
        if isinstance(size, int):
            return size
        try:
            return int(size)
        except Exception:
            return None
    return None


def _strip_gate_truncation_marker(text: str) -> str:
    return re.sub(r"\n*\[open .+? for full content; truncated from \d+ chars\]\s*$", "", text).rstrip()


def _t4_terminal_columns(value: str) -> int:
    """Return a conservative terminal-column width for mixed CJK/ASCII text."""

    columns = 0
    for char in value:
        if unicodedata.combining(char):
            continue
        columns += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return columns


def _t4_wrap_terminal_text(value: str, *, columns: int) -> list[str]:
    """Wrap normalized prose by display columns, including double-width CJK glyphs."""

    if columns <= 0:
        return [value]
    lines: list[str] = []
    current = ""
    current_columns = 0
    for char in value:
        char_columns = _t4_terminal_columns(char)
        if current and current_columns + char_columns > columns:
            lines.append(current.rstrip())
            current = "" if char.isspace() else char
            current_columns = 0 if char.isspace() else char_columns
            continue
        if not current and char.isspace():
            continue
        current += char
        current_columns += char_columns
    if current or not lines:
        lines.append(current.rstrip())
    return lines


def _t4_wrap_terminal_prose(value: Any, *, indent: int = 0, width: int = 88) -> list[str]:
    """Wrap a non-field terminal line by display columns."""

    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    prefix = " " * indent
    available = max(1, width - _t4_terminal_columns(prefix))
    return [prefix + part for part in _t4_wrap_terminal_text(normalized, columns=available)]


def _t4_truncate_terminal_title(value: str, *, columns: int) -> str:
    """Keep candidate headings scannable even when a legacy title is verbose."""

    normalized = re.sub(r"\s+", " ", str(value or "未命名候选")).strip()
    if _t4_terminal_columns(normalized) <= columns:
        return normalized
    retained: list[str] = []
    used = 0
    for char in normalized:
        char_columns = _t4_terminal_columns(char)
        if used + char_columns > max(1, columns - 3):
            break
        retained.append(char)
        used += char_columns
    return "".join(retained).rstrip() + "..."


def _t4_wrap_terminal_field(label: str, value: Any, *, indent: int = 0, width: int = 88) -> list[str]:
    """Render one T4 field without hiding CJK content beyond terminal width."""

    normalized = re.sub(r"\s+", " ", str(value or "待补充")).strip()
    prefix = " " * indent + f"{label}："
    continuation = " " * (indent + 2)
    first_columns = max(1, width - _t4_terminal_columns(prefix))
    continuation_columns = max(1, width - _t4_terminal_columns(continuation))
    parts = _t4_wrap_terminal_text(normalized, columns=first_columns)
    if len(parts) <= 1:
        return [prefix + parts[0]]

    wrapped = [prefix + parts[0]]
    for part in parts[1:]:
        wrapped.extend(continuation + fragment for fragment in _t4_wrap_terminal_text(part, columns=continuation_columns))
    return wrapped


def _t4_terminal_lane_guide() -> list[str]:
    """Return public Gate1 lane semantics for the terminal decision panel."""

    return [
        "通道说明（候选角色，不是模型内部推理）：",
        "  D 主线：面向论文主贡献的候选路线；可被选择、合并或重构。",
        "  Bridge：由已确认的跨领域机制导出；必须回查 bridge 文献笔记 section 后才能形成最终论断。",
        "  证据不足：保留在面板中以便人工判断，但在补足明确证据前不应作为最终主张。",
        "  S 补充：用于证伪、失败分析或消融；默认不应单独承担一篇论文的主贡献。",
        "补充通道：",
        "  S1 mechanism_challenge：挑战声称机制，检查替代解释或失效边界。",
        "  S2 reverse_operation：移除、反转或关闭机制成分，形成消融/反事实检验。",
        "  S3 subgroup_failure：定位子群、状态或数据条件下的失败模式。",
        "  S4 missing_area_exploration：探索已确认空白；先补证据，再决定是否升级为主线。",
    ]


def _t4_candidate_lane_description(item: dict[str, Any], candidate_id: str) -> str:
    """Explain the candidate's decision role from persisted fields only."""

    lane = str(item.get("lane") or item.get("constraint_status") or "").strip().lower()
    origin = str(item.get("origin") or item.get("idea_origin") or "").strip().lower()
    if "not_supported" in lane or "evidence" in lane and "not" in lane:
        return "证据不足候选：可讨论，但必须补足对应笔记 section 的机制证据后才可选择为主方向。"
    if "bridge" in lane or "bridge" in origin:
        return "桥接候选：来自跨领域机制迁移；选择后必须核验 bridge domain 的对应笔记 section。"
    if candidate_id.upper().startswith("S") or "supplement" in lane:
        return "补充候选：服务于机制挑战、反向操作、子群失败或缺口探索，默认作为主线的验证/反证模块。"
    return "主线候选：面向论文主贡献的可选路线；仍须通过 Gate1 后半段的定向证据回查。"


def _format_t4_candidate_overview(value: Any) -> str:
    """Render complete, boxed Gate1 candidate cards from audited fields."""

    if not isinstance(value, dict):
        return "候选方向概览暂不可用；请检查 ideation/_candidate_directions.json。"
    candidates = value.get("candidates") if isinstance(value.get("candidates"), list) else []
    if not candidates:
        return "候选方向概览暂不可用；请检查 ideation/_candidate_directions.json。"
    width = 88
    divider = "=" * width
    lines = [
        divider,
        "T4 Gate1 完整候选方向卡片",
        *_t4_wrap_terminal_prose("请比较创新、假设/机制、可证伪预测、最小验证、证据基础和风险。", width=width),
        *_t4_wrap_terminal_prose("以下仅展示可审计候选、评分和文献锚点，不展示模型内部推理。选择后 T4 后半段必须重新打开列出的论文笔记 section。", width=width),
        "",
    ]
    for guide_line in _t4_terminal_lane_guide():
        indent = len(guide_line) - len(guide_line.lstrip())
        lines.extend(_t4_wrap_terminal_prose(guide_line.strip(), indent=indent, width=width))
    lines.append(divider)
    score_labels = (
        ("novelty", "新颖性"),
        ("feasibility", "可行性"),
        ("impact", "影响力"),
        ("evaluability", "可评估性"),
        ("differentiation", "差异化"),
        ("cost", "资源/实施成本"),
        ("contribution_strength", "贡献强度"),
    )
    for item in candidates:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("id") or "?")
        lane = str(item.get("lane") or "候选方向")
        title = _t4_truncate_terminal_title(
            str(item.get("display_title") or item.get("title") or "未命名候选"),
            columns=width - _t4_terminal_columns(f"| {candidate_id} | {lane} | "),
        )
        full_title = str(item.get("full_title") or title)
        original = str(item.get("original_title") or "")
        value_text = str(item.get("value") or "待补充")
        mechanism = str(item.get("mechanism") or "待补充")
        minimum = item.get("minimum_validation") if isinstance(item.get("minimum_validation"), dict) else {}
        dataset = str(minimum.get("dataset") or "待确定")
        baseline = str(minimum.get("baseline") or "待确定")
        metric = str(minimum.get("metric") or "待确定")
        signal = str(minimum.get("expected_signal") or "待确定")
        evidence = str(item.get("evidence") or "需回查文献笔记")
        count = item.get("support_count")
        scores = item.get("scores") if isinstance(item.get("scores"), dict) else {}
        warning = str(item.get("warning") or "选择后需回查证据。")
        innovation = item.get("innovation") if isinstance(item.get("innovation"), dict) else {}
        hypotheses = item.get("candidate_hypotheses") if isinstance(item.get("candidate_hypotheses"), list) else []
        merges = item.get("merge_opportunities") if isinstance(item.get("merge_opportunities"), list) else []
        score_rationale = item.get("score_rationale") if isinstance(item.get("score_rationale"), dict) else {}
        lines.extend(
            [
                "",
                divider,
                f"| {candidate_id} | {lane} | {title}",
            ]
        )
        lines.extend(_t4_wrap_terminal_field("候选定位", _t4_candidate_lane_description(item, candidate_id), indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("研究问题", item.get("target_problem"), indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("候选来源", item.get("origin") or "未标注", indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("机制家族", item.get("mechanism_family") or "未标注", indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("核心主张", value_text, indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("技术机制", mechanism, indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("可检验预测", item.get("prediction"), indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("反事实/证伪", item.get("counterfactual"), indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("完整方向描述", full_title, indent=2, width=width))
        lines.extend(_t4_wrap_terminal_field("实践/管理含义", item.get("practical_implication"), indent=2, width=width))
        lines.append("  核心创新：")
        lines.extend(_t4_wrap_terminal_field("创新是什么", innovation.get("summary"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("创新类型", innovation.get("type"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("相对最近工作的变化", innovation.get("delta") or innovation.get("novelty_delta"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("为何非普通增量", innovation.get("non_incremental") or innovation.get("non_incremental_reason"), indent=4, width=width))
        lines.append("  候选假设与机制（Gate1 草案，非最终 hypotheses.md）：")
        if hypotheses:
            for hypothesis in hypotheses[:3]:
                if not isinstance(hypothesis, dict):
                    continue
                hypothesis_id = str(hypothesis.get("id") or candidate_id + "-H?")
                lines.extend(
                    [
                        f"    {hypothesis_id}",
                    ]
                )
                lines.extend(_t4_wrap_terminal_field("命题", hypothesis.get("statement"), indent=6, width=width))
                lines.extend(_t4_wrap_terminal_field("机制", hypothesis.get("mechanism"), indent=6, width=width))
                lines.extend(_t4_wrap_terminal_field("可观测预测", hypothesis.get("prediction") or hypothesis.get("observable_prediction"), indent=6, width=width))
                lines.extend(_t4_wrap_terminal_field("判别测试", hypothesis.get("test") or hypothesis.get("discriminating_test"), indent=6, width=width))
        else:
            lines.append("    当前未提供候选 H1/H2/H3；不能在展示层补造，需重分析或定向回查。")
        if len(hypotheses) < 2:
            lines.append("    注：目前少于两条已落盘假设；H2/H3 需以补充证据为前提。")
        lines.append("  可组合关系：")
        if merges:
            for merge in merges[:4]:
                if not isinstance(merge, dict):
                    continue
                lines.extend(_t4_wrap_terminal_field(
                    str(merge.get("combine") or "未提供组合") + f"（与 {merge.get('with') or '未指定'}）",
                    merge.get("rationale") or "未提供组合理由",
                    indent=4,
                    width=width,
                ))
        else:
            lines.append("    当前未提供；可输入“合并 D1-H1 + D3-H1”并说明保留哪条机制。")
        lines.append("  最小验证：")
        lines.extend(_t4_wrap_terminal_field("数据/任务", dataset, indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("对照基线", baseline, indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("指标", metric, indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("预期信号", signal, indent=4, width=width))
        lines.append("  评分与依据（1-5）：")
        if scores:
            for key, label in score_labels:
                if scores.get(key) is None:
                    continue
                lines.extend(_t4_wrap_terminal_field(f"{label} {scores[key]}/5", score_rationale.get(key) or item.get("basis_summary") or "未提供单维依据；需复核接地材料。", indent=4, width=width))
        else:
            lines.append("    当前未评分；不能据此自动排序。")
        lines.append("  证据与风险：")
        evidence_text = evidence + (f"；关联文献笔记 {count} 篇" if count else "")
        lines.extend(_t4_wrap_terminal_field("证据基础", evidence_text, indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("接地摘要", item.get("basis_summary"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("选择建议", item.get("selection_recommendation"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("反事实复核", item.get("counterfactual_check"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("最近先例", item.get("nearest_prior_work"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("新颖性信号", item.get("novelty_signal"), indent=4, width=width))
        lines.extend(_t4_wrap_terminal_field("风险/Kill criteria", warning, indent=4, width=width))
        if original:
            lines.extend(_t4_wrap_terminal_field("英文原题", original, indent=2, width=width))
        support = item.get("supporting_papers") if isinstance(item.get("supporting_papers"), list) else []
        lines.append("  支撑文献与对应笔记 section：")
        if not support:
            lines.append("    当前候选未附带支撑论文；选择前需要补证据。")
        for index, paper in enumerate(support, start=1):
            if not isinstance(paper, dict):
                continue
            lines.extend(
                [
                    f"    {index}. {paper.get('title') or '未命名论文'}",
                ]
            )
            lines.extend(_t4_wrap_terminal_field("引用", paper.get("citation"), indent=6, width=width))
            lines.extend(_t4_wrap_terminal_field("证据等级", paper.get("evidence_level"), indent=6, width=width))
            lines.extend(_t4_wrap_terminal_field("笔记路径", paper.get("note_path") or paper.get("source_file") or paper.get("path"), indent=6, width=width))
            lines.extend(_t4_wrap_terminal_field("该候选使用的证据", paper.get("claim_used") or paper.get("claim"), indent=6, width=width))
        lines.append(divider)
    hint = str(value.get("input_hint") or "")
    if hint:
        lines.extend(["", divider, "如何提交选择", *_t4_wrap_terminal_prose(hint, width=width)])
    detail_path = str(value.get("detail_path") or "")
    if detail_path:
        lines.extend(_t4_wrap_terminal_field("完整卡片（含原始证据摘录）", detail_path, width=width))
    navigation = value.get("file_navigation") if isinstance(value.get("file_navigation"), list) else []
    if navigation:
        lines.extend(["", divider, "文件导航（可直接打开核验）"])
        for item in navigation:
            if not isinstance(item, dict):
                continue
            lines.extend(_t4_wrap_terminal_field(f"- {item.get('path')}", item.get("purpose"), width=width))
    lines.append(divider)
    return "\n".join(lines)


def _format_t36_survey_gate_field(key: str, value: Any) -> str | None:
    if key == "synthesis_preview":
        return _format_t36_synthesis_preview(value)
    if key == "weak_evidence_preview":
        return _format_t36_weak_evidence_preview(value)
    return None


def _format_t36_synthesis_preview(value: Any) -> str:
    path, raw_text = _path_summary_text(value, default_path="literature/synthesis.md")
    text = _strip_gate_truncation_marker(raw_text)
    size = _path_summary_size(value)
    headings = _extract_markdown_headings(text, limit=7)
    bullets = _extract_first_markdown_bullets(text, limit=5)
    note_refs = len(set(re.findall(r"\[note:([^\]\s]+)\]", text)))
    citation_keys = {
        key.strip()
        for group in re.findall(r"\\cite(?:t|p)?\{([^}]+)\}", text)
        for key in group.split(",")
        if key.strip()
    }

    lines = [
        f"文件: {path}",
        "T3.5 已完成 literature synthesis。它会继续作为 T4 idea fuel；是否写综述论文是额外分支选择。",
    ]
    if size is not None:
        lines.append(f"规模: 约 {size} 字符")
    signal_bits = []
    if headings:
        signal_bits.append("章节 " + str(len(headings)))
    if note_refs:
        signal_bits.append(f"note 引用 {note_refs}")
    if citation_keys:
        signal_bits.append(f"BibTeX 引用键 {len(citation_keys)}")
    if signal_bits:
        lines.append("结构信号: " + "；".join(signal_bits))
    if headings:
        lines.append("主要章节:")
        for item in headings:
            lines.append(f"- {item}")
    if bullets:
        lines.append("关键摘录:")
        for item in bullets:
            lines.append(f"- {_compact_text(item.lstrip('- ').strip(), 160)}")
    lines.append("现在只需判断：是否额外撰写 taxonomy-driven survey。选择“不写综述”会直接进入 T4，不会丢弃 synthesis。")
    return "\n".join(lines)


def _format_t36_weak_evidence_preview(value: Any) -> str:
    path, raw_text = _path_summary_text(value, default_path="literature/metadata_triage.md")
    text = _strip_gate_truncation_marker(raw_text)
    size = _path_summary_size(value)
    bullets = _extract_first_markdown_bullets(text, limit=5)
    lines = [
        f"文件: {path}",
        "作用: 标记 abstract-only / metadata-only / 低证据材料。它们可用于补资源或覆盖提示，不能直接支撑强 claim。",
    ]
    if size is not None:
        lines.append(f"规模: 约 {size} 字符")
    if bullets:
        lines.append("提示摘录:")
        for item in bullets:
            lines.append(f"- {_compact_text(item.lstrip('- ').strip(), 150)}")
    else:
        lines.append("当前没有可压缩展示的条目；详情见文件。")
    return "\n".join(lines)


def _format_t2_coverage_gate_field(key: str, value: Any) -> str | None:
    if key == "search_log":
        return _format_t2_search_log_summary(value)
    if key == "missing_areas":
        return _format_t2_missing_areas_summary(value)
    if key == "domain_map":
        return _format_t2_domain_map_summary(value)
    if key == "access_audit":
        return _format_t2_access_audit_summary(value)
    if key == "deep_read_queue_preview":
        return _format_t2_deep_read_queue_summary(value)
    return None


def _format_t2_search_log_summary(value: Any) -> str:
    path, text = _path_summary_text(value, default_path="literature/search_log.md")
    raw_count = _find_first_int(text, r"原始结果:\s*([0-9,]+)")
    dedup_count = _find_first_int(text, r"去重后:\s*([0-9,]+)")
    retained = _find_first_int(text, r"\bretained=([0-9,]+)")
    backlog = _find_first_int(text, r"\bbacklog=([0-9,]+)")
    deep_target = _find_first_int(text, r"\bdeep_read_target=([0-9,]+)")

    lines = [f"文件: {path}", "T2 已完成检索、去重、保留候选切分和 deep-read queue 构建。"]
    metrics = []
    if raw_count is not None:
        metrics.append(f"原始结果 {raw_count}")
    if dedup_count is not None:
        metrics.append(f"去重后 {dedup_count}")
    if retained is not None:
        metrics.append(f"保留候选 {retained}")
    if backlog is not None:
        metrics.append(f"backlog {backlog}")
    if deep_target is not None:
        metrics.append(f"精读目标 {deep_target}")
    if metrics:
        lines.append("- " + "；".join(metrics))

    bucket_rows = _extract_markdown_table_rows(text, "Bucket 覆盖")
    bucket_bits = []
    for row in bucket_rows[:6]:
        if len(row) >= 4:
            bucket_bits.append(f"{row[0]}: {row[3]} retained")
    if bucket_bits:
        lines.append("- 覆盖桶: " + "；".join(bucket_bits))

    bridge_rows = _extract_markdown_table_rows(text, "Bridge Domain Plan 覆盖")
    if bridge_rows:
        status_counts: dict[str, int] = {}
        for row in bridge_rows:
            status = row[-1] if row else "unknown"
            status_counts[status] = status_counts.get(status, 0) + 1
        lines.append(
            "- Bridge plan: "
            + "；".join(f"{status} {count}" for status, count in sorted(status_counts.items()))
        )

    for label in (
        "Active 切分前轻量补全",
        "多源摘要回填",
        "OpenAlex citation snowball 补全",
        "Crossref citation snowball 补全",
        "T2 raw 元数据缓存回写",
    ):
        line = _find_bullet_starting_with(text, label)
        if line:
            lines.append("- " + _format_t2_diagnostic_line(label, line))

    lines.append("详情保存在该文件；CLI 只展示摘要，不展开完整检索表。")
    return "\n".join(lines)


def _format_t2_diagnostic_line(label: str, line: str) -> str:
    values = _parse_key_values(line)
    if label == "Active 切分前轻量补全":
        return (
            "资源轻量补全: "
            f"候选 {values.get('candidate', '?')}/{values.get('input', '?')}；"
            f"摘要线索 {values.get('abstract_after', '?')}；"
            f"PDF 线索 {values.get('pdf_hint_after', '?')}；"
            f"引用线索 {values.get('reference_hint_after', '?')}"
        )
    if label == "多源摘要回填":
        return (
            "摘要补全: "
            f"尝试 {values.get('attempted_single', values.get('attempted', '?'))}；"
            f"填充 {values.get('filled', '?')}；"
            f"仍缺 {values.get('remaining_missing_abstract', '?')}"
        )
    if label == "OpenAlex citation snowball 补全":
        return (
            "OpenAlex 引用扩展: "
            f"来源 {values.get('sources_used', '?')}；"
            f"看到引用 {values.get('reference_items_seen', '?')}；"
            f"新增/合并 {values.get('raw_persisted_or_merged', values.get('added', '?'))}；"
            f"失败 {values.get('failed', '?')}"
        )
    if label == "Crossref citation snowball 补全":
        return (
            "Crossref 引用扩展: "
            f"来源 {values.get('sources_used', '?')}；"
            f"看到引用 {values.get('reference_items_seen', '?')}；"
            f"title 解析 {values.get('title_references_resolved', '?')}；"
            f"新增 {values.get('raw_persisted', values.get('added', '?'))}；"
            f"失败 {values.get('failed', '?')}"
        )
    if label == "T2 raw 元数据缓存回写":
        return (
            "raw 元数据缓存: "
            f"总记录 {values.get('records_after', '?')}；"
            f"合并 {values.get('merged', '?')}；"
            f"新增 {values.get('appended', '?')}"
        )
    return _compact_text(line.lstrip("- ").strip(), 180)


def _parse_key_values(line: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)=([^,;。]+)", line):
        values[match.group(1)] = match.group(2).strip()
    return values


def _format_t2_missing_areas_summary(value: Any) -> str:
    path, text = _path_summary_text(value, default_path="literature/missing_areas.md")
    lines = [
        f"文件: {path}",
        "这是检索覆盖提示，不是最终研究缺口结论；进入 T3 后还需要结合阅读笔记复核。",
    ]
    good = _extract_bullets_under_heading(text, "覆盖较好的主题", limit=3)
    weak = _extract_bullets_under_heading(text, "覆盖不足的主题", limit=5)
    hints = _extract_hint_titles(text, limit=5)
    if good:
        lines.append("覆盖较好: " + "；".join(item.lstrip("- ").strip() for item in good))
    if weak:
        lines.append("建议关注/补检:")
        for item in weak:
            lines.append(f"- {item.lstrip('- ').strip()}")
    elif hints:
        lines.append("建议关注/补检:")
        for item in hints:
            lines.append(f"- {item}")
    lines.append("如果这些提示与你的研究边界不符，可以选择回到 T2 扩检/调整 query。")
    return "\n".join(lines)


def _format_t2_domain_map_summary(value: Any) -> str:
    path, text = _path_summary_text(value, default_path="literature/domain_map.json")
    lines = [f"文件: {path}"]
    try:
        data = json.loads(text)
    except Exception:
        data = None
    if isinstance(data, dict):
        counts = []
        for key, label in (
            ("core", "core"),
            ("theory_bridge", "theory_bridge"),
            ("adjacent", "adjacent"),
            ("citation_edges", "citation_edges"),
        ):
            value = data.get(key)
            if isinstance(value, list):
                counts.append(f"{label} {len(value)}")
        if counts:
            lines.append("Domain map: " + "；".join(counts))
    else:
        lines.append("Domain map 已生成，用于 T3.5 synthesis 和 T4 idea；CLI 不展开原始 JSON。")
    lines.append("重点看它是否覆盖 core / theory_bridge / adjacent 三类角色；详情见文件。")
    return "\n".join(lines)


def _format_t2_access_audit_summary(value: Any) -> str:
    path, text = _path_summary_text(value, default_path="literature/access_audit.md")
    lines = [f"文件: {path}", "可读性与证据级别摘要:"]
    wanted_prefixes = (
        "候选论文总数",
        "`literature/pdfs/` 本地 PDF",
        "`user_seeds/pdfs/` 可匹配的 seed PDF",
        "`FULL_TEXT`",
        "`ABSTRACT_ONLY`",
        "`METADATA_ONLY`",
        "Access hint `POSSIBLE_FULL_TEXT`",
    )
    found = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        body = stripped[2:].strip()
        if any(body.startswith(prefix) for prefix in wanted_prefixes):
            found.append(body)
    for item in found[:8]:
        lines.append(f"- {item}")
    lines.append("Top candidate 表保存在文件中；CLI 不展开完整表格。")
    return "\n".join(lines)


def _format_t2_deep_read_queue_summary(value: Any) -> str:
    path, text = _path_summary_text(value, default_path="literature/deep_read_queue.jsonl")
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            entries.append(item)
        if len(entries) >= 5:
            break
    lines = [f"文件: {path}", "T3 将优先按这个 deep-read queue 阅读。前几条预览:"]
    if entries:
        for item in entries:
            rank = item.get("queue_rank") or item.get("rank") or "?"
            title = _compact_text(item.get("title") or item.get("paper_id") or "untitled", 100)
            bucket = item.get("target_bucket") or item.get("search_bucket") or item.get("queue_reason") or "unknown"
            evidence = item.get("evidence_level") or item.get("access_level_hint") or "unknown"
            lines.append(f"- #{rank} {title}（{bucket}; {evidence}）")
    else:
        lines.append("- 暂无法解析预览；请打开文件查看。")
    lines.append("如果队列方向明显不对，选择回到 T2 扩检/调整 query。")
    return "\n".join(lines)


def _find_first_int(text: str, pattern: str) -> int | None:
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except Exception:
        return None


def _find_bullet_starting_with(text: str, label: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- " + label):
            return stripped
    return None


def _extract_markdown_headings(text: str, *, limit: int) -> list[str]:
    headings: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        match = re.match(r"^#{1,3}\s+(.+)$", stripped)
        if not match:
            continue
        title = match.group(1).strip()
        if title and title not in headings:
            headings.append(_compact_text(title, 140))
        if len(headings) >= limit:
            break
    return headings


def _extract_first_markdown_bullets(text: str, *, limit: int) -> list[str]:
    bullets: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("- ", "* ")):
            continue
        body = stripped[2:].strip()
        if not body or set(body) <= {"-", "=", ":"}:
            continue
        bullets.append(body)
        if len(bullets) >= limit:
            break
    return bullets


def _extract_markdown_table_rows(text: str, heading: str) -> list[list[str]]:
    rows: list[list[str]] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_section:
                break
            in_section = heading in stripped
            continue
        if not in_section or not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or cells[0] in {"---", "Bucket", "Bridge", "#"} or set(cells[0]) <= {"-", ":"}:
            continue
        if len(cells) >= 2 and all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(cells)
    return rows


def _extract_bullets_under_heading(text: str, heading: str, *, limit: int) -> list[str]:
    bullets: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_section:
                break
            in_section = heading in stripped
            continue
        if in_section and stripped.startswith("- "):
            bullets.append(stripped)
            if len(bullets) >= limit:
                break
    return bullets


def _extract_hint_titles(text: str, *, limit: int) -> list[str]:
    hints: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"^###\s+提示\s+\d+\s*:\s*(.+)$", stripped)
        if match:
            hints.append(match.group(1).strip())
            if len(hints) >= limit:
                break
    return hints


def _format_t2_parameter_preview(value: dict[str, Any]) -> str:
    """Render a compact preset comparison plus explicit language policy."""

    lines = [
        f"任务类型：{value.get('detected_profile', 'unknown')}",
        f"当前推荐：{value.get('recommended_label') or value.get('recommended_option')}",
    ]
    recommended_summary = value.get("recommended_summary") if isinstance(value.get("recommended_summary"), dict) else {}
    if recommended_summary:
        lines.append("回车将采用：" + _format_t2_compact_summary(recommended_summary))
    lines.extend(
        [
            "自定义输入使用 LLM 意图解析，并由本地规则验证数值和语言策略；LLM 不可用时会明确降级提示。",
            "可直接输入：英文稿，候选30篇，精读15篇，粗读15篇；或：中文稿，允许中文文献，精读30篇。",
            "",
            "写作语言与检索边界：",
            "- 英文稿（en）：自动排除所有非 seed 中文文献，不检索、不主动引用；中文 seed 仅保留为上下文线索。",
            "- 中文稿（zh）：中文和英文文献均可进入候选池，中文来源会标记权威性复核状态。",
            "- 双语稿（mixed）：中文和英文均可保留，并在后续引用审计中标识语言与证据等级。",
            "- 自动（auto）：根据项目和你的输入推断；最终确认页会展示实际生效语言和中文文献动作。",
        ]
    )
    options = value.get("options")
    if isinstance(options, dict) and options:
        lines.append("")
        lines.append("档位比较：")
        for option_id, option in options.items():
            if not isinstance(option, dict):
                continue
            summary = option.get("summary") if isinstance(option.get("summary"), dict) else {}
            marker = "（推荐）" if option.get("recommended") else ""
            compact = option.get("compact_preview") or _format_t2_compact_summary(summary)
            lines.append(f"- {option.get('label') or option_id}{marker}：{compact}")
    lines.append("选“自定义”后只需输入一次整句；未写字段保留当前推荐，确认页会显示 LLM/规则解析来源。")
    return "\n".join(lines)


def _format_t2_selected_parameters_summary(value: dict[str, Any]) -> str:
    path = str(value.get("path") or "literature/literature_params.json")
    raw = str(value.get("summary") or "")
    data = _parse_t2_params_summary(raw)
    if data is None:
        return "\n".join(
            [
                f"文件: {path}",
                "关键参数摘要暂无法解析；完整参数仍已写入该文件。",
                "请打开文件检查，或选择“返回重选参数”。",
            ]
        )
    if not isinstance(data, dict):
        return f"文件: {path}"

    summary = data.get("selected_summary") if isinstance(data.get("selected_summary"), dict) else {}
    reader = data.get("reader") if isinstance(data.get("reader"), dict) else {}
    abstract_sweep = reader.get("abstract_sweep") if isinstance(reader.get("abstract_sweep"), dict) else {}
    quality = data.get("literature_quality") if isinstance(data.get("literature_quality"), dict) else {}
    lines = [
        f"文件: {path}",
        f"已选择档位: {data.get('selected_label') or data.get('selected_option')}",
        "关键参数:",
    ]
    explained_summary = {
        "active_pool_max": summary.get("active_pool_max") or (data.get("t2_finalize") or {}).get("active_pool_max"),
        "deep_read_min": reader.get("deep_read_min"),
        "deep_read_target": reader.get("deep_read_target"),
        "deep_read_max": reader.get("deep_read_max"),
        "require_deep_read_target": reader.get("require_deep_read_target"),
        "abstract_sweep_target": abstract_sweep.get("lite_paper_num"),
        "manuscript_language": quality.get("manuscript_language", "auto"),
        "include_chinese_literature": quality.get("include_chinese_literature", "auto"),
        "chinese_literature_policy": quality.get("chinese_literature_policy", "review_flag_only"),
        "effective_non_seed_chinese_action": quality.get("effective_non_seed_chinese_action"),
    }
    lines.extend(_format_t2_explained_summary_lines(explained_summary))
    captured = data.get("captured") if isinstance(data.get("captured"), dict) else {}
    parser_source = str(captured.get("parser_source") or "").strip()
    if parser_source:
        parser_labels = {
            "llm_validated": "LLM 意图解析 + 本地规则校验",
            "llm_fallback": "LLM 不可用后已降级为本地规则解析",
            "deterministic_fallback": "本次运行未配置 LLM，已使用本地规则解析",
        }
        lines.append(f"参数解析：{parser_labels.get(parser_source, parser_source)}")
    if captured:
        compact = "; ".join(f"{key}={value}" for key, value in captured.items() if value not in (None, ""))
        if compact:
            lines.append(f"用户自定义输入: {compact}")
    lines.append("确认后才会启动 T2；如果这里不符合预期，请选择返回重选参数。")
    return "\n".join(lines)


def _parse_t2_params_summary(raw: str) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except Exception:
        data = {}
        for key in (
            "selected_label",
            "selected_option",
            "confirmation_summary",
            "selected_summary",
            "t2_finalize",
            "reader",
            "literature_quality",
            "captured",
        ):
            value = _extract_json_value_for_key(text, key)
            if value is not None:
                data[key] = value
        if not data:
            return None
    return data if isinstance(data, dict) else None


def _extract_json_value_for_key(text: str, key: str) -> Any | None:
    marker = f'"{key}"'
    start = text.find(marker)
    if start < 0:
        return None
    colon = text.find(":", start + len(marker))
    if colon < 0:
        return None
    payload = text[colon + 1 :].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(payload)
    except Exception:
        return None
    return value


def _format_t2_explained_summary(summary: dict[str, Any]) -> str:
    return "；".join(_format_t2_explained_summary_lines(summary))


def _format_t2_compact_summary(summary: dict[str, Any]) -> str:
    total_target = _t2_summary_total_read_target(summary)
    require = "读满目标" if summary.get("require_deep_read_target") is True else "达到最低线可继续"
    return (
        f"候选 {summary.get('active_pool_max')} | 精读 {summary.get('deep_read_target')} | "
        f"摘要轻读 {summary.get('abstract_sweep_target')} | 总覆盖约 {total_target} | {require}"
    )


def _format_t2_explained_summary_lines(summary: dict[str, Any]) -> list[str]:
    deep_min = summary.get("deep_read_min")
    deep_target = summary.get("deep_read_target")
    deep_max = summary.get("deep_read_max")
    require = summary.get("require_deep_read_target")
    require_text = "未达目标不进入 T3.5" if require is True else "达到最低线即可继续" if require is False else "按系统默认判断"
    total_target = _t2_summary_total_read_target(summary)
    manuscript_language = str(summary.get("manuscript_language", "auto"))
    include_chinese = str(summary.get("include_chinese_literature", "auto"))
    effective_action = str(summary.get("effective_non_seed_chinese_action") or "")
    lines = [
        f"总阅读覆盖：约 {total_target} 篇（total=deep_read_target+abstract_sweep；可选：total=30 或 总共30）",
        f"保留候选：{summary.get('active_pool_max')} 篇（active_pool_max={summary.get('active_pool_max')}；可选：120/180/240 或自定义）",
        f"深入阅读：目标 {deep_target} 篇（deep_read={deep_min}/{deep_target}/{deep_max}；格式：min/target/max）",
        f"读满目标门槛：{require_text}（require_target={require}；可选：true/false）",
        f"摘要轻读：{summary.get('abstract_sweep_target')} 篇（abstract_sweep={summary.get('abstract_sweep_target')}；别名：粗读/略读/rough；可选：数字或 all_readable）",
        f"稿件语言：{manuscript_language}（manuscript_language={manuscript_language}；可选：auto/en/zh/mixed）",
        f"中文文献：{include_chinese}（include_zh={include_chinese}；可选：auto/true/false；策略={summary.get('chinese_literature_policy', 'review_flag_only')}）",
    ]
    if manuscript_language == "en" or effective_action == "exclude":
        lines.append("生效检索策略：英文稿，自动排除非 seed 中文文献；不会将其加入检索候选或主动引用。")
    elif manuscript_language == "zh":
        lines.append("生效检索策略：中文和英文文献均可进入候选池；中文来源会进行权威性复核标记。")
    elif manuscript_language == "mixed":
        lines.append("生效检索策略：中文和英文文献均可进入候选池；后续引用审计会保留语言和证据等级。")
    else:
        lines.append("生效检索策略：尚为自动推断；开始 T2 前请确认中文文献准入设置。")
    return lines


def _t2_summary_total_read_target(summary: dict[str, Any]) -> int | str | None:
    abstract_target = summary.get("abstract_sweep_target")
    if str(abstract_target).strip().casefold() in {"all", "all_readable", "unlimited", "全部"}:
        return summary.get("active_pool_max")
    try:
        return int(summary.get("deep_read_target") or 0) + int(abstract_target or 0)
    except (TypeError, ValueError):
        return summary.get("active_pool_max")
