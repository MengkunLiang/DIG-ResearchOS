from __future__ import annotations

"""LLM 路由与 provider 调用层。"""

import asyncio
from dataclasses import dataclass, field
import os
from pathlib import Path
import time
from typing import Any

import yaml

from .errors import ConfigurationError, LLMProviderError
from .logger import get_logger
from .rate_limiter import EndpointRateLimiter

try:  # pragma: no cover - optional import exercised in integration use
    import litellm
except Exception:  # pragma: no cover
    litellm = None


_log = get_logger("llm_client")


@dataclass
class Endpoint:
    """一个具体 API endpoint 的连接信息。"""

    name: str
    provider: str
    api_key_env: str | None = None
    api_base: str | None = None
    api_base_env: str | None = None
    api_version: str | None = None
    extra_headers: dict[str, Any] = field(default_factory=dict)
    rate_limit: dict[str, Any] = field(default_factory=dict)

    def litellm_provider(self) -> str:
        """返回传给 LiteLLM 的 provider 前缀。"""

        return self.provider

    def to_litellm_kwargs(self) -> dict[str, Any]:
        """把 endpoint 配置转换成 litellm 需要的关键字参数。

        支持多种环境变量名称：
        - 优先使用配置中指定的环境变量名
        - 如果未设置，尝试常见的备选名称（如 OPENAI_API_KEY）
        """
        kwargs: dict[str, Any] = {}
        if self.api_key_env:
            key = os.environ.get(self.api_key_env)
            # 如果主环境变量未设置，尝试备选名称
            if not key:
                # 常见的备选环境变量名
                fallback_names = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]
                for fallback in fallback_names:
                    key = os.environ.get(fallback)
                    if key:
                        _log.info(
                            "using_fallback_api_key",
                            requested=self.api_key_env,
                            fallback=fallback,
                        )
                        break

            if not key:
                raise ConfigurationError(
                    f"Endpoint '{self.name}' requires env var {self.api_key_env} "
                    f"(or fallback: OPENAI_API_KEY, ANTHROPIC_API_KEY)"
                )
            kwargs["api_key"] = key

        api_base = self.api_base
        if self.api_base_env:
            api_base = os.environ.get(self.api_base_env)
            # 如果主环境变量未设置，尝试备选名称
            if not api_base:
                fallback_names = ["OPENAI_API_BASE", "OPENAI_BASE_URL"]
                for fallback in fallback_names:
                    api_base = os.environ.get(fallback)
                    if api_base:
                        _log.info(
                            "using_fallback_api_base",
                            requested=self.api_base_env,
                            fallback=fallback,
                        )
                        break

            if not api_base:
                raise ConfigurationError(
                    f"Endpoint '{self.name}' requires env var {self.api_base_env} "
                    f"(or fallback: OPENAI_API_BASE, OPENAI_BASE_URL)"
                )

        if api_base:
            kwargs["api_base"] = api_base
        if self.api_version:
            kwargs["api_version"] = self.api_version
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        return kwargs


@dataclass
class ModelBinding:
    """某个 tier/profile 下绑定到 endpoint 的模型。"""

    model: str
    endpoint: str
    max_context: int = 100_000

    def qualified(self, endpoint_obj: Endpoint) -> str:
        provider = endpoint_obj.litellm_provider()
        if self.model.startswith(f"{provider}/"):
            return self.model
        # OpenAI-compatible endpoints 经常使用带 `/` 的原始模型名，
        # 例如 `Pro/deepseek-ai/DeepSeek-V3.2`。这类名字仍然需要
        # 由 LiteLLM 看到 `openai/...` 前缀，不能仅凭 `/` 判断为已限定。
        if provider == "openai":
            return f"{provider}/{self.model}"
        if "/" in self.model:
            return self.model
        return f"{provider}/{self.model}"


@dataclass
class TierConfig:
    primary: ModelBinding
    fallback: list[ModelBinding] = field(default_factory=list)


@dataclass
class Profile:
    name: str
    tiers: dict[str, TierConfig]


@dataclass
class LLMResponse:
    raw: Any
    model_used: str
    endpoint_used: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_ms: int


class LLMClient:
    """ResearchOS 对 litellm 的薄封装。

    核心职责：
    - 读取 model_routing.yaml；
    - 解析 endpoints / profiles / fallback；
    - 提供 `resolve()`、`chat()`、`selftest()` 等统一接口；
    - 在真正调用 provider 前先经过本地 rate limiter。
    """

    def __init__(self, routing_config_path: Path):
        if not routing_config_path.exists():
            raise ConfigurationError(f"Routing config not found: {routing_config_path}")
        self._load_env_file(routing_config_path)
        self.routing_config_path = routing_config_path
        self.raw = yaml.safe_load(routing_config_path.read_text(encoding="utf-8")) or {}
        self.endpoints = self._parse_endpoints(self.raw)
        self.profiles = self._parse_profiles(self.raw)
        self.default_profile_name = self.raw.get("default_profile", "default")
        self.truncation_cfg = self.raw.get("truncation", {})
        self.rate_limiter = EndpointRateLimiter(self.raw.get("endpoints") or {})
        self._validate()

    def _load_env_file(self, routing_config_path: Path) -> None:
        """从项目根目录 `.env` 里补充环境变量。

        这里使用 `setdefault`，意味着：
        - shell 中已显式设置的值优先；
        - `.env` 只作为本地开发便利，不强行覆盖外部环境。
        """
        project_env = routing_config_path.parent.parent / ".env"
        if not project_env.exists():
            return
        for raw_line in project_env.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ.setdefault(key, value)

    def _parse_endpoints(self, raw: dict[str, Any]) -> dict[str, Endpoint]:
        endpoints = {
            name: Endpoint(name=name, **cfg) for name, cfg in (raw.get("endpoints") or {}).items()
        }
        if not endpoints:
            raise ConfigurationError("model_routing.yaml: 'endpoints' is empty")
        return endpoints

    def _parse_profiles(self, raw: dict[str, Any]) -> dict[str, Profile]:
        profiles: dict[str, Profile] = {}
        for profile_name, profile_cfg in (raw.get("profiles") or {}).items():
            tiers: dict[str, TierConfig] = {}
            for tier_name, tier_cfg in profile_cfg.items():
                primary = ModelBinding(**tier_cfg["primary"])
                fallback = [ModelBinding(**item) for item in tier_cfg.get("fallback", [])]
                tiers[tier_name] = TierConfig(primary=primary, fallback=fallback)
            profiles[profile_name] = Profile(name=profile_name, tiers=tiers)
        if not profiles:
            raise ConfigurationError("model_routing.yaml: 'profiles' is empty")
        return profiles

    def _validate(self) -> None:
        if self.default_profile_name not in self.profiles:
            raise ConfigurationError(
                f"default_profile '{self.default_profile_name}' not found in profiles"
            )
        for profile_name, profile in self.profiles.items():
            for tier_name, tier in profile.tiers.items():
                for binding in [tier.primary, *tier.fallback]:
                    if binding.endpoint not in self.endpoints:
                        raise ConfigurationError(
                            f"profile={profile_name} tier={tier_name} uses unknown endpoint "
                            f"{binding.endpoint}"
                        )

    def resolve(
        self,
        *,
        profile: str | None,
        tier: str,
        model_override: str | None,
    ) -> list[tuple[ModelBinding, Endpoint]]:
        """解析某次调用的候选模型链路。

        返回值按优先级排序：
        - 第一个是 primary；
        - 后续是 fallback。
        """
        if model_override:
            binding, endpoint = self._find_binding_for_override(model_override)
            return [(binding, endpoint)]
        profile_name = profile or self.default_profile_name
        profile_obj = self.profiles.get(profile_name)
        if profile_obj is None:
            raise ConfigurationError(f"Unknown profile: {profile_name}")
        tier_cfg = profile_obj.tiers.get(tier)
        if tier_cfg is None:
            raise ConfigurationError(f"Profile '{profile_name}' has no tier '{tier}'")
        out = [(tier_cfg.primary, self.endpoints[tier_cfg.primary.endpoint])]
        out.extend((binding, self.endpoints[binding.endpoint]) for binding in tier_cfg.fallback)
        return out

    def _find_binding_for_override(self, model_override: str) -> tuple[ModelBinding, Endpoint]:
        for profile in self.profiles.values():
            for tier in profile.tiers.values():
                for binding in [tier.primary, *tier.fallback]:
                    if binding.model == model_override:
                        return binding, self.endpoints[binding.endpoint]
        endpoint = next(iter(self.endpoints.values()))
        _log.warning(
            "override_no_binding_found",
            model=model_override,
            fallback_endpoint=endpoint.name,
        )
        return ModelBinding(model=model_override, endpoint=endpoint.name), endpoint

    def get_context_window(self, binding: ModelBinding) -> int:
        return binding.max_context

    def get_truncation_config(self) -> dict[str, Any]:
        return self.truncation_cfg

    def _any_binding_for(self, endpoint_name: str) -> ModelBinding | None:
        for profile in self.profiles.values():
            for tier in profile.tiers.values():
                for binding in [tier.primary, *tier.fallback]:
                    if binding.endpoint == endpoint_name:
                        return binding
        return None

    async def selftest(self, profiles_to_check: list[str] | None = None) -> dict[str, dict[str, Any]]:
        """对 profile 涉及的 endpoint 做最小连通性检查。"""
        if litellm is None:
            raise LLMProviderError("litellm is not installed")

        profiles = profiles_to_check or [self.default_profile_name]
        endpoints_to_check: set[str] = set()
        for profile_name in profiles:
            profile = self.profiles.get(profile_name)
            if profile is None:
                continue
            for tier in profile.tiers.values():
                endpoints_to_check.add(tier.primary.endpoint)
                for fallback in tier.fallback:
                    endpoints_to_check.add(fallback.endpoint)

        results: dict[str, dict[str, Any]] = {}
        for endpoint_name in sorted(endpoints_to_check):
            endpoint = self.endpoints[endpoint_name]
            binding = self._any_binding_for(endpoint_name)
            if binding is None:
                results[endpoint_name] = {"ok": False, "error": "no binding", "latency_ms": 0}
                continue
            started = time.time()
            try:
                await litellm.acompletion(
                    model=binding.qualified(endpoint),
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                    timeout=10,
                    **endpoint.to_litellm_kwargs(),
                )
                results[endpoint_name] = {
                    "ok": True,
                    "error": None,
                    "latency_ms": int((time.time() - started) * 1000),
                }
            except Exception as exc:
                results[endpoint_name] = {
                    "ok": False,
                    "error": str(exc)[:200],
                    "latency_ms": int((time.time() - started) * 1000),
                }
        return results

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
        tier: str,
        profile: str | None = None,
        model_override: str | None = None,
        timeout: int = 120,
        max_retries_per_model: int = 2,
    ) -> LLMResponse:
        """执行一次模型调用。

        注意这里有两层保护：
        - 对同一个候选模型做重试；
        - 当前候选彻底失败后，才会切换到 fallback 模型。
        """
        if litellm is None:
            raise LLMProviderError("litellm is not installed")

        errors: list[str] = []
        candidates = self.resolve(profile=profile, tier=tier, model_override=model_override)
        for binding, endpoint in candidates:
            qualified = binding.qualified(endpoint)
            for attempt in range(max_retries_per_model):
                started = time.time()
                try:
                    # 先做本地限流，避免一撞 provider rate limit 就误触 fallback。
                    estimated_tokens = self.count_tokens(messages, binding) + 4000
                    await self.rate_limiter.wait(endpoint.name, estimated_tokens)
                    kwargs: dict[str, Any] = {
                        "model": qualified,
                        "messages": messages,
                        "temperature": temperature,
                        "timeout": timeout,
                        **endpoint.to_litellm_kwargs(),
                    }
                    if tools:
                        kwargs["tools"] = tools
                        kwargs["tool_choice"] = "auto"
                    raw = await litellm.acompletion(**kwargs)
                    usage = getattr(raw, "usage", None)
                    hidden = getattr(raw, "_hidden_params", {}) or {}
                    return LLMResponse(
                        raw=raw,
                        model_used=qualified,
                        endpoint_used=endpoint.name,
                        tokens_in=getattr(usage, "prompt_tokens", 0),
                        tokens_out=getattr(usage, "completion_tokens", 0),
                        cost_usd=float(hidden.get("response_cost") or 0.0),
                        duration_ms=int((time.time() - started) * 1000),
                    )
                except Exception as exc:
                    errors.append(f"{qualified}@{endpoint.name} attempt {attempt + 1}: {exc!r}")
                    await asyncio.sleep(min(2**attempt, 8))
        raise LLMProviderError(
            f"All candidates failed (profile={profile or self.default_profile_name}, "
            f"tier={tier}). Errors: {errors}"
        )

    def count_tokens(self, messages: list[dict[str, Any]], binding: ModelBinding) -> int:
        """尽量准确地估算 token；若 provider 不支持，则退化到字符近似。"""
        if litellm is None:
            return sum(len(str(item.get("content", ""))) for item in messages) // 4
        try:
            return litellm.token_counter(model=binding.model, messages=messages)
        except Exception:
            total = sum(len(str(item.get("content", ""))) for item in messages)
            for message in messages:
                for tool_call in message.get("tool_calls", []):
                    total += len(tool_call.get("function", {}).get("arguments", ""))
            return total // 4
