#!/usr/bin/env python3
"""
T1 PIAgent 测试脚本 - 通过 extra 参数传递 user_topic

用途：
    测试通过 ExecutionContext.extra 传递 user_topic 的方式运行 T1。

用法：
    python scripts/test_t1_with_topic.py

输出产物：
    - project.yaml: 项目配置
    - user_seeds/: 种子文件目录

前置条件：
    - 需要配置 config/model_routing.yaml
    - 需要有效的 LLM API key

注意：
    此脚本使用硬编码的 API key，仅用于开发测试。
"""

import asyncio
import os
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from researchos.runtime.orchestrator import AgentRunner
from researchos.runtime.agent import ExecutionContext
from researchos.tools.workspace_policy import WorkspaceAccessPolicy
from researchos.tools.registry import ToolRegistry
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.human_gate import HumanInterface
from researchos.agents.registry import TASK_TO_AGENT_MAP
from researchos.runtime.llm_client import LLMClient
from researchos.config.loader import load_model_routing_config


async def main():
    # 设置环境变量
    os.environ.setdefault("OPENAI_API_KEY", "sk-o75I3UPDDeWXWmYkrLfuaUcho9qijDDO4SF2yhJYtDbX4Hef")
    os.environ.setdefault("OPENAI_BASE_URL", "https://sg.uiuiapi.com/v1")

    # 配置
    workspace_dir = Path("/tmp/researchos_real_test_20260419_163709")
    user_topic = "efficient attention mechanisms for transformers"

    print(f"[TEST] Workspace: {workspace_dir}")
    print(f"[TEST] Topic: {user_topic}")

    # 初始化组件
    policy = WorkspaceAccessPolicy(workspace_dir)
    registry = ToolRegistry()
    register_builtin_tools(registry)

    # 创建LLM客户端
    model_config = load_model_routing_config(
        Path(__file__).parent.parent / "config" / "model_routing.yaml"
    )
    llm_client = LLMClient(model_config)

    # 创建HumanInterface（自动回答模式）
    class AutoHumanInterface(HumanInterface):
        async def ask_approval(self, *, tool_name: str, arguments: dict) -> bool:
            print(f"[AUTO-APPROVE] Tool: {tool_name}")
            return True

        async def ask_clarification(self, *, question: str, suggestions: list[str] | None = None) -> str:
            print(f"[AUTO-ANSWER] Question: {question}")
            if suggestions:
                print(f"[AUTO-ANSWER] Suggestions: {suggestions}")
                # 自动选择第一个建议
                return suggestions[0]
            # 返回topic
            return user_topic

        async def present_gate(self, *, gate_id: str, presentation: dict, options: list[dict]) -> dict:
            print(f"[AUTO-GATE] Gate: {gate_id}")
            print(f"[AUTO-GATE] Options: {options}")
            # 自动选择第一个选项
            if options:
                return options[0]
            return {}

    human = AutoHumanInterface()

    # 获取T1 agent类
    agent_class = TASK_TO_AGENT_MAP.get("T1")
    if not agent_class:
        print("[ERROR] T1 agent not found in registry")
        return

    # 创建agent实例
    agent = agent_class()

    # 创建ExecutionContext，通过extra传递user_topic
    ctx = ExecutionContext(
        workspace_dir=workspace_dir,
        policy=policy,
        tool_registry=registry,
        human=human,
        mode="init",
        extra={"user_topic": user_topic}  # 关键：传递user_topic
    )

    # 创建AgentRunner
    runner = AgentRunner(
        agent=agent,
        ctx=ctx,
        llm_client=llm_client,
        log_dir=workspace_dir / "_runtime" / "logs"
    )

    print("[TEST] Starting T1 agent...")

    # 运行agent
    try:
        result = await runner.run()
        print(f"\n[TEST] Agent finished with status: {result.status}")
        print(f"[TEST] Steps: {result.steps_taken}")
        print(f"[TEST] Output: {result.output}")

        # 检查输出文件
        project_yaml = workspace_dir / "project.yaml"
        if project_yaml.exists():
            print(f"\n[TEST] ✅ project.yaml created")
            print(project_yaml.read_text()[:500])
        else:
            print(f"\n[TEST] ❌ project.yaml NOT created")

    except Exception as e:
        print(f"\n[TEST] ❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
