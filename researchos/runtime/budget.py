from __future__ import annotations

from dataclasses import dataclass, field
import time

from .errors import BudgetExceeded


@dataclass
class BudgetTracker:
    max_steps: int
    max_tokens: int
    max_wall_seconds: int
    steps: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    started_at: float = field(default_factory=time.time)

    def tick_step(self) -> None:
        self.steps += 1

    def add_tokens(self, tin: int, tout: int, cost: float) -> None:
        self.tokens_in += tin
        self.tokens_out += tout
        self.cost_usd += cost

    def elapsed_seconds(self) -> float:
        return time.time() - self.started_at

    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    def check(self) -> None:
        if self.steps > self.max_steps:
            raise BudgetExceeded("steps", self.max_steps, self.steps)
        if self.total_tokens() > self.max_tokens:
            raise BudgetExceeded("tokens", self.max_tokens, self.total_tokens())
        if self.elapsed_seconds() > self.max_wall_seconds:
            raise BudgetExceeded(
                "wall_seconds", self.max_wall_seconds, self.elapsed_seconds()
            )

    def snapshot(self) -> dict[str, float | int]:
        return {
            "steps": self.steps,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_total": self.total_tokens(),
            "cost_usd": round(self.cost_usd, 6),
            "elapsed_s": round(self.elapsed_seconds(), 3),
        }

    def extend_limit(self, dimension: str, delta: int | float) -> None:
        """动态增加某个预算上限。"""

        if dimension == "steps":
            self.max_steps += int(delta)
            return
        if dimension == "tokens":
            self.max_tokens += int(delta)
            return
        if dimension == "wall_seconds":
            self.max_wall_seconds += int(delta)
            return
        raise ValueError(f"unknown budget dimension: {dimension}")
