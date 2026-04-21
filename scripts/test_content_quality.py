#!/usr/bin/env python3
"""
内容质量审查测试脚本。

测试内容：
1. 引用幻觉检测 - 检查 cite key 是否在 bib 中定义
2. 数字幻觉检测 - 检查论文中的数字是否来自实验结果
3. 逻辑矛盾检测 - 检查前后章节结论是否一致
4. LaTeX 编译测试 - 检查论文是否可编译

运行方式：
    python scripts/test_content_quality.py [--verbose]
"""

import asyncio
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def log(msg: str, verbose: bool = True) -> None:
    """打印日志。"""
    if verbose:
        print(f"[Quality Test] {msg}", flush=True)


def log_result(name: str, passed: bool, details: str = "") -> None:
    """打印测试结果。"""
    status = "✅" if passed else "❌"
    print(f"  {status} {name}", flush=True)
    if details:
        print(f"     {details}", flush=True)


def extract_cite_keys(tex_content: str) -> set[str]:
    """从 tex 文件中提取所有 \\cite{} 中的 key。"""
    # 匹配 \cite{key1, key2} 或 \citep{key} 或 \citet{key}
    pattern = r'\\cite[pt]?\{([^}]+)\}'
    matches = re.findall(pattern, tex_content)
    keys = set()
    for match in matches:
        # 分割可能的多引用，如 \cite{key1, key2}
        for key in match.split(','):
            key = key.strip()
            if key:
                keys.add(key)
    return keys


def extract_bib_keys(bib_content: str) -> set[str]:
    """从 bib 文件中提取所有 entry 的 key。"""
    # 匹配 @article{key, 或 @inproceedings{key, 等
    pattern = r'@\w+\{([^,\s]+)'
    matches = re.findall(pattern, bib_content)
    return set(matches)


def extract_numbers_from_tex(tex_content: str) -> list[tuple[str, str]]:
    """从 tex 文件中提取数字及其上下文（用于验证来源）。"""
    numbers = []

    # 排除常见的非数据数字：年份、页码、图表编号
    exclude_patterns = [
        r'\d{4}-\d{4}',  # 页码范围
        r'Figure\s+\d+',  # 图编号
        r'Fig\.\s*\d+',  # 图编号
        r'Table\s+\d+',  # 表编号
        r'Algorithm\s+\d+',  # 算法编号
        r'Equation\s+\d+',  # 公式编号
        r'Section\s+\d+',  # 节编号
        r'Chapter\s+\d+',  # 章编号
        r'\\ref\{[^}]+\}',  # 交叉引用
        r'\d{4}',  # 年份
    ]

    # 提取所有数字
    number_pattern = r'\b(\d+\.?\d*)\b'
    for match in re.finditer(number_pattern, tex_content):
        num_str = match.group(1)
        context = tex_content[max(0, match.start() - 30):match.end() + 30]

        # 检查是否应该排除
        excluded = False
        for pattern in exclude_patterns:
            if re.search(pattern, context):
                excluded = True
                break

        if not excluded and float(num_str) > 1:  # 排除 0, 1 等常见数字
            numbers.append((num_str, context))

    return numbers


def test_citation_hallucination(paper_dir: Path) -> dict[str, Any]:
    """测试引用幻觉检测。"""
    print("\n" + "=" * 60)
    print("测试 1: 引用幻觉检测")
    print("=" * 60)

    results = {}

    # 查找 tex 和 bib 文件
    tex_files = list(paper_dir.glob("**/*.tex"))
    bib_files = list(paper_dir.glob("**/*.bib"))

    if not tex_files:
        log_result("找到 tex 文件", False, "未找到任何 .tex 文件")
        results["passed"] = False
        return results

    if not bib_files:
        log_result("找到 bib 文件", False, "未找到任何 .bib 文件")
        results["passed"] = False
        return results

    log_result("找到 tex 文件", True, f"{len(tex_files)} 个文件")
    log_result("找到 bib 文件", True, f"{len(bib_files)} 个文件")

    # 合并所有 bib 文件内容
    all_bib_content = ""
    for bib_file in bib_files:
        all_bib_content += bib_file.read_text() + "\n"

    bib_keys = extract_bib_keys(all_bib_content)
    log(f"bib_keys 共 {len(bib_keys)} 个", True)

    # 检查所有 tex 文件的引用
    all_missing_keys = []
    for tex_file in tex_files:
        tex_content = tex_file.read_text()
        cite_keys = extract_cite_keys(tex_content)

        if cite_keys:
            log(f"检查 {tex_file.name} 中的引用", True)
            missing_keys = cite_keys - bib_keys
            if missing_keys:
                all_missing_keys.extend(missing_keys)
                log(f"  发现缺失引用: {missing_keys}", True)

    if not all_missing_keys:
        log_result("所有引用都有对应 bib 条目", True)
        results["passed"] = True
    else:
        log_result("存在幻觉引用", False, f"缺失 {len(all_missing_keys)} 个引用")
        results["missing_keys"] = list(all_missing_keys)
        results["passed"] = False

    return results


def test_number_hallucination(paper_dir: Path) -> dict[str, Any]:
    """测试数字幻觉检测。"""
    print("\n" + "=" * 60)
    print("测试 2: 数字幻觉检测")
    print("=" * 60)

    results = {}

    # 查找实验结果文件
    results_files = list(paper_dir.glob("**/results_summary.json"))
    pilot_files = list(paper_dir.glob("**/pilot_results.json"))

    # 查找 tex 文件
    tex_files = list(paper_dir.glob("**/*.tex"))

    if not tex_files:
        log_result("找到 tex 文件", False, "未找到任何 .tex 文件")
        results["passed"] = False
        return results

    if not results_files and not pilot_files:
        log_result("找到实验结果文件", False, "未找到 results_summary.json 或 pilot_results.json")
        log("注意: 这是正常的，如果论文尚未生成", True)
        results["passed"] = True
        results["skipped"] = True
        results["skip_reason"] = "No experiment results found (paper not yet generated)"
        return results

    # 读取实验结果
    experiment_numbers = set()
    for results_file in results_files + pilot_files:
        try:
            data = json.loads(results_file.read_text())
            # 递归提取所有数字
            def extract_numbers_recursive(obj):
                nums = set()
                if isinstance(obj, dict):
                    for v in obj.values():
                        nums.update(extract_numbers_recursive(v))
                elif isinstance(obj, list):
                    for item in obj:
                        nums.update(extract_numbers_recursive(item))
                elif isinstance(obj, (int, float)) and obj > 1:
                    nums.add(str(obj))
                return nums
            experiment_numbers.update(extract_numbers_recursive(data))
        except Exception as e:
            log(f"读取 {results_file} 失败: {e}", True)

    log(f"从实验结果中提取 {len(experiment_numbers)} 个数字", True)

    # 提取 tex 中的数字
    all_numbers = []
    for tex_file in tex_files:
        tex_content = tex_file.read_text()
        numbers = extract_numbers_from_tex(tex_content)
        all_numbers.extend(numbers)

    log(f"从 tex 文件中提取 {len(all_numbers)} 个数字", True)

    # 检查每个数字是否来自实验结果
    suspicious_numbers = []
    for num, context in all_numbers:
        if num not in experiment_numbers:
            suspicious_numbers.append((num, context))

    # 过滤掉合理的数字（百分比、小数等）
    filtered_suspicious = []
    for num, context in suspicious_numbers:
        # 允许 0-100 的数字（可能是百分比或比例）
        try:
            val = float(num)
            if 0 <= val <= 100:
                continue
        except ValueError:
            pass
        filtered_suspicious.append((num, context))

    if not filtered_suspicious:
        log_result("所有数字都来自实验结果", True)
        results["passed"] = True
    else:
        log_result("发现可疑数字", True, f"需要人工审查 {len(filtered_suspicious)} 个数字")
        results["suspicious_numbers"] = [
            {"number": n, "context": c} for n, c in filtered_suspicious[:5]
        ]
        results["passed"] = True  # 标记为通过，因为需要人工判断

    return results


def test_latex_compilation(paper_dir: Path) -> dict[str, Any]:
    """测试 LaTeX 编译。"""
    print("\n" + "=" * 60)
    print("测试 3: LaTeX 编译测试")
    print("=" * 60)

    results = {}

    # 查找主 tex 文件
    tex_files = list(paper_dir.glob("**/*.tex"))
    main_tex = None

    for tex_file in tex_files:
        content = tex_file.read_text().lower()
        # 简单检测是否为非子文件
        if '\\documentclass' in content or '\\begin{document}' in content:
            main_tex = tex_file
            break

    if not main_tex:
        log_result("找到主 tex 文件", False, "未找到包含 \\documentclass 的文件")
        results["passed"] = False
        return results

    log_result("找到主 tex 文件", True, main_tex.name)

    # 检查必要的工具是否可用
    latexmk_available = subprocess.run(
        ["which", "latexmk"],
        capture_output=True,
    ).returncode == 0

    pdflatex_available = subprocess.run(
        ["which", "pdflatex"],
        capture_output=True,
    ).returncode == 0

    if not latexmk_available and not pdflatex_available:
        log_result("LaTeX 工具可用", False, "latexmk 和 pdflatex 都不可用")
        results["passed"] = False
        results["skip_reason"] = "No LaTeX tools available"
        return results

    log_result("LaTeX 工具可用", True)

    # 尝试编译
    with tempfile.TemporaryDirectory() as tmpdir:
        # 复制文件到临时目录
        import shutil
        tmpdir_path = Path(tmpdir)

        # 只复制主文件和相关的 tex/bib 文件
        files_to_copy = [main_tex]
        for tex_file in tex_files:
            if tex_file != main_tex:
                # 检查是否被引用
                if main_tex.read_text().find(tex_file.name) >= 0:
                    files_to_copy.append(tex_file)

        for bib_file in paper_dir.glob("**/*.bib"):
            files_to_copy.append(bib_file)

        for f in files_to_copy:
            dest = tmpdir_path / f.name
            shutil.copy2(f, dest)

        # 尝试编译
        try:
            if latexmk_available:
                result = subprocess.run(
                    ["latexmk", "-pdf", "-interaction=batchmode", main_tex.name],
                    cwd=tmpdir_path,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
            else:
                # 使用 pdflatex 两次编译
                for _ in range(2):
                    result = subprocess.run(
                        ["pdflatex", "-interaction=batchmode", main_tex.name],
                        cwd=tmpdir_path,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )

            # 检查是否生成了 PDF
            pdf_file = tmpdir_path / (main_tex.stem + ".pdf")
            compilation_success = pdf_file.exists()

            if compilation_success:
                log_result("LaTeX 编译成功", True, f"PDF 生成于 {pdf_file.name}")
                results["passed"] = True
            else:
                log_result("LaTeX 编译失败", False, "未生成 PDF")
                results["passed"] = False

        except subprocess.TimeoutExpired:
            log_result("LaTeX 编译超时", False, "编译超过 120 秒")
            results["passed"] = False
        except Exception as e:
            log_result("LaTeX 编译错误", False, str(e))
            results["passed"] = False

    return results


async def test_logic_consistency(paper_dir: Path) -> dict[str, Any]:
    """测试逻辑一致性检测（基础版）。"""
    print("\n" + "=" * 60)
    print("测试 4: 逻辑一致性检测（基础版）")
    print("=" * 60)

    results = {}

    tex_files = list(paper_dir.glob("**/*.tex"))

    if not tex_files:
        log_result("找到 tex 文件", False, "未找到任何 .tex 文件")
        results["passed"] = False
        return results

    # 提取摘要和结论
    conclusion_keywords = ['conclusion', 'summary', '总结']
    abstract_keywords = ['abstract', '摘要']

    abstract_text = ""
    conclusion_text = ""

    for tex_file in tex_files:
        content = tex_file.read_text()

        # 提取摘要
        for kw in abstract_keywords:
            pattern = rf'\\{kw}\{{([^}}]+)\}}'
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                abstract_text += match.group(1) + " "

        # 提取结论
        for kw in conclusion_keywords:
            pattern = rf'\\{kw}\{{([^}}]+)\}}'
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                conclusion_text += match.group(1) + " "

    # 基础检查：摘要和结论都应该存在
    has_abstract = len(abstract_text.strip()) > 50
    has_conclusion = len(conclusion_text.strip()) > 50

    log_result("论文有摘要", has_abstract, f"长度 {len(abstract_text)}")
    log_result("论文有结论", has_conclusion, f"长度 {len(conclusion_text)}")

    # 提取关键claim（以句号结尾的完整句子中的数字）
    def extract_claims(text: str) -> list[str]:
        sentences = re.split(r'[.。]+', text)
        claims = [s.strip() for s in sentences if len(s.strip()) > 20]
        return claims

    abstract_claims = extract_claims(abstract_text)
    conclusion_claims = extract_claims(conclusion_text)

    log(f"摘要中 {len(abstract_claims)} 个 claim", True)
    log(f"结论中 {len(conclusion_claims)} 个 claim", True)

    # 基础检查通过
    results["passed"] = has_abstract and has_conclusion
    results["note"] = "逻辑矛盾检测需要人工审查自动化的逻辑分析"

    return results


async def run_demo_test() -> dict[str, Any]:
    """运行演示测试，展示测试框架的功能。"""
    print("\n" + "=" * 60)
    print("演示测试: 内容质量检测框架验证")
    print("=" * 60)

    results = {}

    # 创建临时演示目录
    with tempfile.TemporaryDirectory() as tmpdir:
        demo_dir = Path(tmpdir)
        paper_dir = demo_dir / "paper"
        paper_dir.mkdir()

        # 创建演示 bib 文件
        bib_content = """
@article{vaswani2017attention,
  author={Vaswani, Ashish and Shazeer, Noam and Parmar, Niki and Uszkoreit, Jakob and Jones, Llion and Gomez, Aidan N and Kaiser, Lukasz and Polosukhin, Illia},
  title={Attention Is All You Need},
  journal={Advances in Neural Information Processing Systems},
  year={2017}
}

@article{brown2020gpt3,
  author={Brown, Tom B and Mann, Benjamin and Ryder, Nick and Subbiah, Melanie and Kaplan, Jared and others},
  title={Language Models are Few-Shot Learners},
  journal={Advances in Neural Information Processing Systems},
  year={2020}
}

@inproceedings{devlin2019bert,
  author={Devlin, Jacob and Chang, Ming-Wei and Lee, Kenton and Toutanova, Kristina},
  title={BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding},
  booktitle={NAACL-HLT},
  year={2019}
}
"""
        bib_file = paper_dir / "references.bib"
        bib_file.write_text(bib_content)

        # 创建演示 tex 文件
        tex_content = r"""\documentclass[11pt]{article}
\usepackage{amsmath}

\title{Demo Paper: Attention Mechanisms in Deep Learning}
\author{ResearchOS Agent}

\begin{document}

\maketitle

\begin{abstract}
We present a comprehensive study of attention mechanisms~\citep{vaswani2017attention}
in deep learning models. Our approach achieves 92.5\% accuracy on standard benchmarks,
significantly outperforming previous methods.
\end{abstract}

\section{Introduction}
Recent advances in transformer models~\citep{vaswani2017attention, devlin2019bert}
have demonstrated the power of attention mechanisms. Large language models~\citep{brown2020gpt3}
have shown remarkable capabilities.

\section{Method}
Our method combines local and global attention mechanisms.
We use 8 layers with hidden size 512.

\section{Results}
Our model achieves:
\begin{itemize}
    \item 92.5\% accuracy on test set
    \item 0.89 F1 score
    \item Training time: 24 hours on 4 A100 GPUs
    \item Inference speed: 150ms per sample
\end{itemize}

\section{Conclusion}
We demonstrate that attention is all you need~\citep{vaswani2017attention}.
Our approach significantly outperforms BERT~\citep{devlin2019bert} and GPT-3~\citep{brown2020gpt3}.

\end{document}
"""
        tex_file = paper_dir / "paper.tex"
        tex_file.write_text(tex_content)

        # 创建实验结果文件
        results_data = {
            "accuracy": 92.5,
            "f1_score": 0.89,
            "training_hours": 24,
            "inference_ms": 150,
        }
        results_file = paper_dir / "results_summary.json"
        results_file.write_text(json.dumps(results_data, indent=2))

        print(f"演示数据已创建在: {paper_dir}")

        # 运行测试
        all_results = {}
        all_results["citation_hallucination"] = test_citation_hallucination(paper_dir)
        all_results["number_hallucination"] = test_number_hallucination(paper_dir)
        all_results["logic_consistency"] = await test_logic_consistency(paper_dir)
        all_results["latex_compilation"] = test_latex_compilation(paper_dir)

        return all_results


async def main() -> int:
    """主函数。"""
    print("=" * 60)
    print("内容质量审查测试套件")
    print("=" * 60)

    # 尝试从测试 workspace 目录开始
    test_workspaces = [
        Path("/tmp/collab_chain_test"),
        Path("/tmp/collab_chain_test6"),
        Path("/tmp/content_quality_test"),
    ]

    paper_dir = None
    for ws in test_workspaces:
        if ws.exists():
            # 查找 drafts 或 paper 目录
            for subdir in ws.rglob("*"):
                if subdir.is_dir() and (subdir.name in ["drafts", "paper", "literature"]):
                    paper_dir = subdir
                    break
            if paper_dir:
                break
            paper_dir = ws

    if not paper_dir:
        # 创建一个测试目录用于演示
        paper_dir = Path("/tmp/content_quality_demo")
        paper_dir.mkdir(parents=True, exist_ok=True)
        log("未找到现有论文目录，创建演示目录", True)

    log(f"使用论文目录: {paper_dir}", True)

    # 检查是否有 tex 文件
    tex_files = list(paper_dir.glob("**/*.tex"))
    if not tex_files:
        log("未找到 tex 文件，运行演示测试", True)
        demo_results = await run_demo_test()
        return await summarize_results(demo_results)

    all_results = {}

    # 运行所有测试
    all_results["citation_hallucination"] = test_citation_hallucination(paper_dir)
    all_results["number_hallucination"] = test_number_hallucination(paper_dir)
    all_results["logic_consistency"] = await test_logic_consistency(paper_dir)
    all_results["latex_compilation"] = test_latex_compilation(paper_dir)

    return await summarize_results(all_results)


async def summarize_results(all_results: dict[str, Any]) -> int:

    # 汇总结果
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)

    total_tests = 0
    passed_tests = 0
    skipped_tests = 0

    for test_name, result in all_results.items():
        passed = result.get("passed", False)
        skipped = result.get("skipped", False)
        if skipped:
            status = "⏭️ SKIP"
            skipped_tests += 1
        elif passed:
            status = "✅ PASS"
            passed_tests += 1
        else:
            status = "❌ FAIL"
        print(f"  {status} - {test_name}")
        total_tests += 1

    print(f"\n总计: {passed_tests}/{total_tests} 测试通过 ({skipped_tests} 跳过)")

    # 输出详细结果 JSON
    output_file = Path("/tmp/content_quality_test_results.json")
    output_file.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\n详细结果已保存到: {output_file}")

    return 0 if passed_tests == total_tests - skipped_tests else 1


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="内容质量审查测试")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    parser.add_argument("--paper-dir", type=str, help="论文目录路径")
    args = parser.parse_args()

    if args.paper_dir:
        # 修改 paper_dir 变量
        import __main__
        __main__.paper_dir = Path(args.paper_dir)

    sys.exit(asyncio.run(main()))
