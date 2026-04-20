#!/usr/bin/env python3
"""T2-T4真实LLM测试脚本"""

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


async def run_task(task_id: str, workspace_dir: Path, human_answer: str = "yes"):
    """运行单个task"""
    print(f"\n{'='*60}")
    print(f"Running {task_id}")
    print(f"{'='*60}\n")

    # 创建组件
    registry = ToolRegistry()
    register_builtin_tools(registry)

    llm_client = LLMClient(Path(__file__).parent.parent / "config" / "model_routing.yaml")
    human = AutoHumanInterface(human_answer)

    # 创建runner
    runner = SingleTaskRunner(
        task_id=task_id,
        workspace=workspace_dir,
        llm_client=llm_client,
        tool_registry=registry,
        human_interface=human,
        runtime_settings=None,
    )

    # 运行
    try:
        result = await runner.run()
        # result可能是int（步数）或RunResult对象
        if isinstance(result, int):
            print(f"\n[{task_id}] ✅ Finished with {result} steps")
            return True, result
        else:
            print(f"\n[{task_id}] ✅ Finished with status: {result.status}")
            print(f"[{task_id}] Steps: {result.steps_taken}")
            print(f"[{task_id}] Tokens: {result.tokens_in} in / {result.tokens_out} out")
            print(f"[{task_id}] Cost: ${result.cost_usd:.4f}")
            return True, result
    except Exception as e:
        print(f"\n[{task_id}] ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False, None


async def main():
    # 设置环境变量
    os.environ["OPENAI_API_KEY"] = "sk-o75I3UPDDeWXWmYkrLfuaUcho9qijDDO4SF2yhJYtDbX4Hef"
    os.environ["OPENAI_BASE_URL"] = "https://sg.uiuiapi.com/v1"

    # 配置
    workspace_dir = Path("/tmp/researchos_real_test_20260419_163709")

    print(f"[TEST] Workspace: {workspace_dir}")
    print(f"[TEST] Testing T2-T4 pipeline\n")

    # 检查T1输出
    project_yaml = workspace_dir / "project.yaml"
    if not project_yaml.exists():
        print("[TEST] ❌ project.yaml not found. Please run T1 first.")
        return

    print(f"[TEST] ✅ project.yaml exists")

    # T2: Scout Agent (文献检索)
    print("\n[TEST] Starting T2 (Scout Agent)...")
    success, result = await run_task("T2", workspace_dir)
    if not success:
        print("[TEST] ❌ T2 failed, stopping pipeline")
        return

    # 检查T2输出
    papers_raw = workspace_dir / "literature" / "papers_raw.jsonl"
    papers_dedup = workspace_dir / "literature" / "papers_dedup.jsonl"
    if papers_raw.exists():
        lines = papers_raw.read_text().strip().split("\n")
        print(f"[TEST] ✅ papers_raw.jsonl: {len(lines)} papers")
    if papers_dedup.exists():
        lines = papers_dedup.read_text().strip().split("\n")
        print(f"[TEST] ✅ papers_dedup.jsonl: {len(lines)} papers")

    # T3: Reader Agent (深度阅读) - 简化版
    print("\n[TEST] Starting T3 (Reader Agent) - simplified...")
    print("[TEST] Note: T3 may take a long time with many papers")
    print("[TEST] Consider manually limiting papers_dedup.jsonl to 3-5 papers for testing")

    # 询问是否继续T3
    print("\n[TEST] Continue with T3? (This may take a long time)")
    print("[TEST] Press Ctrl+C to skip T3 and T3.5")

    try:
        success, result = await run_task("T3", workspace_dir)
        if not success:
            print("[TEST] ❌ T3 failed, stopping pipeline")
            return

        # 检查T3输出
        paper_notes_dir = workspace_dir / "literature" / "paper_notes"
        if paper_notes_dir.exists():
            notes = list(paper_notes_dir.glob("*.md"))
            print(f"[TEST] ✅ paper_notes: {len(notes)} notes")

        # T3.5: 文献综合
        print("\n[TEST] Starting T3.5 (Literature Synthesis)...")
        success, result = await run_task("T3.5", workspace_dir)
        if not success:
            print("[TEST] ❌ T3.5 failed, stopping pipeline")
            return

        # 检查T3.5输出
        synthesis = workspace_dir / "literature" / "synthesis.md"
        if synthesis.exists():
            print(f"[TEST] ✅ synthesis.md created")

    except KeyboardInterrupt:
        print("\n[TEST] T3/T3.5 skipped by user")

    # T4: Ideation Agent (假设生成)
    print("\n[TEST] Starting T4 (Ideation Agent)...")
    success, result = await run_task("T4", workspace_dir, human_answer="proceed")
    if not success:
        print("[TEST] ❌ T4 failed")
        return

    # 检查T4输出
    hypotheses = workspace_dir / "ideation" / "hypotheses.md"
    exp_plan = workspace_dir / "ideation" / "exp_plan.yaml"
    if hypotheses.exists():
        print(f"[TEST] ✅ hypotheses.md created")
    if exp_plan.exists():
        print(f"[TEST] ✅ exp_plan.yaml created")

    print("\n[TEST] ✅ All tests completed!")


if __name__ == "__main__":
    asyncio.run(main())
