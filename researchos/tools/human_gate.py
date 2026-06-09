from __future__ import annotations

"""人机交互抽象。"""

from abc import ABC, abstractmethod
import json
import re


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

    async def ask_approval(self, *, tool_name: str, arguments: dict) -> bool:
        print("\n" + "═" * 60)
        print(f"工具请求批准: {tool_name}")
        print(json.dumps(arguments, indent=2, ensure_ascii=False))
        print("═" * 60)
        try:
            answer = input("批准执行? [y/N]: ").strip().lower()
        except EOFError as exc:
            raise HumanInputUnavailable(f"{tool_name} 需要用户批准，但当前输入不可用。") from exc
        return answer in {"y", "yes"}

    async def ask_clarification(
        self, *, question: str, suggestions: list[str] | None = None
    ) -> str:
        print("\n" + "═" * self.SEPARATOR_WIDTH)
        print("需要人工输入")
        print("═" * self.SEPARATOR_WIDTH)
        print(question)
        if suggestions:
            print("\n参考选项 / 建议：")
            print(json.dumps(suggestions, indent=2, ensure_ascii=False))
        print("-" * self.SEPARATOR_WIDTH)
        for attempt in range(1, self.CLARIFICATION_EMPTY_RETRIES + 1):
            print("请输入回答（输入完成后，在最后输入单独一行 END，或按 Ctrl+D 提交）:")

            lines: list[str] = []
            try:
                while True:
                    line = input("> ")
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
        print("\n" + "═" * 60)
        print(f"GATE {gate_id}")
        if title:
            print(title)
        if description:
            print(description)
        print("═" * 60)
        for key, value in presentation.items():
            if key.startswith("_"):
                continue
            print(f"\n【{key}】")
            if isinstance(value, str):
                print(value)
            else:
                print(json.dumps(value, indent=2, ensure_ascii=False))
        for idx, option in enumerate(options, start=1):
            default_marker = " [默认]" if option.get("is_default") else ""
            print(f"[{idx}] {option['label']}{default_marker}")
            if option.get("parameter_preview"):
                print(f"    参数: {option['parameter_preview']}")
            if option.get("description"):
                print(f"    作用: {option['description']}")
        selected = None
        while selected is None:
            try:
                raw_answer = input("请选择: ").strip()
            except EOFError:
                raise HumanInputUnavailable(f"Gate {gate_id} 需要用户选择，但当前输入不可用。") from None
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
                print(f"无效选择: {raw_answer!r}。请输入 1-{len(options)}。")
                continue
            selected = options[answer]
        captured: dict[str, str] = {}
        for field_name in selected.get("collect_input", []):
            prompt = self._collect_input_prompt(selected, field_name)
            try:
                captured[field_name] = input(f"{prompt}: ").strip()
            except EOFError as exc:
                raise HumanInputUnavailable(f"Gate {gate_id} 需要输入 {field_name}，但当前输入不可用。") from exc
        option_id = selected.get("id") or selected.get("key")
        if gate_id == "t5_executor_gate" and option_id == "codex_cli":
            print(
                "codex_cli 将允许在 external_executor/workdir 内运行真实实验，"
                "可能消耗较多算力/时间。"
            )
            try:
                confirm = input("确认允许真实实验？输入 yes 继续，其它任意输入降级为 Claude Code 窗口: ").strip()
            except EOFError as exc:
                raise HumanInputUnavailable("codex_cli 真实执行需要二次确认，但当前输入不可用。") from exc
            if confirm.lower() != "yes":
                option_id = "claude_code_window"
                captured["downgraded_from"] = "codex_cli"
                captured["downgrade_reason"] = "codex_cli confirmation was not yes"
        return {"option_id": option_id, "captured": captured}

    @staticmethod
    def _default_option_id(gate_id: str, options: list[dict] | None = None) -> str | None:
        for option in options or []:
            if option.get("is_default"):
                return option.get("id") or option.get("key")
        if gate_id == "t2_literature_param_gate":
            return "survey_balanced"
        if gate_id == "t5_executor_gate":
            return "mock_dry_run"
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
