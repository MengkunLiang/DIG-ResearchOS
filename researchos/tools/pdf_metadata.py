"""PDF 元数据提取工具。

用于从 PDF 文件中提取论文元数据（标题、作者、年份等）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    import PyPDF2
    HAS_PYPDF2 = True
except ImportError:
    HAS_PYPDF2 = False


def extract_pdf_metadata(pdf_path: Path) -> dict[str, Any]:
    """从 PDF 文件提取元数据。

    尝试从以下来源提取：
    1. PDF 元数据（如果可用）
    2. 文件名解析

    Args:
        pdf_path: PDF 文件路径

    Returns:
        包含以下字段的字典：
        - id: 论文 ID（基于文件名）
        - title: 论文标题
        - authors: 作者列表
        - year: 发表年份
        - role: 种子论文角色（默认 "anchor"）
        - source: 数据源（"manual"）
    """
    metadata = {
        "id": f"seed:{pdf_path.stem}",
        "title": "",
        "authors": [{"name": "Unknown"}],
        "year": None,
        "role": "anchor",
        "source": "manual",
        "doi": "",
        "abstract": "",
        "venue": "",
    }

    # 尝试从 PDF 元数据提取
    if HAS_PYPDF2:
        try:
            with open(pdf_path, "rb") as f:
                reader = PyPDF2.PdfReader(f)
                pdf_metadata = reader.metadata

                if pdf_metadata:
                    # 提取标题
                    if pdf_metadata.get("/Title"):
                        metadata["title"] = str(pdf_metadata["/Title"])

                    # 提取作者
                    if pdf_metadata.get("/Author"):
                        author_str = str(pdf_metadata["/Author"])
                        # 简单分割作者（可能需要更复杂的解析）
                        authors = [a.strip() for a in author_str.split(",")]
                        metadata["authors"] = [{"name": a} for a in authors if a]

                    # 提取年份（从创建日期）
                    if pdf_metadata.get("/CreationDate"):
                        date_str = str(pdf_metadata["/CreationDate"])
                        year_match = re.search(r"(\d{4})", date_str)
                        if year_match:
                            metadata["year"] = int(year_match.group(1))
        except Exception:
            # PDF 读取失败，继续使用文件名解析
            pass

    # 如果 PDF 元数据不可用，从文件名解析
    if not metadata["title"]:
        metadata["title"], metadata["authors"], metadata["year"] = _parse_filename(pdf_path.name)

    return metadata


def _parse_filename(filename: str) -> tuple[str, list[dict], int | None]:
    """从文件名解析论文信息。

    支持的格式：
    - "Author 等 - 2026 - Title.pdf"
    - "Author et al - 2026 - Title.pdf"
    - "Title (2026).pdf"
    - "Title.pdf"

    Args:
        filename: 文件名

    Returns:
        (title, authors, year)
    """
    # 移除 .pdf 扩展名
    name = filename.replace(".pdf", "").strip()

    # 尝试匹配 "Author 等 - 2026 - Title" 格式
    match = re.match(r"^(.+?)\s*[-–—]\s*(\d{4})\s*[-–—]\s*(.+)$", name)
    if match:
        author_str, year_str, title = match.groups()
        authors = [{"name": author_str.strip()}]
        year = int(year_str)
        return title.strip(), authors, year

    # 尝试匹配 "Title (2026)" 格式
    match = re.match(r"^(.+?)\s*\((\d{4})\)$", name)
    if match:
        title, year_str = match.groups()
        year = int(year_str)
        return title.strip(), [{"name": "Unknown"}], year

    # 尝试提取年份（任何位置的 4 位数字）
    year_match = re.search(r"(\d{4})", name)
    year = int(year_match.group(1)) if year_match else None

    # 默认：整个文件名作为标题
    return name, [{"name": "Unknown"}], year


def scan_seed_papers(seed_dir: Path) -> list[dict[str, Any]]:
    """扫描种子论文目录，提取所有 PDF 的元数据。

    Args:
        seed_dir: 种子论文目录路径

    Returns:
        论文元数据列表
    """
    if not seed_dir.exists():
        return []

    papers = []
    for pdf_file in seed_dir.glob("*.pdf"):
        try:
            metadata = extract_pdf_metadata(pdf_file)
            papers.append(metadata)
        except Exception as e:
            # 记录错误但继续处理其他文件
            print(f"Warning: Failed to extract metadata from {pdf_file.name}: {e}")
            continue

    return papers
