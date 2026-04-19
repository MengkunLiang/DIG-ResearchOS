from __future__ import annotations

"""论文 PDF 处理工具。

当前文件先实现 `extract_paper_sections` 这一项 runtime 能力，供后续 Reader 类 agent
按 section 粒度读取论文正文，避免把整篇 PDF 文本一次性塞给模型。

实现约束：
1. 只能读取 workspace 内相对路径指向的 PDF；
2. 运行时延迟导入 `pdfplumber`，避免把测试建立在真实 PDF 重依赖之上；
3. section 识别采用“足够稳妥”的启发式，而不是试图做完整版版面分析；
4. 返回给 LLM 的 `content` 做截断和格式化，`data.sections` 保留结构化 section 文本。
"""

import importlib
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..runtime.errors import ToolAccessDenied, ToolRuntimeError
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy


# section 输出给 LLM 时，每个 section 最多暴露多少字符。
# 这里控制的是 `ToolResult.content`，结构化的 `data["sections"]` 仍保留完整文本，
# 方便后续 agent 做二次裁剪或写入 artifact。
MAX_CONTENT_CHARS_PER_SECTION = 3000

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


class ExtractSectionsParams(BaseModel):
    """`extract_paper_sections` 的参数定义。"""

    pdf_path: str = Field(..., description="相对 workspace 的 PDF 路径")
    sections: list[str] | None = Field(
        None,
        description=(
            "要抽取的 section 名，如 ['introduction', 'method', 'results']。"
            "传 None 时返回所有识别到的 section。"
        ),
    )


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
            sections_out = self._extract(abs_path, params.sections)
        except ModuleNotFoundError:
            return ToolResult(
                ok=False,
                content="缺少 pdfplumber 依赖，无法解析 PDF。",
                error="dependency_missing",
            )
        except Exception as exc:  # pragma: no cover - 具体异常由 ToolRuntimeError 包装
            raise ToolRuntimeError(self.name, exc) from exc

        if not sections_out:
            return ToolResult(
                ok=True,
                content="未识别到符合条件的 section。",
                data={"sections": {}, "pdf": params.pdf_path},
            )

        return ToolResult(
            ok=True,
            content=self._format_sections(sections_out),
            data={"sections": sections_out, "pdf": params.pdf_path},
        )

    def _extract(self, pdf_path: Path, wanted: list[str] | None) -> dict[str, str]:
        """读取 PDF 并按 section 分桶聚合文本。

        实现思路：
        1. 逐页读取纯文本；
        2. 逐行判断是否像 section header；
        3. 如果命中，则切换当前 section；
        4. 否则把文本归入当前 section。

        若在正文前还没识别到明确 section，则内容会暂存到 `preamble`，
        这样不会丢失标题页、摘要前的前置信息。
        """

        sections: dict[str, list[str]] = {}
        current_section = "preamble"

        for raw_line in self._iter_pdf_lines(pdf_path):
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
        if wanted:
            return {
                name: text
                for name, text in joined.items()
                if self._matches_wanted_section(name, wanted)
            }
        return joined

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

        has_numbering_prefix = bool(
            re.match(r"^(?:\d+(?:\.\d+)*|[IVXLCM]+)[\.\)]?\s+\S", line, flags=re.IGNORECASE)
        )
        if has_numbering_prefix:
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
        for name, text in sections.items():
            clipped = text[:MAX_CONTENT_CHARS_PER_SECTION]
            block_lines = [f"## {name}", "", clipped]
            if len(text) > MAX_CONTENT_CHARS_PER_SECTION:
                block_lines.extend(
                    [
                        "",
                        (
                            f"[... truncated, full length: {len(text)} chars, "
                            f"shown: {MAX_CONTENT_CHARS_PER_SECTION}]"
                        ),
                    ]
                )
            blocks.append("\n".join(block_lines).strip())
        return "\n\n---\n\n".join(blocks)


async def extract_paper_sections(
    policy: WorkspaceAccessPolicy,
    pdf_path: str,
    sections: list[str] | None = None,
) -> ToolResult:
    """便捷函数：直接执行一次 section 抽取。"""

    return await ExtractSectionsTool(policy).execute(pdf_path=pdf_path, sections=sections)
