"""声明-证据可追溯性验证器

验证论文中的定量声明是否有对应的源引用注释。
"""

import re
from pathlib import Path
from typing import Tuple, List


def validate_claim_traceability(
    tex_path: Path, workspace: Path
) -> Tuple[bool, List[str]]:
    """检查论文中的定量声明是否有 SOURCE 注释

    Args:
        tex_path: LaTeX 文件路径
        workspace: 工作空间根目录

    Returns:
        (is_valid, errors): 验证是否通过和错误列表
    """
    if not tex_path.exists():
        return False, [f"LaTeX 文件不存在: {tex_path}"]

    try:
        content = tex_path.read_text(encoding="utf-8")
    except Exception as e:
        return False, [f"无法读取 LaTeX 文件: {e}"]

    errors = []

    # 提取所有数值声明（简化版：查找百分比、小数、科学计数法）
    # 匹配模式：数字 + 可选单位（%、accuracy、F1等）
    number_pattern = r"\b\d+\.?\d*%|\b\d+\.?\d*\s*(accuracy|precision|recall|F1|BLEU|ROUGE)"

    lines = content.split("\n")
    for line_num, line in enumerate(lines, 1):
        # 跳过注释行
        if line.strip().startswith("%"):
            continue

        # 查找数值声明
        matches = re.finditer(number_pattern, line, re.IGNORECASE)
        for match in matches:
            claim_text = match.group(0)

            # 检查前后几行是否有 SOURCE 注释
            has_source = False
            search_range = range(max(0, line_num - 3), min(len(lines), line_num + 2))

            for check_line_num in search_range:
                check_line = lines[check_line_num]
                if re.search(r"%\s*SOURCE:", check_line, re.IGNORECASE):
                    has_source = True
                    break

            if not has_source:
                errors.append(
                    f"第 {line_num} 行发现未标注来源的声明: '{claim_text}'"
                )

    # 验证 SOURCE 注释引用的文件是否存在
    source_pattern = r"%\s*SOURCE:\s*([^\s:]+)"
    for line_num, line in enumerate(lines, 1):
        match = re.search(source_pattern, line, re.IGNORECASE)
        if match:
            source_file = match.group(1)
            source_path = workspace / source_file

            if not source_path.exists():
                errors.append(
                    f"第 {line_num} 行引用的源文件不存在: {source_file}"
                )

    return len(errors) == 0, errors


def extract_claims_from_tex(tex_path: Path) -> List[Tuple[int, str]]:
    """从 LaTeX 文件中提取所有定量声明

    Args:
        tex_path: LaTeX 文件路径

    Returns:
        List of (line_number, claim_text)
    """
    if not tex_path.exists():
        return []

    try:
        content = tex_path.read_text(encoding="utf-8")
    except Exception:
        return []

    claims = []
    number_pattern = r"\b\d+\.?\d*%|\b\d+\.?\d*\s*(accuracy|precision|recall|F1|BLEU|ROUGE)"

    lines = content.split("\n")
    for line_num, line in enumerate(lines, 1):
        if line.strip().startswith("%"):
            continue

        matches = re.finditer(number_pattern, line, re.IGNORECASE)
        for match in matches:
            claims.append((line_num, match.group(0)))

    return claims
