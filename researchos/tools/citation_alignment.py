from __future__ import annotations

"""Conservative citation-to-claim alignment checks for generated TeX.

These helpers are intentionally heuristic. They do not judge scholarship; they
flag clear signs that a citation key is being used as padding for an unrelated
claim. Low-confidence cases should remain writable review material.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bibtex import parse_bib_entries


_LATEX_CITATION_CONTEXT_RE = re.compile(
    r"\\(?:cite|citep|citet|citealp|citealt|citeauthor|citeyear|parencite|textcite|autocite|footcite|supercite)\*?"
    r"(?:\[[^\]]*\]){0,2}\{([^}]+)\}",
    flags=re.IGNORECASE,
)

_LATEX_COMMAND_RE = re.compile(r"\\[A-Za-z]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
_CJK_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")

_LATIN_STOPWORDS = {
    "about",
    "across",
    "after",
    "against",
    "also",
    "among",
    "analysis",
    "and",
    "are",
    "article",
    "based",
    "because",
    "between",
    "both",
    "boundary",
    "can",
    "case",
    "claim",
    "claims",
    "common",
    "comparison",
    "conditions",
    "context",
    "contexts",
    "contribution",
    "data",
    "design",
    "different",
    "does",
    "each",
    "effect",
    "evaluation",
    "evidence",
    "field",
    "findings",
    "for",
    "from",
    "future",
    "have",
    "into",
    "journal",
    "literature",
    "main",
    "mechanism",
    "mechanisms",
    "method",
    "methods",
    "model",
    "models",
    "more",
    "paper",
    "papers",
    "prior",
    "problem",
    "proposed",
    "research",
    "results",
    "review",
    "scope",
    "section",
    "setting",
    "settings",
    "shows",
    "specific",
    "studies",
    "study",
    "such",
    "support",
    "supports",
    "systems",
    "than",
    "that",
    "the",
    "their",
    "these",
    "this",
    "through",
    "under",
    "using",
    "when",
    "where",
    "which",
    "while",
    "with",
    "within",
    "work",
    "works",
}

_CJK_STOPWORDS = {
    "研究",
    "论文",
    "文献",
    "方法",
    "模型",
    "机制",
    "系统",
    "问题",
    "分析",
    "比较",
    "证据",
    "结果",
    "领域",
    "综述",
    "框架",
    "未来",
    "方向",
    "基于",
    "通过",
    "本文",
    "已有",
    "相关",
    "当前",
    "不同",
    "主要",
}

_WEAK_BIB_TITLE_WORDS = {
    "paper",
    "study",
    "title",
    "test",
    "unknown",
    "article",
    "research",
}

_WEAK_ALIGNMENT_TOKENS = {
    "alignment",
    "analysis",
    "approach",
    "assessment",
    "course",
    "curriculum",
    "design",
    "education",
    "evaluation",
    "higher",
    "learning",
    "method",
    "model",
    "teaching",
    "课程",
    "教育",
    "教学",
    "学习",
    "高校",
    "高等",
    "方法",
    "模型",
    "设计",
    "评估",
    "评价",
    "对齐",
}

_STRONG_CLAIM_RE = re.compile(
    r"\b(?:prove[sd]?|demonstrat(?:e|es|ed)|show(?:s|ed)?|find(?:s|ing)?|"
    r"reveal(?:s|ed)?|validate[sd]?|confirm(?:s|ed)?|outperform(?:s|ed)?|"
    r"improve(?:s|d)?|significant(?:ly)?|causal|causality|mechanism|mechanistic|"
    r"empirical(?:ly)?|experiment(?:s|al)?|effect(?:s|ive|iveness)?)\b|"
    r"证明|表明|显示|发现|验证|证实|显著|因果|机制|实证|实验|提升|改善|导致|影响",
    flags=re.IGNORECASE,
)

_BROAD_CONTEXT_RE = re.compile(
    r"\b(?:review|survey|literature|stream|streams|field|area|body of work|prior work|"
    r"research agenda|future directions|taxonomy|framework|background|context|scope)\b|"
    r"综述|文献|领域|研究流派|研究脉络|未来方向|研究议程|分类|框架|背景|范围",
    flags=re.IGNORECASE,
)

_ABSTRACT_ONLY_RE = re.compile(r"\b(?:ABSTRACT|METADATA)\s*[-_ ]?\s*ONLY\b|摘要|元数据", flags=re.IGNORECASE)
_EVIDENCE_BOUNDARY_RE = re.compile(
    r"\b(?:abstract|metadata|trend|context|coverage|hint|boundary|preliminary|limited)\b|"
    r"摘要|元数据|趋势|背景|覆盖|提示|边界|有限|初步",
    flags=re.IGNORECASE,
)

_TOKEN_ALIAS_RULES: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"constructive\s+alignment", re.IGNORECASE), ("建设性", "对齐", "目标", "教学", "评估")),
    (re.compile(r"curriculum\s+alignment", re.IGNORECASE), ("课程", "对齐", "标准", "教学", "评估")),
    (re.compile(r"curriculum\s+mapping", re.IGNORECASE), ("课程", "映射", "对齐")),
    (re.compile(r"smart\s+construction", re.IGNORECASE), ("智能", "建造", "课程群")),
    (re.compile(r"interdisciplinary\s+integration", re.IGNORECASE), ("跨学科", "融合", "交叉")),
    (re.compile(r"internationali[sz]ation|international\s+students?", re.IGNORECASE), ("国际", "国际化", "留学生")),
    (re.compile(r"traditional\s+(?:chinese\s+)?(?:cultural\s+)?arts?", re.IGNORECASE), ("传统", "文化", "艺术")),
    (re.compile(r"higher\s+education", re.IGNORECASE), ("高校", "高等教育")),
    (re.compile(r"teaching\s+for\s+enhanced\s+learning|student\s+does", re.IGNORECASE), ("学生", "学习", "教学", "对齐")),
    (re.compile(r"wushu|martial\s+arts?", re.IGNORECASE), ("武术", "拳种", "技击", "套路")),
    (re.compile(r"建设性对齐|对齐", re.IGNORECASE), ("constructive", "alignment", "teaching", "assessment")),
    (re.compile(r"课程群|课程", re.IGNORECASE), ("curriculum", "course")),
    (re.compile(r"武术|拳种|技击|套路", re.IGNORECASE), ("wushu", "martial", "arts")),
    (re.compile(r"国际化|留学生", re.IGNORECASE), ("internationalization", "international", "students")),
    (re.compile(r"智能建造", re.IGNORECASE), ("smart", "construction")),
)

_NEGATIVE_SUPPORT_LINE_RE = re.compile(
    r"无关|完全无关|不适用|do[_ -]?not[_ -]?cite|irrelevant|not\s+relevant",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class CitationOccurrence:
    key: str
    context: str
    position: int


def citation_alignment_issues(
    *,
    tex: str,
    bibtex: str,
    support_text_by_key: dict[str, str] | None = None,
    max_issues: int = 12,
) -> list[str]:
    """Return clear citation-context mismatch issues.

    The check is deliberately conservative:
    - missing/invalid keys are left to existing citation-key checks;
    - broad survey background sentences are not failed for low token overlap;
    - very generic BibTeX titles are ignored because they provide too little
      signal for deterministic alignment.
    """

    records = bib_records_by_key(bibtex)
    if not records:
        return []
    issues: list[str] = []
    for occ in iter_citation_occurrences(tex):
        record = records.get(occ.key)
        if not record:
            continue
        evidence_status = str(record.get("evidence_status") or "")
        context_plain = plain_latex_text(occ.context)
        if _ABSTRACT_ONLY_RE.search(evidence_status) and _STRONG_CLAIM_RE.search(context_plain) and not _EVIDENCE_BOUNDARY_RE.search(context_plain):
            issues.append(
                f"{occ.key}: abstract/metadata-only source appears to support a strong claim without evidence boundary; "
                f"context={_shorten(context_plain, 180)}"
            )
            if len(issues) >= max_issues:
                return issues

        title = str(record.get("title") or "")
        support_text = str((support_text_by_key or {}).get(occ.key) or "")
        support_source_text = " ".join(
            str(record.get(name) or "")
            for name in ("title", "keywords", "abstract", "note", "annotation", "journal", "booktitle")
        )
        source_tokens = _meaningful_tokens(support_source_text + "\n" + support_text)
        if _title_too_weak(source_tokens):
            continue
        context_tokens = _meaningful_tokens(context_plain)
        if not context_tokens:
            continue
        overlap = source_tokens & context_tokens
        if overlap and not _only_weak_overlap(overlap, context_plain):
            continue
        if _BROAD_CONTEXT_RE.search(context_plain) and not _STRONG_CLAIM_RE.search(context_plain):
            continue
        if len(context_tokens) < 4:
            continue
        issues.append(
            f"{occ.key}: citation context has no topical overlap with BibTeX title `{_shorten(title, 90)}`; "
            f"context={_shorten(context_plain, 180)}"
        )
        if len(issues) >= max_issues:
            return issues
    return issues


def citation_support_text_by_key(
    workspace: Path,
    *,
    keys: set[str] | None = None,
    literature_dir: str = "literature",
) -> dict[str, str]:
    """Load compact paper-note support text keyed by BibTeX key.

    The loader is best-effort and optional. It reads ``literature/citation_map.json``
    and the mapped paper note files so the deterministic audit can compare a
    citation context against the actual note summary, not only the BibTeX title.
    """

    root = workspace / literature_dir
    citation_map_path = root / "citation_map.json"
    if not citation_map_path.exists():
        return {}
    try:
        data = json.loads(citation_map_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    entries = data.get("entries") if isinstance(data, dict) else []
    if not isinstance(entries, list):
        return {}
    wanted = set(keys or [])
    support: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("bib_key") or "").strip()
        if not key or (wanted and key not in wanted):
            continue
        parts = [
            str(entry.get("title") or ""),
            str(entry.get("display_label") or ""),
            str(entry.get("evidence_level") or ""),
            " ".join(str(alias) for alias in entry.get("aliases") or [] if isinstance(alias, str))[:1000],
        ]
        source_file = str(entry.get("source_file") or "").strip()
        if source_file:
            note_path = root / source_file
            if note_path.exists() and note_path.is_file():
                try:
                    parts.append(_extract_note_alignment_text(note_path.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    pass
        text = "\n".join(part for part in parts if part).strip()
        if text:
            support[key] = text[:8000]
    return support


def bib_records_by_key(bibtex: str) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for entry in parse_bib_entries(bibtex or ""):
        key = str(entry.get("key") or "").strip()
        fields = entry.get("fields") if isinstance(entry.get("fields"), dict) else {}
        if not key:
            continue
        text_fields = {
            name: str(fields.get(name) or "").strip()
            for name in ("title", "keywords", "abstract", "note", "annotation", "journal", "booktitle")
        }
        records[key] = {
            **text_fields,
            "evidence_status": " ".join(
                text_fields.get(name, "") for name in ("note", "annotation") if text_fields.get(name)
            ),
        }
    return records


def iter_citation_occurrences(tex: str) -> list[CitationOccurrence]:
    occurrences: list[CitationOccurrence] = []
    for match in _LATEX_CITATION_CONTEXT_RE.finditer(tex or ""):
        context = _citation_context(tex, match.start(), match.end())
        for key in match.group(1).split(","):
            clean = key.strip()
            if clean:
                occurrences.append(CitationOccurrence(key=clean, context=context, position=match.start()))
    return occurrences


def plain_latex_text(text: str) -> str:
    text = re.sub(r"\\(?:cite|citep|citet|citealp|citealt|citeauthor|citeyear|parencite|textcite|autocite|footcite|supercite)\*?(?:\[[^\]]*\]){0,2}\{([^}]+)\}", " ", text or "", flags=re.IGNORECASE)
    text = re.sub(r"\\(?:section|subsection|subsubsection|paragraph)\*?\{([^{}]*)\}", r" \1 ", text)
    text = _LATEX_COMMAND_RE.sub(" ", text)
    text = re.sub(r"[{}$^_~%&#]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _citation_context(tex: str, start: int, end: int) -> str:
    left = max(tex.rfind(".", 0, start), tex.rfind("。", 0, start), tex.rfind(";", 0, start), tex.rfind("；", 0, start), tex.rfind("\n\n", 0, start))
    right_candidates = [idx for idx in (tex.find(".", end), tex.find("。", end), tex.find(";", end), tex.find("；", end), tex.find("\n\n", end)) if idx >= 0]
    right = min(right_candidates) if right_candidates else min(len(tex), end + 280)
    context = tex[left + 1 : right + 1].strip()
    if len(context) < 80:
        context = tex[max(0, start - 160) : min(len(tex), end + 160)].strip()
    return context


def _meaningful_tokens(text: str) -> set[str]:
    raw_text = str(text or "")
    tokens = _basic_meaningful_tokens(raw_text)
    for pattern, aliases in _TOKEN_ALIAS_RULES:
        if pattern.search(raw_text):
            for alias in aliases:
                tokens.update(_basic_meaningful_tokens(alias))
    return tokens


def _basic_meaningful_tokens(text: str) -> set[str]:
    text = plain_latex_text(str(text or "")).casefold()
    tokens: set[str] = set()
    for token in _LATIN_TOKEN_RE.findall(text):
        token = token.strip("-")
        if len(token) < 4 or token in _LATIN_STOPWORDS or token.isdigit():
            continue
        if token.endswith("s") and len(token) > 5:
            token = token[:-1]
        tokens.add(token)
    for token in _CJK_TOKEN_RE.findall(text):
        if token in _CJK_STOPWORDS:
            continue
        tokens.add(token)
        if len(token) >= 4:
            for idx in range(0, len(token) - 1):
                gram = token[idx : idx + 2]
                if gram not in _CJK_STOPWORDS:
                    tokens.add(gram)
    return tokens


def _extract_note_alignment_text(note: str) -> str:
    lines: list[str] = []
    keep_next = False
    for raw_line in (note or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line.strip())
        if not line:
            continue
        if _NEGATIVE_SUPPORT_LINE_RE.search(line):
            continue
        if line.startswith("# "):
            lines.append(line[:240])
            continue
        if re.match(
            r"^##\s+(?:1\.|2\.|3\.|4\.|A\.|B\.|13\.|14\.|15\.)",
            line,
            flags=re.IGNORECASE,
        ):
            keep_next = True
            lines.append(line[:180])
            continue
        if line.startswith("## "):
            keep_next = False
            continue
        if keep_next or line.startswith("- **Citation Quality Rationale**"):
            lines.append(line[:420])
        if len("\n".join(lines)) >= 6000:
            break
    return "\n".join(lines)


def _title_too_weak(tokens: set[str]) -> bool:
    if len(tokens) < 2:
        return True
    return bool(tokens) and all(token in _WEAK_BIB_TITLE_WORDS for token in tokens)


def _only_weak_overlap(overlap: set[str], context: str) -> bool:
    if not overlap:
        return False
    if not _STRONG_CLAIM_RE.search(context):
        return False
    return all(token in _WEAK_ALIGNMENT_TOKENS for token in overlap)


def _shorten(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1] + "…"
