#!/usr/bin/env python3
"""简化的T3测试脚本 - 只用3篇论文"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from researchos.cli_runners import SingleTaskRunner
from researchos.runtime.llm_client import LLMClient
from researchos.tools.registry import ToolRegistry
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.human_gate import HumanInterface
from scripts._script_env import ensure_script_llm_env


class AutoHumanInterface(HumanInterface):
    """自动回答的HumanInterface"""

    def __init__(self, default_answer: str = "yes"):
        self.default_answer = default_answer
        self.call_count = 0

    async def ask_approval(self, *, tool_name: str, arguments: dict) -> bool:
        print(f"[AUTO-APPROVE] Tool: {tool_name}")
        return True

    async def ask_clarification(self, *, question: str, suggestions: list[str] | None = None) -> str:
        self.call_count += 1
        print(f"\n[AUTO-ANSWER #{self.call_count}] Question: {question[:100]}...")
        if suggestions:
            print(f"[AUTO-ANSWER] Suggestions: {suggestions}")
            return suggestions[0] if suggestions else self.default_answer
        return self.default_answer

    async def present_gate(self, *, gate_id: str, presentation: dict, options: list[dict]) -> dict:
        print(f"[AUTO-GATE] Gate: {gate_id}")
        if options:
            print(f"[AUTO-GATE] Selecting first option")
            return options[0]
        return {}


async def main():
    ensure_script_llm_env(Path(__file__).parent.parent)

    # 配置
    workspace_dir = Path("/tmp/researchos_real_test_20260419_163709")

    print(f"[TEST] Workspace: {workspace_dir}")
    print(f"[TEST] Starting T3 agent (Reader)...\n")

    # 检查papers_dedup.jsonl
    papers_file = workspace_dir / "literature" / "papers_dedup.jsonl"
    if not papers_file.exists():
        print("[TEST] ❌ papers_dedup.jsonl not found")
        return

    lines = papers_file.read_text().strip().split('\n')
    print(f"[TEST] Found {len(lines)} papers in papers_dedup.jsonl")

    # 创建组件
    registry = ToolRegistry()
    register_builtin_tools(registry)

    llm_client = LLMClient(Path(__file__).parent.parent / "config" / "model_routing.yaml")
    human = AutoHumanInterface()

    # 创建runner
    runner = SingleTaskRunner(
        task_id="T3",
        workspace=workspace_dir,
        llm_client=llm_client,
        tool_registry=registry,
        human_interface=human,
        runtime_settings=None,
    )

    # 运行
    try:
        result = await runner.run()
        print(f"\n[TEST] ✅ T3 finished")
        print(f"[TEST] Result: {result}")

        # 检查输出文件
        paper_notes_dir = workspace_dir / "literature" / "paper_notes"
        if paper_notes_dir.exists():
            notes = list(paper_notes_dir.glob("*.md"))
            print(f"\n[TEST] ✅ Generated {len(notes)} paper notes")
            for note in notes:
                print(f"  - {note.name}")
        else:
            print(f"\n[TEST] ❌ paper_notes directory not found")

        comparison_table = workspace_dir / "literature" / "comparison_table.csv"
        if comparison_table.exists():
            print(f"[TEST] ✅ comparison_table.csv created")
        else:
            print(f"[TEST] ⚠️ comparison_table.csv not found")

        related_work = workspace_dir / "literature" / "related_work.bib"
        if related_work.exists():
            print(f"[TEST] ✅ related_work.bib created")
        else:
            print(f"[TEST] ⚠️ related_work.bib not found")

    except Exception as e:
        print(f"\n[TEST] ❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
