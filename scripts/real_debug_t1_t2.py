#!/usr/bin/env python
"""T1+T2真实调试脚本 - 使用真实LLM和API"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from researchos.agents.pi import PIAgent
from researchos.agents.scout import ScoutAgent
from researchos.runtime.agent import ExecutionContext
from researchos.runtime.logger import configure_logging
from researchos.runtime.orchestrator import AgentRunner
from researchos.runtime.llm_client import LLMClient
from researchos.testing.mocks import MockHumanInterface
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.registry import ToolRegistry


async def run_t1(workspace: Path, topic: str) -> bool:
    """运行T1 PIAgent"""
    print("\n" + "="*60)
    print("开始运行 T1 PIAgent（项目初始化）")
    print("="*60)

    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "_runtime" / "traces").mkdir(parents=True, exist_ok=True)
    (workspace / "_runtime" / "logs").mkdir(parents=True, exist_ok=True)
    (workspace / "user_seeds").mkdir(parents=True, exist_ok=True)

    registry = ToolRegistry()
    register_builtin_tools(registry)

    # 使用真实LLM
    from researchos.runtime.config import load_runtime_settings
    runtime_settings = load_runtime_settings()
    llm = LLMClient(routing_config_path=Path("config/model_routing.yaml"))

    # 预设回答（模拟用户输入）
    clarifications = [
        # 第1轮：研究方向详细信息
        f"""我的研究方向是{topic}。

研究问题的具体边界：
- 研究agent如何高效检索和利用长期记忆
- 关注retrieval-augmented generation在agent系统中的应用
- 探索memory indexing和retrieval策略

目标会议：NeurIPS 2026

计算资源约束：
- GPU预算：200小时 A100
- 内存限制：64GB
- 项目截止日期：2026-12-01
""",
        # 第2轮：已有基础
        """已读论文：
1. "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks" (Lewis et al., 2020)
2. "MemPrompt: Memory-assisted Prompt Editing with User Feedback" (Madaan et al., 2022)
3. "Generative Agents: Interactive Simulacra of Human Behavior" (Park et al., 2023)

初步想法：
- 设计分层memory架构（short-term + long-term）
- 使用vector database进行高效检索
- 探索memory consolidation策略

必须遵守的约束：
- 必须在单GPU上可运行（推理阶段）
- 检索延迟<100ms
- 支持增量更新
""",
        # 第3轮：确认
        "确认，请继续生成配置文件。",
    ]

    human = MockHumanInterface(clarifications=clarifications)

    ctx = ExecutionContext(
        workspace_dir=workspace,
        project_id="agent-memory-retrieval",
        task_id="T1",
        run_id="t1_real_run",
        outputs_expected={
            "project": workspace / "project.yaml",
            "seed_papers": workspace / "user_seeds" / "seed_papers.jsonl",
            "seed_ideas": workspace / "user_seeds" / "seed_ideas.md",
            "seed_constraints": workspace / "user_seeds" / "seed_constraints.md",
        },
        mode="init",
        extra={"user_topic": topic},
    )

    runner = AgentRunner(PIAgent(), registry, llm, human)
    result = await runner.run(ctx)

    print("\n" + "="*60)
    print("T1 PIAgent 运行结果:")
    print("="*60)
    print(f"成功: {result.ok}")
    print(f"停止原因: {result.stop_reason}")
    print(f"步数: {result.steps_used}")
    print(f"Token输入: {result.tokens_in}")
    print(f"Token输出: {result.tokens_out}")
    print(f"成本: ${result.cost_usd:.4f}")
    print(f"\n产出文件:")
    for name, path in result.outputs_produced.items():
        exists = path.exists() if isinstance(path, Path) else False
        print(f"  {name}: {path} {'✓' if exists else '✗'}")
    print(f"\nTrace文件: {result.trace_file}")
    print("="*60)

    return result.ok


async def run_t2(workspace: Path) -> bool:
    """运行T2 ScoutAgent"""
    print("\n" + "="*60)
    print("开始运行 T2 ScoutAgent（文献检索）")
    print("="*60)

    (workspace / "literature").mkdir(parents=True, exist_ok=True)

    registry = ToolRegistry()
    register_builtin_tools(registry)

    # 使用真实LLM
    from researchos.runtime.config import load_runtime_settings
    runtime_settings = load_runtime_settings()
    llm = LLMClient(routing_config_path=Path("config/model_routing.yaml"))

    # T2不需要用户交互
    human = MockHumanInterface(clarifications=[])

    ctx = ExecutionContext(
        workspace_dir=workspace,
        project_id="agent-memory-retrieval",
        task_id="T2",
        run_id="t2_real_run",
        outputs_expected={
            "papers_raw": workspace / "literature" / "papers_raw.jsonl",
            "papers_dedup": workspace / "literature" / "papers_dedup.jsonl",
            "search_log": workspace / "literature" / "search_log.md",
            "missing_areas": workspace / "literature" / "missing_areas.md",
        },
        mode="search",
        extra={},
    )

    runner = AgentRunner(ScoutAgent(), registry, llm, human)
    result = await runner.run(ctx)

    print("\n" + "="*60)
    print("T2 ScoutAgent 运行结果:")
    print("="*60)
    print(f"成功: {result.ok}")
    print(f"停止原因: {result.stop_reason}")
    print(f"步数: {result.steps_used}")
    print(f"Token输入: {result.tokens_in}")
    print(f"Token输出: {result.tokens_out}")
    print(f"成本: ${result.cost_usd:.4f}")
    print(f"\n产出文件:")
    for name, path in result.outputs_produced.items():
        exists = path.exists() if isinstance(path, Path) else False
        if exists and name == "papers_dedup":
            # 统计论文数量
            import json
            count = sum(1 for _ in path.open())
            print(f"  {name}: {path} ✓ ({count}篇)")
        else:
            print(f"  {name}: {path} {'✓' if exists else '✗'}")
    print(f"\nTrace文件: {result.trace_file}")
    print("="*60)

    return result.ok


async def main_async(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()

    # 运行T1
    if args.run_t1:
        t1_ok = await run_t1(workspace, args.topic)
        if not t1_ok:
            print("\n❌ T1运行失败，停止执行")
            return 1
        print("\n✓ T1运行成功")

    # 运行T2
    if args.run_t2:
        # 检查T1产出
        project_file = workspace / "project.yaml"
        if not project_file.exists():
            print("\n❌ 缺少project.yaml，请先运行T1")
            return 1

        t2_ok = await run_t2(workspace)
        if not t2_ok:
            print("\n❌ T2运行失败")
            return 1
        print("\n✓ T2运行成功")

    print("\n" + "="*60)
    print("✓ 所有任务完成")
    print("="*60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="T1+T2真实调试（使用真实LLM和API）")
    parser.add_argument("--workspace", default="./workspace/real_agent_memory", help="工作目录")
    parser.add_argument("--topic", default="agent memory retrieval", help="研究主题")
    parser.add_argument("--run-t1", action="store_true", help="运行T1")
    parser.add_argument("--run-t2", action="store_true", help="运行T2")
    parser.add_argument("--all", action="store_true", help="运行T1和T2")
    args = parser.parse_args()

    if args.all:
        args.run_t1 = True
        args.run_t2 = True

    if not args.run_t1 and not args.run_t2:
        print("请指定 --run-t1, --run-t2 或 --all")
        return 1

    configure_logging()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
