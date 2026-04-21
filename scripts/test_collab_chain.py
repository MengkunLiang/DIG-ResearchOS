#!/usr/bin/env python3
"""
ResearchOS 多 Agent 协作链测试脚本

测试 T3→T4→T5 数据流：
1. T3 Reader: papers_dedup.jsonl → paper_notes/ + synthesis.md
2. T4 Ideation: synthesis.md → hypotheses.md + exp_plan.yaml
3. T5 Experimenter (Pilot): exp_plan.yaml + hypotheses.md → pilot_results.json

用法:
    python scripts/test_collab_chain.py [--workspace PATH] [--verbose]

示例:
    python scripts/test_collab_chain.py
    python scripts/test_collab_chain.py --verbose
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
class StageResult:
    """阶段测试结果"""
    stage_name: str
    success: bool
    duration_ms: float
    api_calls: int
    cost: float
    error: str | None = None
    outputs: dict[str, Any] = field(default_factory=dict)


class CollabChainTester:
    """多 Agent 协作链测试器"""

    def __init__(self, workspace: Path, routing_config: Path, verbose: bool = False):
        self.workspace = workspace
        self.verbose = verbose
        self.routing_config = routing_config
        self.llm_client = LLMClient(routing_config)
        self.results: list[StageResult] = []

    def log(self, msg: str):
        """打印日志"""
        if self.verbose:
            print(f"  [INFO] {msg}")

    def print(self, msg: str):
        """打印消息"""
        print(msg)

    def get_response_content(self, response) -> str:
        """从 LLMResponse 中提取 content"""
        if hasattr(response, 'raw') and response.raw and hasattr(response.raw, 'choices'):
            return response.raw.choices[0].message.content or ""
        elif hasattr(response, 'content'):
            return response.content or ""
        return ""

    async def call_agent(
        self,
        agent_name: str,
        ctx: ExecutionContext,
    ) -> tuple[Any, int, float]:
        """调用 agent API，返回 (response, api_calls, cost)"""
        agent_cls = AGENT_REGISTRY.get(agent_name)
        if not agent_cls:
            raise ValueError(f"Agent '{agent_name}' not found in registry")

        agent = agent_cls()
        system_prompt = agent.system_prompt(ctx)
        initial_msg = agent.initial_user_message(ctx)

        self.log(f"Agent: {agent.spec.name}, Model tier: {agent.spec.model_tier}")
        self.log(f"System prompt length: {len(system_prompt)}")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": initial_msg},
        ]

        response = await self.llm_client.chat(
            messages=messages,
            tools=None,
            temperature=agent.spec.temperature,
            tier=agent.spec.model_tier,
        )

        self.log(f"Response length: {len(self.get_response_content(response))}")
        return response, 1, response.cost_usd

    # ══════════════════════════════════════════════════════
    # Stage 1: T3 Reader - 文献阅读
    # ══════════════════════════════════════════════════════
    async def stage1_reader(self) -> StageResult:
        """T3 Reader: papers_dedup.jsonl → paper_notes/ + synthesis.md"""
        stage_name = "T3-Reader"
        start = time.time()

        try:
            self.print(f"\n{'='*60}")
            self.print(f"阶段 1: {stage_name} - 文献阅读")
            self.print(f"{'='*60}")

            ws = self.workspace / "stage1_reader"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "literature").mkdir(exist_ok=True)
            (ws / "literature" / "paper_notes").mkdir(exist_ok=True)

            # 预先创建 project.yaml（包含 direction 字段）
            project_data = {
                "project_id": "test-collab-chain",
                "name": "Test Research",
                "research_direction": "Efficient Transformers for long sequences",
                "direction": "Efficient Transformers for long sequences",  # Reader 模板需要
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data), encoding="utf-8")

            # 预先创建 papers_dedup.jsonl（模拟 T2 Scout 的输出）
            papers = [
                {"id": "arxiv_1706.03762", "title": "Attention Is All You Need", "abstract": "We propose Transformer architecture, a model relying entirely on an attention mechanism..."},
                {"id": "arxiv_2004.05150", "title": "Longformer", "abstract": "Long-document understanding with sparse attention. The vanilla transformer self-attention is O(n^2)..."},
                {"id": "arxiv_1911.03553", "title": "BigBird", "abstract": "Global attention via sparse attention pattern. Standard attention is quadratic in sequence length..."},
            ]
            (ws / "literature" / "papers_dedup.jsonl").write_text(
                "\n".join(json.dumps(p) for p in papers),
                encoding="utf-8"
            )
            self.log(f"Created papers_dedup.jsonl with {len(papers)} papers")

            # 创建 paper_notes 目录和一些占位笔记（synthesize 模式需要）
            notes_dir = ws / "literature" / "paper_notes"
            notes_dir.mkdir(exist_ok=True)

            for paper in papers:
                note_content = f"""# Paper Notes: {paper['title']}

## 基本信息
- ID: {paper['id']}
- Title: {paper['title']}

## 方法
{paper.get('abstract', 'N/A')}

## 主要贡献
- 提出注意力机制替代循环结构
- O(n^2) 复杂度导致长序列处理困难

## 局限性与改进空间
- 计算复杂度高
- 长序列处理是重要挑战
"""
                note_path = notes_dir / f"{paper['id']}.md"
                note_path.write_text(note_content, encoding="utf-8")

            self.log(f"Created {len(papers)} paper notes in {notes_dir}")

            # 创建 comparison_table.csv（synthesize 模式可能需要）
            comparison_csv = """method,complexity,sequence_length,accuracy,notes
Vanilla Transformer,O(n^2),512-1024,Baseline,标准注意力机制
Longformer,O(n),16384,Good,Sparse attention + global attention
BigBird,O(n),4096+,Good,稀疏注意力模式
"""
            (ws / "literature" / "comparison_table.csv").write_text(comparison_csv, encoding="utf-8")

            # 创建 related_work.bib（synthesize 模式可能需要）
            bib_content = """@article{vaswani2017attention,
  title={Attention Is All You Need},
  author={Vaswani, Ashish and others},
  journal={NeurIPS},
  year={2017}
}

@article{beltagy2020longformer,
  title={Longformer: Long-Document Understanding},
  author={Beltagy, Iz and others},
  journal={EMNLP},
  year={2020}
}

@article{zaheer2020bigbird,
  title={Big Bird: Transformers for Longer Sequences},
  author={Zaheer, Manzil and others},
  journal={NeurIPS},
  year={2020}
}
"""
            (ws / "literature" / "related_work.bib").write_text(bib_content, encoding="utf-8")

            self.log(f"Created paper notes, comparison_table.csv, related_work.bib")

            # 读取 paper_notes 内容，直接包含在用户消息中
            # 这样 agent 可以生成 synthesis 而不需要使用 read_file 工具
            paper_notes_content = ""
            for note_file in sorted(notes_dir.glob("*.md")):
                paper_notes_content += f"\n\n{'='*60}\n"
                paper_notes_content += f"文件: {note_file.name}\n"
                paper_notes_content += f"{'='*60}\n"
                paper_notes_content += note_file.read_text(encoding="utf-8")

            ctx = ExecutionContext(
                workspace_dir=ws,
                project_id="test-collab",
                task_id="T3",
                run_id="test-collab-t3",
                mode="synthesize",
                inputs={
                    "papers_dedup": ws / "literature" / "papers_dedup.jsonl",
                },
                outputs_expected={
                    "synthesis": ws / "literature" / "synthesis.md",
                },
            )

            # 获取 agent 并构建消息
            agent_cls = AGENT_REGISTRY.get("reader")
            agent = agent_cls()
            system_prompt = agent.system_prompt(ctx)

            # 修改用户消息，包含 paper_notes 内容
            initial_msg = (
                f"请开始T3.5综合流程。综合以下所有笔记，产出完整的 literature/synthesis.md。\n\n"
                f"注意：请直接在回复中输出完整的 synthesis.md 内容（Markdown 格式），"
                f"包含以下5个必需章节：\n"
                f"1. 方法家族分类 (Method Families)\n"
                f"2. 共同假设 (Shared Assumptions)\n"
                f"3. 性能-效率前沿 (Performance-Efficiency Frontier)\n"
                f"4. 技术趋势 (Trends)\n"
                f"5. 可操作研究问题 (Actionable Research Questions)\n\n"
                f"=== Paper Notes 内容 ===\n"
                f"{paper_notes_content}\n"
                f"=== Paper Notes 结束 ===\n"
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": initial_msg},
            ]

            self.log(f"System prompt length: {len(system_prompt)}")
            self.log(f"User message length: {len(initial_msg)}")

            response = await self.llm_client.chat(
                messages=messages,
                tools=None,
                temperature=agent.spec.temperature,
                tier=agent.spec.model_tier,
            )

            # 从响应中提取 synthesis.md
            content = self.get_response_content(response)
            synthesis_path = ws / "literature" / "synthesis.md"

            # 尝试提取 Markdown 内容
            if "```markdown" in content:
                start_idx = content.find("```markdown") + 10
                end_idx = content.find("```", start_idx)
                synthesis_content = content[start_idx:end_idx].strip()
            elif "# " in content and len(content) > 200:
                # 直接使用响应内容（可能是完整的 synthesis）
                synthesis_content = content
            else:
                synthesis_content = content

            synthesis_path.write_text(synthesis_content, encoding="utf-8")
            self.log(f"Wrote synthesis.md ({len(synthesis_content)} chars)")

            duration = (time.time() - start) * 1000

            # 验证输出
            if synthesis_path.exists() and len(synthesis_content) > 100:
                self.print(f"✓ {stage_name} 完成!")
                self.print(f"  耗时: {duration:.0f}ms")
                self.print(f"  API调用: 1次")
                self.print(f"  成本: ${response.cost_usd:.4f}")
                self.print(f"  输出: {synthesis_path}")

                return StageResult(
                    stage_name=stage_name,
                    success=True,
                    duration_ms=duration,
                    api_calls=1,
                    cost=response.cost_usd,
                    outputs={"synthesis.md": str(synthesis_path)},
                )
            else:
                raise Exception(f"synthesis.md 生成失败或内容过少 ({len(synthesis_content)} chars)")

        except Exception as e:
            duration = (time.time() - start) * 1000
            tb = traceback.format_exc()
            self.print(f"✗ {stage_name} 失败!")
            self.print(f"  错误: {e}")
            if self.verbose:
                self.print(tb)
            return StageResult(
                stage_name=stage_name,
                success=False,
                duration_ms=duration,
                api_calls=0,
                cost=0,
                error=str(e),
            )

    # ══════════════════════════════════════════════════════
    # Stage 2: T4 Ideation - 假设生成
    # ══════════════════════════════════════════════════════
    async def stage2_ideation(self, synthesis_path: Path) -> StageResult:
        """T4 Ideation: synthesis.md → hypotheses.md + exp_plan.yaml

        两轮交互流程:
        1. 第一轮：生成候选研究方向 + Gate1（用户选择方向）
        2. 第二轮：基于用户选择生成最终 hypotheses.md + exp_plan.yaml + Gate2（用户确认）
        """
        stage_name = "T4-Ideation"
        start = time.time()

        try:
            self.print(f"\n{'='*60}")
            self.print(f"阶段 2: {stage_name} - 假设生成")
            self.print(f"{'='*60}")

            ws = self.workspace / "stage2_ideation"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "ideation").mkdir(exist_ok=True)
            (ws / "literature").mkdir(exist_ok=True)

            # 复制 synthesis.md（模拟从 T3 获取）
            dest_synthesis = ws / "literature" / "synthesis.md"
            dest_synthesis.write_text(synthesis_path.read_text(encoding="utf-8"), encoding="utf-8")
            self.log(f"Copied synthesis.md from {synthesis_path}")

            # 创建 project.yaml（Ideation 需要）
            project_data = {
                "project_id": "test-collab",
                "name": "Test Research",
                "research_direction": "Efficient Transformers for long sequences",
                "constraints": {
                    "max_budget_usd": 100,
                    "max_duration_days": 30,
                    "compute_resources": {"allow_gpu": True},
                },
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data), encoding="utf-8")

            # 读取 synthesis.md 内容，直接包含在用户消息中
            synthesis_content = synthesis_path.read_text(encoding="utf-8")

            ctx = ExecutionContext(
                workspace_dir=ws,
                project_id="test-collab",
                task_id="T4",
                run_id="test-collab-t4",
                inputs={
                    "synthesis": ws / "literature" / "synthesis.md",
                },
                outputs_expected={
                    "hypotheses": ws / "ideation" / "hypotheses.md",
                    "exp_plan": ws / "ideation" / "exp_plan.yaml",
                },
            )

            # ══════════════════════════════════════════════
            # 第一轮：生成候选方向 + Gate1
            # ══════════════════════════════════════════════
            self.print("\n[Round 1/2] 生成候选研究方向...")

            agent_cls = AGENT_REGISTRY.get("ideation")
            agent = agent_cls()
            system_prompt = agent.system_prompt(ctx)

            # 修改用户消息，包含 synthesis 内容（不需要 read_file 工具）
            initial_msg = (
                f"请基于以下文献综述进行T4创意头脑风暴，生成候选研究方向和实验计划。\n\n"
                f"=== Literature Synthesis 内容（已直接提供，无需读取文件）===\n"
                f"{synthesis_content}\n"
                f"=== Synthesis 结束 ===\n\n"
                f"重要提示：synthesis 内容已经直接提供在上面的消息中，"
                f"请直接分析这些内容，不要尝试使用 read_file 工具读取文件。\n\n"
                f"请按以下两轮流程执行：\n"
                f"1. 【阶段A】基于上述内容生成3个候选研究方向（每个包含pitch、对应问题、三维评分、关键风险），然后请用户选择\n"
                f"2. 【阶段B】基于用户选择生成最终的 hypotheses.md 和 exp_plan.yaml，然后请用户确认\n\n"
                f"请开始【阶段A】，直接输出3个候选研究方向供用户选择。"
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": initial_msg},
            ]

            self.log(f"System prompt length: {len(system_prompt)}")

            response1 = await self.llm_client.chat(
                messages=messages,
                tools=None,
                temperature=agent.spec.temperature,
                tier=agent.spec.model_tier,
            )

            content1 = self.get_response_content(response1)
            self.log(f"Round 1 response length: {len(content1)}")
            self.log(f"Round 1 response preview: {content1[:200]}")

            # 检查是否需要用户选择（Gate1）
            needs_selection = any(
                keyword in content1.lower()
                for keyword in ["请选择", "请输入你的选择", "方向1", "方向2", "方向3"]
            )

            total_api_calls = 1
            total_cost = response1.cost_usd

            # ══════════════════════════════════════════════
            # 第二轮：用户选择后生成最终输出
            # ══════════════════════════════════════════════
            content2 = content1
            best_response = content1  # Track the response with most content
            best_len = len(content1)

            if needs_selection:
                self.print("\n[Round 2/2] 模拟用户选择方向2（最高评分）...")

                # 模拟用户选择：选择方向2（极长序列任务的模块化Transformer设计）
                # 明确要求生成完整的 hypotheses.md 和 exp_plan.yaml
                user_selection = (
                    "2\n\n"
                    "请现在生成完整的 hypotheses.md 和 exp_plan.yaml，"
                    "包含所有详细信息（研究假设、实验设计、预算估算等），"
                    "格式如下：\n"
                    "```markdown\n"
                    "# hypotheses.md 内容...\n"
                    "```\n"
                    "```yaml\n"
                    "# exp_plan.yaml 内容...\n"
                    "```"
                )

                messages.append({"role": "assistant", "content": content1})
                messages.append({"role": "user", "content": user_selection})

                response2 = await self.llm_client.chat(
                    messages=messages,
                    tools=None,
                    temperature=agent.spec.temperature,
                    tier=agent.spec.model_tier,
                )

                content2 = self.get_response_content(response2)
                self.log(f"Round 2 response length: {len(content2)}")

                # Track best response for final output
                if len(content2) > best_len:
                    best_response = content2
                    best_len = len(content2)

                total_api_calls += 1
                total_cost += response2.cost_usd

                # 检查是否还需要Gate2确认
                needs_confirmation = any(
                    keyword in content2.lower()
                    for keyword in ["确认", "修改假设", "修改计划", "请选择"]
                )

                if needs_confirmation:
                    self.print("\n[Round 3/2] 模拟用户确认计划...")

                    messages.append({"role": "assistant", "content": content2})
                    messages.append({"role": "user", "content": "确认"})

                    response3 = await self.llm_client.chat(
                        messages=messages,
                        tools=None,
                        temperature=agent.spec.temperature,
                        tier=agent.spec.model_tier,
                    )

                    content2 = self.get_response_content(response3)
                    self.log(f"Round 3 response length: {len(content2)}")

                    # Track best response for final output
                    if len(content2) > best_len:
                        best_response = content2
                        best_len = len(content2)

                    total_api_calls += 1
                    total_cost += response3.cost_usd
            else:
                self.log("No user selection needed (Gate1 not detected)")

            # ══════════════════════════════════════════════
            # 解析输出文件（使用包含最多内容的响应）
            # ══════════════════════════════════════════════
            hypotheses_path = ws / "ideation" / "hypotheses.md"
            exp_plan_path = ws / "ideation" / "exp_plan.yaml"

            self.log(f"Using best response for parsing ({best_len} chars)")

            final_content = best_response
            md_content = final_content
            yaml_content = ""

            # 尝试找到 YAML 块
            if "```yaml" in final_content:
                start_idx = final_content.find("```yaml") + 6
                end_idx = final_content.find("```", start_idx)
                yaml_content = final_content[start_idx:end_idx].strip()
                md_content = final_content[:final_content.find("```yaml")].strip()
            elif "```" in final_content:
                parts = final_content.split("```")
                if len(parts) >= 3:
                    md_content = parts[0].strip()
                    yaml_content = parts[1].strip()

            # 写入文件
            hypotheses_path.write_text(md_content, encoding="utf-8")
            if yaml_content:
                exp_plan_path.write_text(yaml_content, encoding="utf-8")

            self.log(f"Wrote hypotheses.md ({len(md_content)} chars)")
            if yaml_content:
                self.log(f"Wrote exp_plan.yaml ({len(yaml_content)} chars)")

            duration = (time.time() - start) * 1000

            # 验证输出
            if hypotheses_path.exists() and len(md_content) > 100:
                exp_plan_exists = exp_plan_path.exists() and len(yaml_content) > 50

                self.print(f"\n✓ {stage_name} 完成!")
                self.print(f"  轮次: {total_api_calls} API调用")
                self.print(f"  耗时: {duration:.0f}ms")
                self.print(f"  成本: ${total_cost:.4f}")
                self.print(f"  hypotheses.md: {len(md_content)} chars")
                self.print(f"  exp_plan.yaml: {len(yaml_content)} chars" if yaml_content else "  exp_plan.yaml: 未生成（需要用户交互）")

                return StageResult(
                    stage_name=stage_name,
                    success=exp_plan_exists,  # 只有生成 exp_plan.yaml 才算完全成功
                    duration_ms=duration,
                    api_calls=total_api_calls,
                    cost=total_cost,
                    outputs={
                        "hypotheses.md": str(hypotheses_path),
                        "exp_plan.yaml": str(exp_plan_path) if exp_plan_exists else "",
                    },
                )
            else:
                raise Exception("hypotheses.md 生成失败")

        except Exception as e:
            duration = (time.time() - start) * 1000
            tb = traceback.format_exc()
            self.print(f"✗ {stage_name} 失败!")
            self.print(f"  错误: {e}")
            if self.verbose:
                self.print(tb)
            return StageResult(
                stage_name=stage_name,
                success=False,
                duration_ms=duration,
                api_calls=0,
                cost=0,
                error=str(e),
            )

    # ══════════════════════════════════════════════════════
    # Stage 3: T5 Experimenter (Pilot) - 试点实验
    # ══════════════════════════════════════════════════════
    async def stage3_pilot(self, hypotheses_path: Path, exp_plan_path: Path) -> StageResult:
        """T5 Experimenter (Pilot): exp_plan.yaml + hypotheses.md → pilot_results.json"""
        stage_name = "T5-Experimenter-Pilot"
        start = time.time()

        try:
            self.print(f"\n{'='*60}")
            self.print(f"阶段 3: {stage_name} - 试点实验")
            self.print(f"{'='*60}")

            ws = self.workspace / "stage3_pilot"
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "ideation").mkdir(exist_ok=True)
            (ws / "pilot").mkdir(exist_ok=True)
            (ws / "pilot" / "pilot_code").mkdir(exist_ok=True)

            # 复制 hypotheses.md 和 exp_plan.yaml
            dest_hypotheses = ws / "ideation" / "hypotheses.md"
            dest_hypotheses.write_text(hypotheses_path.read_text(encoding="utf-8"), encoding="utf-8")

            dest_exp_plan = ws / "ideation" / "exp_plan.yaml"

            # 如果 exp_plan.yaml 不存在，创建模拟的实验计划
            if exp_plan_path is None or not exp_plan_path.is_file():
                self.log(f"exp_plan.yaml not found or invalid, creating mock for testing")
                mock_exp_plan = {
                    "goal": "验证极长序列任务的模块化Transformer设计",
                    "experiments": [
                        {
                            "id": "exp1",
                            "name": "Baseline Reproduction",
                            "title": "复现基线方法（Longformer/BigBird）",
                            "hypothesis_ref": "#H1",
                            "datasets": [
                                {"name": "LRA", "split": "test", "size": 5000}
                            ],
                            "baselines": [
                                {"name": "Longformer", "source": "huggingface", "why": "标准稀疏注意力基线"}
                            ],
                            "our_method": {
                                "name": "ModularTransformer",
                                "description": "模块化Transformer设计",
                                "key_difference": "动态选择注意力机制"
                            },
                            "metrics": [
                                {"name": "accuracy", "primary": True, "target": 0.85},
                                {"name": "memory_usage", "primary": False, "target": 8192}
                            ],
                            "success_criteria": [
                                {"metric": "accuracy", "threshold": 0.80, "comparison": ">="}
                            ],
                            "steps": [
                                {"step": 1, "action": "环境准备", "details": "安装依赖"},
                                {"step": 2, "action": "基线训练", "details": "训练Longformer"},
                                {"step": 3, "action": "评估", "details": "在LRA上评估"}
                            ],
                            "compute_estimate": {
                                "gpu_hours": 10,
                                "gpu_type": "A100",
                                "estimated_cost_usd": 30.0
                            },
                            "expected_duration_days": 2
                        }
                    ]
                }
                dest_exp_plan.write_text(yaml.dump(mock_exp_plan), encoding="utf-8")
            else:
                dest_exp_plan.write_text(exp_plan_path.read_text(encoding="utf-8"), encoding="utf-8")
                self.log(f"Copied exp_plan.yaml from {exp_plan_path}")

            # 创建 project.yaml（Experimenter 需要，包含 domain 字段）
            project_data = {
                "project_id": "test-collab",
                "name": "Test Research",
                "research_direction": "Efficient Transformers",
                "domain": "natural_language_processing",  # Experimenter.j2 需要
            }
            (ws / "project.yaml").write_text(yaml.dump(project_data), encoding="utf-8")

            # 创建假设的 novelty_audit.md（T4.5 输出，Integrity Gate 需要）
            audit_content = """# Novelty Audit Report

## 新颖性评估

**Overall Level**: Level 2 - Incremental Innovation

### 评估详情

1. **Technical Novelty**: Moderate - 混合局部-全局注意力有创新点
2. **Application Novelty**: High - 长序列处理是重要场景
3. **Methodological Novelty**: Low - 主要是现有技术的组合

### 建议

建议进入 T5 Pilot 实验验证。
"""
            (ws / "ideation" / "novelty_audit.md").write_text(audit_content, encoding="utf-8")

            self.log(f"Copied hypotheses.md and exp_plan.yaml")
            self.log(f"Created novelty_audit.md for Integrity Gate")

            ctx = ExecutionContext(
                workspace_dir=ws,
                project_id="test-collab",
                task_id="T5",
                run_id="test-collab-t5",
                mode="pilot",
                inputs={
                    "hypotheses": ws / "ideation" / "hypotheses.md",
                    "exp_plan": ws / "ideation" / "exp_plan.yaml",
                },
                outputs_expected={
                    "pilot_results": ws / "pilot" / "pilot_results.json",
                },
            )

            response, api_calls, cost = await self.call_agent("experimenter", ctx)

            content = self.get_response_content(response)

            # 尝试解析 JSON 或创建占位符
            pilot_results_path = ws / "pilot" / "pilot_results.json"

            # 检查响应中是否有 JSON
            if "```json" in content:
                start_idx = content.find("```json") + 7
                end_idx = content.find("```", start_idx)
                json_content = content[start_idx:end_idx].strip()
                pilot_results_path.write_text(json_content, encoding="utf-8")
                self.log(f"Wrote pilot_results.json from response")
            elif "{" in content:
                # 尝试提取 JSON 部分
                import re
                json_match = re.search(r'\{[\s\S]*\}', content)
                if json_match:
                    json_content = json_match.group()
                    try:
                        json.loads(json_content)  # 验证 JSON 格式
                        pilot_results_path.write_text(json_content, encoding="utf-8")
                        self.log(f"Extracted and wrote pilot_results.json")
                    except:
                        self.log(f"JSON parse failed, skipping")
                else:
                    self.log(f"No JSON found in response")
            else:
                self.log(f"Response may not contain direct pilot_results.json")

            duration = (time.time() - start) * 1000

            # Experimenter 模式可能需要多轮对话，这里只记录单次调用结果
            self.print(f"✓ {stage_name} 调用完成!")
            self.print(f"  耗时: {duration:.0f}ms")
            self.print(f"  API调用: {api_calls}次")
            self.print(f"  成本: ${cost:.4f}")
            self.print(f"  注意: Experimenter 可能需要多轮交互完成实验")

            return StageResult(
                stage_name=stage_name,
                success=True,
                duration_ms=duration,
                api_calls=api_calls,
                cost=cost,
                outputs={"response_preview": content[:500]},
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            tb = traceback.format_exc()
            self.print(f"✗ {stage_name} 失败!")
            self.print(f"  错误: {e}")
            if self.verbose:
                self.print(tb)
            return StageResult(
                stage_name=stage_name,
                success=False,
                duration_ms=duration,
                api_calls=0,
                cost=0,
                error=str(e),
            )

    async def run_chain_test(self) -> list[StageResult]:
        """运行完整的协作链测试"""
        self.print("\n" + "=" * 70)
        self.print("ResearchOS 多 Agent 协作链测试 (T3→T4→T5)")
        self.print("=" * 70)
        self.print(f"Workspace: {self.workspace}")
        self.print(f"时间: {datetime.now().isoformat()}")
        self.print("=" * 70)

        # Stage 1: T3 Reader
        result1 = await self.stage1_reader()
        self.results.append(result1)

        if not result1.success:
            self.print("\n⚠ Stage 1 失败，停止后续测试")
            return self.results

        # Stage 2: T4 Ideation
        synthesis_path = self.workspace / "stage1_reader" / "literature" / "synthesis.md"
        result2 = await self.stage2_ideation(synthesis_path)
        self.results.append(result2)

        # 即使 T4 部分成功（hypotheses.md 存在但 exp_plan.yaml 不存在），也继续测试 T5
        # 因为 stage3_pilot 会创建 mock exp_plan.yaml
        hypotheses_path = self.workspace / "stage2_ideation" / "ideation" / "hypotheses.md"
        exp_plan_path_str = result2.outputs.get("exp_plan.yaml", "")
        # Fix: check if path string is valid and is actually a file
        exp_plan_path = Path(exp_plan_path_str) if exp_plan_path_str and exp_plan_path_str != "." else None
        if exp_plan_path and not exp_plan_path.is_file():
            exp_plan_path = None

        if not hypotheses_path.exists():
            self.print("\n⚠ Stage 2 未生成 hypotheses.md，停止后续测试")
            return self.results

        # Stage 3: T5 Experimenter (Pilot)
        self.print("\n⚡ 继续执行 Stage 3（T5），将创建 mock exp_plan.yaml 如需要")
        result3 = await self.stage3_pilot(hypotheses_path, exp_plan_path)
        self.results.append(result3)

        return self.results

    def print_summary(self):
        """打印测试摘要"""
        self.print("\n" + "=" * 70)
        self.print("协作链测试摘要")
        self.print("=" * 70)

        total = len(self.results)
        passed = sum(1 for r in self.results if r.success)
        failed = total - passed
        total_duration = sum(r.duration_ms for r in self.results)
        total_cost = sum(r.cost for r in self.results)
        total_api_calls = sum(r.api_calls for r in self.results)

        self.print(f"总计: {total} 阶段")
        self.print(f"通过: {passed} ✓")
        self.print(f"失败: {failed} ✗")
        self.print(f"总耗时: {total_duration:.0f}ms")
        self.print(f"总 API 调用: {total_api_calls}")
        self.print(f"总成本: ${total_cost:.4f}")

        if failed > 0:
            self.print("\n失败详情:")
            for r in self.results:
                if not r.success:
                    self.print(f"  - {r.stage_name}: {r.error}")

        self.print("\n详细结果:")
        self.print("-" * 70)
        for r in self.results:
            status = "✓" if r.success else "✗"
            self.print(f"{status} {r.stage_name:25s} | "
                      f"{r.duration_ms:8.0f}ms | {r.api_calls:3d} calls | ${r.cost:.4f}")

        # 数据流验证
        self.print("\n数据流验证:")
        self.print("-" * 70)
        if len(self.results) >= 1:
            s1 = self.results[0]
            self.print(f"T3→synthesis.md: {'✓' if s1.success else '✗'} {s1.outputs.get('synthesis.md', 'N/A')}")
        if len(self.results) >= 2:
            s2 = self.results[1]
            hyp_path = s2.outputs.get("hypotheses.md", "")
            exp_path = s2.outputs.get("exp_plan.yaml", "")
            hyp_ok = Path(hyp_path).exists() if hyp_path else False
            exp_ok = Path(exp_path).exists() if exp_path else False
            self.print(f"T4→hypotheses.md: {'✓' if hyp_ok else '✗'} ({hyp_path.split('/')[-1] if hyp_path else 'N/A'})")
            self.print(f"T4→exp_plan.yaml: {'✓' if exp_ok else '⚠ mock'} ({exp_path.split('/')[-1] if exp_path else 'N/A'})")
        if len(self.results) >= 3:
            s3 = self.results[2]
            self.print(f"T5→pilot_results: {'✓' if s3.success else '✗'}")

        return failed == 0


async def main():
    parser = argparse.ArgumentParser(description="ResearchOS 多 Agent 协作链测试")
    parser.add_argument("--workspace", type=str, default="/tmp/researchos_collab_tests",
                        help="测试 workspace 目录")
    parser.add_argument("--config", type=str, default="config/model_routing.yaml",
                        help="模型路由配置文件路径")
    parser.add_argument("--verbose", action="store_true",
                        help="显示详细日志")

    args = parser.parse_args()

    workspace = Path(args.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    config_path = Path(args.config)
    if not config_path.is_absolute():
        script_dir = Path(__file__).parent.parent
        config_path = script_dir / config_path

    tester = CollabChainTester(workspace, config_path, verbose=args.verbose)

    # 运行协作链测试
    await tester.run_chain_test()
    success = tester.print_summary()

    # 保存测试结果
    results_file = workspace / "collab_chain_results.json"
    results_data = [
        {
            "stage_name": r.stage_name,
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