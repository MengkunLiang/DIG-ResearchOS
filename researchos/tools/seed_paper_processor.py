from __future__ import annotations

"""种子论文处理工具。

支持用户提供种子论文的多种方式：
- PDF 文件路径
- arXiv ID
- DOI
- 论文标题 + 作者
"""

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..literature_identity import is_workspace_guide_or_template
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy
from ..runtime.logger import get_logger

_LOG = get_logger("seed_paper_processor")


_GENERIC_PDF_TITLE_VALUES = {
    "untitled",
    "unknown",
    "document",
    "paper",
    "article",
    "main",
    "fulltext",
}


def _is_likely_pdf_front_matter_line(title: str) -> bool:
    candidate = _clean_pdf_title_candidate(title)
    chinese_front_matter = (
        "作者",
        "作者简介",
        "作者单位",
        "单位",
        "通讯作者",
        "基金项目",
        "基金",
        "摘要",
        "【摘要】",
        "〔摘要〕",
        "要:",
        "要：",
        "关键词",
        "关键字",
        "中图分类号",
        "文献标识码",
        "收稿日期",
        "引用格式",
        "本文格式",
    )
    if candidate.startswith(chinese_front_matter):
        return True
    if re.match(r"^第\s*\d+\s*作者简介", candidate):
        return True
    return bool(
        re.match(
            r"^(author|authors|abstract|keywords|received|corresponding author|funding)\b",
            candidate,
            flags=re.IGNORECASE,
        )
        or re.search(
            r"\b(department|school|college|university|institute|laboratory|lab|affiliation|email)\b",
            candidate,
            flags=re.IGNORECASE,
        )
    )


def _is_likely_pdf_abstract_or_body_line(title: str) -> bool:
    candidate = _clean_pdf_title_candidate(title)
    if not candidate:
        return True
    compact = re.sub(r"\s+", "", candidate)
    if len(candidate) > 120 and re.search(r"[，,。.；;]", candidate):
        return True
    if re.match(
        r"^(本文|本研究|文章|研究指出|研究认为|目的|方法|结果|结论|为了解决|基于当前|针对当前|通过文本分析)",
        compact,
    ):
        return True
    if re.match(
        r"^(this paper|this study|we study|we propose|we investigate|in this paper|in this study|abstract)\b",
        candidate,
        flags=re.IGNORECASE,
    ):
        return True
    punctuation_count = len(re.findall(r"[，,。.；;]", candidate))
    if punctuation_count >= 3 and len(candidate) > 60:
        return True
    return False


def _is_likely_pdf_affiliation_line(title: str) -> bool:
    candidate = _clean_pdf_title_candidate(title)
    if not candidate:
        return True
    compact = re.sub(r"\s+", "", candidate)
    if re.match(r"^[（(]?\s*\d+\.\s*[\u4e00-\u9fffA-Za-z]", candidate):
        return True
    if re.search(r"\d{6}", candidate) and re.search(r"(大学|学院|研究院|研究所|中心|上海|北京|广州|武汉|南京|河南|河北|山东|江苏|浙江)", candidate):
        return True
    institution_marker = r"(?:大学(?!生)|学院|研究院|研究所|实验室|中心|教研室|基础教育课程教材)"
    if re.fullmatch(rf".{{0,30}}{institution_marker}.{{0,30}}", compact) and len(compact) <= 70:
        if not re.search(r"(研究|建设|构建|优化|探索|实践|体系|课程|专业|人才|发展)", compact):
            return True
    if re.search(r"(作者单位|通讯地址|邮编|基金项目)", compact):
        return True
    if re.search(
        r"\b(department|school|college|university|institute|laboratory|lab|center|centre|academy)\b",
        candidate,
        flags=re.IGNORECASE,
    ) and len(candidate) <= 100:
        return True
    if re.search(r"(@|e-?mail|通讯地址|邮编)", candidate, flags=re.IGNORECASE):
        return True
    return False


def _clean_pdf_title_candidate(value: Any) -> str:
    """Normalize one possible PDF title without losing Chinese text."""

    text = str(value or "").strip()
    if not text:
        return ""
    replacements = {
        "\u3000": " ",
        "\ufeff": "",
        "．": ".",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"\s+", " ", text)
    # Chinese PDF page headers often contain one Chinese character per cell.
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    return text.strip(" \t\r\n-–—|")


def _clean_pdf_filename_stem_title(value: Any) -> str:
    """Turn a seed PDF filename stem into a plausible title fallback."""

    text = str(value or "").strip()
    if not text:
        return ""
    # Common CNKI/manual seed filenames use `Title_Author` or `Title-Author`.
    # Keep the title side when the suffix looks like a short Chinese author list.
    for separator in ("_", " - ", "－", "——"):
        if separator not in text:
            continue
        head, tail = text.rsplit(separator, 1)
        tail_clean = _clean_pdf_title_candidate(tail)
        if re.fullmatch(r"[\u4e00-\u9fff·、,，]{2,18}", tail_clean):
            text = head
            break
    text = text.replace("_", " ").replace("-", " ")
    return _clean_pdf_title_candidate(text)


def _filename_stem_has_title_author_pattern(value: Any) -> bool:
    """Return true when a PDF stem looks like a deliberate Title_Author name."""

    text = str(value or "").strip()
    if not text:
        return False
    for separator in ("_", " - ", "－", "——"):
        if separator not in text:
            continue
        head, tail = text.rsplit(separator, 1)
        head = _clean_pdf_title_candidate(head)
        tail = _clean_pdf_title_candidate(tail)
        if head and re.fullmatch(r"[\u4e00-\u9fff·、,，]{2,18}", tail):
            return True
    return False


def _title_signal_counts(title: str) -> tuple[int, int, int]:
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", title))
    ascii_letters = len(re.findall(r"[A-Za-z]", title))
    digits = len(re.findall(r"\d", title))
    return chinese_chars, ascii_letters, digits


def _is_likely_pdf_header_or_journal_title(title: str) -> bool:
    """Reject journal mastheads, issue headers, DOI lines, and generic PDF metadata."""

    candidate = _clean_pdf_title_candidate(title)
    if not candidate:
        return True

    lowered = candidate.casefold()
    compact = re.sub(r"\s+", "", candidate)
    chinese_chars, ascii_letters, digits = _title_signal_counts(candidate)

    if lowered in _GENERIC_PDF_TITLE_VALUES:
        return True
    if lowered.startswith(("microsoft word", "untitled", "springer", "elsevier")) and len(candidate) <= 40:
        return True
    if re.fullmatch(r"[\W\d_]+", candidate):
        return True
    if chinese_chars + ascii_letters < 4:
        return True
    if _is_likely_pdf_front_matter_line(candidate):
        return True
    if _is_likely_pdf_affiliation_line(candidate):
        return True
    if _is_likely_pdf_abstract_or_body_line(candidate):
        return True

    issue_patterns = [
        r"第\s*\d+\s*卷.*第\s*\d+\s*期",
        r"第\s*\d+\s*期.*第\s*\d+\s*卷",
        r"\bvol\.?\s*\d+.*\bno\.?\s*\d+",
        r"\bvolume\s*\d+.*\b(issue|number)\s*\d+",
        r"\bissn\b",
        r"\bcn\s*\d{2}[-/]\d+",
        r"\bdoi\s*[:：]",
    ]
    if any(re.search(pattern, candidate, flags=re.IGNORECASE) for pattern in issue_patterns):
        return True

    if re.fullmatch(r"《[^》]{2,30}》(?:[（(][^）)]*(?:月刊|双月刊|季刊|周刊)[^）)]*[）)])?", candidate):
        return True
    if re.search(r"(月刊|双月刊|季刊|周刊)", candidate) and len(compact) <= 24 and "研究" not in candidate:
        return True
    if re.fullmatch(r"[\u4e00-\u9fff]{2,18}(学报|杂志|论坛|评论)$", compact) and digits == 0:
        return True
    if re.search(r"(journal|transactions|proceedings|conference)", lowered) and digits >= 2 and len(candidate) <= 90:
        return True
    if re.fullmatch(r"(journal|transactions|proceedings|conference|review|bulletin|guide)(?:\s+of|\s+on|\s+for)?[A-Za-z\s&,.:-]{0,70}", candidate, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"[A-Z][A-Za-z\s&,.:-]{3,80}", candidate) and re.search(
        r"\b(journal|review|bulletin|guide|transactions|proceedings|academy|education administration)\b",
        lowered,
    ):
        return True

    return False


def _pdf_title_score(title: str, *, source: str, line_index: int | None = None) -> float:
    candidate = _clean_pdf_title_candidate(title)
    if _is_likely_pdf_header_or_journal_title(candidate):
        return -1000.0
    chinese_chars, ascii_letters, digits = _title_signal_counts(candidate)
    length = len(candidate)
    score = 0.0
    if source == "first_page":
        score += 6.0
    elif source == "metadata":
        score += 4.0
    elif source == "filename":
        score += 6.0
    if line_index is not None:
        score += max(0.0, 5.0 - min(line_index, 20) * 0.2)
    score += min(chinese_chars + ascii_letters / 2, 24) * 0.15
    if 10 <= length <= 150:
        score += 3.0
    elif 6 <= length < 10:
        score += 1.0
    elif length > 180:
        score -= 3.0
    if any(mark in candidate for mark in (":", "：", "——", "--")):
        score += 1.0
    if re.search(r"(研究|模型|算法|治理|风险|机制|效应|analysis|model|learning|risk|governance)", candidate, flags=re.I):
        score += 1.0
    if digits > max(4, chinese_chars + ascii_letters):
        score -= 2.0
    filename_like = source == "filename" and re.search(r"(研究|建设|构建|优化|探索|实践|体系|课程|专业|人才|发展|analysis|model|study|research)", candidate, flags=re.I)
    if filename_like:
        score += 2.0
    return score


def _pdf_title_candidates_from_text(text: str) -> list[dict[str, Any]]:
    lines = [_clean_pdf_title_candidate(line) for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    candidates: list[dict[str, Any]] = []
    for idx, line in enumerate(lines[:70]):
        if len(line) > 220:
            continue
        score = _pdf_title_score(line, source="first_page", line_index=idx)
        if score > -100:
            candidates.append({"title": line, "source": "first_page", "line_index": idx, "score": score})
        if idx + 1 < len(lines):
            next_line = lines[idx + 1]
            if _is_likely_pdf_front_matter_line(next_line) or _pdf_title_score(
                next_line,
                source="first_page",
                line_index=idx + 1,
            ) <= -100:
                continue
            merged = _clean_pdf_title_candidate(f"{line} {lines[idx + 1]}")
            if len(merged) <= 220:
                if _is_likely_pdf_header_or_journal_title(merged):
                    continue
                merged_score = _pdf_title_score(merged, source="first_page", line_index=idx) - 0.5
                if merged_score > -100:
                    candidates.append(
                        {
                            "title": merged,
                            "source": "first_page",
                            "line_index": idx,
                            "score": merged_score,
                        }
                    )
    return candidates


def _choose_pdf_title(
    *,
    metadata_title: Any = "",
    first_page_text: str = "",
    filename_stem: str = "",
) -> dict[str, Any]:
    """Choose a paper title from PDF metadata, first-page text, and filename fallback."""

    candidates: list[dict[str, Any]] = []
    rejected: list[str] = []

    meta_title = _clean_pdf_title_candidate(metadata_title)
    if meta_title:
        score = _pdf_title_score(meta_title, source="metadata")
        if score > -100:
            candidates.append({"title": meta_title, "source": "metadata", "score": score})
        else:
            rejected.append(meta_title)

    for candidate in _pdf_title_candidates_from_text(first_page_text):
        candidates.append(candidate)

    filename_title = _clean_pdf_filename_stem_title(filename_stem)
    filename_rejected = False
    if filename_title:
        score = _pdf_title_score(filename_title, source="filename")
        if score > -100:
            candidates.append({"title": filename_title, "source": "filename", "score": score})
        else:
            filename_rejected = True
            rejected.append(filename_title)

    if candidates:
        filename_candidates = [item for item in candidates if item.get("source") == "filename"]
        page_candidates = [item for item in candidates if item.get("source") == "first_page"]
        if page_candidates:
            for item in page_candidates:
                if _is_likely_noisy_first_page_title(str(item.get("title") or "")):
                    item["score"] = float(item.get("score", 0.0)) - 6.0
        if filename_candidates and page_candidates:
            best_filename = max(filename_candidates, key=lambda item: float(item.get("score", 0.0)))
            best_page = max(page_candidates, key=lambda item: float(item.get("score", 0.0)))
            filename_title = str(best_filename.get("title") or "")
            page_title = str(best_page.get("title") or "")
            if (
                _filename_stem_has_title_author_pattern(filename_stem)
                and _looks_like_better_filename_title(filename_title)
            ):
                return {
                    "title": filename_title,
                    "title_source": "filename",
                    "title_confidence": "heuristic_high"
                    if float(best_filename.get("score", 0.0)) >= 10
                    else "heuristic_medium",
                    "metadata_review_required": True,
                    "rejected_title_candidates": [
                        *rejected,
                        *[str(item.get("title") or "") for item in page_candidates[:5]],
                    ][:8],
                }
            if (
                _looks_like_better_filename_title(filename_title)
                and _is_likely_noisy_first_page_title(page_title)
            ):
                return {
                    "title": filename_title,
                    "title_source": "filename",
                    "title_confidence": "heuristic_medium",
                    "metadata_review_required": True,
                    "rejected_title_candidates": [*rejected, page_title][:8],
                }
        best = max(
            candidates,
            key=lambda item: (
                float(item.get("score", 0.0)),
                -int(item.get("line_index", 999)),
                len(str(item.get("title", ""))),
            ),
        )
        result = {
            "title": best["title"],
            "title_source": best.get("source", "unknown"),
            "title_confidence": "heuristic_high" if float(best.get("score", 0.0)) >= 8 else "heuristic_medium",
            "rejected_title_candidates": rejected[:8],
        }
        if best.get("source") == "filename" and first_page_text.strip():
            noisy_pages = [
                str(item.get("title") or "")
                for item in page_candidates
                if _is_likely_noisy_first_page_title(str(item.get("title") or ""))
            ]
            result["metadata_review_required"] = True
            if noisy_pages:
                result["rejected_title_candidates"] = [*rejected, *noisy_pages][:8]
            elif not result.get("rejected_title_candidates"):
                result["rejected_title_candidates"] = ["first_page_noise_detected"]
        return result

    fallback = filename_title or "Untitled seed paper"
    return {
        "title": fallback,
        "title_source": "filename_review_required" if filename_rejected else "filename_fallback",
        "title_confidence": "needs_review",
        "metadata_review_required": True,
        "rejected_title_candidates": rejected[:8],
    }


def _looks_like_better_filename_title(title: str) -> bool:
    candidate = _clean_pdf_title_candidate(title)
    if not candidate or _is_likely_pdf_header_or_journal_title(candidate):
        return False
    chinese_chars, ascii_letters, _digits = _title_signal_counts(candidate)
    if len(candidate) < 6 or len(candidate) > 120:
        return False
    return bool(
        chinese_chars >= 6
        or ascii_letters >= 12
        or re.search(r"(研究|建设|构建|优化|探索|实践|体系|课程|专业|人才|发展|analysis|model|study|research)", candidate, flags=re.I)
    )


def _is_likely_noisy_first_page_title(title: str) -> bool:
    candidate = _clean_pdf_title_candidate(title)
    if not candidate:
        return True
    return (
        _is_likely_pdf_front_matter_line(candidate)
        or _is_likely_pdf_affiliation_line(candidate)
        or _is_likely_pdf_abstract_or_body_line(candidate)
        or _is_likely_pdf_header_or_journal_title(candidate)
    )


def _first_text(value: Any, default: str = "") -> str:
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
        return default
    if isinstance(value, str):
        return value.strip()
    if value in (None, "", [], {}):
        return default
    return str(value).strip()


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, "", [], {}):
            return None
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _year_from_crossref_payload(message: dict[str, Any]) -> int | None:
    published = message.get("published-print") or message.get("published-online") or message.get("published") or message.get("issued")
    date_parts = (published or {}).get("date-parts") if isinstance(published, dict) else None
    if not isinstance(date_parts, list) or not date_parts or not isinstance(date_parts[0], list) or not date_parts[0]:
        return None
    return _safe_int(date_parts[0][0])


def _crossref_author_names(message: dict[str, Any]) -> list[str]:
    raw_authors = message.get("author") or []
    if not isinstance(raw_authors, list):
        return []
    names: list[str] = []
    for author in raw_authors[:10]:
        if isinstance(author, str):
            name = author.strip()
        elif isinstance(author, dict):
            given = str(author.get("given") or "").strip()
            family = str(author.get("family") or "").strip()
            name = f"{given} {family}".strip() or str(author.get("name") or "").strip()
        else:
            name = str(author).strip()
        if name:
            names.append(name)
    return names


class ProcessSeedPaperParams(BaseModel):
    """处理种子论文参数"""

    source: str = Field(..., description="论文来源：pdf_path、arxiv_id、doi、title")
    value: str = Field(..., description="对应的值（文件路径、ID、标题等）")
    role: str = Field("reference", description="论文角色：anchor 或 reference")
    why_relevant: str = Field("", description="为什么相关")
    authors: list[str] = Field(default_factory=list, description="作者列表（可选）")
    year: int | None = Field(None, description="年份（可选）")


class ProcessSeedPaperTool(Tool):
    """处理种子论文工具。

    将用户提供的种子论文（PDF、arXiv ID、DOI等）转换为标准格式，
    并提取元数据（标题、作者、年份等）。
    """

    name = "process_seed_paper"
    description = (
        "处理用户提供的种子论文，支持 PDF 文件、arXiv ID、DOI 等多种输入方式。"
        "自动提取论文元数据（标题、作者、年份）并转换为标准格式。"
    )
    parameters_schema = ProcessSeedPaperParams
    timeout_seconds = 60.0
    requires_human_approval = False
    idempotent = True

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = ProcessSeedPaperParams(**kwargs)

        try:
            if params.source == "pdf_path":
                return await self._process_pdf(params)
            elif params.source == "arxiv_id":
                return await self._process_arxiv(params)
            elif params.source == "doi":
                return await self._process_doi(params)
            elif params.source == "title":
                return await self._process_title(params)
            else:
                return ToolResult(
                    ok=False,
                    content=f"不支持的来源类型: {params.source}",
                    error="unsupported_source",
                )
        except Exception as e:
            _LOG.error("process_seed_paper_failed", source=params.source, error=str(e))
            return ToolResult(
                ok=False,
                content=f"处理种子论文失败: {e}",
                error="processing_failed",
            )

    async def _process_pdf(self, params: ProcessSeedPaperParams) -> ToolResult:
        """处理 PDF 文件。

        步骤：
        1. 验证 PDF 文件存在
        2. 提取元数据（标题、作者、年份）
        3. 复制 PDF 到 user_seeds/pdfs/ 目录
        4. 返回标准格式的论文信息
        """
        # 解析 PDF 路径
        pdf_path = Path(params.value)
        if not pdf_path.is_absolute():
            # 相对路径，相对于 workspace
            pdf_path = self.policy.workspace_dir / pdf_path

        if not pdf_path.exists():
            return ToolResult(
                ok=False,
                content=f"PDF 文件不存在: {params.value}",
                error="pdf_not_found",
            )
        if not pdf_path.is_file():
            return ToolResult(
                ok=False,
                content=f"PDF 路径不是文件: {params.value}",
                error="not_a_file",
            )
        if is_workspace_guide_or_template(pdf_path):
            return ToolResult(
                ok=False,
                content=f"不能把 workspace 说明/模板文件作为 seed PDF: {pdf_path.name}",
                error="invalid_seed_file",
            )
        if pdf_path.suffix.lower() != ".pdf":
            return ToolResult(
                ok=False,
                content=f"文件不是 PDF 格式: {pdf_path.suffix}",
                error="invalid_format",
            )
        try:
            if not pdf_path.read_bytes()[:5].startswith(b"%PDF"):
                return ToolResult(
                    ok=False,
                    content="文件扩展名为 PDF，但文件头不是 %PDF。",
                    error="invalid_pdf_header",
                )
        except OSError as exc:
            return ToolResult(ok=False, content=f"无法读取 PDF: {exc}", error="read_failed")

        # 提取元数据
        metadata = await self._extract_pdf_metadata(pdf_path)

        # 复制 PDF 到 user_seeds/pdfs/
        pdfs_dir = self.policy.workspace_dir / "user_seeds" / "pdfs"
        pdfs_dir.mkdir(parents=True, exist_ok=True)

        dest_path = pdfs_dir / pdf_path.name
        if not dest_path.exists():
            import shutil
            shutil.copy2(pdf_path, dest_path)

        # 构建标准格式
        paper_info = {
            "title": metadata.get("title", pdf_path.stem),
            "authors": params.authors or metadata.get("authors", []),
            "year": params.year or metadata.get("year"),
            "role": params.role,
            "why_relevant": params.why_relevant,
            "pdf_path": f"user_seeds/pdfs/{pdf_path.name}",
            "title_source": metadata.get("title_source", "unknown"),
            "title_confidence": metadata.get("title_confidence", "unknown"),
        }
        if metadata.get("metadata_review_required"):
            paper_info["metadata_review_required"] = True
        if metadata.get("rejected_title_candidates"):
            paper_info["rejected_title_candidates"] = metadata["rejected_title_candidates"]

        # 写入 seed_papers.jsonl
        sync_action, persisted_paper_info = await self._append_to_seed_papers(paper_info)

        return ToolResult(
            ok=True,
            content=(
                f"✅ 成功处理 PDF 论文\n"
                f"标题: {persisted_paper_info['title']}\n"
                f"作者: {', '.join((persisted_paper_info.get('authors') or [])[:3])}{'...' if len(persisted_paper_info.get('authors') or []) > 3 else ''}\n"
                f"年份: {persisted_paper_info.get('year')}\n"
                f"角色: {persisted_paper_info.get('role', paper_info['role'])}\n"
                f"PDF 已复制到: {persisted_paper_info.get('pdf_path', paper_info['pdf_path'])}\n"
                f"seed_papers.jsonl: {sync_action}"
            ),
            data={"paper": persisted_paper_info},
        )

    async def _extract_pdf_metadata(self, pdf_path: Path) -> dict[str, Any]:
        """从 PDF 提取元数据。

        尝试多种方法：
        1. PyMuPDF (fitz)
        2. pdfplumber
        3. 文件名解析
        """
        metadata: dict[str, Any] = {}
        metadata_title = ""
        first_page_text = ""

        # 方法 1: PyMuPDF
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(pdf_path)
            meta = doc.metadata

            if meta.get("title"):
                metadata_title = str(meta["title"])
            if meta.get("author"):
                # 作者可能是逗号分隔的字符串
                authors = [a.strip() for a in meta["author"].split(",")]
                metadata["authors"] = authors
            if meta.get("creationDate"):
                # 尝试提取年份
                import re
                year_match = re.search(r"(\d{4})", meta["creationDate"])
                if year_match:
                    metadata["year"] = int(year_match.group(1))

            doc.close()
        except Exception as e:
            _LOG.debug("pymupdf_extraction_failed", error=str(e))

        # 方法 2: pdfplumber / PyMuPDF page text
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(pdf_path)
            if doc.page_count:
                first_page_text = doc[0].get_text("text") or ""
            doc.close()
        except Exception as e:
            _LOG.debug("pymupdf_text_extraction_failed", error=str(e))

        if not first_page_text:
            try:
                import pdfplumber
                with pdfplumber.open(pdf_path) as pdf:
                    first_page = pdf.pages[0]
                    first_page_text = first_page.extract_text() or ""
            except Exception as e:
                _LOG.debug("pdfplumber_extraction_failed", error=str(e))

        title_selection = _choose_pdf_title(
            metadata_title=metadata_title,
            first_page_text=first_page_text,
            filename_stem=pdf_path.stem,
        )
        metadata.update(title_selection)

        return metadata

    async def _process_arxiv(self, params: ProcessSeedPaperParams) -> ToolResult:
        """处理 arXiv ID。

        步骤：
        1. 验证 arXiv ID 格式
        2. 从 arXiv API 获取元数据
        3. 返回标准格式的论文信息
        """
        arxiv_id = params.value.strip()

        # 验证格式（如 2401.12345 或 arXiv:2401.12345）
        import re
        if arxiv_id.startswith("arXiv:"):
            arxiv_id = arxiv_id[6:]

        if not re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", arxiv_id):
            return ToolResult(
                ok=False,
                content=f"无效的 arXiv ID 格式: {params.value}",
                error="invalid_arxiv_id",
            )

        # 从 arXiv API 获取元数据
        try:
            import httpx
            url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
            async with httpx.AsyncClient(follow_redirects=True) as client:
                response = await client.get(url, timeout=10.0)
                response.raise_for_status()

                # 解析 XML
                import xml.etree.ElementTree as ET
                root = ET.fromstring(response.text)

                # 提取信息
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                entry = root.find("atom:entry", ns)

                if entry is None:
                    return ToolResult(
                        ok=False,
                        content=f"未找到 arXiv 论文: {arxiv_id}",
                        error="arxiv_not_found",
                    )

                title = entry.find("atom:title", ns).text.strip()
                authors = [
                    author.find("atom:name", ns).text
                    for author in entry.findall("atom:author", ns)
                ]
                published = entry.find("atom:published", ns).text
                year = int(published[:4])

                paper_info = {
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "role": params.role,
                    "why_relevant": params.why_relevant,
                    "arxiv_id": arxiv_id,
                    "url": f"https://arxiv.org/abs/{arxiv_id}",
                }

                # 写入 seed_papers.jsonl
                sync_action, persisted_paper_info = await self._append_to_seed_papers(paper_info)

                return ToolResult(
                    ok=True,
                    content=(
                        f"✅ 成功从 arXiv 获取论文信息\n"
                        f"arXiv ID: {arxiv_id}\n"
                        f"标题: {persisted_paper_info.get('title', title)}\n"
                        f"作者: {', '.join((persisted_paper_info.get('authors') or authors)[:3])}{'...' if len(persisted_paper_info.get('authors') or authors) > 3 else ''}\n"
                        f"年份: {persisted_paper_info.get('year', year)}\n"
                        f"角色: {params.role}\n"
                        f"URL: https://arxiv.org/abs/{arxiv_id}\n"
                        f"seed_papers.jsonl: {sync_action}"
                    ),
                    data={"paper": persisted_paper_info},
                )
        except Exception as e:
            _LOG.error("arxiv_fetch_failed", arxiv_id=arxiv_id, error=str(e))
            return ToolResult(
                ok=False,
                content=f"获取 arXiv 元数据失败: {e}",
                error="arxiv_fetch_failed",
            )

    async def _process_doi(self, params: ProcessSeedPaperParams) -> ToolResult:
        """处理 DOI。

        步骤：
        1. 验证 DOI 格式
        2. 检查是否为 arXiv DOI，如果是则转换为 arXiv ID
        3. 从 CrossRef API 获取元数据
        4. 返回标准格式的论文信息
        """
        doi = params.value.strip()

        # 移除 doi: 前缀（如果有）
        if doi.lower().startswith("doi:"):
            doi = doi[4:].strip()

        # 检查是否为 arXiv DOI (格式: 10.48550/arXiv.XXXX.XXXXX)
        import re
        arxiv_doi_match = re.match(r"10\.48550/arXiv\.(\d{4}\.\d{4,5}(?:v\d+)?)", doi)
        if arxiv_doi_match:
            arxiv_id = arxiv_doi_match.group(1)
            _LOG.info("arxiv_doi_detected", doi=doi, arxiv_id=arxiv_id)
            # 转换为 arXiv 处理
            params.value = arxiv_id
            return await self._process_arxiv(params)

        # 从 CrossRef API 获取元数据
        try:
            import httpx
            url = f"https://api.crossref.org/works/{doi}"
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=10.0)
                response.raise_for_status()

                data = response.json()
                message = data["message"]

                title = _first_text(message.get("title"), doi)
                authors = _crossref_author_names(message)
                year = _year_from_crossref_payload(message)

                paper_info = {
                    "title": title,
                    "authors": authors,
                    "year": year,
                    "role": params.role,
                    "why_relevant": params.why_relevant,
                    "doi": doi,
                    "url": f"https://doi.org/{doi}",
                }

                # 写入 seed_papers.jsonl
                sync_action, persisted_paper_info = await self._append_to_seed_papers(paper_info)

                return ToolResult(
                    ok=True,
                    content=(
                        f"✅ 成功从 DOI 获取论文信息\n"
                        f"DOI: {doi}\n"
                        f"标题: {persisted_paper_info.get('title', title)}\n"
                        f"seed_papers.jsonl: {sync_action}"
                    ),
                    data={"paper": persisted_paper_info},
                )
        except Exception as e:
            _LOG.error("doi_fetch_failed", doi=doi, error=str(e))
            return ToolResult(
                ok=False,
                content=f"获取 DOI 元数据失败: {e}",
                error="doi_fetch_failed",
            )

    async def _process_title(self, params: ProcessSeedPaperParams) -> ToolResult:
        """处理论文标题。

        用户直接提供标题和作者，不需要额外处理。
        """
        if not params.authors:
            return ToolResult(
                ok=False,
                content="使用标题方式时必须提供作者列表",
                error="missing_authors",
            )

        paper_info = {
            "title": params.value,
            "authors": params.authors,
            "year": params.year,
            "role": params.role,
            "why_relevant": params.why_relevant,
        }

        # 写入 seed_papers.jsonl
        sync_action, persisted_paper_info = await self._append_to_seed_papers(paper_info)

        return ToolResult(
            ok=True,
            content=(
                f"✅ 成功处理论文\n"
                f"标题: {persisted_paper_info.get('title', params.value)}\n"
                f"seed_papers.jsonl: {sync_action}"
            ),
            data={"paper": persisted_paper_info},
        )

    async def _append_to_seed_papers(self, paper_info: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """将论文信息追加到 seed_papers.jsonl 文件。

        Args:
            paper_info: 论文信息字典
        """
        seed_papers_path = self.policy.workspace_dir / "user_seeds" / "seed_papers.jsonl"

        # 确保目录存在
        seed_papers_path.parent.mkdir(parents=True, exist_ok=True)

        records = _load_seed_paper_records(seed_papers_path)
        match_index = _find_seed_record_match(records, paper_info)
        if match_index is not None:
            existing = records[match_index]
            if _seed_record_quality(existing) >= _seed_record_quality(paper_info):
                _LOG.info("seed_paper_existing_kept", title=existing.get("title", "unknown"))
                return "已存在高置信记录，未重复追加", existing
            records[match_index] = {**existing, **paper_info}
            _write_seed_paper_records(seed_papers_path, records)
            _LOG.info("seed_paper_replaced", title=paper_info.get("title", "unknown"))
            return "已替换同一论文的低置信记录", records[match_index]

        records.append(paper_info)
        _write_seed_paper_records(seed_papers_path, records)
        _LOG.info("seed_paper_appended", title=paper_info.get("title", "unknown"))
        return "已追加新记录", paper_info


def _load_seed_paper_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            records.append(data)
    return records


def _write_seed_paper_records(path: Path, records: list[dict[str, Any]]) -> None:
    content = "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
    path.write_text(content + ("\n" if content else ""), encoding="utf-8")


def _find_seed_record_match(records: list[dict[str, Any]], paper_info: dict[str, Any]) -> int | None:
    target_keys = _seed_record_keys(paper_info)
    if not target_keys:
        return None
    for idx, record in enumerate(records):
        if target_keys & _seed_record_keys(record):
            return idx
    return None


def _seed_record_keys(record: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for field in ("pdf_path", "seed_pdf_path"):
        value = str(record.get(field) or "").strip()
        if value:
            keys.add(f"pdf:{Path(value).name.casefold()}")
            normalized = _normalize_seed_pdf_basename(value)
            if normalized:
                keys.add(f"pdf_norm:{normalized}")
    doi = str(record.get("doi") or "").strip().casefold().removeprefix("doi:")
    if doi:
        keys.add(f"doi:{doi}")
    arxiv_id = str(record.get("arxiv_id") or "").strip().casefold().removeprefix("arxiv:")
    if arxiv_id:
        keys.add(f"arxiv:{arxiv_id}")
    title = _clean_pdf_title_candidate(record.get("title"))
    year = str(record.get("year") or "").strip()
    if title:
        title_key = re.sub(r"\W+", "", title.casefold())
        if title_key:
            keys.add(f"title:{title_key}")
            if year:
                keys.add(f"title_year:{title_key}|{year}")
    return keys


def _normalize_seed_pdf_basename(value: Any) -> str:
    name = Path(str(value or "").strip()).name.casefold()
    if not name:
        return ""
    return re.sub(r"[\s\"'“”‘’`]+", "", name)


def _seed_record_quality(record: dict[str, Any]) -> float:
    title = str(record.get("title") or "").strip()
    score = _pdf_title_score(title, source="filename") if title else -1000.0
    source = str(record.get("title_source") or "").strip().casefold()
    confidence = str(record.get("title_confidence") or "").strip().casefold()
    if "manual" in source:
        score += 20
    if "high" in confidence:
        score += 10
    elif "medium" in confidence:
        score += 4
    if record.get("metadata_review_required"):
        score -= 2
    if _is_likely_noisy_first_page_title(title):
        score -= 20
    return score
