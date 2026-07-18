from __future__ import annotations

"""论文PDF获取和文本提取工具。

提供三个工具：
1. append_file - 追加内容到文件
2. fetch_paper_pdf - 下载论文PDF
3. extract_pdf_text - 提取PDF全文文本
"""

import os
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

try:
    import httpx
except ModuleNotFoundError:
    httpx = None

from pydantic import BaseModel, Field

from ..literature_identity import add_identity_key_variants, paper_record_match_keys
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
    description = "下载论文PDF到workspace。支持arXiv ID、doi:10...、裸DOI和部分开放获取DOI。"
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
            timeout = httpx_mod.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
            headers = {
                "User-Agent": (
                    "ResearchOS/0.1 "
                    "(mailto:%s)" % os.environ.get("RESEARCHER_EMAIL", "researcher@example.com")
                ),
                "Accept": "application/pdf,text/html;q=0.8,*/*;q=0.5",
            }
            async with httpx_mod.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
                pdf_candidates = await self._resolve_pdf_candidates(client, params.paper_id)
                if not pdf_candidates:
                    return ToolResult(
                        ok=False,
                        content=f"Unsupported or unresolved paper ID format: {params.paper_id}",
                        error="unsupported_id",
                    )

                response = None
                pdf_url = None
                candidate_errors: list[dict[str, Any]] = []
                discovered_candidates: list[str] = []
                for candidate in pdf_candidates:
                    try:
                        response = await client.get(candidate)
                    except httpx_mod.TimeoutException as exc:
                        candidate_errors.append(
                            {"url": candidate, "error": "timeout", "detail": str(exc)}
                        )
                        continue
                    except Exception as exc:  # pragma: no cover - 具体网络异常由 ToolResult 兜底
                        candidate_errors.append(
                            {"url": candidate, "error": type(exc).__name__, "detail": str(exc)}
                        )
                        continue

                    try:
                        response.raise_for_status()
                    except Exception as exc:  # pragma: no cover - 状态码分支由 ToolResult 兜底
                        status_code = getattr(response, "status_code", None)
                        candidate_errors.append(
                            {
                                "url": candidate,
                                "error": f"http_{status_code}" if status_code else "http_error",
                                "detail": str(exc),
                            }
                        )
                        continue

                    if not self._looks_like_pdf(response, candidate):
                        html_candidates = self._extract_pdf_links_from_html(response, candidate)
                        new_candidates = [
                            item
                            for item in html_candidates
                            if item not in pdf_candidates and item not in discovered_candidates
                        ]
                        if new_candidates:
                            discovered_candidates.extend(new_candidates)
                            pdf_candidates.extend(new_candidates[:5])
                        candidate_errors.append(
                            {
                                "url": candidate,
                                "error": "not_pdf",
                                "status_code": getattr(response, "status_code", None),
                                "content_type": response.headers.get("content-type", ""),
                                "discovered_pdf_links": new_candidates[:5],
                            }
                        )
                        continue
                    abs_path.write_bytes(response.content)
                    pdf_url = candidate
                    break

                if response is None or pdf_url is None:
                    self._record_fetch_attempt(
                        params.paper_id,
                        params.save_path,
                        ok=False,
                        candidates=pdf_candidates,
                        candidate_errors=candidate_errors,
                    )
                    error_summary = self._format_candidate_errors(candidate_errors)
                    return ToolResult(
                        ok=False,
                        content=(
                            f"Failed to download PDF for {params.paper_id}. "
                            f"Tried {len(pdf_candidates)} candidate URLs. {error_summary}"
                        ),
                        error="download_failed",
                        data={
                            "paper_id": params.paper_id,
                            "path": params.save_path,
                            "candidates_tried": pdf_candidates,
                            "candidate_errors": candidate_errors,
                            # Reader will preserve the retrieval receipt and
                            # produce an ABSTRACT-ONLY note. This is an
                            # evidence downgrade, not a run-stopping failure.
                            "display_disposition": "auto_fallback",
                            "fallback_available": True,
                            "fallback_action": "abstract_only_note",
                        },
                    )

            self._record_fetch_attempt(
                params.paper_id,
                params.save_path,
                ok=True,
                candidates=pdf_candidates,
                candidate_errors=candidate_errors,
                url=pdf_url,
                size=len(response.content),
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
            self._record_fetch_attempt(params.paper_id, params.save_path, ok=False, error=str(exc))
            if httpx is not None and isinstance(exc, httpx.HTTPError):
                return ToolResult(
                    ok=False,
                    content=f"Failed to download PDF: {exc}",
                    error="download_failed",
                    data={
                        "paper_id": params.paper_id,
                        "path": params.save_path,
                        "display_disposition": "auto_fallback",
                        "fallback_available": True,
                        "fallback_action": "abstract_only_note",
                    },
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
        candidates.extend(self._workspace_metadata_pdf_candidates(paper_id))
        doi = self._normalize_doi(paper_id)

        # arXiv格式: arxiv:2301.12345 或 2301.12345
        if paper_id.startswith("arxiv:"):
            arxiv_id = paper_id[6:]
            candidates.extend(self._arxiv_pdf_candidates(arxiv_id))
        elif self._looks_like_arxiv_id(paper_id):
            candidates.extend(self._arxiv_pdf_candidates(paper_id))

        # DOI / OpenAlex work id：优先从 OpenAlex 补开放获取位置
        if doi is not None:
            arxiv_id_from_doi = self._arxiv_id_from_doi(doi)
            if arxiv_id_from_doi:
                candidates.extend(self._arxiv_pdf_candidates(arxiv_id_from_doi))
            candidates.extend(await self._openalex_pdf_candidates(client, doi=doi))
            candidates.extend(await self._unpaywall_pdf_candidates(client, doi))
            candidates.extend(self._doi_fallback_candidates(doi))
        elif paper_id.startswith("W") and paper_id[1:].isdigit():
            candidates.extend(await self._openalex_pdf_candidates(client, openalex_id=paper_id))

        # 有些上游记录会把 DOI URL 或 arXiv abs URL 直接放进 id
        if paper_id.startswith("http://") or paper_id.startswith("https://"):
            candidates.extend(self._url_to_pdf_candidates(paper_id))

        return self._dedupe_candidates(candidates)

    def _workspace_metadata_pdf_candidates(self, paper_id: str) -> list[str]:
        """Use existing literature metadata to recover PDF URLs for aliased IDs.

        Reader often calls this tool with a canonical id (OpenAlex/noopenalex)
        while the raw record may already contain an arXiv URL, DOI, or pdf_url.
        This helper only extracts mechanical URL candidates from local
        artifacts; it does not change evidence level or relevance judgments.
        """

        lookup_keys: set[str] = set()
        add_identity_key_variants(lookup_keys, paper_id)
        if not lookup_keys:
            return []

        candidates: list[str] = []
        for record in self._iter_workspace_paper_records():
            record_keys = paper_record_match_keys(record)
            for extra_key in ("paper_id", "normalized_id"):
                add_identity_key_variants(record_keys, record.get(extra_key))
            if not (lookup_keys & record_keys):
                continue
            candidates.extend(self._paper_record_pdf_candidates(record))
        return candidates

    def _iter_workspace_paper_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for rel_path in (
            "literature/deep_read_queue.jsonl",
            "literature/papers_verified.jsonl",
            "literature/papers_dedup.jsonl",
            "literature/papers_raw.jsonl",
        ):
            try:
                path = self.policy.resolve_read(rel_path)
            except ToolAccessDenied:
                continue
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    records.append(payload)
        return records

    def _paper_record_pdf_candidates(self, record: dict[str, Any]) -> list[str]:
        candidates: list[str] = []

        for key in (
            "pdf_url",
            "open_access_pdf_url",
            "oa_pdf_url",
            "best_pdf_url",
            "full_text_url",
            "pmc_pdf_url",
            "url_for_pdf",
            "landing_page_url",
            "url",
        ):
            candidates.extend(self._value_to_pdf_candidates(record.get(key)))

        for key in ("openAccessPdf", "open_access_pdf", "oa_pdf", "best_oa_location"):
            candidates.extend(self._value_to_pdf_candidates(record.get(key)))

        external_ids = record.get("externalIds") if isinstance(record.get("externalIds"), dict) else {}
        arxiv_id = external_ids.get("ArXiv") or record.get("arxiv_id")
        if isinstance(arxiv_id, str) and arxiv_id.strip():
            candidates.extend(self._arxiv_pdf_candidates(arxiv_id.strip().replace("arxiv:", "")))

        doi = str(record.get("doi") or external_ids.get("DOI") or "").strip()
        if doi:
            normalized_doi = self._normalize_doi(doi) or doi.replace("https://doi.org/", "")
            arxiv_id_from_doi = self._arxiv_id_from_doi(normalized_doi)
            if arxiv_id_from_doi:
                candidates.extend(self._arxiv_pdf_candidates(arxiv_id_from_doi))
            candidates.extend(self._doi_fallback_candidates(normalized_doi))

        for location in self._iter_record_locations(record):
            candidates.extend(self._location_pdf_candidates(location))

        return candidates

    @staticmethod
    def _iter_record_locations(record: dict[str, Any]) -> list[dict[str, Any]]:
        locations: list[dict[str, Any]] = []
        for key in (
            "best_oa_location",
            "primary_location",
            "openAccessPdf",
            "open_access_pdf",
            "oa_pdf",
        ):
            value = record.get(key)
            if isinstance(value, dict):
                locations.append(value)
        for key in (
            "locations",
            "oa_locations",
            "open_access_locations",
            "openAccessLocations",
            "open_access_pdfs",
        ):
            raw_locations = record.get(key)
            if isinstance(raw_locations, list):
                locations.extend(item for item in raw_locations if isinstance(item, dict))
        return locations

    @classmethod
    def _value_to_pdf_candidates(cls, value: Any) -> list[str]:
        if isinstance(value, str) and value.strip():
            return cls._url_to_pdf_candidates(value.strip())
        if isinstance(value, dict):
            return cls._location_pdf_candidates(value)
        if isinstance(value, list):
            candidates: list[str] = []
            for item in value:
                candidates.extend(cls._value_to_pdf_candidates(item))
            return candidates
        return []

    @classmethod
    def _location_pdf_candidates(cls, location: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        for key in (
            "pdf_url",
            "url_for_pdf",
            "pdfUrl",
            "pdfURL",
            "oa_pdf_url",
            "best_pdf_url",
            "full_text_url",
            "pmc_pdf_url",
        ):
            value = location.get(key)
            if isinstance(value, str) and value.strip():
                candidates.extend(cls._url_to_pdf_candidates(value.strip()))
        for key in (
            "landing_page_url",
            "url",
            "source_url",
            "host_page_url",
            "downloadUrl",
            "download_url",
        ):
            value = location.get(key)
            if isinstance(value, str) and value.strip():
                candidates.extend(cls._url_to_pdf_candidates(value.strip()))
        return candidates

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
    def _normalize_doi(paper_id: str) -> str | None:
        """接受 doi:10...、https://doi.org/10... 和裸 DOI，统一返回裸 DOI。"""

        normalized = paper_id.strip()
        lower = normalized.lower()
        if lower.startswith("doi:"):
            normalized = normalized[4:].strip()
            lower = normalized.lower()
        if lower.startswith("https://doi.org/"):
            normalized = normalized[len("https://doi.org/") :].strip()
            lower = normalized.lower()
        elif lower.startswith("http://doi.org/"):
            normalized = normalized[len("http://doi.org/") :].strip()
            lower = normalized.lower()
        return normalized if lower.startswith("10.") else None

    @staticmethod
    def _arxiv_id_from_doi(doi: str) -> str | None:
        """arXiv DOI（10.48550/arXiv.xxxx）可直接映射到 arXiv PDF。"""

        normalized = doi.strip()
        marker = "10.48550/arxiv."
        lower = normalized.lower()
        if not lower.startswith(marker):
            return None
        arxiv_id = normalized[len(marker) :].strip()
        return arxiv_id or None

    @staticmethod
    def _doi_fallback_candidates(doi: str) -> list[str]:
        candidates = [
            f"https://doi.org/{doi}",
            f"https://dx.doi.org/{doi}",
        ]
        doi_lower = doi.lower()
        if doi_lower.startswith("10.18653/v1/"):
            anthology_id = doi.split("/", 1)[1]
            candidates.append(f"https://aclanthology.org/{anthology_id}.pdf")
        return candidates

    @classmethod
    def _url_to_pdf_candidates(cls, url: str) -> list[str]:
        candidates = [url]
        if "arxiv.org/abs/" in url:
            candidates.append(url.replace("/abs/", "/pdf/") + ".pdf")
        if "arxiv.org/html/" in url:
            candidates.append(url.replace("/html/", "/pdf/") + ".pdf")
        return candidates

    @classmethod
    def _extract_pdf_links_from_html(cls, response: "httpx.Response", base_url: str) -> list[str]:
        content_type = response.headers.get("content-type", "").casefold()
        raw_text = getattr(response, "text", None)
        if raw_text is None:
            raw_content = getattr(response, "content", b"")
            raw_text = raw_content.decode("utf-8", errors="ignore") if isinstance(raw_content, bytes) else str(raw_content)
        if "html" not in content_type and not raw_text[:200].lstrip().lower().startswith("<"):
            return []
        html = raw_text[:300_000]
        candidates: list[str] = []
        patterns = [
            r'<meta[^>]+name=["\']citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']citation_pdf_url["\']',
            r'<link[^>]+type=["\']application/pdf["\'][^>]+href=["\']([^"\']+)["\']',
            r'<a[^>]+href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']',
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, html, flags=re.IGNORECASE):
                url = urljoin(base_url, match.group(1).strip())
                if url:
                    candidates.extend(cls._url_to_pdf_candidates(url))
        return cls._dedupe_candidates(candidates)

    def _record_fetch_attempt(
        self,
        paper_id: str,
        save_path: str,
        *,
        ok: bool,
        candidates: list[str] | None = None,
        candidate_errors: list[dict[str, Any]] | None = None,
        url: str | None = None,
        size: int | None = None,
        error: str | None = None,
    ) -> None:
        payload = {
            "paper_id": paper_id,
            "save_path": save_path,
            "ok": ok,
            "url": url or "",
            "size": size,
            "error": error or "",
            "candidate_count": len(candidates or []),
            "candidate_errors": (candidate_errors or [])[:10],
        }
        try:
            path = self.policy.resolve_write("literature/pdf_fetch_attempts.jsonl")
        except ToolAccessDenied:
            # Diagnostic attempts are runtime metadata, not the PDF artifact
            # requested by the agent.  If a narrow tool policy only allows the
            # target save path, still keep the attempt log inside the workspace
            # so failed PDF resolution is debuggable after the run.
            path = self.policy.workspace_dir / "literature" / "pdf_fetch_attempts.jsonl"
        except Exception:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            return

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
            normalized_doi = self._normalize_doi(doi) or doi.strip()
            if not normalized_doi:
                return []
            url = f"https://api.openalex.org/works/doi:{quote(normalized_doi, safe='')}"
        else:
            identifier = str(openalex_id or "").strip()
            if not identifier:
                return []
            url = f"https://api.openalex.org/works/{quote(identifier, safe=':/')}"
        params = {
            "mailto": (
                os.environ.get("RESEARCHER_EMAIL")
                or os.environ.get("OPENALEX_MAILTO")
                or "researcher@example.com"
            )
        }

        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
        except Exception:
            return []

        work = response.json()
        candidates: list[str] = []

        for key in ("best_oa_location", "primary_location", "openAccessPdf"):
            value = work.get(key)
            if isinstance(value, dict):
                candidates.extend(self._location_pdf_candidates(value))
        for key in ("locations", "oa_locations", "open_access_locations"):
            for location in work.get(key, []) or []:
                if isinstance(location, dict):
                    candidates.extend(self._location_pdf_candidates(location))

        doi_value = work.get("doi")
        if isinstance(doi_value, str) and doi_value.startswith("https://doi.org/"):
            candidates.append(doi_value)

        return candidates

    async def _unpaywall_pdf_candidates(
        self,
        client: "httpx.AsyncClient",
        doi: str,
    ) -> list[str]:
        """从 Unpaywall 按 DOI 查询开放获取 PDF 候选。"""

        normalized_doi = self._normalize_doi(doi) or doi.strip()
        if not normalized_doi:
            return []
        email = os.environ.get("RESEARCHER_EMAIL", "researcher@example.com")
        try:
            response = await client.get(
                f"https://api.unpaywall.org/v2/{quote(normalized_doi, safe='')}",
                params={"email": email},
            )
            response.raise_for_status()
        except Exception:
            return []

        payload = response.json()
        candidates: list[str] = []
        for key in ("best_oa_location", "openAccessPdf"):
            value = payload.get(key)
            if isinstance(value, dict):
                candidates.extend(self._location_pdf_candidates(value))
        for key in ("oa_locations", "open_access_locations"):
            for location in payload.get(key, []) or []:
                if isinstance(location, dict):
                    candidates.extend(self._location_pdf_candidates(location))
        candidates.extend(self._value_to_pdf_candidates(payload.get("url_for_pdf")))
        candidates.extend(self._value_to_pdf_candidates(payload.get("pdf_url")))
        return candidates

    @staticmethod
    def _looks_like_pdf(response: "httpx.Response", url: str) -> bool:
        content_type = response.headers.get("content-type", "").lower()
        if "application/pdf" in content_type:
            return True
        content = response.content[:5]
        if content.startswith(b"%PDF-"):
            return True
        if url.lower().endswith(".pdf") and not content_type:
            return True
        return False

    @staticmethod
    def _format_candidate_errors(candidate_errors: list[dict[str, Any]]) -> str:
        if not candidate_errors:
            return "No candidate errors were captured."
        snippets = []
        for item in candidate_errors[-3:]:
            error = item.get("error") or "unknown"
            url = item.get("url") or ""
            detail = item.get("detail") or item.get("content_type") or ""
            snippets.append(f"{error} at {url}: {detail}".strip())
        return "Recent errors: " + " | ".join(snippets)

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
    max_chars: int = Field(
        default=50_000,
        ge=1000,
        le=300_000,
        description="最多返回多少字符；T3 可用于全文或分块阅读 PDF",
    )
    start_page: int = Field(default=1, ge=1, description="从第几页开始提取，1-based")
    max_pages: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="最多读取多少页；不传则读取全文后再按 max_chars 截断",
    )


class ExtractPdfTextTool(Tool):
    """提取PDF全文文本。"""

    name = "extract_pdf_text"
    description = (
        "提取PDF文件文本。返回内容开头包含 total_pages、extracted_page_range、"
        "preview_truncated_by_max_chars 等覆盖信息；T3 如要标记 FULL-TEXT，必须覆盖全部页面且无截断。"
    )
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

            text_parts = []
            visited_pages: list[int] = []
            pages_with_text: list[int] = []
            total_pages = 0
            with pdfplumber.open(abs_path) as pdf:
                total_pages = len(pdf.pages)
                for page_num, page in enumerate(pdf.pages, start=1):
                    if page_num < params.start_page:
                        continue
                    if params.max_pages is not None and page_num >= params.start_page + params.max_pages:
                        break
                    visited_pages.append(page_num)
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        pages_with_text.append(page_num)
                        text_parts.append(f"--- Page {page_num} ---\n{page_text}")

            full_text = "\n\n".join(text_parts)

            content_preview = full_text[: params.max_chars]
            truncated = len(full_text) > params.max_chars
            if truncated:
                content_preview += (
                    f"\n\n[... truncated, full length: {len(full_text)} chars, "
                    f"shown: {params.max_chars}]"
                )

            start_page = visited_pages[0] if visited_pages else None
            end_page = visited_pages[-1] if visited_pages else None
            extracted_range = (
                f"{start_page}-{end_page}" if start_page is not None and end_page is not None else "none"
            )
            covers_pdf_end = end_page is not None and end_page >= total_pages
            covers_full_pdf = params.start_page == 1 and covers_pdf_end
            complete_pdf_read = covers_full_pdf and not truncated and bool(pages_with_text)
            next_start_page = (end_page + 1) if end_page is not None and end_page < total_pages else None
            metadata_header = "\n".join(
                [
                    "[PDF extraction metadata]",
                    f"- pdf: {params.pdf_path}",
                    f"- total_pages: {total_pages}",
                    f"- extracted_page_range: {extracted_range}",
                    f"- pages_visited: {len(visited_pages)}",
                    f"- pages_with_text: {len(pages_with_text)}",
                    f"- requested_start_page: {params.start_page}",
                    f"- requested_max_pages: {params.max_pages if params.max_pages is not None else 'FULL'}",
                    f"- preview_truncated_by_max_chars: {str(truncated).lower()}",
                    f"- covers_full_pdf: {str(covers_full_pdf).lower()}",
                    f"- complete_pdf_read: {str(complete_pdf_read).lower()}",
                    f"- next_start_page: {next_start_page if next_start_page is not None else 'none'}",
                    (
                        "- note: If preview_truncated_by_max_chars=true, re-read a narrower page range "
                        "before marking the note FULL-TEXT."
                    ),
                ]
            )
            content = f"{metadata_header}\n\n{content_preview}" if content_preview else metadata_header

            return ToolResult(
                ok=True,
                content=content,
                data={
                    "pdf": params.pdf_path,
                    "text_preview": content_preview,
                    "length": len(full_text),
                    "pages": len(text_parts),
                    "total_pages": total_pages,
                    "pages_visited": len(visited_pages),
                    "pages_with_text": len(pages_with_text),
                    "range_start_page": start_page,
                    "end_page": end_page,
                    "extracted_page_range": extracted_range,
                    "covers_full_pdf": covers_full_pdf,
                    "complete_pdf_read": complete_pdf_read,
                    "next_start_page": next_start_page,
                    "start_page": params.start_page,
                    "max_pages": params.max_pages,
                    "max_chars": params.max_chars,
                    "truncated": truncated,
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
