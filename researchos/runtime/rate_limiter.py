from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


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
        for name, cfg in endpoints_cfg.items():
            rate_limit = cfg.get("rate_limit") or {}
            self.buckets[name] = TokenBucket(
                rate_per_minute=int(rate_limit.get("tokens_per_minute", 200_000)),
                burst=int(rate_limit.get("burst", 200_000)),
            )

    async def wait(self, endpoint_name: str, estimated_tokens: int) -> None:
        bucket = self.buckets.get(endpoint_name)
        if bucket is not None:
            await bucket.acquire(max(estimated_tokens, 1))
