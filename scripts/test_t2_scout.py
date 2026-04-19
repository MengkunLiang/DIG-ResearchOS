#!/usr/bin/env python3
"""T2 Scout Agent测试脚本"""

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


async def main():
    # 设置环境变量
    os.environ["UIUIAPI_API_KEY"] = "sk-o75I3UPDDeWXWmYkrLfuaUcho9qijDDO4SF2yhJYtDbX4Hef"
    os.environ["UIUIAPI_BASE_URL"] = "https://sg.uiuiapi.com/v1"

    # 配置
    workspace_dir = Path("/tmp/researchos_test_t2_20260419")
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # 复制project.yaml
    src_project = Path("/tmp/researchos_real_test_20260419_163709/project.yaml")
    dst_project = workspace_dir / "project.yaml"
    if src_project.exists():
        import shutil
        shutil.copy2(src_project, dst_project)
        print(f"[TEST] Copied project.yaml")

    print(f"[TEST] Workspace: {workspace_dir}")
    print(f"[TEST] Starting T2 (Scout Agent)...\n")

    # 创建组件
    registry = ToolRegistry()
    register_builtin_tools(registry)

    llm_client = LLMClient(Path(__file__).parent.parent / "config" / "model_routing.yaml")
    human = AutoHumanInterface()

    # 创建runner
    runner = SingleTaskRunner(
        task_id="T2",
        workspace=workspace_dir,
        llm_client=llm_client,
        tool_registry=registry,
        human_interface=human,
        runtime_settings=None,
    )

    # 运行
    try:
        result = await runner.run()
        print(f"\n[TEST] ✅ Agent finished with status: {result.status}")
        print(f"[TEST] Steps: {result.steps_taken}")
        print(f"[TEST] Tokens: {result.tokens_in} in / {result.tokens_out} out / {result.tokens_in + result.tokens_out} total")
        print(f"[TEST] Cost: ${result.cost_usd:.4f}")

        # 检查输出文件
        papers_raw = workspace_dir / "literature" / "papers_raw.jsonl"
        papers_dedup = workspace_dir / "literature" / "papers_dedup.jsonl"
        search_log = workspace_dir / "literature" / "search_log.md"
        missing_areas = workspace_dir / "literature" / "missing_areas.md"

        if papers_raw.exists():
            lines = papers_raw.read_text().strip().split("\n")
            print(f"\n[TEST] ✅ papers_raw.jsonl: {len(lines)} papers")
        else:
            print(f"\n[TEST] ❌ papers_raw.jsonl NOT created")

        if papers_dedup.exists():
            lines = papers_dedup.read_text().strip().split("\n")
            print(f"[TEST] ✅ papers_dedup.jsonl: {len(lines)} papers")
        else:
            print(f"[TEST] ❌ papers_dedup.jsonl NOT created")

        if search_log.exists():
            print(f"[TEST] ✅ search_log.md created")
        else:
            print(f"[TEST] ⚠️  search_log.md NOT created (optional)")

        if missing_areas.exists():
            print(f"[TEST] ✅ missing_areas.md created")
        else:
            print(f"[TEST] ⚠️  missing_areas.md NOT created (optional)")

        return 0 if result.ok else 1

    except Exception as e:
        print(f"\n[TEST] ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
