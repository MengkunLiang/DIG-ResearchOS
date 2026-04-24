#!/usr/bin/env python3
"""T4 Ideation Agent测试脚本"""

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
    """自动回答的HumanInterface - 为T4定制"""

    def __init__(self):
        self.call_count = 0
        self.gate_count = 0

    async def ask_approval(self, *, tool_name: str, arguments: dict) -> bool:
        print(f"[AUTO-APPROVE] Tool: {tool_name}")
        return True

    async def ask_clarification(self, *, question: str, suggestions: list[str] | None = None) -> str:
        self.call_count += 1
        print(f"\n[AUTO-ANSWER #{self.call_count}] Question: {question[:100]}...")
        if suggestions:
            print(f"[AUTO-ANSWER] Suggestions: {suggestions}")
            return suggestions[0] if suggestions else "proceed"
        return "proceed"

    async def present_gate(self, *, gate_id: str, presentation: dict, options: list[dict]) -> dict:
        self.gate_count += 1
        print(f"\n[AUTO-GATE #{self.gate_count}] Gate: {gate_id}")
        print(f"[AUTO-GATE] Presentation keys: {list(presentation.keys())}")

        # Gate1: 假设评审 - 自动选择"proceed"
        if gate_id == "gate1_hypothesis_review":
            print(f"[AUTO-GATE] Gate1: Hypothesis Review - Auto-approving")
            for option in options:
                if option.get("action") == "proceed":
                    return option
            return options[0] if options else {"action": "proceed"}

        # Gate2: 实验计划评审 - 自动选择"proceed"
        elif gate_id == "gate2_experiment_review":
            print(f"[AUTO-GATE] Gate2: Experiment Review - Auto-approving")
            for option in options:
                if option.get("action") == "proceed":
                    return option
            return options[0] if options else {"action": "proceed"}

        # 默认：选择第一个选项
        else:
            print(f"[AUTO-GATE] Unknown gate, selecting first option")
            return options[0] if options else {}


async def main():
    ensure_script_llm_env(Path(__file__).parent.parent)

    # 使用T3.5的输出作为输入
    workspace_dir = Path("/tmp/researchos_test_t3_20260419")

    if not workspace_dir.exists():
        print("[TEST] ❌ T3.5 workspace not found. Please run T3.5 first.")
        return 1

    # 检查T3.5输出
    synthesis = workspace_dir / "literature" / "synthesis.md"
    if not synthesis.exists():
        print("[TEST] ❌ synthesis.md not found. Please run T3.5 first.")
        return 1

    print(f"[TEST] Workspace: {workspace_dir}")
    print(f"[TEST] Starting T4 (Ideation Agent)...\n")

    # 创建组件
    registry = ToolRegistry()
    register_builtin_tools(registry)

    llm_client = LLMClient(Path(__file__).parent.parent / "config" / "model_routing.yaml")
    human = AutoHumanInterface()

    # 创建runner
    runner = SingleTaskRunner(
        task_id="T4",
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
        print(f"[TEST] Gates encountered: {human.gate_count}")

        # 检查输出文件
        hypotheses = workspace_dir / "ideation" / "hypotheses.md"
        exp_plan = workspace_dir / "ideation" / "exp_plan.yaml"
        risks = workspace_dir / "ideation" / "risks.md"

        if hypotheses.exists():
            content = hypotheses.read_text()
            print(f"\n[TEST] ✅ hypotheses.md created ({len(content)} chars)")
        else:
            print(f"\n[TEST] ❌ hypotheses.md NOT created")

        if exp_plan.exists():
            content = exp_plan.read_text()
            print(f"[TEST] ✅ exp_plan.yaml created ({len(content)} chars)")
        else:
            print(f"[TEST] ❌ exp_plan.yaml NOT created")

        if risks.exists():
            content = risks.read_text()
            print(f"[TEST] ✅ risks.md created ({len(content)} chars)")
        else:
            print(f"[TEST] ⚠️  risks.md NOT created (optional)")

        return exit_code

    except Exception as e:
        print(f"\n[TEST] ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
