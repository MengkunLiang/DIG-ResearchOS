#!/usr/bin/env python
"""
T2 ScoutAgent 调试脚本 - Mock模式

用途：
    调试文献普查 Agent (ScoutAgent)，验证 search 模式。

用法：
    python scripts/debug_t2_agent.py --mock [--workspace ./workspace/debug_t2]

输出产物：
    - literature/papers_raw.jsonl: 原始检索结果
    - literature/papers_dedup.jsonl: 去重后论文
    - literature/search_log.md: 检索日志
    - literature/missing_areas.md: 文献缺口分析

前置条件：
    需要存在 project.yaml（脚本会自动创建）

示例：
    python scripts/debug_t2_agent.py --mock
    python scripts/debug_t2_agent.py --mock --workspace /tmp/t2_debug
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from researchos.agents.scout import ScoutAgent
from researchos.runtime.agent import AgentSpec, ExecutionContext
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


def build_mock_llm_for_t2() -> MockLLMClient:
    """构建T2 Scout模式的mock LLM响应"""
    return MockLLMClient(
        responses=[
            # 第1轮：读取project.yaml
            FakeRawCompletion(
                message=FakeLLMMessage(
                    content="我将开始T2文献普查流程，首先读取项目配置。",
                    tool_calls=[
                        FakeToolCall(
                            name="read_file",
                            arguments={"path": "project.yaml"},
                            id="tc_read_project",
                        )
                    ],
                ),
                prompt_tokens=200,
                completion_tokens=50,
            ),
            # 第2轮：写入papers_raw.jsonl（模拟检索结果）
            FakeRawCompletion(
                message=FakeLLMMessage(
                    content="根据研究方向，我将模拟检索结果。",
                    tool_calls=[
                        FakeToolCall(
                            name="write_file",
                            arguments={
                                "path": "literature/papers_raw.jsonl",
                                "content": """{"id": "paper-1", "source": "semantic_scholar", "title": "Discrete Diffusion Models for Language", "authors": [{"name": "Alice"}], "year": 2024, "abstract": "We propose discrete diffusion for language modeling.", "venue": "NeurIPS", "citationCount": 10, "externalIds": {"DOI": "10.1000/paper1"}, "url": "https://example.com/1"}
{"id": "paper-2", "source": "arxiv", "title": "Factorized Discrete Diffusion", "authors": [{"name": "Bob"}], "year": 2024, "abstract": "Factorized approach to discrete diffusion.", "venue": "arXiv", "citationCount": 5, "externalIds": {"ArXiv": "2401.00001"}, "url": "https://arxiv.org/abs/2401.00001"}
{"id": "paper-3", "source": "semantic_scholar", "title": "Language Models with Discrete Diffusion", "authors": [{"name": "Charlie"}], "year": 2023, "abstract": "Applying discrete diffusion to language.", "venue": "ICML", "citationCount": 20, "externalIds": {"DOI": "10.1000/paper3"}, "url": "https://example.com/3"}
{"id": "paper-4", "source": "semantic_scholar", "title": "Diffusion Language Models", "authors": [{"name": "Diana"}], "year": 2023, "abstract": "Diffusion-based language generation.", "venue": "ACL", "citationCount": 15, "externalIds": {"DOI": "10.1000/paper4"}, "url": "https://example.com/4"}
{"id": "paper-5", "source": "arxiv", "title": "Discrete Diffusion for NLP", "authors": [{"name": "Eve"}], "year": 2024, "abstract": "NLP applications of discrete diffusion.", "venue": "arXiv", "citationCount": 3, "externalIds": {"ArXiv": "2402.00001"}, "url": "https://arxiv.org/abs/2402.00001"}
{"id": "paper-6", "source": "semantic_scholar", "title": "Factorized Language Models", "authors": [{"name": "Frank"}], "year": 2023, "abstract": "Factorization in language models.", "venue": "EMNLP", "citationCount": 12, "externalIds": {"DOI": "10.1000/paper6"}, "url": "https://example.com/6"}
{"id": "paper-7", "source": "semantic_scholar", "title": "Discrete Diffusion Theory", "authors": [{"name": "Grace"}], "year": 2022, "abstract": "Theoretical foundations of discrete diffusion.", "venue": "ICLR", "citationCount": 30, "externalIds": {"DOI": "10.1000/paper7"}, "url": "https://example.com/7"}
{"id": "paper-8", "source": "arxiv", "title": "Language Generation with Diffusion", "authors": [{"name": "Henry"}], "year": 2024, "abstract": "Diffusion for language generation.", "venue": "arXiv", "citationCount": 2, "externalIds": {"ArXiv": "2403.00001"}, "url": "https://arxiv.org/abs/2403.00001"}
{"id": "paper-9", "source": "semantic_scholar", "title": "Discrete Models for Text", "authors": [{"name": "Iris"}], "year": 2023, "abstract": "Discrete modeling approaches for text.", "venue": "NAACL", "citationCount": 8, "externalIds": {"DOI": "10.1000/paper9"}, "url": "https://example.com/9"}
{"id": "paper-10", "source": "semantic_scholar", "title": "Diffusion-Based NLP", "authors": [{"name": "Jack"}], "year": 2024, "abstract": "NLP with diffusion models.", "venue": "AAAI", "citationCount": 6, "externalIds": {"DOI": "10.1000/paper10"}, "url": "https://example.com/10"}
{"id": "paper-11", "source": "arxiv", "title": "Factorized Diffusion Models", "authors": [{"name": "Kate"}], "year": 2023, "abstract": "Factorization in diffusion models.", "venue": "arXiv", "citationCount": 4, "externalIds": {"ArXiv": "2304.00001"}, "url": "https://arxiv.org/abs/2304.00001"}
{"id": "paper-12", "source": "semantic_scholar", "title": "Language Modeling Advances", "authors": [{"name": "Leo"}], "year": 2024, "abstract": "Recent advances in language modeling.", "venue": "ACL", "citationCount": 18, "externalIds": {"DOI": "10.1000/paper12"}, "url": "https://example.com/12"}
{"id": "paper-13", "source": "semantic_scholar", "title": "Discrete Diffusion Applications", "authors": [{"name": "Mia"}], "year": 2023, "abstract": "Applications of discrete diffusion.", "venue": "EMNLP", "citationCount": 11, "externalIds": {"DOI": "10.1000/paper13"}, "url": "https://example.com/13"}
{"id": "paper-14", "source": "arxiv", "title": "Diffusion for Language Understanding", "authors": [{"name": "Noah"}], "year": 2024, "abstract": "Understanding language with diffusion.", "venue": "arXiv", "citationCount": 1, "externalIds": {"ArXiv": "2405.00001"}, "url": "https://arxiv.org/abs/2405.00001"}
{"id": "paper-15", "source": "semantic_scholar", "title": "Discrete Language Models", "authors": [{"name": "Olivia"}], "year": 2023, "abstract": "Discrete approaches to language modeling.", "venue": "ICLR", "citationCount": 25, "externalIds": {"DOI": "10.1000/paper15"}, "url": "https://example.com/15"}
{"id": "paper-16", "source": "semantic_scholar", "title": "Factorized NLP Models", "authors": [{"name": "Paul"}], "year": 2024, "abstract": "Factorization in NLP.", "venue": "NeurIPS", "citationCount": 7, "externalIds": {"DOI": "10.1000/paper16"}, "url": "https://example.com/16"}
{"id": "paper-17", "source": "arxiv", "title": "Diffusion Models Survey", "authors": [{"name": "Quinn"}], "year": 2024, "abstract": "Survey of diffusion models.", "venue": "arXiv", "citationCount": 9, "externalIds": {"ArXiv": "2406.00001"}, "url": "https://arxiv.org/abs/2406.00001"}
{"id": "paper-18", "source": "semantic_scholar", "title": "Language Generation Methods", "authors": [{"name": "Rachel"}], "year": 2023, "abstract": "Methods for language generation.", "venue": "ACL", "citationCount": 14, "externalIds": {"DOI": "10.1000/paper18"}, "url": "https://example.com/18"}
{"id": "paper-19", "source": "semantic_scholar", "title": "Discrete Diffusion Techniques", "authors": [{"name": "Sam"}], "year": 2024, "abstract": "Techniques for discrete diffusion.", "venue": "ICML", "citationCount": 13, "externalIds": {"DOI": "10.1000/paper19"}, "url": "https://example.com/19"}
{"id": "paper-20", "source": "arxiv", "title": "Factorized Language Generation", "authors": [{"name": "Tina"}], "year": 2023, "abstract": "Factorized approaches to generation.", "venue": "arXiv", "citationCount": 5, "externalIds": {"ArXiv": "2307.00001"}, "url": "https://arxiv.org/abs/2307.00001"}
""",
                            },
                            id="tc_write_raw",
                        )
                    ],
                ),
                prompt_tokens=250,
                completion_tokens=100,
            ),
            # 第3轮：写入papers_dedup.jsonl（去重后，转换为string array格式）
            FakeRawCompletion(
                message=FakeLLMMessage(
                    content="现在进行去重处理，产出最终论文池。",
                    tool_calls=[
                        FakeToolCall(
                            name="write_file",
                            arguments={
                                "path": "literature/papers_dedup.jsonl",
                                "content": """{"id": "paper-1", "source": "semantic_scholar", "title": "Discrete Diffusion Models for Language", "authors": ["Alice"], "year": 2024, "abstract": "We propose discrete diffusion for language modeling.", "venue": "NeurIPS", "citation_count": 10, "url": "https://example.com/1", "relevance_score": 0.95}
{"id": "paper-2", "source": "arxiv", "title": "Factorized Discrete Diffusion", "authors": ["Bob"], "year": 2024, "abstract": "Factorized approach to discrete diffusion.", "venue": "arXiv", "citation_count": 5, "url": "https://arxiv.org/abs/2401.00001", "relevance_score": 0.92}
{"id": "paper-3", "source": "semantic_scholar", "title": "Language Models with Discrete Diffusion", "authors": ["Charlie"], "year": 2023, "abstract": "Applying discrete diffusion to language.", "venue": "ICML", "citation_count": 20, "url": "https://example.com/3", "relevance_score": 0.90}
{"id": "paper-4", "source": "semantic_scholar", "title": "Diffusion Language Models", "authors": ["Diana"], "year": 2023, "abstract": "Diffusion-based language generation.", "venue": "ACL", "citation_count": 15, "url": "https://example.com/4", "relevance_score": 0.88}
{"id": "paper-5", "source": "arxiv", "title": "Discrete Diffusion for NLP", "authors": ["Eve"], "year": 2024, "abstract": "NLP applications of discrete diffusion.", "venue": "arXiv", "citation_count": 3, "url": "https://arxiv.org/abs/2402.00001", "relevance_score": 0.87}
{"id": "paper-6", "source": "semantic_scholar", "title": "Factorized Language Models", "authors": ["Frank"], "year": 2023, "abstract": "Factorization in language models.", "venue": "EMNLP", "citation_count": 12, "url": "https://example.com/6", "relevance_score": 0.85}
{"id": "paper-7", "source": "semantic_scholar", "title": "Discrete Diffusion Theory", "authors": ["Grace"], "year": 2022, "abstract": "Theoretical foundations of discrete diffusion.", "venue": "ICLR", "citation_count": 30, "url": "https://example.com/7", "relevance_score": 0.84}
{"id": "paper-8", "source": "arxiv", "title": "Language Generation with Diffusion", "authors": ["Henry"], "year": 2024, "abstract": "Diffusion for language generation.", "venue": "arXiv", "citation_count": 2, "url": "https://arxiv.org/abs/2403.00001", "relevance_score": 0.83}
{"id": "paper-9", "source": "semantic_scholar", "title": "Discrete Models for Text", "authors": ["Iris"], "year": 2023, "abstract": "Discrete modeling approaches for text.", "venue": "NAACL", "citation_count": 8, "url": "https://example.com/9", "relevance_score": 0.82}
{"id": "paper-10", "source": "semantic_scholar", "title": "Diffusion-Based NLP", "authors": ["Jack"], "year": 2024, "abstract": "NLP with diffusion models.", "venue": "AAAI", "citation_count": 6, "url": "https://example.com/10", "relevance_score": 0.81}
{"id": "paper-11", "source": "arxiv", "title": "Factorized Diffusion Models", "authors": ["Kate"], "year": 2023, "abstract": "Factorization in diffusion models.", "venue": "arXiv", "citation_count": 4, "url": "https://arxiv.org/abs/2304.00001", "relevance_score": 0.80}
{"id": "paper-12", "source": "semantic_scholar", "title": "Language Modeling Advances", "authors": ["Leo"], "year": 2024, "abstract": "Recent advances in language modeling.", "venue": "ACL", "citation_count": 18, "url": "https://example.com/12", "relevance_score": 0.79}
{"id": "paper-13", "source": "semantic_scholar", "title": "Discrete Diffusion Applications", "authors": ["Mia"], "year": 2023, "abstract": "Applications of discrete diffusion.", "venue": "EMNLP", "citation_count": 11, "url": "https://example.com/13", "relevance_score": 0.78}
{"id": "paper-14", "source": "arxiv", "title": "Diffusion for Language Understanding", "authors": ["Noah"], "year": 2024, "abstract": "Understanding language with diffusion.", "venue": "arXiv", "citation_count": 1, "url": "https://arxiv.org/abs/2405.00001", "relevance_score": 0.77}
{"id": "paper-15", "source": "semantic_scholar", "title": "Discrete Language Models", "authors": ["Olivia"], "year": 2023, "abstract": "Discrete approaches to language modeling.", "venue": "ICLR", "citation_count": 25, "url": "https://example.com/15", "relevance_score": 0.76}
{"id": "paper-16", "source": "semantic_scholar", "title": "Factorized NLP Models", "authors": ["Paul"], "year": 2024, "abstract": "Factorization in NLP.", "venue": "NeurIPS", "citation_count": 7, "url": "https://example.com/16", "relevance_score": 0.75}
{"id": "paper-17", "source": "arxiv", "title": "Diffusion Models Survey", "authors": ["Quinn"], "year": 2024, "abstract": "Survey of diffusion models.", "venue": "arXiv", "citation_count": 9, "url": "https://arxiv.org/abs/2406.00001", "relevance_score": 0.74}
{"id": "paper-18", "source": "semantic_scholar", "title": "Language Generation Methods", "authors": ["Rachel"], "year": 2023, "abstract": "Methods for language generation.", "venue": "ACL", "citation_count": 14, "url": "https://example.com/18", "relevance_score": 0.73}
{"id": "paper-19", "source": "semantic_scholar", "title": "Discrete Diffusion Techniques", "authors": ["Sam"], "year": 2024, "abstract": "Techniques for discrete diffusion.", "venue": "ICML", "citation_count": 13, "url": "https://example.com/19", "relevance_score": 0.72}
{"id": "paper-20", "source": "arxiv", "title": "Factorized Language Generation", "authors": ["Tina"], "year": 2023, "abstract": "Factorized approaches to generation.", "venue": "arXiv", "citation_count": 5, "url": "https://arxiv.org/abs/2307.00001", "relevance_score": 0.71}
""",
                            },
                            id="tc_write_dedup",
                        )
                    ],
                ),
                prompt_tokens=300,
                completion_tokens=150,
            ),
            # 第4轮：写入search_log.md
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="write_file",
                            arguments={
                                "path": "literature/search_log.md",
                                "content": """# T2 文献检索日志

## 检索策略
- 关键词: discrete diffusion, language model, factorized
- 数据源: Semantic Scholar (主), arXiv (辅)
- 时间范围: 2022-2024

## 检索结果
- 原始论文数: 20篇
- 去重后: 20篇
- 去重率: 0%（无重复）

## 质量评估
- 高相关性(>0.9): 3篇
- 中相关性(0.8-0.9): 7篇
- 低相关性(<0.8): 10篇
""",
                            },
                            id="tc_write_log",
                        )
                    ],
                ),
                prompt_tokens=150,
                completion_tokens=50,
            ),
            # 第5轮：写入missing_areas.md
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="write_file",
                            arguments={
                                "path": "literature/missing_areas.md",
                                "content": """# 文献缺口分析

## 已覆盖领域
- Discrete diffusion基础理论
- Language modeling应用
- Factorized方法

## 潜在缺口
- 大规模实验验证较少
- 与Transformer结合的研究不足
- 效率优化方面的工作有限

## 建议补充方向
- 寻找更多实验性论文
- 关注最新的arXiv预印本
""",
                            },
                            id="tc_write_missing",
                        )
                    ],
                ),
                prompt_tokens=100,
                completion_tokens=40,
            ),
            # 完成
            FakeRawCompletion(
                message=FakeLLMMessage(
                    tool_calls=[
                        FakeToolCall(
                            name="finish_task",
                            arguments={"summary": "T2 文献普查完成，产出20篇去重论文"},
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
    (workspace / "literature").mkdir(parents=True, exist_ok=True)

    # 准备T2的前置输入：project.yaml
    project_yaml = workspace / "project.yaml"
    if not project_yaml.exists():
        project_yaml.write_text(
            """project_id: test-project
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
            encoding="utf-8",
        )

    registry = ToolRegistry()
    register_builtin_tools(registry)

    # 创建简化版ScoutAgent（移除MCP工具）
    scout_agent = ScoutAgent()
    scout_agent.spec = AgentSpec(
        name="scout",
        model_tier="medium",
        tool_names=[
            "read_file",
            "write_file",
            "search_papers",
            "fetch_paper_metadata",
            "finish_task",
        ],
        max_steps=50,
        max_tokens_total=120_000,
        max_wall_seconds=1800,
        temperature=0.5,
        allowed_read_prefixes=["", "user_seeds/"],
        allowed_write_prefixes=["literature/"],
        prompt_template="scout.j2",
    )

    llm = build_mock_llm_for_t2() if args.mock else None
    if llm is None:
        raise SystemExit("当前脚本只支持 --mock 模式")

    ctx = ExecutionContext(
        workspace_dir=workspace,
        project_id="test-project",
        task_id="T2",
        run_id="t2_debug_run",
        outputs_expected={
            "papers_raw": workspace / "literature" / "papers_raw.jsonl",
            "papers_dedup": workspace / "literature" / "papers_dedup.jsonl",
            "search_log": workspace / "literature" / "search_log.md",
            "missing_areas": workspace / "literature" / "missing_areas.md",
        },
        mode="search",
        extra={},
    )

    human = MockHumanInterface(clarifications=[])

    runner = AgentRunner(scout_agent, registry, llm, human)
    result = await runner.run(ctx)

    print("\n" + "="*60)
    print("T2 ScoutAgent 调试结果:")
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
    parser = argparse.ArgumentParser(description="调试T2 ScoutAgent")
    parser.add_argument("--mock", action="store_true", help="使用mock LLM")
    parser.add_argument("--workspace", default="./workspace/debug_t2", help="工作目录")
    args = parser.parse_args()
    configure_logging()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
