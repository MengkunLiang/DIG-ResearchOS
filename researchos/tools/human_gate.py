from __future__ import annotations

from abc import ABC, abstractmethod
import json


class HumanInterface(ABC):
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
    async def ask_approval(self, *, tool_name: str, arguments: dict) -> bool:
        print(f"APPROVAL REQUIRED: {tool_name}")
        print(json.dumps(arguments, indent=2, ensure_ascii=False))
        answer = input("批准执行? [y/N]: ").strip().lower()
        return answer in {"y", "yes"}

    async def ask_clarification(
        self, *, question: str, suggestions: list[str] | None = None
    ) -> str:
        print(question)
        if suggestions:
            print(json.dumps(suggestions, indent=2, ensure_ascii=False))
        return input("请输入回答: ").strip()

    async def present_gate(
        self, *, gate_id: str, presentation: dict, options: list[dict]
    ) -> dict:
        print(f"GATE {gate_id}")
        print(json.dumps(presentation, indent=2, ensure_ascii=False))
        for idx, option in enumerate(options, start=1):
            print(f"[{idx}] {option['label']}")
        answer = int(input("请选择: ").strip()) - 1
        selected = options[answer]
        captured: dict[str, str] = {}
        for field_name in selected.get("collect_input", []):
            captured[field_name] = input(f"{field_name}: ").strip()
        return {"option_id": selected["id"], "captured": captured}

