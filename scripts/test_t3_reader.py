#!/usr/bin/env python3
"""T3 Reader Agent测试脚本（简化版，只处理3-5篇论文）"""

import asyncio
import json
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


def create_test_papers(workspace_dir: Path, num_papers: int = 3):
    """创建测试用的papers_dedup.jsonl（只包含少量arXiv论文）"""

    # 使用真实的arXiv论文ID
    test_papers = [
        {
            "id": "arxiv:2104.09864",
            "source": "arxiv",
            "title": "Attention is Not All You Need: Pure Attention Loses Rank Doubly Exponentially with Depth",
            "authors": ["Yihe Dong", "Jean-Baptiste Cordonnier", "Andreas Loukas"],
            "year": 2021,
            "venue": "arXiv",
            "source_type": "preprint",
            "relevance_score": 0.85,
            "why_relevant": "分析了纯注意力机制的理论局限性",
            "abstract": "Attention-based architectures have become ubiquitous in machine learning...",
            "citation_count": 89,
            "url": "https://arxiv.org/abs/2104.09864"
        },
        {
            "id": "arxiv:2112.05682",
            "source": "arxiv",
            "title": "Efficient Transformers: A Survey",
            "authors": ["Yi Tay", "Mostafa Dehghani", "Dara Bahri", "Donald Metzler"],
            "year": 2022,
            "venue": "arXiv",
            "source_type": "preprint",
            "relevance_score": 0.90,
            "why_relevant": "全面综述了高效Transformer架构",
            "abstract": "Transformer model architectures have garnered immense interest...",
            "citation_count": 456,
            "url": "https://arxiv.org/abs/2112.05682"
        },
        {
            "id": "arxiv:2006.04768",
            "source": "arxiv",
            "title": "Linformer: Self-Attention with Linear Complexity",
            "authors": ["Sinong Wang", "Belinda Z. Li", "Madian Khabsa", "Han Fang", "Hao Ma"],
            "year": 2020,
            "venue": "arXiv",
            "source_type": "preprint",
            "relevance_score": 0.88,
            "why_relevant": "提出了线性复杂度的自注意力机制",
            "abstract": "Large transformer models have shown extraordinary success...",
            "citation_count": 523,
            "url": "https://arxiv.org/abs/2006.04768"
        },
    ]

    # 只取前num_papers篇
    papers = test_papers[:num_papers]

    # 写入papers_dedup.jsonl
    lit_dir = workspace_dir / "literature"
    lit_dir.mkdir(parents=True, exist_ok=True)

    papers_file = lit_dir / "papers_dedup.jsonl"
    with open(papers_file, "w", encoding="utf-8") as f:
        for paper in papers:
            f.write(json.dumps(paper, ensure_ascii=False) + "\n")

    print(f"[TEST] Created test papers_dedup.jsonl with {len(papers)} papers")
    return papers


async def main():
    # 设置环境变量
    os.environ["UIUIAPI_API_KEY"] = "sk-o75I3UPDDeWXWmYkrLfuaUcho9qijDDO4SF2yhJYtDbX4Hef"
    os.environ["UIUIAPI_BASE_URL"] = "https://sg.uiuiapi.com/v1"

    # 配置
    workspace_dir = Path("/tmp/researchos_test_t3_20260419")
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # 复制project.yaml
    src_project = Path("/tmp/researchos_real_test_20260419_163709/project.yaml")
    dst_project = workspace_dir / "project.yaml"
    if src_project.exists():
        import shutil
        shutil.copy2(src_project, dst_project)
        print(f"[TEST] Copied project.yaml")

    # 创建测试用的papers_dedup.jsonl（只有3篇论文）
    papers = create_test_papers(workspace_dir, num_papers=3)

    print(f"[TEST] Workspace: {workspace_dir}")
    print(f"[TEST] Starting T3 (Reader Agent) with {len(papers)} papers...\n")

    # 创建组件
    registry = ToolRegistry()
    register_builtin_tools(registry)

    llm_client = LLMClient(Path(__file__).parent.parent / "config" / "model_routing.yaml")
    human = AutoHumanInterface()

    # 创建runner
    runner = SingleTaskRunner(
        task_id="T3",
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
        paper_notes_dir = workspace_dir / "literature" / "paper_notes"
        comparison_table = workspace_dir / "literature" / "comparison_table.csv"
        related_work_bib = workspace_dir / "literature" / "related_work.bib"

        if paper_notes_dir.exists():
            notes = list(paper_notes_dir.glob("*.md"))
            print(f"\n[TEST] ✅ paper_notes: {len(notes)} notes")
            for note in notes:
                print(f"  - {note.name}")
        else:
            print(f"\n[TEST] ❌ paper_notes directory NOT created")

        if comparison_table.exists():
            print(f"[TEST] ✅ comparison_table.csv created")
        else:
            print(f"[TEST] ⚠️  comparison_table.csv NOT created (optional)")

        if related_work_bib.exists():
            print(f"[TEST] ✅ related_work.bib created")
        else:
            print(f"[TEST] ⚠️  related_work.bib NOT created (optional)")

        return 0 if result.ok else 1

    except Exception as e:
        print(f"\n[TEST] ❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
