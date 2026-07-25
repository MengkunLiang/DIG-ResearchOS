from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field


_LOG = logging.getLogger(__name__)


@dataclass
class TokenBucket:
    rate_per_minute: int
    burst: int
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self) -> None:
        self._tokens = float(self.burst)
        self._last_refill = time.monotonic()

    async def acquire(self, amount: int) -> None:
        """Wait for a locally configured token budget when it can admit ``amount``.

        A token bucket can never hold more than ``burst``.  Treating a single
        request larger than that value as a normal deficit would therefore
        sleep forever.  The caller bypasses such a request instead: provider
        quota enforcement can still return a concrete rate-limit error, while
        ResearchOS never turns one large literature-reading request into an
        invisible local deadlock.
        """

        if self.rate_per_minute <= 0 or self.burst <= 0 or amount > self.burst:
            return
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last_refill
                refill_rate = self.rate_per_minute / 60.0
                self._tokens = min(self.burst, self._tokens + elapsed * refill_rate)
                self._last_refill = now
                if self._tokens >= amount:
                    self._tokens -= amount
                    return
                deficit = amount - self._tokens
                wait_seconds = deficit / refill_rate if refill_rate > 0 else 30
                await asyncio.sleep(min(wait_seconds, 30))


class EndpointRateLimiter:
    def __init__(self, endpoints_cfg: dict[str, dict]):
        self.buckets: dict[str, TokenBucket] = {}
        self._oversized_warned: set[str] = set()
        for name, cfg in endpoints_cfg.items():
            rate_limit = cfg.get("rate_limit") or {}
            if not isinstance(rate_limit, dict):
                continue
            # New one-model settings opt out by default.  Legacy endpoint
            # profiles did not have an ``enabled`` flag; when they already
            # declare a rate-limit mapping, retain their prior behaviour.
            enabled = rate_limit.get("enabled")
            if enabled is None:
                enabled = bool(rate_limit)
            elif not isinstance(enabled, bool):
                enabled = str(enabled).strip().casefold() in {"1", "true", "yes", "on"}
            if not enabled:
                continue
            try:
                tokens_per_minute = max(1, int(rate_limit.get("tokens_per_minute", 200_000)))
            except (TypeError, ValueError):
                tokens_per_minute = 200_000
            try:
                burst = max(1, int(rate_limit.get("burst", 200_000)))
            except (TypeError, ValueError):
                burst = 200_000
            self.buckets[name] = TokenBucket(
                rate_per_minute=tokens_per_minute,
                burst=burst,
            )

    async def wait(self, endpoint_name: str, estimated_tokens: int) -> None:
        bucket = self.buckets.get(endpoint_name)
        if bucket is not None:
            amount = max(estimated_tokens, 1)
            if amount > bucket.burst:
                if endpoint_name not in self._oversized_warned:
                    self._oversized_warned.add(endpoint_name)
                    _LOG.warning(
                        "local_rate_limit_bypassed_for_oversized_request",
                        extra={
                            "endpoint": endpoint_name,
                            "estimated_tokens": amount,
                            "burst": bucket.burst,
                        },
                    )
                return
            await bucket.acquire(amount)
