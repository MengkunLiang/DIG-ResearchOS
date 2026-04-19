from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, Field

from .base import Tool, ToolResult


_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


@dataclass(frozen=True)
class WebFetchAllowlist:
    allowed_schemes: frozenset[str]
    allowed_hosts: frozenset[str]
    allow_all_hosts: bool

    @classmethod
    def from_env(cls) -> "WebFetchAllowlist":
        schemes_raw = os.getenv("RESEARCHOS_WEB_FETCH_ALLOWED_SCHEMES", "http,https")
        hosts_raw = os.getenv("RESEARCHOS_WEB_FETCH_ALLOWED_HOSTS", "")
        schemes = frozenset(
            part.strip().lower() for part in schemes_raw.split(",") if part.strip()
        ) or frozenset({"http", "https"})
        hosts = frozenset(part.strip().lower() for part in hosts_raw.split(",") if part.strip())
        return cls(
            allowed_schemes=schemes,
            allowed_hosts=hosts,
            allow_all_hosts=not hosts,
        )

    def is_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        scheme = parsed.scheme.lower()
        if scheme not in self.allowed_schemes:
            return False
        if self.allow_all_hosts:
            return True
        return any(host == allowed or host.endswith(f".{allowed}") for allowed in self.allowed_hosts)


class WebFetchParams(BaseModel):
    url: str = Field(..., description="要抓取的 URL")
    timeout_seconds: int = Field(default=10, ge=1, le=30, description="请求超时秒数")
    max_bytes: int = Field(
        default=200_000,
        ge=1,
        le=1_000_000,
        description="最多读取多少字节，超出则截断",
    )


class WebFetchTool(Tool):
    name = "web_fetch"
    description = "抓取网页内容，支持显式 allowlist 和有限重定向"
    parameters_schema = WebFetchParams
    timeout_seconds = 30.0

    def __init__(
        self,
        *,
        allowlist: WebFetchAllowlist | None = None,
        user_agent: str = "ResearchOS/0.1 web_fetch",
        max_redirects: int = 5,
    ) -> None:
        self.allowlist = allowlist or WebFetchAllowlist.from_env()
        self.user_agent = user_agent
        self.max_redirects = max_redirects

    async def execute(self, **kwargs) -> ToolResult:
        url = kwargs["url"]
        timeout_seconds = kwargs["timeout_seconds"]
        max_bytes = kwargs["max_bytes"]

        if not self.allowlist.is_allowed(url):
            return ToolResult(ok=False, content=f"URL not allowed: {url}", error="access_denied")

        redirect_chain: list[str] = []
        current_url = url
        timeout = httpx.Timeout(timeout_seconds)

        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=timeout,
                headers={"User-Agent": self.user_agent},
            ) as client:
                for _ in range(self.max_redirects + 1):
                    if not self.allowlist.is_allowed(current_url):
                        return ToolResult(
                            ok=False,
                            content=f"Redirect target not allowed: {current_url}",
                            error="access_denied",
                            data={"redirect_chain": redirect_chain},
                        )

                    async with client.stream("GET", current_url) as response:
                        if response.status_code in _REDIRECT_STATUS_CODES:
                            location = response.headers.get("location")
                            if not location:
                                return ToolResult(
                                    ok=False,
                                    content="Redirect response missing Location header",
                                    error="redirect_error",
                                    data={"status_code": response.status_code},
                                )
                            current_url = urljoin(str(response.url), location)
                            redirect_chain.append(current_url)
                            continue

                        body, truncated = await self._read_body(response, max_bytes=max_bytes)
                        text = body.decode(response.encoding or "utf-8", errors="replace")
                        ok = 200 <= response.status_code < 300
                        return ToolResult(
                            ok=ok,
                            content=text,
                            data={
                                "url": str(response.url),
                                "status_code": response.status_code,
                                "content_type": response.headers.get("content-type", ""),
                                "truncated": truncated,
                                "redirect_chain": redirect_chain,
                            },
                            error=None if ok else "http_error",
                        )
        except httpx.HTTPError as exc:
            return ToolResult(ok=False, content=f"Request failed: {exc}", error="request_failed")

        return ToolResult(
            ok=False,
            content=f"Too many redirects while fetching: {url}",
            error="too_many_redirects",
            data={"redirect_chain": redirect_chain},
        )

    @staticmethod
    async def _read_body(
        response: httpx.Response, *, max_bytes: int
    ) -> tuple[bytes, bool]:
        chunks: list[bytes] = []
        total = 0
        truncated = False
        async for chunk in response.aiter_bytes():
            remaining = max_bytes - total
            if remaining <= 0:
                truncated = True
                break
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                total += remaining
                truncated = True
                break
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks), truncated
