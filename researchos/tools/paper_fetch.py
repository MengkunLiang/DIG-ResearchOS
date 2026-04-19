from __future__ import annotations

"""论文PDF获取和文本提取工具。

提供三个工具：
1. append_file - 追加内容到文件
2. fetch_paper_pdf - 下载论文PDF
3. extract_pdf_text - 提取PDF全文文本
"""

import os
from pathlib import Path
from typing import Any

try:
    import httpx
except ModuleNotFoundError:
    httpx = None

from pydantic import BaseModel, Field

from ..runtime.errors import ToolAccessDenied, ToolRuntimeError
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy


class AppendFileParams(BaseModel):
    path: str = Field(..., description="相对 workspace 的路径")
    content: str = Field(..., description="要追加的文本内容")


class AppendFileTool(Tool):
    """追加内容到文件末尾。"""

    name = "append_file"
    description = "追加 UTF-8 文本内容到 workspace 中的文件末尾"
    parameters_schema = AppendFileParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs) -> ToolResult:
        path = kwargs["path"]
        content = kwargs["content"]
        try:
            abs_path = self.policy.resolve_write(path)
            # 确保父目录存在
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            # 追加模式写入
            with abs_path.open("a", encoding="utf-8") as f:
                f.write(content)
            return ToolResult(
                ok=True,
                content=f"Appended {len(content)} chars to {path}",
                data={"path": path, "bytes": len(content.encode('utf-8'))},
            )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except OSError as exc:
            raise ToolRuntimeError("append_file", exc) from exc


class FetchPaperPdfParams(BaseModel):
    paper_id: str = Field(..., description="论文ID，如 arxiv:2301.12345 或 doi:10.1234/...")
    save_path: str = Field(..., description="保存PDF的相对路径")


class FetchPaperPdfTool(Tool):
    """下载论文PDF到workspace。"""

    name = "fetch_paper_pdf"
    description = "下载论文PDF到workspace。支持arXiv ID和部分DOI。"
    parameters_schema = FetchPaperPdfParams
    timeout_seconds = 120.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = FetchPaperPdfParams(**kwargs)

        try:
            abs_path = self.policy.resolve_write(params.save_path)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")

        # 确保父目录存在
        abs_path.parent.mkdir(parents=True, exist_ok=True)

        # 构造下载URL
        pdf_url = self._get_pdf_url(params.paper_id)
        if not pdf_url:
            return ToolResult(
                ok=False,
                content=f"Unsupported paper ID format: {params.paper_id}",
                error="unsupported_id",
            )

        try:
            if httpx is None:
                raise ModuleNotFoundError("httpx")

            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                response = await client.get(pdf_url)
                response.raise_for_status()

                # 写入PDF文件
                abs_path.write_bytes(response.content)

            return ToolResult(
                ok=True,
                content=f"Downloaded PDF to {params.save_path} ({len(response.content)} bytes)",
                data={"path": params.save_path, "size": len(response.content), "url": pdf_url},
            )
        except ModuleNotFoundError:
            return ToolResult(
                ok=False,
                content="缺少 httpx 依赖，无法下载PDF。",
                error="dependency_missing",
            )
        except Exception as exc:
            if httpx is not None and isinstance(exc, httpx.HTTPError):
                return ToolResult(
                    ok=False,
                    content=f"Failed to download PDF: {exc}",
                    error="download_failed",
                )
            raise ToolRuntimeError(self.name, exc) from exc

    @staticmethod
    def _get_pdf_url(paper_id: str) -> str | None:
        """根据paper_id构造PDF下载URL。"""
        paper_id = paper_id.strip()

        # arXiv格式: arxiv:2301.12345 或 2301.12345
        if paper_id.startswith("arxiv:"):
            arxiv_id = paper_id[6:]
            return f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        elif "." in paper_id and paper_id.replace(".", "").replace("v", "").isdigit():
            # 看起来像arXiv ID
            return f"https://arxiv.org/pdf/{paper_id}.pdf"

        # 其他格式暂不支持
        return None


class ExtractPdfTextParams(BaseModel):
    pdf_path: str = Field(..., description="相对 workspace 的 PDF 路径")


class ExtractPdfTextTool(Tool):
    """提取PDF全文文本。"""

    name = "extract_pdf_text"
    description = "提取PDF文件的全文文本内容"
    parameters_schema = ExtractPdfTextParams
    timeout_seconds = 60.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = ExtractPdfTextParams(**kwargs)

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
            # 延迟导入pdfplumber
            import importlib
            pdfplumber = importlib.import_module("pdfplumber")

            # 提取全文
            text_parts = []
            with pdfplumber.open(abs_path) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        text_parts.append(f"--- Page {page_num} ---\n{page_text}")

            full_text = "\n\n".join(text_parts)

            # 限制返回给LLM的文本长度
            MAX_CHARS = 50000
            content_preview = full_text[:MAX_CHARS]
            if len(full_text) > MAX_CHARS:
                content_preview += f"\n\n[... truncated, full length: {len(full_text)} chars]"

            return ToolResult(
                ok=True,
                content=content_preview,
                data={
                    "pdf": params.pdf_path,
                    "full_text": full_text,
                    "length": len(full_text),
                    "pages": len(text_parts),
                },
            )
        except ModuleNotFoundError:
            return ToolResult(
                ok=False,
                content="缺少 pdfplumber 依赖，无法解析 PDF。",
                error="dependency_missing",
            )
        except Exception as exc:
            raise ToolRuntimeError(self.name, exc) from exc
