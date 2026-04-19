#!/usr/bin/env python3
"""T4测试脚本 - Ideation Agent"""

import asyncio
import os
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from researchos.cli_runners import SingleTaskRunner
from researchos.runtime.llm_client import LLMClient
from researchos.tools.registry import ToolRegistry
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.human_gate import HumanInterface


class AutoHumanInterface(HumanInterface):
    """自动回答的HumanInterface，用于T4的Gate1和Gate2"""

    def __init__(self):
        self.call_count = 0

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
        print(f"\n[AUTO-GATE] Gate: {gate_id}")
        print(f"[AUTO-GATE] Presentation keys: {list(presentation.keys())}")

        if gate_id == "gate1_hypothesis_review":
            # Gate1: 选择"proceed"继续
            print("[AUTO-GATE] Selecting 'proceed' for Gate1")
            for opt in options:
                if opt.get("action") == "proceed":
                    return opt
            return options[0] if options else {}

        elif gate_id == "gate2_plan_approval":
            # Gate2: 选择"approve"批准
            print("[AUTO-GATE] Selecting 'approve' for Gate2")
            for opt in options:
                if opt.get("action") == "approve":
                    return opt
            return options[0] if options else {}

        # 默认选择第一个选项
        print(f"[AUTO-GATE] Selecting first option")
        return options[0] if options else {}


async def main():
    # 设置环境变量
    os.environ["UIUIAPI_API_KEY"] = "sk-o75I3UPDDeWXWmYkrLfuaUcho9qijDDO4SF2yhJYtDbX4Hef"
    os.environ["UIUIAPI_BASE_URL"] = "https://sg.uiuiapi.com/v1"

    # 配置
    workspace_dir = Path("/tmp/researchos_real_test_20260419_163709")

    print(f"[TEST] Workspace: {workspace_dir}")
    print(f"[TEST] Starting T4 agent (Ideation)...\n")

    # 检查synthesis.md
    synthesis_file = workspace_dir / "literature" / "synthesis.md"
    if not synthesis_file.exists():
        print("[TEST] ❌ synthesis.md not found")
        return

    print(f"[TEST] ✅ synthesis.md exists")

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
        result = await runner.run()
        print(f"\n[TEST] ✅ T4 finished")
        print(f"[TEST] Result: {result}")

        # 检查输出文件
        hypotheses = workspace_dir / "ideation" / "hypotheses.md"
        if hypotheses.exists():
            print(f"\n[TEST] ✅ hypotheses.md created")
            content = hypotheses.read_text()
            print(f"[TEST] Size: {len(content)} bytes")
        else:
            print(f"\n[TEST] ❌ hypotheses.md not found")

        exp_plan = workspace_dir / "ideation" / "exp_plan.yaml"
        if exp_plan.exists():
            print(f"[TEST] ✅ exp_plan.yaml created")
            content = exp_plan.read_text()
            print(f"[TEST] Size: {len(content)} bytes")
        else:
            print(f"[TEST] ❌ exp_plan.yaml not found")

        risks = workspace_dir / "ideation" / "risks.md"
        if risks.exists():
            print(f"[TEST] ✅ risks.md created")
        else:
            print(f"[TEST] ⚠️ risks.md not found (optional)")

    except Exception as e:
        print(f"\n[TEST] ❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
