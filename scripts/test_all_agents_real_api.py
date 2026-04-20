#!/usr/bin/env python3
"""
ResearchOS Agent 全面真实 API 测试脚本

用法:
    python scripts/test_all_agents_real_api.py [--workspace PATH] [--agent AGENT] [--verbose]

示例:
    # 测试所有 agent
    python scripts/test_all_agents_real_api.py

    # 只测试 T1 (PIAgent)
    python scripts/test_all_agents_real_api.py --agent hello

    # 指定 workspace
    python scripts/test_all_agents_real_api.py --workspace /tmp/test_agents
"""

import argparse
import asyncio
import json
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from researchos.agents.registry import AGENT_REGISTRY
from researchos.runtime.agent import AgentSpec, ExecutionContext
from researchos.runtime.llm_client import LLMClient


@dataclass
class TestResult:
    """测试结果"""
    agent_name: str
    task_id: str
    success: bool
    duration_ms: float
    api_calls: int
    cost: float
    error: str | None = None
    outputs: dict[str, Any] = field(default_factory=dict)


class AgentRealAPITester:
    """Agent 真实 API 测试器"""

    def __init__(self, workspace: Path, routing_config: Path, verbose: bool = False):
        self.workspace = workspace
        self.verbose = verbose
        self.routing_config = routing_config
        self.llm_client = LLMClient(routing_config)
        self.results: list[TestResult] = []

    def log(self, msg: str):
        """打印日志"""
        if self.verbose:
            print(f"  [INFO] {msg}")

    def get_response_content(self, response) -> str:
        """从 LLMResponse 中提取 content"""
        if hasattr(response, 'raw') and response.raw and hasattr(response.raw, 'choices'):
            return response.raw.choices[0].message.content or ""
        elif hasattr(response, 'content'):
            return response.content or ""
        return ""

    def print(self, msg: str):
        """打印消息"""
        print(msg)

    async def call_agent_api(
        self,
        agent: Any,
        ctx: ExecutionContext,
        multi_round: bool = False,
        extra_messages: list[str] | None = None
    ) -> tuple[Any, int, float]:
        """调用 agent API，返回 (response, api_calls, cost)"""
        system_prompt = agent.system_prompt(ctx)
        initial_msg = agent.initial_user_message(ctx)

        self.log(f"System prompt length: {len(system_prompt)}")
        self.log(f"Initial message: {initial_msg[:100]}...")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": initial_msg},
        ]

        if extra_messages:
            for msg in extra_messages:
                messages.append({"role": "user", "content": msg})

        # 调用 API
        response = await self.llm_client.chat(
            messages=messages,
            tools=None,  # 测试模式不使用工具
            temperature=agent.spec.temperature,
            tier=agent.spec.model_tier,
        )

        total_cost = response.cost_usd
        api_calls = 1

        content = self.get_response_content(response)
        self.log(f"Response: {content[:200]}...")

        return response, api_calls, total_cost

    async def test_hello_agent(self) -> TestResult:
        """测试 HELLO agent - 最简单的测试"""
        agent_name = "hello"
        task_id = "HELLO"
        start = time.time()

        try:
            self.print(f"\n{'='*60}")
            self.print(f"测试 {agent_name} (Task: {task_id})")
            self.print(f"{'='*60}")

            ws = self.workspace / "hello_test"
            ws.mkdir(parents=True, exist_ok=True)

            ctx = ExecutionContext(
                workspace_dir=ws,
                project_id="test-hello",
                task_id=task_id,
                run_id=f"test-{task_id.lower()}",
                outputs_expected={"hello_file": ws / "hello.txt"},
            )

            agent_cls = AGENT_REGISTRY.get(agent_name)
            if not agent_cls:
                return TestResult(
                    agent_name=agent_name,
                    task_id=task_id,
                    success=False,
                    duration_ms=0,
                    api_calls=0,
                    cost=0,
                    error=f"Agent '{agent_name}' not found in registry",
                )

            agent = agent_cls()
            self.log(f"Agent: {agent.spec.name}")
            self.log(f"Model tier: {agent.spec.model_tier}")

            response, api_calls, cost = await self.call_agent_api(agent, ctx)

            # 写入输出文件
            content = self.get_response_content(response)
            output_file = ws / "hello.txt"
            output_file.write_text(content, encoding="utf-8")

            duration = (time.time() - start) * 1000

            self.print(f"✓ {agent_name} 测试通过!")
            self.print(f"  耗时: {duration:.0f}ms")
            self.print(f"  API调用: {api_calls}次")
            self.print(f"  成本: ${cost:.4f}")
            self.print(f"  模型: {response.model_used}")

            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=True,
                duration_ms=duration,
                api_calls=api_calls,
                cost=cost,
                outputs={"hello.txt": str(output_file)},
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            tb = traceback.format_exc()
            self.print(f"✗ {agent_name} 测试失败!")
            self.print(f"  错误: {e}")
            if self.verbose:
                self.print(tb)
            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=False,
                duration_ms=duration,
                api_calls=0,
                cost=0,
                error=str(e),
            )

    async def test_pi_agent_init(self) -> TestResult:
        """测试 PI Agent (T1) - 项目初始化"""
        agent_name = "pi"
        task_id = "T1"
        start = time.time()

        try:
            self.print(f"\n{'='*60}")
            self.print(f"测试 {agent_name} (Task: {task_id}) - Init Mode")
            self.print(f"{'='*60}")

            ws = self.workspace / "pi_test"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "user_seeds").mkdir(exist_ok=True)

            # 预先创建 seed 文件
            user_topic = "Efficient Transformer architectures for long-sequence modeling"
            (ws / "user_seeds" / "seed_papers.jsonl").write_text(
                '{"title": "Attention Is All You Need", "authors": ["Vaswani et al."], "year": 2017, "role": "anchor"}\n'
                '{"title": "Longformer", "authors": ["Beltagy et al."], "year": 2020, "role": "related"}\n',
                encoding="utf-8"
            )
            (ws / "user_seeds" / "seed_ideas.md").write_text(
                "# Initial Ideas\n\n- Explore sparse attention mechanisms\n- Reduce quadratic complexity\n",
                encoding="utf-8"
            )
            (ws / "user_seeds" / "seed_constraints.md").write_text(
                "# Constraints\n\n- GPU required\n- Budget: $50\n",
                encoding="utf-8"
            )

            ctx = ExecutionContext(
                workspace_dir=ws,
                project_id="test-pi-init",
                task_id=task_id,
                run_id="test-t1",
                mode="init",
                extra={"user_topic": user_topic},
                outputs_expected={
                    "project": ws / "project.yaml",
                },
            )

            agent_cls = AGENT_REGISTRY.get(agent_name)
            if not agent_cls:
                return TestResult(
                    agent_name=agent_name,
                    task_id=task_id,
                    success=False,
                    duration_ms=0,
                    api_calls=0,
                    cost=0,
                    error=f"Agent '{agent_name}' not found",
                )

            agent = agent_cls()
            self.log(f"Agent: {agent.spec.name}")
            self.log(f"Topic: {user_topic}")

            # 多轮对话模拟
            system_prompt = agent.system_prompt(ctx)
            initial_msg = agent.initial_user_message(ctx)

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": initial_msg},
            ]

            # Round 1
            response1 = await self.llm_client.chat(
                messages=messages,
                tools=None,
                temperature=agent.spec.temperature,
                tier=agent.spec.model_tier,
            )
            content1 = self.get_response_content(response1)
            messages.append({"role": "assistant", "content": content1})
            self.log(f"Round 1 response: {content1[:150]}...")

            # Round 2: 用户确认
            confirm_msg = f"研究方向是: {user_topic}。请继续生成 project.yaml。"
            messages.append({"role": "user", "content": confirm_msg})

            response2 = await self.llm_client.chat(
                messages=messages,
                tools=None,
                temperature=agent.spec.temperature,
                tier=agent.spec.model_tier,
            )
            content2 = self.get_response_content(response2)
            messages.append({"role": "assistant", "content": content2})

            # 从响应中提取 YAML
            content = content2
            if "```yaml" in content:
                start_idx = content.find("```yaml") + 6
                end_idx = content.find("```", start_idx)
                yaml_content = content[start_idx:end_idx].strip()
                project_path = ws / "project.yaml"
                project_path.write_text(yaml_content, encoding="utf-8")
                self.log(f"Wrote project.yaml ({len(yaml_content)} chars)")

            duration = (time.time() - start) * 1000
            total_cost = response1.cost_usd + response2.cost_usd

            self.print(f"✓ {agent_name} 测试完成!")
            self.print(f"  耗时: {duration:.0f}ms")
            self.print(f"  API调用: 2次")
            self.print(f"  成本: ${total_cost:.4f}")

            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=True,
                duration_ms=duration,
                api_calls=2,
                cost=total_cost,
                outputs={"project.yaml": str(ws / "project.yaml")},
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            tb = traceback.format_exc()
            self.print(f"✗ {agent_name} 测试失败!")
            self.print(f"  错误: {e}")
            if self.verbose:
                self.print(tb)
            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=False,
                duration_ms=duration,
                api_calls=0,
                cost=0,
                error=str(e),
            )

    async def test_scout_agent(self) -> TestResult:
        """测试 Scout Agent (T2) - 文献检索"""
        agent_name = "scout"
        task_id = "T2"
        start = time.time()

        try:
            self.print(f"\n{'='*60}")
            self.print(f"测试 {agent_name} (Task: {task_id})")
            self.print(f"{'='*60}")

            ws = self.workspace / "scout_test"
            ws.mkdir(parents=True, exist_ok=True)

            # 预先创建 project.yaml
            project_data = {
                "project_id": "test-scout",
                "research_direction": "Efficient Transformers for long sequences",
                "keywords": ["transformer", "long-sequence", "attention"],
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data), encoding="utf-8")

            ctx = ExecutionContext(
                workspace_dir=ws,
                project_id="test-scout",
                task_id=task_id,
                run_id="test-t2",
                inputs={"project": ws / "project.yaml"},
                outputs_expected={
                    "papers_raw": ws / "literature" / "papers_raw.jsonl",
                    "search_log": ws / "literature" / "search_log.md",
                },
            )

            agent_cls = AGENT_REGISTRY.get(agent_name)
            if not agent_cls:
                return TestResult(
                    agent_name=agent_name,
                    task_id=task_id,
                    success=False,
                    duration_ms=0,
                    api_calls=0,
                    cost=0,
                    error=f"Agent '{agent_name}' not found",
                )

            agent = agent_cls()
            self.log(f"Agent: {agent.spec.name}")

            response, api_calls, cost = await self.call_agent_api(agent, ctx)

            duration = (time.time() - start) * 1000

            self.print(f"✓ {agent_name} 测试完成!")
            self.print(f"  耗时: {duration:.0f}ms")
            self.print(f"  API调用: {api_calls}次")
            self.print(f"  成本: ${cost:.4f}")

            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=True,
                duration_ms=duration,
                api_calls=api_calls,
                cost=cost,
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            tb = traceback.format_exc()
            self.print(f"✗ {agent_name} 测试失败!")
            self.print(f"  错误: {e}")
            if self.verbose:
                self.print(tb)
            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=False,
                duration_ms=duration,
                api_calls=0,
                cost=0,
                error=str(e),
            )

    async def test_reader_agent(self) -> TestResult:
        """测试 Reader Agent (T3) - 文献阅读"""
        agent_name = "reader"
        task_id = "T3"
        start = time.time()

        try:
            self.print(f"\n{'='*60}")
            self.print(f"测试 {agent_name} (Task: {task_id}) - Read Mode")
            self.print(f"{'='*60}")

            ws = self.workspace / "reader_test"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "literature").mkdir(exist_ok=True)

            # 预先创建 project.yaml 和 papers_dedup.jsonl
            project_data = {
                "project_id": "test-reader",
                "research_direction": "Efficient Transformers for long sequences",
                "direction": "Efficient Transformers for long sequences",  # Reader 模板需要这个字段
                "keywords": ["transformer", "long-sequence", "attention"],
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data), encoding="utf-8")

            # 预先创建 papers_dedup.jsonl
            papers = [
                {"id": "arxiv:1706.03762", "title": "Attention Is All You Need"},
                {"id": "arxiv:2004.05150", "title": "Longformer"},
            ]
            (ws / "literature" / "papers_dedup.jsonl").write_text(
                "\n".join(json.dumps(p) for p in papers),
                encoding="utf-8"
            )

            ctx = ExecutionContext(
                workspace_dir=ws,
                project_id="test-reader",
                task_id=task_id,
                run_id="test-t3",
                mode="read",
                inputs={
                    "papers_dedup": ws / "literature" / "papers_dedup.jsonl",
                },
                outputs_expected={
                    "paper_notes_dir": ws / "literature" / "paper_notes",
                },
            )

            agent_cls = AGENT_REGISTRY.get(agent_name)
            if not agent_cls:
                return TestResult(
                    agent_name=agent_name,
                    task_id=task_id,
                    success=False,
                    duration_ms=0,
                    api_calls=0,
                    cost=0,
                    error=f"Agent '{agent_name}' not found",
                )

            agent = agent_cls()
            self.log(f"Agent: {agent.spec.name}")

            response, api_calls, cost = await self.call_agent_api(agent, ctx)

            duration = (time.time() - start) * 1000

            self.print(f"✓ {agent_name} 测试完成!")
            self.print(f"  耗时: {duration:.0f}ms")
            self.print(f"  API调用: {api_calls}次")
            self.print(f"  成本: ${cost:.4f}")

            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=True,
                duration_ms=duration,
                api_calls=api_calls,
                cost=cost,
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            tb = traceback.format_exc()
            self.print(f"✗ {agent_name} 测试失败!")
            self.print(f"  错误: {e}")
            if self.verbose:
                self.print(tb)
            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=False,
                duration_ms=duration,
                api_calls=0,
                cost=0,
                error=str(e),
            )

    async def test_ideation_agent(self) -> TestResult:
        """测试 Ideation Agent (T4) - 假设生成"""
        agent_name = "ideation"
        task_id = "T4"
        start = time.time()

        try:
            self.print(f"\n{'='*60}")
            self.print(f"测试 {agent_name} (Task: {task_id})")
            self.print(f"{'='*60}")

            ws = self.workspace / "ideation_test"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "ideation").mkdir(exist_ok=True)
            (ws / "literature").mkdir(exist_ok=True)

            # 预先创建 synthesis.md
            synthesis = """# 文献综述

## 现有方法
1. **Vanilla Transformer**: O(n²) 注意力复杂度
2. **Longformer**: 局部注意力 + 全局注意力
3. **BigBird**: 稀疏注意力机制

## 研究空白
- 现有方法在超长序列上仍有效率问题
- 缺少结合局部和全局注意力的最优方案
"""
            (ws / "literature" / "synthesis.md").write_text(synthesis, encoding="utf-8")

            ctx = ExecutionContext(
                workspace_dir=ws,
                project_id="test-ideation",
                task_id=task_id,
                run_id="test-t4",
                inputs={
                    "synthesis": ws / "literature" / "synthesis.md",
                },
                outputs_expected={
                    "hypotheses": ws / "ideation" / "hypotheses.md",
                    "exp_plan": ws / "ideation" / "exp_plan.yaml",
                },
            )

            agent_cls = AGENT_REGISTRY.get(agent_name)
            if not agent_cls:
                return TestResult(
                    agent_name=agent_name,
                    task_id=task_id,
                    success=False,
                    duration_ms=0,
                    api_calls=0,
                    cost=0,
                    error=f"Agent '{agent_name}' not found",
                )

            agent = agent_cls()
            self.log(f"Agent: {agent.spec.name}")

            response, api_calls, cost = await self.call_agent_api(agent, ctx)

            duration = (time.time() - start) * 1000

            self.print(f"✓ {agent_name} 测试完成!")
            self.print(f"  耗时: {duration:.0f}ms")
            self.print(f"  API调用: {api_calls}次")
            self.print(f"  成本: ${cost:.4f}")

            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=True,
                duration_ms=duration,
                api_calls=api_calls,
                cost=cost,
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            tb = traceback.format_exc()
            self.print(f"✗ {agent_name} 测试失败!")
            self.print(f"  错误: {e}")
            if self.verbose:
                self.print(tb)
            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=False,
                duration_ms=duration,
                api_calls=0,
                cost=0,
                error=str(e),
            )

    async def test_novelty_auditor_agent(self) -> TestResult:
        """测试 NoveltyAuditor Agent (T4.5) - 新颖性审计"""
        agent_name = "novelty_auditor"
        task_id = "T4.5"
        start = time.time()

        try:
            self.print(f"\n{'='*60}")
            self.print(f"测试 {agent_name} (Task: {task_id})")
            self.print(f"{'='*60}")

            ws = self.workspace / "novelty_auditor_test"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "ideation").mkdir(exist_ok=True)

            # 预先创建 hypotheses.md
            hypotheses = """# 研究假设

## H1: 混合局部-全局注意力机制
结合局部注意力的效率和全局注意力的表达能力。
"""
            (ws / "ideation" / "hypotheses.md").write_text(hypotheses, encoding="utf-8")

            ctx = ExecutionContext(
                workspace_dir=ws,
                project_id="test-novelty-auditor",
                task_id=task_id,
                run_id="test-t45",
                inputs={
                    "hypotheses": ws / "ideation" / "hypotheses.md",
                },
                outputs_expected={
                    "novelty_audit": ws / "ideation" / "novelty_audit.md",
                },
            )

            agent_cls = AGENT_REGISTRY.get(agent_name)
            if not agent_cls:
                return TestResult(
                    agent_name=agent_name,
                    task_id=task_id,
                    success=False,
                    duration_ms=0,
                    api_calls=0,
                    cost=0,
                    error=f"Agent '{agent_name}' not found",
                )

            agent = agent_cls()
            self.log(f"Agent: {agent.spec.name}")

            response, api_calls, cost = await self.call_agent_api(agent, ctx)

            duration = (time.time() - start) * 1000

            self.print(f"✓ {agent_name} 测试完成!")
            self.print(f"  耗时: {duration:.0f}ms")
            self.print(f"  API调用: {api_calls}次")
            self.print(f"  成本: ${cost:.4f}")

            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=True,
                duration_ms=duration,
                api_calls=api_calls,
                cost=cost,
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            tb = traceback.format_exc()
            self.print(f"✗ {agent_name} 测试失败!")
            self.print(f"  错误: {e}")
            if self.verbose:
                self.print(tb)
            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=False,
                duration_ms=duration,
                api_calls=0,
                cost=0,
                error=str(e),
            )

    async def test_novelty_agent(self) -> TestResult:
        """测试 Novelty Agent (T6) - 新颖性最终验证"""
        agent_name = "novelty"
        task_id = "T6"
        start = time.time()

        try:
            self.print(f"\n{'='*60}")
            self.print(f"测试 {agent_name} (Task: {task_id})")
            self.print(f"{'='*60}")

            ws = self.workspace / "novelty_test"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "novelty").mkdir(exist_ok=True)

            ctx = ExecutionContext(
                workspace_dir=ws,
                project_id="test-novelty",
                task_id=task_id,
                run_id="test-t6",
                outputs_expected={
                    "novelty_report": ws / "novelty" / "novelty_report.md",
                },
            )

            agent_cls = AGENT_REGISTRY.get(agent_name)
            if not agent_cls:
                return TestResult(
                    agent_name=agent_name,
                    task_id=task_id,
                    success=False,
                    duration_ms=0,
                    api_calls=0,
                    cost=0,
                    error=f"Agent '{agent_name}' not found",
                )

            agent = agent_cls()
            self.log(f"Agent: {agent.spec.name}")

            response, api_calls, cost = await self.call_agent_api(agent, ctx)

            duration = (time.time() - start) * 1000

            self.print(f"✓ {agent_name} 测试完成!")
            self.print(f"  耗时: {duration:.0f}ms")
            self.print(f"  API调用: {api_calls}次")
            self.print(f"  成本: ${cost:.4f}")

            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=True,
                duration_ms=duration,
                api_calls=api_calls,
                cost=cost,
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            tb = traceback.format_exc()
            self.print(f"✗ {agent_name} 测试失败!")
            self.print(f"  错误: {e}")
            if self.verbose:
                self.print(tb)
            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=False,
                duration_ms=duration,
                api_calls=0,
                cost=0,
                error=str(e),
            )

    async def test_writer_agent(self) -> TestResult:
        """测试 Writer Agent (T8) - 论文写作"""
        agent_name = "writer"
        task_id = "T8-WRITE"
        start = time.time()

        try:
            self.print(f"\n{'='*60}")
            self.print(f"测试 {agent_name} (Task: {task_id}) - Outline Mode")
            self.print(f"{'='*60}")

            ws = self.workspace / "writer_test"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "drafts").mkdir(exist_ok=True)
            (ws / "experiments").mkdir(exist_ok=True)

            # 预先创建 results_summary.json
            results = {
                "core_metrics": {
                    "accuracy": {"value": 0.92, "target": 0.85},
                    "speed": {"value": 1.5, "target": 2.0},
                }
            }
            (ws / "experiments" / "results_summary.json").write_text(
                json.dumps(results, indent=2), encoding="utf-8"
            )

            ctx = ExecutionContext(
                workspace_dir=ws,
                project_id="test-writer",
                task_id=task_id,
                run_id="test-t8",
                mode="outline",
                inputs={
                    "results_summary": ws / "experiments" / "results_summary.json",
                },
                outputs_expected={
                    "outline": ws / "drafts" / "outline.md",
                },
            )

            agent_cls = AGENT_REGISTRY.get(agent_name)
            if not agent_cls:
                return TestResult(
                    agent_name=agent_name,
                    task_id=task_id,
                    success=False,
                    duration_ms=0,
                    api_calls=0,
                    cost=0,
                    error=f"Agent '{agent_name}' not found",
                )

            agent = agent_cls()
            self.log(f"Agent: {agent.spec.name}")

            response, api_calls, cost = await self.call_agent_api(agent, ctx)

            duration = (time.time() - start) * 1000

            self.print(f"✓ {agent_name} 测试完成!")
            self.print(f"  耗时: {duration:.0f}ms")
            self.print(f"  API调用: {api_calls}次")
            self.print(f"  成本: ${cost:.4f}")

            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=True,
                duration_ms=duration,
                api_calls=api_calls,
                cost=cost,
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            tb = traceback.format_exc()
            self.print(f"✗ {agent_name} 测试失败!")
            self.print(f"  错误: {e}")
            if self.verbose:
                self.print(tb)
            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=False,
                duration_ms=duration,
                api_calls=0,
                cost=0,
                error=str(e),
            )

    async def test_reviewer_agent(self) -> TestResult:
        """测试 Reviewer Agent (T8) - 论文审稿"""
        agent_name = "reviewer"
        task_id = "T8-REVIEW-1"
        start = time.time()

        try:
            self.print(f"\n{'='*60}")
            self.print(f"测试 {agent_name} (Task: {task_id})")
            self.print(f"{'='*60}")

            ws = self.workspace / "reviewer_test"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "drafts").mkdir(exist_ok=True)
            (ws / "drafts" / "review_rounds").mkdir(exist_ok=True)

            # 预先创建 paper.tex
            paper_tex = r"""\documentclass{article}
\title{Test Paper}
\begin{document}
\maketitle
\section{Introduction}
This is a test paper.
\section{Method}
Our method achieves 0.92 accuracy.
\section{Conclusion}
We propose a new approach.
\end{document}
"""
            (ws / "drafts" / "paper.tex").write_text(paper_tex, encoding="utf-8")

            ctx = ExecutionContext(
                workspace_dir=ws,
                project_id="test-reviewer",
                task_id=task_id,
                run_id="test-t8-review",
                extra={"round": 1},
                inputs={
                    "paper": ws / "drafts" / "paper.tex",
                },
                outputs_expected={
                    "review_report": ws / "drafts" / "review_rounds" / "round_1.md",
                },
            )

            agent_cls = AGENT_REGISTRY.get(agent_name)
            if not agent_cls:
                return TestResult(
                    agent_name=agent_name,
                    task_id=task_id,
                    success=False,
                    duration_ms=0,
                    api_calls=0,
                    cost=0,
                    error=f"Agent '{agent_name}' not found",
                )

            agent = agent_cls()
            self.log(f"Agent: {agent.spec.name}")

            response, api_calls, cost = await self.call_agent_api(agent, ctx)

            duration = (time.time() - start) * 1000

            self.print(f"✓ {agent_name} 测试完成!")
            self.print(f"  耗时: {duration:.0f}ms")
            self.print(f"  API调用: {api_calls}次")
            self.print(f"  成本: ${cost:.4f}")

            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=True,
                duration_ms=duration,
                api_calls=api_calls,
                cost=cost,
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            tb = traceback.format_exc()
            self.print(f"✗ {agent_name} 测试失败!")
            self.print(f"  错误: {e}")
            if self.verbose:
                self.print(tb)
            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=False,
                duration_ms=duration,
                api_calls=0,
                cost=0,
                error=str(e),
            )

    async def test_submission_agent(self) -> TestResult:
        """测试 Submission Agent (T9) - 投稿准备"""
        agent_name = "submission"
        task_id = "T9"
        start = time.time()

        try:
            self.print(f"\n{'='*60}")
            self.print(f"测试 {agent_name} (Task: {task_id})")
            self.print(f"{'='*60}")

            ws = self.workspace / "submission_test"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "drafts").mkdir(exist_ok=True)

            # 预先创建 project.yaml（Submission Agent 需要 project.name）
            project_data = {
                "project_id": "test-submission",
                "name": "Efficient Hybrid Attention Research",
                "research_direction": "Efficient Transformers for long sequences",
                "target_venue": "neurips2026",
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data), encoding="utf-8")

            # 预先创建 paper.tex
            paper_tex = r"""\documentclass{article}
\title{Efficient Hybrid Attention for Long Sequences}
\begin{document}
\maketitle
\section{Introduction}
This paper proposes a new approach.
\section{Method}
Our method achieves state-of-the-art results.
\section{Conclusion}
We present a novel method.
\end{document}
"""
            (ws / "drafts" / "paper.tex").write_text(paper_tex, encoding="utf-8")

            ctx = ExecutionContext(
                workspace_dir=ws,
                project_id="test-submission",
                task_id=task_id,
                run_id="test-t9",
                inputs={
                    "paper": ws / "drafts" / "paper.tex",
                },
                outputs_expected={
                    "bundle_dir": ws / "submission" / "bundle",
                },
            )

            agent_cls = AGENT_REGISTRY.get(agent_name)
            if not agent_cls:
                return TestResult(
                    agent_name=agent_name,
                    task_id=task_id,
                    success=False,
                    duration_ms=0,
                    api_calls=0,
                    cost=0,
                    error=f"Agent '{agent_name}' not found",
                )

            agent = agent_cls()
            self.log(f"Agent: {agent.spec.name}")

            response, api_calls, cost = await self.call_agent_api(agent, ctx)

            duration = (time.time() - start) * 1000

            self.print(f"✓ {agent_name} 测试完成!")
            self.print(f"  耗时: {duration:.0f}ms")
            self.print(f"  API调用: {api_calls}次")
            self.print(f"  成本: ${cost:.4f}")

            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=True,
                duration_ms=duration,
                api_calls=api_calls,
                cost=cost,
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            tb = traceback.format_exc()
            self.print(f"✗ {agent_name} 测试失败!")
            self.print(f"  错误: {e}")
            if self.verbose:
                self.print(tb)
            return TestResult(
                agent_name=agent_name,
                task_id=task_id,
                success=False,
                duration_ms=duration,
                api_calls=0,
                cost=0,
                error=str(e),
            )

    async def run_all_tests(self) -> list[TestResult]:
        """运行所有测试"""
        tests = [
            ("hello", self.test_hello_agent),
            ("pi", self.test_pi_agent_init),
            ("scout", self.test_scout_agent),
            ("reader", self.test_reader_agent),
            ("ideation", self.test_ideation_agent),
            ("novelty_auditor", self.test_novelty_auditor_agent),
            ("novelty", self.test_novelty_agent),
            ("writer", self.test_writer_agent),
            ("reviewer", self.test_reviewer_agent),
            ("submission", self.test_submission_agent),
        ]

        self.print("\n" + "=" * 70)
        self.print("ResearchOS Agent 真实 API 测试")
        self.print("=" * 70)
        self.print(f"Workspace: {self.workspace}")
        self.print(f"Config: {self.routing_config}")
        self.print(f"时间: {datetime.now().isoformat()}")
        self.print("=" * 70)

        for agent_name, test_func in tests:
            try:
                result = await test_func()
                self.results.append(result)
            except Exception as e:
                self.print(f"\n✗ {agent_name} 测试执行失败: {e}")
                self.results.append(TestResult(
                    agent_name=agent_name,
                    task_id="UNKNOWN",
                    success=False,
                    duration_ms=0,
                    api_calls=0,
                    cost=0,
                    error=str(e),
                ))

        return self.results

    def print_summary(self):
        """打印测试摘要"""
        self.print("\n" + "=" * 70)
        self.print("测试摘要")
        self.print("=" * 70)

        total = len(self.results)
        passed = sum(1 for r in self.results if r.success)
        failed = total - passed
        total_duration = sum(r.duration_ms for r in self.results)
        total_cost = sum(r.cost for r in self.results)
        total_api_calls = sum(r.api_calls for r in self.results)

        self.print(f"总计: {total}")
        self.print(f"通过: {passed} ✓")
        self.print(f"失败: {failed} ✗")
        self.print(f"总耗时: {total_duration:.0f}ms")
        self.print(f"总 API 调用: {total_api_calls}")
        self.print(f"总成本: ${total_cost:.4f}")

        if failed > 0:
            self.print("\n失败详情:")
            for r in self.results:
                if not r.success:
                    self.print(f"  - {r.agent_name} ({r.task_id}): {r.error}")

        self.print("\n详细结果:")
        self.print("-" * 70)
        for r in self.results:
            status = "✓" if r.success else "✗"
            self.print(f"{status} {r.agent_name:20s} | {r.task_id:15s} | "
                      f"{r.duration_ms:8.0f}ms | {r.api_calls:3d} calls | ${r.cost:.4f}")

        return failed == 0


async def main():
    parser = argparse.ArgumentParser(description="ResearchOS Agent 真实 API 测试")
    parser.add_argument("--workspace", type=str, default="/tmp/researchos_agent_tests",
                        help="测试 workspace 目录")
    parser.add_argument("--config", type=str, default="config/model_routing.yaml",
                        help="模型路由配置文件路径")
    parser.add_argument("--agent", type=str, default=None,
                        help="只测试指定的 agent")
    parser.add_argument("--verbose", action="store_true",
                        help="显示详细日志")

    args = parser.parse_args()

    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    config_path = Path(args.config)
    if not config_path.is_absolute():
        # 相对于当前目录
        script_dir = Path(__file__).parent.parent
        config_path = script_dir / config_path

    tester = AgentRealAPITester(workspace, config_path, verbose=args.verbose)

    if args.agent:
        # 运行单个测试
        agent_name = args.agent.lower()
        test_map = {
            "hello": tester.test_hello_agent,
            "pi": tester.test_pi_agent_init,
            "scout": tester.test_scout_agent,
            "reader": tester.test_reader_agent,
            "ideation": tester.test_ideation_agent,
            "novelty_auditor": tester.test_novelty_auditor_agent,
            "novelty": tester.test_novelty_agent,
            "writer": tester.test_writer_agent,
            "reviewer": tester.test_reviewer_agent,
            "submission": tester.test_submission_agent,
        }

        if agent_name in test_map:
            result = await test_map[agent_name]()
            tester.results.append(result)
            success = result.success
        else:
            print(f"未知 agent: {args.agent}")
            print(f"可用 agents: {', '.join(test_map.keys())}")
            success = False
    else:
        # 运行所有测试
        await tester.run_all_tests()
        success = tester.print_summary()

    # 保存测试结果
    results_file = workspace / "test_results.json"
    results_data = [
        {
            "agent_name": r.agent_name,
            "task_id": r.task_id,
            "success": r.success,
            "duration_ms": r.duration_ms,
            "api_calls": r.api_calls,
            "cost": r.cost,
            "error": r.error,
        }
        for r in tester.results
    ]
    results_file.write_text(json.dumps(results_data, indent=2), encoding="utf-8")
    print(f"\n测试结果已保存到: {results_file}")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
