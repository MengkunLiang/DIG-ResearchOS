"""数值精度一致性验证器

验证论文中的数值与 results_summary.json 中的数值是否一致。
"""

import json
import re
from pathlib import Path
from typing import Tuple, List, Dict, Any


def validate_number_consistency(
    results_json: Path,
    tex_path: Path,
    tolerance: float = 0.01
) -> Tuple[bool, List[str]]:
    """确保论文中的数值与 results_summary.json 一致

    Args:
        results_json: 结果摘要 JSON 文件路径
        tex_path: LaTeX 文件路径
        tolerance: 允许的相对误差（默认 1%）

    Returns:
        (is_valid, errors): 验证是否通过和错误列表
    """
    errors = []

    # 读取 results_summary.json
    if not results_json.exists():
        return False, [f"结果文件不存在: {results_json}"]

    try:
        with open(results_json, "r", encoding="utf-8") as f:
            results = json.load(f)
    except Exception as e:
        return False, [f"无法读取结果文件: {e}"]

    # 读取 LaTeX 文件
    if not tex_path.exists():
        return False, [f"LaTeX 文件不存在: {tex_path}"]

    try:
        tex_content = tex_path.read_text(encoding="utf-8")
    except Exception as e:
        return False, [f"无法读取 LaTeX 文件: {e}"]

    # 提取 results 中的所有数值
    result_numbers = _extract_numbers_from_dict(results)

    # 提取 LaTeX 中的所有数值
    tex_numbers = _extract_numbers_from_tex(tex_content)

    # 检查每个 LaTeX 中的数值是否在 results 中存在
    for line_num, number, context in tex_numbers:
        # 查找最接近的 result 数值
        closest_match = None
        min_diff = float("inf")

        for key, value in result_numbers.items():
            if isinstance(value, (int, float)):
                diff = abs(value - number) / max(abs(value), abs(number), 1e-10)
                if diff < min_diff:
                    min_diff = diff
                    closest_match = (key, value)

        # 如果没有找到匹配或误差超过容差
        if closest_match is None:
            errors.append(
                f"第 {line_num} 行的数值 {number} 在结果文件中未找到对应项"
            )
        elif min_diff > tolerance:
            key, value = closest_match
            errors.append(
                f"第 {line_num} 行的数值 {number} 与结果文件中的 {key}={value} "
                f"不一致（误差 {min_diff*100:.2f}% > {tolerance*100}%）"
            )

    return len(errors) == 0, errors


def _extract_numbers_from_dict(
    data: Dict[str, Any],
    prefix: str = ""
) -> Dict[str, float]:
    """从嵌套字典中提取所有数值

    Args:
        data: 字典数据
        prefix: 键前缀（用于嵌套）

    Returns:
        扁平化的键值对字典
    """
    numbers = {}

    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key

        if isinstance(value, (int, float)):
            numbers[full_key] = float(value)
        elif isinstance(value, dict):
            numbers.update(_extract_numbers_from_dict(value, full_key))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, (int, float)):
                    numbers[f"{full_key}[{i}]"] = float(item)
                elif isinstance(item, dict):
                    numbers.update(_extract_numbers_from_dict(item, f"{full_key}[{i}]"))

    return numbers


def _extract_numbers_from_tex(tex_content: str) -> List[Tuple[int, float, str]]:
    """从 LaTeX 内容中提取所有数值

    Args:
        tex_content: LaTeX 文件内容

    Returns:
        List of (line_number, number, context)
    """
    numbers = []

    # 匹配数值模式：整数、小数、百分比
    number_pattern = r"\b(\d+\.?\d*)\s*%?(?=\s|,|\.|\)|\}|$)"

    lines = tex_content.split("\n")
    for line_num, line in enumerate(lines, 1):
        # 跳过注释行
        if line.strip().startswith("%"):
            continue

        # 跳过 LaTeX 命令和引用
        if re.search(r"\\(cite|ref|label|section|subsection)", line):
            continue

        matches = re.finditer(number_pattern, line)
        for match in matches:
            try:
                number = float(match.group(1))
                # 过滤掉明显不是结果的数值（如年份、页码等）
                if 1900 <= number <= 2100:  # 可能是年份
                    continue
                if number > 1000:  # 可能是其他标识符
                    continue

                context = line.strip()[:50]  # 保留上下文
                numbers.append((line_num, number, context))
            except ValueError:
                continue

    return numbers
