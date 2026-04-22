from __future__ import annotations

"""人机交互抽象。"""

from abc import ABC, abstractmethod
import json


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
        answer = int(input("请选择: ").strip()) - 1
        selected = options[answer]
        captured: dict[str, str] = {}
        for field_name in selected.get("collect_input", []):
            captured[field_name] = input(f"{field_name}: ").strip()
        option_id = selected.get("id") or selected.get("key")
        return {"option_id": option_id, "captured": captured}
