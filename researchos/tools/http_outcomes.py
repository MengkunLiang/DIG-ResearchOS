"""Bounded, user-safe classification for public scholarly API failures.

The detailed exception still belongs in the runtime trace.  A tool result,
however, must tell the orchestrator whether a failure is retryable and whether
other retrieval sources can continue, without treating a transient provider
issue as a corrupted research workspace.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .base import ToolResult


def retry_after_seconds(response: httpx.Response | None, *, fallback: float, maximum: float = 30.0) -> float:
    """Return a bounded retry delay from Retry-After when the API supplies one."""

    if response is not None:
        raw = response.headers.get("retry-after")
        if raw:
            try:
                return max(0.0, min(float(raw), maximum))
            except ValueError:
                pass
    return fallback


async def bounded_retry_sleep(response: httpx.Response | None, *, attempt: int) -> None:
    await asyncio.sleep(retry_after_seconds(response, fallback=float(2**attempt)))


def retry_after_hint_seconds(response: httpx.Response | None) -> float | None:
    """Read Retry-After without turning a multi-hour cooldown into a sleep."""

    if response is None:
        return None
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def scholarly_http_failure(
    *,
    source: str,
    exc: Exception,
    attempts: int,
    fallback_available: bool = True,
    action: str = "检索",
    response: httpx.Response | None = None,
) -> ToolResult:
    """Convert expected API/network failures into structured ``ToolResult`` data."""

    failure_class = "provider_error"
    retriable = False
    status_code: int | None = None
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code == 429:
            failure_class, retriable = "rate_limited", True
        elif 500 <= status_code <= 599:
            failure_class, retriable = "http_5xx", True
        elif status_code in {408, 409, 425}:
            failure_class, retriable = "transient_http", True
        elif status_code == 404:
            failure_class = "not_found"
        else:
            failure_class = f"http_{status_code}"
    elif isinstance(exc, httpx.TimeoutException):
        failure_class, retriable = "timeout", True
    elif isinstance(exc, httpx.RequestError):
        failure_class, retriable = "network_unavailable", True

    if failure_class == "rate_limited":
        content = f"{source} 暂时触发速率限制；其他可用来源会继续。"
    elif failure_class in {"network_unavailable", "timeout", "http_5xx", "transient_http"}:
        content = f"{source} {action}暂时不可用；其他可用来源会继续。"
    elif failure_class == "not_found":
        content = f"{source} 未找到请求的记录。"
    else:
        content = f"{source} {action}未完成（{failure_class}）。"
    data: dict[str, Any] = {
        "source": source.casefold().replace("/", "_"),
        "failure_class": failure_class,
        "exception_type": type(exc).__name__,
        "retriable": retriable,
        "fallback_available": fallback_available,
        "attempts": attempts,
    }
    if status_code is not None:
        data["http_status"] = status_code
    retry_after = retry_after_hint_seconds(response)
    if retry_after is not None:
        data["retry_after_seconds"] = retry_after
    return ToolResult(ok=False, content=content, data=data, error=failure_class)


def provider_cooldown_result(*, source: str, retry_after_seconds: float) -> ToolResult:
    """Return a non-blocking result when a provider advertised a cooldown."""

    return ToolResult(
        ok=False,
        content=f"{source} 仍处于 API 冷却期；其他可用来源会继续。",
        error="rate_limited",
        data={
            "source": source.casefold().replace("/", "_"),
            "failure_class": "rate_limited",
            "retriable": True,
            "fallback_available": True,
            "retry_after_seconds": max(0.0, retry_after_seconds),
            "cooldown_active": True,
        },
    )
