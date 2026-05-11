from __future__ import annotations

"""论文 PDF 处理工具。

当前文件先实现 `extract_paper_sections` 这一项 runtime 能力，供后续 Reader 类 agent
按 section 粒度读取论文正文，避免把整篇 PDF 文本一次性塞给模型。

实现约束：
1. 只能读取 workspace 内相对路径指向的 PDF；
2. 运行时延迟导入 `pdfplumber`，避免把测试建立在真实 PDF 重依赖之上；
3. section 识别采用“足够稳妥”的启发式，而不是试图做完整版版面分析；
4. 返回给 LLM 的 `content` 做总量截断和格式化，`data.sections` 也只保留有界预览，
   避免 trace / 后续上下文被整篇 PDF 正文撑爆。
"""

import importlib
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..runtime.errors import ToolAccessDenied, ToolRuntimeError
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy


# T3 Reader 默认只需要这些核心章节。不要在 sections=None 时把 references /
# appendix 等长尾内容全部返回，否则多篇论文精读会稳定触发上下文爆炸。
DEFAULT_TARGET_SECTIONS = [
    "abstract",
    "introduction",
    "related work",
    "method",
    "results",
    "discussion",
    "conclusion",
    "limitations",
]

# section 输出给 LLM 时的硬上限。单 section 和总量都要限制；只限制单 section
# 不够，因为 PDF 解析器可能把一篇论文切成几十个 section。
MAX_CONTENT_CHARS_PER_SECTION = 2500
MAX_CONTENT_CHARS_TOTAL = 12000

# `ToolResult.data` 会进入 trace metadata。它虽然不直接发送给 LLM，但体积过大
# 会拖慢 trace 读写与问题排查，因此也只保存预览和长度信息。
MAX_DATA_CHARS_PER_SECTION = 3000
MAX_DATA_CHARS_TOTAL = 18000

# section 标题常见别名。这里不是为了“强制规范化”为某一个固定标签，
# 而是为了让 wanted sections 过滤更稳一些，例如用户要求 `method` 时，
# 也能匹配到 `methodology` / `materials and methods`。
SECTION_ALIAS_GROUPS: dict[str, set[str]] = {
    "abstract": {"abstract", "summary"},
    "introduction": {"introduction", "intro", "background"},
    "related work": {"related work", "prior work", "literature review"},
    "method": {"method", "methods", "methodology", "approach", "materials and methods"},
    "results": {
        "result",
        "results",
        "findings",
        "evaluation",
        "experiments",
        "experimental results",
    },
    "discussion": {"discussion", "analysis"},
    "conclusion": {"conclusion", "conclusions", "future work", "concluding remarks"},
}

# 标题识别时优先信任的高频 header 关键词。用于识别 Title Case 短行。
COMMON_SECTION_HEADERS = {
    alias
    for aliases in SECTION_ALIAS_GROUPS.values()
    for alias in aliases
}.union(
    {
        "preliminaries",
        "problem formulation",
        "dataset",
        "datasets",
        "experiments and results",
        "implementation details",
        "limitations",
        "appendix",
        "references",
    }
)

FALLBACK_SECTION_PATTERNS: dict[str, list[str]] = {
    "abstract": [r"abstract"],
    "introduction": [r"introduction", r"intro"],
    "related work": [r"related\s+work", r"prior\s+work", r"literature\s+review"],
    "method": [r"method", r"methods", r"methodology", r"approach", r"materials\s+and\s+methods"],
    "results": [r"results?", r"findings", r"evaluation", r"experiments?", r"experimental\s+results"],
    "discussion": [r"discussion", r"analysis"],
    "conclusion": [r"conclusions?", r"future\s+work", r"concluding\s+remarks"],
}


class ExtractSectionsParams(BaseModel):
    """`extract_paper_sections` 的参数定义。"""

    pdf_path: str = Field(..., description="相对 workspace 的 PDF 路径")
    sections: list[str] | None = Field(
        None,
        description=(
            "要抽取的 section 名，如 ['introduction', 'method', 'results']。"
            "传 None 时使用 Reader 默认核心章节，不返回整篇论文所有 section。"
        ),
    )

    @field_validator("sections", mode="before")
    @classmethod
    def _coerce_sections(cls, value: object) -> object:
        """兼容模型把 JSON array 当字符串传进来的常见错误。"""

        if value is None:
            return None
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            if raw.startswith("["):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            return [part.strip() for part in re.split(r"[,;/\n]+", raw) if part.strip()]
        return value


class ExtractSectionsTool(Tool):
    """从 PDF 中抽取 section 文本。

    这里不尝试实现复杂版面恢复，只基于逐页 `extract_text()` 的行文本做切分。
    对 ResearchOS 当前 runtime 的目标来说，这个粒度已经足以支撑 Reader agent：
    - 先按标题切到 section；
    - 再把目标 section 的正文送给 LLM；
    - 避免整篇论文引起 context 膨胀。
    """

    name = "extract_paper_sections"
    description = (
        "从 PDF 抽取指定 section 的文本。"
        "底层使用 pdfplumber 和启发式标题识别。"
    )
    parameters_schema = ExtractSectionsParams
    timeout_seconds = 60.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行工具并把 PDF 内容切成多个 section。"""

        params = ExtractSectionsParams(**kwargs)
        try:
            abs_path = self.policy.resolve_read(params.pdf_path)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")

        if not abs_path.exists():
            return ToolResult(
                ok=False,
                content=f"PDF not found: {params.pdf_path}",
                error="not_found",
            )
        if abs_path.suffix.lower() != ".pdf":
            return ToolResult(
                ok=False,
                content=f"Path is not a PDF file: {params.pdf_path}",
                error="not_pdf",
            )

        try:
            wanted_sections = params.sections or list(DEFAULT_TARGET_SECTIONS)
            sections_out, quality = self._extract(abs_path, wanted_sections)
            quality["default_sections_used"] = params.sections is None
            quality["requested_sections"] = wanted_sections
        except ModuleNotFoundError:
            return ToolResult(
                ok=False,
                content="缺少 pdfplumber 依赖，无法解析 PDF。请安装 requirements.txt 中的依赖后重试。",
                error="dependency_missing",
            )
        except Exception as exc:  # pragma: no cover - 具体异常由 ToolRuntimeError 包装
            raise ToolRuntimeError(self.name, exc) from exc

        if not sections_out:
            return ToolResult(
                ok=True,
                content="未识别到符合条件的 section。",
                data={"sections": {}, "pdf": params.pdf_path, "quality": quality},
            )

        summary_line = ""
        if quality["recommend_full_text_fallback"]:
            summary_line = (
                "section 质量一般；请优先基于已返回的有界章节与 metadata 生成保守笔记。"
                "只有缺少关键信息时，才用 extract_pdf_text 并设置较小 max_chars。"
            )

        return ToolResult(
            ok=True,
            content=(
                f"{summary_line}\n\n{self._format_sections(sections_out)}"
                if summary_line
                else self._format_sections(sections_out)
            ),
            data={
                "sections": self._preview_sections_for_data(sections_out),
                "section_lengths": {name: len(text) for name, text in sections_out.items()},
                "pdf": params.pdf_path,
                "quality": quality,
            },
        )

    def _extract(self, pdf_path: Path, wanted: list[str] | None) -> tuple[dict[str, str], dict[str, Any]]:
        """读取 PDF 并按 section 分桶聚合文本。

        实现思路：
        1. 逐页读取纯文本；
        2. 逐行判断是否像 section header；
        3. 如果命中，则切换当前 section；
        4. 否则把文本归入当前 section。

        若在正文前还没识别到明确 section，则内容会暂存到 `preamble`，
        这样不会丢失标题页、摘要前的前置信息。
        """

        raw_lines = self._iter_pdf_lines(pdf_path)
        sections: dict[str, list[str]] = {}
        current_section = "preamble"

        for raw_line in raw_lines:
            stripped = raw_line.strip()
            if not stripped:
                # 空行保留为段落分隔符，但不参与 header 判断。
                if current_section in sections and sections[current_section]:
                    sections[current_section].append("")
                continue

            if self._is_section_header(stripped):
                current_section = self._normalize_section_name(stripped)
                sections.setdefault(current_section, [])
                # 标题本身不再重复塞进正文，避免格式化输出时出现两次 header。
                continue

            sections.setdefault(current_section, []).append(stripped)

        joined: dict[str, str] = {}
        for name, lines in sections.items():
            joined_text = self._join_section_lines(lines)
            if joined_text:
                joined[name] = joined_text
        fallback_used = False
        if wanted:
            filtered = {
                name: text
                for name, text in joined.items()
                if self._matches_wanted_section(name, wanted)
            }
            if self._needs_fallback(filtered, wanted):
                fallback = self._extract_from_full_text(raw_lines, wanted)
                filtered = {**fallback, **filtered} if fallback else filtered
                fallback_used = bool(fallback)
            if not filtered and joined:
                filtered = self._fallback_preview_sections(joined)
                fallback_used = True
            return filtered, self._build_quality_report(filtered, wanted, fallback_used)

        if self._needs_fallback(joined, None):
            fallback = self._extract_from_full_text(raw_lines, None)
            joined = {**fallback, **joined} if fallback else joined
            fallback_used = bool(fallback)
        return joined, self._build_quality_report(joined, wanted, fallback_used)

    def _iter_pdf_lines(self, pdf_path: Path) -> list[str]:
        """借助 pdfplumber 抽取逐行文本。

        单独拆出这个方法有两个目的：
        1. 让测试可以直接 monkeypatch，避免依赖真实 PDF 文件结构；
        2. 把“PDF I/O”和“section 切分”解耦，方便后续替换解析器。
        """

        pdfplumber = self._load_pdfplumber()
        lines: list[str] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                lines.extend(text.splitlines())
        return lines

    @staticmethod
    def _load_pdfplumber() -> Any:
        """延迟导入 `pdfplumber`，便于测试注入假的模块。"""

        return importlib.import_module("pdfplumber")

    @staticmethod
    def _join_section_lines(lines: list[str]) -> str:
        """把同一个 section 的行文本恢复成较可读的段落。"""

        # 连续空行只保留一个，避免格式化结果里出现大段空白。
        compact_lines: list[str] = []
        last_blank = False
        for line in lines:
            is_blank = line == ""
            if is_blank and last_blank:
                continue
            compact_lines.append(line)
            last_blank = is_blank
        return "\n".join(compact_lines).strip()

    @classmethod
    def _is_section_header(cls, line: str) -> bool:
        """判断一行文本是否像论文 section header。

        启发式尽量偏保守，避免把普通短句错判成标题。主要覆盖三类模式：
        1. 编号标题：`1 Introduction`、`2. Methodology`、`III Results`
        2. 全大写短行：`ABSTRACT`
        3. 常见标题词的短行：`Related Work`、`Conclusion`
        """

        normalized = cls._normalize_section_name(line)
        if len(normalized) < 2:
            return False

        # 过长的句子大概率不是标题；标题后带句号/问号也通常不是 section。
        if len(line) > 120 or line.endswith((".", "?", "!", ";")):
            return False

        token_count = len(normalized.split())
        if token_count > 8:
            return False

        if not re.search(r"[a-zA-Z]{3,}", normalized):
            return False

        has_numbering_prefix = bool(
            re.match(r"^(?:\d+(?:\.\d+)*|[IVXLCM]+)[\.\)]?\s+\S", line, flags=re.IGNORECASE)
        )
        if has_numbering_prefix and (
            normalized in COMMON_SECTION_HEADERS
            or any(token in COMMON_SECTION_HEADERS for token in normalized.split())
            or token_count <= 4
        ):
            return True

        # 全大写短行是 PDF 文本中最常见的标题模式之一。
        if line.isupper() and token_count <= 6:
            return True

        # 对大小写正常的短行，只接受“常见标题词”集合，减少误判正文短句。
        return normalized in COMMON_SECTION_HEADERS

    @staticmethod
    def _normalize_section_name(line: str) -> str:
        """把 header 文本规整成稳定的 section 名。

        处理内容包括：
        - 去掉编号前缀，如 `1.`, `2`, `III`
        - 去掉首尾标点
        - 统一小写和空白
        """

        cleaned = line.strip()
        cleaned = re.sub(
            r"^(?:section\s+)?(?:\d+(?:\.\d+)*|[IVXLCM]+)[\.\)]?\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"^[\-\*\#\s]+|[\s:：\-]+$", "", cleaned)
        cleaned = re.sub(r"[^\w\s&/-]+", " ", cleaned)
        cleaned = cleaned.replace("_", " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
        return cleaned

    @classmethod
    def _matches_wanted_section(cls, detected_name: str, wanted_sections: list[str]) -> bool:
        """判断某个检测到的 section 是否满足 wanted 过滤条件。"""

        detected_variants = cls._section_variants(detected_name)
        for wanted in wanted_sections:
            wanted_normalized = cls._normalize_section_name(wanted)
            if not wanted_normalized:
                continue
            wanted_variants = cls._section_variants(wanted_normalized)
            if detected_variants & wanted_variants:
                return True
            # 再补一个宽松的 substring 匹配，兼容 `method` 命中 `proposed method`。
            if any(
                wanted_variant in detected_variant or detected_variant in wanted_variant
                for wanted_variant in wanted_variants
                for detected_variant in detected_variants
            ):
                return True
        return False

    @staticmethod
    def _section_variants(name: str) -> set[str]:
        """给 section 名生成一组用于匹配的别名。"""

        normalized = ExtractSectionsTool._normalize_section_name(name)
        variants = {normalized}

        for canonical, aliases in SECTION_ALIAS_GROUPS.items():
            normalized_aliases = {ExtractSectionsTool._normalize_section_name(item) for item in aliases}
            canonical_normalized = ExtractSectionsTool._normalize_section_name(canonical)
            if normalized == canonical_normalized or normalized in normalized_aliases:
                variants.add(canonical_normalized)
                variants.update(normalized_aliases)

        return {item for item in variants if item}

    @staticmethod
    def _format_sections(sections: dict[str, str]) -> str:
        """把 section 字典格式化成适合直接回给 LLM 的文本。"""

        blocks: list[str] = []
        remaining = MAX_CONTENT_CHARS_TOTAL
        omitted_count = 0
        for name, text in sections.items():
            if remaining <= 0:
                omitted_count += 1
                continue
            section_limit = min(MAX_CONTENT_CHARS_PER_SECTION, remaining)
            clipped = text[:section_limit]
            block_lines = [f"## {name}", "", clipped]
            if len(text) > section_limit:
                block_lines.extend(
                    [
                        "",
                        (
                            f"[... truncated, full length: {len(text)} chars, "
                            f"shown: {section_limit}]"
                        ),
                    ]
                )
            block = "\n".join(block_lines).strip()
            blocks.append(block)
            remaining -= len(clipped)
        if omitted_count:
            blocks.append(f"[Runtime] Omitted {omitted_count} additional sections due to output cap.")
        return "\n\n---\n\n".join(blocks)

    @staticmethod
    def _preview_sections_for_data(sections: dict[str, str]) -> dict[str, str]:
        """给 trace metadata 使用的有界 section 预览。"""

        preview: dict[str, str] = {}
        remaining = MAX_DATA_CHARS_TOTAL
        for name, text in sections.items():
            if remaining <= 0:
                break
            limit = min(MAX_DATA_CHARS_PER_SECTION, remaining)
            clipped = text[:limit]
            if len(text) > limit:
                clipped += (
                    f"\n\n[... truncated for trace metadata, full length: {len(text)} chars]"
                )
            preview[name] = clipped
            remaining -= len(clipped)
        omitted_count = len(sections) - len(preview)
        if omitted_count > 0:
            preview["_omitted"] = f"{omitted_count} sections omitted due to trace metadata cap"
        return preview

    @staticmethod
    def _fallback_preview_sections(sections: dict[str, str]) -> dict[str, str]:
        """没有命中目标章节时，返回前几个非 references 的 section 作为保守预览。"""

        out: dict[str, str] = {}
        excluded = {"references", "bibliography", "acknowledgments", "acknowledgements"}
        for name, text in sections.items():
            if name in excluded:
                continue
            out[name] = text
            if len(out) >= 4:
                break
        return out

    @classmethod
    def _build_quality_report(
        cls,
        sections: dict[str, str],
        wanted: list[str] | None,
        fallback_used: bool,
    ) -> dict[str, Any]:
        suspicious_names = [
            name
            for name in sections
            if name != "preamble"
            and (
                len(cls._normalize_section_name(name)) < 3
                or not re.search(r"[a-zA-Z]{3,}", cls._normalize_section_name(name))
            )
        ]
        matched_wanted = []
        if wanted:
            matched_wanted = [
                name for name in sections if cls._matches_wanted_section(name, wanted)
            ]

        recommend_full_text_fallback = bool(
            suspicious_names
            or (wanted and len(set(matched_wanted)) < min(3, len(wanted)))
            or any(len(text) < 200 for text in sections.values())
        )

        return {
            "fallback_used": fallback_used,
            "suspicious_section_names": suspicious_names,
            "matched_wanted_count": len(set(matched_wanted)) if wanted else len(sections),
            "section_count": len(sections),
            "recommend_full_text_fallback": recommend_full_text_fallback,
        }

    @classmethod
    def _needs_fallback(cls, sections: dict[str, str], wanted: list[str] | None) -> bool:
        if not sections:
            return True
        noisy_names = {
            name for name in sections
            if name != "preamble" and not re.search(r"[a-zA-Z]{3,}", cls._normalize_section_name(name))
        }
        if noisy_names:
            return True
        if wanted:
            matched = {
                name for name in sections
                if cls._matches_wanted_section(name, wanted)
            }
            return len(matched) < min(2, len(wanted))
        non_preamble = [name for name in sections if name != "preamble"]
        return len(non_preamble) < 2

    @classmethod
    def _extract_from_full_text(
        cls,
        lines: list[str],
        wanted: list[str] | None,
    ) -> dict[str, str]:
        text = "\n".join(lines)
        if not text.strip():
            return {}

        target_names = list(FALLBACK_SECTION_PATTERNS.keys())
        if wanted:
            normalized_wanted = [cls._normalize_section_name(item) for item in wanted]
            selected: list[str] = []
            for canonical in target_names:
                variants = cls._section_variants(canonical)
                if any(any(w in variant or variant in w for variant in variants) for w in normalized_wanted if w):
                    selected.append(canonical)
            target_names = selected or target_names

        ordered_matches: list[tuple[int, int, str]] = []
        search_pos = 0
        for canonical in target_names:
            best_match: tuple[int, int, str] | None = None
            for pattern in FALLBACK_SECTION_PATTERNS[canonical]:
                regex = re.compile(
                    rf"(?is)(?<![A-Za-z])(?:section\s+)?(?:\d+(?:\.\d+)*|[IVXLCM]+)?[\.\)]?\s*({pattern})(?![A-Za-z])"
                )
                match = regex.search(text, pos=search_pos)
                if not match:
                    continue
                candidate = (match.start(), match.end(), canonical)
                if best_match is None or candidate[0] < best_match[0]:
                    best_match = candidate
            if best_match is None:
                continue
            ordered_matches.append(best_match)
            search_pos = best_match[1]

        if not ordered_matches:
            return {}

        sections: dict[str, str] = {}
        for idx, (start, end, canonical) in enumerate(ordered_matches):
            next_start = ordered_matches[idx + 1][0] if idx + 1 < len(ordered_matches) else len(text)
            body = text[end:next_start].strip(" \n:-")
            if not body:
                continue
            sections[canonical] = body

        if wanted:
            return {
                name: value for name, value in sections.items()
                if cls._matches_wanted_section(name, wanted)
            }
        return sections


async def extract_paper_sections(
    policy: WorkspaceAccessPolicy,
    pdf_path: str,
    sections: list[str] | None = None,
) -> ToolResult:
    """便捷函数：直接执行一次 section 抽取。"""

    return await ExtractSectionsTool(policy).execute(pdf_path=pdf_path, sections=sections)
