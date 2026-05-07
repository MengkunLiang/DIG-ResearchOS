from __future__ import annotations

"""人机交互抽象。"""

from abc import ABC, abstractmethod
import json
import re


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

    async def ask_approval(self, *, tool_name: str, arguments: dict) -> bool:
        print("\n" + "═" * 60)
        print(f"工具请求批准: {tool_name}")
        print(json.dumps(arguments, indent=2, ensure_ascii=False))
        print("═" * 60)
        answer = input("批准执行? [y/N]: ").strip().lower()
        return answer in {"y", "yes"}

    async def ask_clarification(
        self, *, question: str, suggestions: list[str] | None = None
    ) -> str:
        print(question)
        if suggestions:
            print(json.dumps(suggestions, indent=2, ensure_ascii=False))
        print("请输入回答（多行输入请在最后输入单独一行 'END' 结束，或直接按 Ctrl+D）:")

        # 支持多行输入
        lines = []
        try:
            while True:
                line = input()
                # 如果用户输入 END，停止读取
                if line.strip() == "END":
                    break
                lines.append(line)
        except EOFError:
            # Ctrl+D 触发 EOFError，正常结束
            pass

        return "\n".join(lines).strip()

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
            print(f"[{idx}] {option['label']}")
        selected = None
        while selected is None:
            try:
                raw_answer = input("请选择: ").strip()
            except EOFError:
                # 非交互环境触发 gate 时，默认选择 stop，避免运行时异常崩溃。
                raw_answer = ""
            answer = self._parse_option_index(raw_answer, len(options))
            if answer is None:
                if not raw_answer:
                    selected = next(
                        (option for option in options if (option.get("id") or option.get("key")) == "stop"),
                        options[-1],
                    )
                    break
                print(f"无效选择: {raw_answer!r}。请输入 1-{len(options)}。")
                continue
            selected = options[answer]
        captured: dict[str, str] = {}
        for field_name in selected.get("collect_input", []):
            captured[field_name] = input(f"{field_name}: ").strip()
        option_id = selected.get("id") or selected.get("key")
        return {"option_id": option_id, "captured": captured}

    @staticmethod
    def _parse_option_index(raw_answer: str, option_count: int) -> int | None:
        """解析 CLI gate 选择。

        某些终端会把快捷键或 ANSI 控制字符混进 input，例如 `\x1ba\x1ba1`。
        这里只提取数字，避免预算 gate 因输入噪声直接崩溃。
        """

        cleaned = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw_answer)
        cleaned = cleaned.replace("\x1b", "")
        match = re.search(r"\d+", cleaned)
        if not match:
            return None
        idx = int(match.group(0)) - 1
        if idx < 0 or idx >= option_count:
            return None
        return idx
