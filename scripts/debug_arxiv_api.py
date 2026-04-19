"""arXiv API调试脚本 - 解决论文数据编造问题

目标：
1. 验证arXiv API是否真的返回429
2. 测试正确的API调用方式
3. 添加重试机制
4. 验证返回的数据是真实的
"""

import asyncio
import time
from datetime import datetime
import xml.etree.ElementTree as ET
from html import unescape

try:
    import httpx
except ImportError:
    print("需要安装httpx: pip install httpx")
    exit(1)


async def test_arxiv_api_basic():
    """测试基本的arXiv API调用"""
    print("=" * 60)
    print("测试1: 基本arXiv API调用")
    print("=" * 60)

    # 测试查询
    query = "agent memory retrieval"
    url = f"https://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results=5&sortBy=relevance&sortOrder=descending"

    print(f"查询: {query}")
    print(f"URL: {url}")
    print()

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(url)
            print(f"状态码: {response.status_code}")
            print(f"响应长度: {len(response.text)} 字符")

            if response.status_code == 429:
                print("❌ 返回429错误（速率限制）")
                print(f"响应内容: {response.text[:200]}")
                return False

            if response.status_code == 200:
                print("✅ 成功获取数据")
                # 解析XML
                root = ET.fromstring(response.text)
                ns = {'atom': 'http://www.w3.org/2005/Atom'}

                entries = root.findall('atom:entry', ns)
                print(f"找到 {len(entries)} 篇论文")
                print()

                for i, entry in enumerate(entries, 1):
                    title = entry.findtext('atom:title', namespaces=ns)
                    arxiv_id = entry.findtext('atom:id', namespaces=ns)
                    authors = [a.findtext('atom:name', namespaces=ns)
                              for a in entry.findall('atom:author', ns)]

                    print(f"论文 {i}:")
                    print(f"  ID: {arxiv_id}")
                    print(f"  标题: {title[:80]}...")
                    print(f"  作者: {', '.join(authors[:3])}")
                    print()

                return True

        except Exception as e:
            print(f"❌ 错误: {e}")
            return False


async def test_arxiv_api_with_delay():
    """测试带延迟的多次调用"""
    print("=" * 60)
    print("测试2: 带延迟的多次调用（避免速率限制）")
    print("=" * 60)

    queries = [
        "agent memory retrieval",
        "retrieval augmented generation",
        "vector database memory"
    ]

    results = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, query in enumerate(queries, 1):
            print(f"\n查询 {i}/{len(queries)}: {query}")

            url = f"https://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results=3&sortBy=relevance&sortOrder=descending"

            try:
                response = await client.get(url)
                print(f"  状态码: {response.status_code}")

                if response.status_code == 200:
                    root = ET.fromstring(response.text)
                    ns = {'atom': 'http://www.w3.org/2005/Atom'}
                    entries = root.findall('atom:entry', ns)
                    print(f"  ✅ 获取 {len(entries)} 篇论文")
                    results.extend(entries)
                elif response.status_code == 429:
                    print(f"  ❌ 速率限制")

                # arXiv建议每次请求间隔3秒
                if i < len(queries):
                    print(f"  等待3秒...")
                    await asyncio.sleep(3)

            except Exception as e:
                print(f"  ❌ 错误: {e}")

    print(f"\n总共获取 {len(results)} 篇论文")
    return len(results) > 0


async def test_specific_arxiv_id():
    """测试获取特定arXiv ID的论文"""
    print("=" * 60)
    print("测试3: 获取特定arXiv ID")
    print("=" * 60)

    # 测试一个真实存在的arXiv ID
    test_id = "2601.00002"
    url = f"https://export.arxiv.org/api/query?id_list={test_id}"

    print(f"查询ID: {test_id}")
    print(f"URL: {url}")
    print()

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(url)
            print(f"状态码: {response.status_code}")

            if response.status_code == 200:
                root = ET.fromstring(response.text)
                ns = {'atom': 'http://www.w3.org/2005/Atom'}

                entry = root.find('atom:entry', ns)
                if entry is not None:
                    title = entry.findtext('atom:title', namespaces=ns)
                    authors = [a.findtext('atom:name', namespaces=ns)
                              for a in entry.findall('atom:author', ns)]
                    summary = entry.findtext('atom:summary', namespaces=ns)

                    print("✅ 找到论文:")
                    print(f"  标题: {title}")
                    print(f"  作者: {', '.join(authors)}")
                    print(f"  摘要: {summary[:200]}...")
                    return True
                else:
                    print("❌ 未找到论文")
                    return False

        except Exception as e:
            print(f"❌ 错误: {e}")
            return False


async def test_arxiv_with_retry():
    """测试带重试机制的API调用"""
    print("=" * 60)
    print("测试4: 带重试机制的API调用")
    print("=" * 60)

    query = "agent memory"
    max_retries = 3

    async with httpx.AsyncClient(timeout=30.0) as client:
        for attempt in range(max_retries):
            print(f"\n尝试 {attempt + 1}/{max_retries}")

            url = f"https://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results=5"

            try:
                response = await client.get(url)
                print(f"  状态码: {response.status_code}")

                if response.status_code == 200:
                    root = ET.fromstring(response.text)
                    ns = {'atom': 'http://www.w3.org/2005/Atom'}
                    entries = root.findall('atom:entry', ns)
                    print(f"  ✅ 成功获取 {len(entries)} 篇论文")
                    return True

                elif response.status_code == 429:
                    wait_time = (attempt + 1) * 5
                    print(f"  ❌ 速率限制，等待 {wait_time} 秒后重试...")
                    await asyncio.sleep(wait_time)
                    continue

            except Exception as e:
                print(f"  ❌ 错误: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
                    continue

        print("\n❌ 所有重试都失败")
        return False


async def verify_paper_authenticity():
    """验证论文数据的真实性"""
    print("=" * 60)
    print("测试5: 验证论文数据真实性")
    print("=" * 60)

    # 获取一些论文
    query = "machine learning"
    url = f"https://export.arxiv.org/api/query?search_query=all:{query}&start=0&max_results=3"

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(url)

            if response.status_code != 200:
                print(f"❌ API调用失败: {response.status_code}")
                return False

            root = ET.fromstring(response.text)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}

            for i, entry in enumerate(root.findall('atom:entry', ns)[:3], 1):
                arxiv_id = entry.findtext('atom:id', namespaces=ns)
                title = entry.findtext('atom:title', namespaces=ns)

                # 提取arXiv ID
                id_part = arxiv_id.split('/')[-1]

                print(f"\n论文 {i}:")
                print(f"  ID: {id_part}")
                print(f"  标题: {title[:80]}...")

                # 验证：重新查询这个ID
                verify_url = f"https://export.arxiv.org/api/query?id_list={id_part}"
                verify_response = await client.get(verify_url)

                if verify_response.status_code == 200:
                    verify_root = ET.fromstring(verify_response.text)
                    verify_entry = verify_root.find('atom:entry', ns)

                    if verify_entry is not None:
                        verify_title = verify_entry.findtext('atom:title', namespaces=ns)

                        if verify_title == title:
                            print(f"  ✅ 验证通过：标题匹配")
                        else:
                            print(f"  ❌ 验证失败：标题不匹配")
                            print(f"     原标题: {title[:50]}")
                            print(f"     验证标题: {verify_title[:50]}")
                    else:
                        print(f"  ❌ 验证失败：未找到论文")

                await asyncio.sleep(3)  # 避免速率限制

            return True

        except Exception as e:
            print(f"❌ 错误: {e}")
            return False


async def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("arXiv API 调试测试")
    print("=" * 60)
    print()

    tests = [
        ("基本API调用", test_arxiv_api_basic),
        ("带延迟的多次调用", test_arxiv_api_with_delay),
        ("获取特定ID", test_specific_arxiv_id),
        ("带重试机制", test_arxiv_with_retry),
        ("验证数据真实性", verify_paper_authenticity),
    ]

    results = {}

    for name, test_func in tests:
        try:
            result = await test_func()
            results[name] = result
            print()
        except Exception as e:
            print(f"❌ 测试 '{name}' 崩溃: {e}")
            results[name] = False
            print()

    # 总结
    print("=" * 60)
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
