#!/usr/bin/env python3
"""T3.5 文献综合测试脚本"""

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
        print(f"[AUTO-GATE] Presentation: {presentation}")
        if options:
            print(f"[AUTO-GATE] Selecting first option: {options[0]}")
            return options[0]
        return {}


async def main():
    ensure_script_llm_env(Path(__file__).parent.parent)

    # 使用T3的输出作为输入
    workspace_dir = Path("/tmp/researchos_test_t3_20260419")

    if not workspace_dir.exists():
        print("[TEST] ❌ T3 workspace not found. Please run T3 first.")
        return 1

    # 检查T3输出
    paper_notes_dir = workspace_dir / "literature" / "paper_notes"
    if not paper_notes_dir.exists() or not list(paper_notes_dir.glob("*.md")):
        print("[TEST] ❌ T3 paper_notes not found. Please run T3 first.")
        return 1

    print(f"[TEST] Workspace: {workspace_dir}")
    print(f"[TEST] Starting T3.5 (Literature Synthesis)...\n")

    # 创建组件
    registry = ToolRegistry()
    register_builtin_tools(registry)

    llm_client = LLMClient(Path(__file__).parent.parent / "config" / "model_routing.yaml")
    human = AutoHumanInterface()

    # 创建runner
    runner = SingleTaskRunner(
        task_id="T3.5",
        workspace=workspace_dir,
        llm_client=llm_client,
        tool_registry=registry,
        human_interface=human,
        runtime_settings=None,
    )

    # 运行
    try:
        exit_code = await runner.run()
        print(f"\n[TEST] ✅ Agent finished with exit code: {exit_code}")

        # 检查输出文件
        synthesis = workspace_dir / "literature" / "synthesis.md"

        if synthesis.exists():
            content = synthesis.read_text()
            print(f"\n[TEST] ✅ synthesis.md created ({len(content)} chars)")

            # 检查必需章节
            required_sections = [
                "# 文献综述",
                "## 1. 研究背景",
                "## 2. 核心方法",
                "## 3. 实验结果",
                "## 4. 研究缺口",
                "## 5. 未来方向"
            ]

            missing_sections = []
            for section in required_sections:
                if section not in content:
                    missing_sections.append(section)

            if missing_sections:
                print(f"[TEST] ⚠️  Missing sections: {missing_sections}")
            else:
                print(f"[TEST] ✅ All required sections present")
        else:
            print(f"\n[TEST] ❌ synthesis.md NOT created")

        return exit_code

    except Exception as e:
        print(f"\n[TEST] ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
