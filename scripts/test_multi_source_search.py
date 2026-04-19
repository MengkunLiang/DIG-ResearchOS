"""测试多源论文搜索工具"""

import asyncio
import sys
sys.path.insert(0, '/home/liangmengkun/ResearchOS')

from researchos.tools.multi_source_search import MultiSourceSearchTool


async def test_crossref():
    """测试Crossref API"""
    print("=" * 60)
    print("测试 Crossref API")
    print("=" * 60)

    tool = MultiSourceSearchTool(email="test@example.com")
    result = await tool.execute(
        query="agent memory retrieval",
        max_results=5,
        sources=["crossref"]
    )

    print(f"成功: {result.ok}")
    print(f"内容:\n{result.content}")

    if result.data:
        papers = result.data.get("papers", [])
        print(f"\n获取到 {len(papers)} 篇论文")
        if papers:
            print("\n第一篇论文详情:")
            paper = papers[0]
            for key, value in paper.items():
                if key != "abstract":
                    print(f"  {key}: {value}")

    return result.ok


async def test_europepmc():
    """测试Europe PMC API"""
    print("\n" + "=" * 60)
    print("测试 Europe PMC API")
    print("=" * 60)

    tool = MultiSourceSearchTool()
    result = await tool.execute(
        query="machine learning",
        max_results=5,
        sources=["europepmc"]
    )

    print(f"成功: {result.ok}")
    print(f"内容:\n{result.content}")

    if result.data:
        papers = result.data.get("papers", [])
        print(f"\n获取到 {len(papers)} 篇论文")

    return result.ok


async def test_pubmed():
    """测试PubMed API"""
    print("\n" + "=" * 60)
    print("测试 PubMed API")
    print("=" * 60)

    tool = MultiSourceSearchTool()
    result = await tool.execute(
        query="neural networks",
        max_results=5,
        sources=["pubmed"]
    )

    print(f"成功: {result.ok}")
    print(f"内容:\n{result.content}")

    if result.data:
        papers = result.data.get("papers", [])
        print(f"\n获取到 {len(papers)} 篇论文")

    return result.ok


async def test_multi_source():
    """测试多源搜索"""
    print("\n" + "=" * 60)
    print("测试 多源搜索（Crossref + Europe PMC）")
    print("=" * 60)

    tool = MultiSourceSearchTool()
    result = await tool.execute(
        query="retrieval augmented generation",
        max_results=10,
        sources=["crossref", "europepmc", "arxiv"]
    )

    print(f"成功: {result.ok}")
    print(f"内容:\n{result.content}")

    if result.data:
        papers = result.data.get("papers", [])
        source_stats = result.data.get("source_stats", {})
        print(f"\n获取到 {len(papers)} 篇论文")
        print(f"数据源统计: {source_stats}")

        # 统计各个来源的论文数量
        source_counts = {}
        for paper in papers:
            source = paper.get("source", "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1

        print("\n各来源论文数量:")
        for source, count in source_counts.items():
            print(f"  {source}: {count}")

    return result.ok


async def main():
    """运行所有测试"""
    print("\n多源论文搜索工具测试\n")

    tests = [
        ("Crossref", test_crossref),
        ("Europe PMC", test_europepmc),
        ("PubMed", test_pubmed),
        ("多源搜索", test_multi_source),
    ]

    results = {}

    for name, test_func in tests:
        try:
            result = await test_func()
            results[name] = result
        except Exception as e:
            print(f"\n❌ 测试 '{name}' 失败: {e}")
            results[name] = False

    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    for name, result in results.items():
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{status} - {name}")

    passed = sum(1 for r in results.values() if r)
    total = len(results)
    print(f"\n通过率: {passed}/{total} ({passed/total*100:.1f}%)")


if __name__ == "__main__":
    asyncio.run(main())
