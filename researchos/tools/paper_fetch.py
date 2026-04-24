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
from urllib.parse import quote

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
            if abs_path.exists() and abs_path.stat().st_size > 0:
                suffix = abs_path.suffix.lower()
                if suffix in {".csv", ".bib", ".jsonl", ".md"}:
                    existing_tail = abs_path.read_text(encoding="utf-8")[-1:]
                    if existing_tail and existing_tail != "\n" and content and not content.startswith("\n"):
                        content = "\n" + content
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

        httpx_mod = httpx
        if httpx_mod is None:
            return ToolResult(
                ok=False,
                content="缺少 httpx 依赖，无法下载PDF。",
                error="dependency_missing",
            )

        try:
            async with httpx_mod.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                pdf_candidates = await self._resolve_pdf_candidates(client, params.paper_id)
                if not pdf_candidates:
                    return ToolResult(
                        ok=False,
                        content=f"Unsupported or unresolved paper ID format: {params.paper_id}",
                        error="unsupported_id",
                    )

                last_error = None
                response = None
                pdf_url = None
                for candidate in pdf_candidates:
                    try:
                        response = await client.get(candidate)
                        response.raise_for_status()
                        if not self._looks_like_pdf(response, candidate):
                            last_error = f"URL did not return a PDF: {candidate}"
                            continue
                        abs_path.write_bytes(response.content)
                        pdf_url = candidate
                        break
                    except Exception as exc:  # pragma: no cover - 具体分支由 ToolResult 兜底
                        last_error = str(exc)
                        continue

                if response is None or pdf_url is None:
                    return ToolResult(
                        ok=False,
                        content=(
                            f"Failed to download PDF for {params.paper_id}. "
                            f"Tried {len(pdf_candidates)} candidate URLs. Last error: {last_error}"
                        ),
                        error="download_failed",
                    )

            return ToolResult(
                ok=True,
                content=f"Downloaded PDF to {params.save_path} ({len(response.content)} bytes)",
                data={
                    "path": params.save_path,
                    "size": len(response.content),
                    "url": pdf_url,
                    "candidates_tried": pdf_candidates,
                },
            )
        except Exception as exc:
            if httpx is not None and isinstance(exc, httpx.HTTPError):
                return ToolResult(
                    ok=False,
                    content=f"Failed to download PDF: {exc}",
                    error="download_failed",
                )
            raise ToolRuntimeError(self.name, exc) from exc

    async def _resolve_pdf_candidates(
        self,
        client: "httpx.AsyncClient",
        paper_id: str,
    ) -> list[str]:
        """根据 paper_id 推断一组可能的 PDF URL。"""

        paper_id = paper_id.strip()
        candidates: list[str] = []

        # arXiv格式: arxiv:2301.12345 或 2301.12345
        if paper_id.startswith("arxiv:"):
            arxiv_id = paper_id[6:]
            candidates.extend(self._arxiv_pdf_candidates(arxiv_id))
        elif self._looks_like_arxiv_id(paper_id):
            candidates.extend(self._arxiv_pdf_candidates(paper_id))

        # DOI / OpenAlex work id：优先从 OpenAlex 补开放获取位置
        if paper_id.startswith("10."):
            candidates.extend(await self._openalex_pdf_candidates(client, doi=paper_id))
            candidates.extend(self._doi_fallback_candidates(paper_id))
        elif paper_id.startswith("W") and paper_id[1:].isdigit():
            candidates.extend(await self._openalex_pdf_candidates(client, openalex_id=paper_id))

        # 有些上游记录会把 DOI URL 或 arXiv abs URL 直接放进 id
        if paper_id.startswith("http://") or paper_id.startswith("https://"):
            candidates.extend(self._url_to_pdf_candidates(paper_id))

        return self._dedupe_candidates(candidates)

    @staticmethod
    def _looks_like_arxiv_id(paper_id: str) -> bool:
        normalized = paper_id.strip().replace("arxiv:", "")
        return "." in normalized and normalized.replace(".", "").replace("v", "").isdigit()

    @staticmethod
    def _arxiv_pdf_candidates(arxiv_id: str) -> list[str]:
        return [
            f"https://arxiv.org/pdf/{arxiv_id}.pdf",
            f"https://export.arxiv.org/pdf/{arxiv_id}.pdf",
        ]

    @staticmethod
    def _doi_fallback_candidates(doi: str) -> list[str]:
        return [
            f"https://doi.org/{doi}",
            f"https://dx.doi.org/{doi}",
        ]

    @classmethod
    def _url_to_pdf_candidates(cls, url: str) -> list[str]:
        candidates = [url]
        if "arxiv.org/abs/" in url:
            candidates.append(url.replace("/abs/", "/pdf/") + ".pdf")
        if "arxiv.org/html/" in url:
            candidates.append(url.replace("/html/", "/pdf/") + ".pdf")
        return candidates

    async def _openalex_pdf_candidates(
        self,
        client: "httpx.AsyncClient",
        *,
        doi: str | None = None,
        openalex_id: str | None = None,
    ) -> list[str]:
        """从 OpenAlex work 详情里抽取开放获取 PDF 链接。"""

        if doi is None and openalex_id is None:
            return []

        if doi is not None:
            identifier = f"https://doi.org/{doi}"
        else:
            identifier = openalex_id or ""

        url = f"https://api.openalex.org/works/{quote(identifier, safe=':/')}"
        params = {"mailto": os.environ.get("RESEARCHER_EMAIL", "researcher@example.com")}

        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
        except Exception:
            return []

        work = response.json()
        candidates: list[str] = []

        def add_location(location: dict[str, Any] | None) -> None:
            if not isinstance(location, dict):
                return
            pdf_url = location.get("pdf_url")
            landing_page_url = location.get("landing_page_url")
            if isinstance(pdf_url, str) and pdf_url.strip():
                candidates.append(pdf_url.strip())
            if isinstance(landing_page_url, str) and landing_page_url.strip():
                candidates.extend(self._url_to_pdf_candidates(landing_page_url.strip()))

        add_location(work.get("best_oa_location"))
        add_location(work.get("primary_location"))
        for location in work.get("locations", []) or []:
            add_location(location)

        doi_value = work.get("doi")
        if isinstance(doi_value, str) and doi_value.startswith("https://doi.org/"):
            candidates.append(doi_value)

        return candidates

    @staticmethod
    def _looks_like_pdf(response: "httpx.Response", url: str) -> bool:
        content_type = response.headers.get("content-type", "").lower()
        if "application/pdf" in content_type:
            return True
        if url.lower().endswith(".pdf"):
            return True
        content = response.content[:5]
        return content.startswith(b"%PDF-")

    @staticmethod
    def _dedupe_candidates(candidates: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            normalized = candidate.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out


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
                content="缺少 pdfplumber 依赖，无法解析 PDF。请安装 requirements.txt 中的依赖后重试。",
                error="dependency_missing",
            )
        except Exception as exc:
            raise ToolRuntimeError(self.name, exc) from exc
