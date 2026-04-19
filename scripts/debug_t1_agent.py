#!/usr/bin/env python
"""T1 PIAgent调试脚本 - Mock模式"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from researchos.agents.pi import PIAgent
from researchos.runtime.agent import ExecutionContext
from researchos.runtime.logger import configure_logging
from researchos.runtime.orchestrator import AgentRunner
from researchos.testing.mocks import (
    FakeLLMMessage,
    FakeRawCompletion,
    FakeToolCall,
    MockHumanInterface,
    MockLLMClient,
)
from researchos.tools.builtin import register_builtin_tools
from researchos.tools.registry import ToolRegistry


def build_mock_llm_for_t1() -> MockLLMClient:
    """构建T1 init模式的mock LLM响应"""
    return MockLLMClient(
        responses=[
            # 第1轮：询问用户研究方向
            FakeRawCompletion(
                message=FakeLLMMessage(
                    content="我将开始T1项目初始化流程，首先询问您的研究方向。",
                    tool_calls=[
                        FakeToolCall(
                            name="ask_human",
                            arguments={
                                "question": "请详细描述您的研究方向：\n1. 研究问题的具体边界\n2. 目标会议/期刊\n3. 计算资源约束"
                            },
                            id="tc_ask1",
                        )
                    ],
                ),
                prompt_tokens=100,
                completion_tokens=50,
            ),
            # 第2轮：写入project.yaml（符合schema）
            FakeRawCompletion(
                message=FakeLLMMessage(
                    content="根据您的回答，我将创建项目配置文件。",
                    tool_calls=[
                        FakeToolCall(
                            name="write_file",
                            arguments={
                                "path": "project.yaml",
                                "content": """project_id: test-project
research_direction: discrete diffusion language models
keywords:
  - discrete diffusion
  - language model
  - factorized
created_at: "2026-04-19T14:00:00Z"
constraints:
  max_budget_usd: 1000.0
  compute_resources:
    allow_gpu: true
    max_memory_gb: 32
""",
                            },
                            id="tc_write_project",
                        )
                    ],
                ),
                prompt_tokens=150,
                completion_tokens=80,
            ),
            # 第3轮：创建seed文件
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="write_file",
                            arguments={
                                "path": "user_seeds/seed_papers.jsonl",
                                "content": "",
                            },
                            id="tc_write_seed_papers",
                        )
                    ],
                ),
                prompt_tokens=100,
                completion_tokens=30,
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="write_file",
                            arguments={
                                "path": "user_seeds/seed_ideas.md",
                                "content": "# Seed Ideas\n\n- Explore discrete diffusion for language modeling\n",
                            },
                            id="tc_write_seed_ideas",
                        )
                    ],
                ),
                prompt_tokens=100,
                completion_tokens=30,
            ),
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="write_file",
                            arguments={
                                "path": "user_seeds/seed_constraints.md",
                                "content": "# Constraints\n\n- GPU budget: 100 hours\n",
                            },
                            id="tc_write_seed_constraints",
                        )
                    ],
                ),
                prompt_tokens=100,
                completion_tokens=30,
            ),
            # 完成
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="finish_task",
                            arguments={"summary": "T1 项目初始化完成"},
                            id="tc_finish",
                        )
                    ]
                ),
                prompt_tokens=100,
                completion_tokens=20,
            ),
        ]
    )


async def main_async(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "_runtime" / "traces").mkdir(parents=True, exist_ok=True)
    (workspace / "_runtime" / "logs").mkdir(parents=True, exist_ok=True)
    (workspace / "user_seeds").mkdir(parents=True, exist_ok=True)

    registry = ToolRegistry()
    register_builtin_tools(registry)

    llm = build_mock_llm_for_t1() if args.mock else None
    if llm is None:
        raise SystemExit("当前脚本只支持 --mock 模式")

    ctx = ExecutionContext(
        workspace_dir=workspace,
        project_id="test-project",
        task_id="T1",
        run_id="t1_debug_run",
        outputs_expected={
            "project": workspace / "project.yaml",
            "seed_papers": workspace / "user_seeds" / "seed_papers.jsonl",
            "seed_ideas": workspace / "user_seeds" / "seed_ideas.md",
            "seed_constraints": workspace / "user_seeds" / "seed_constraints.md",
        },
        mode="init",
        extra={"user_topic": "discrete diffusion language models"},
    )

    # Mock human interface会自动回答ask_human
    human = MockHumanInterface(
        clarifications=["我的研究方向是discrete diffusion language models，目标NeurIPS，有100小时GPU预算"]
    )

    runner = AgentRunner(PIAgent(), registry, llm, human)
    result = await runner.run(ctx)

    print("\n" + "="*60)
    print("T1 PIAgent 调试结果:")
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

    return 0 if result.ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="调试T1 PIAgent")
    parser.add_argument("--mock", action="store_true", help="使用mock LLM")
    parser.add_argument("--workspace", default="./workspace/debug_t1", help="工作目录")
    args = parser.parse_args()
    configure_logging()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
