from __future__ import annotations

"""LLM 路由与 provider 调用层。"""

import asyncio
from dataclasses import dataclass, field
import inspect
import logging
import os
from pathlib import Path
import time
from typing import Any

import yaml

from .errors import ConfigurationError, LLMProviderError
from .logger import get_logger
from .rate_limiter import EndpointRateLimiter
from .user_settings import (
    apply_model_routing_overrides,
    load_user_settings,
    should_apply_default_user_settings,
)
from .system_config import config_file_path


def _pre_suppress_litellm_import_logs() -> None:
    """Suppress LiteLLM import-time provider probes before importing litellm.

    LiteLLM can emit Bedrock/SageMaker preload warnings during module import,
    before ResearchOS has a chance to call `configure_logging`.  Those warnings
    are not actionable for normal OpenAI-compatible routes and they make even
    config-only CLI commands look broken, so suppress them at the boundary.
    """

    os.environ.setdefault("LITELLM_LOG", "ERROR")
    os.environ.setdefault("LITELLM_VERBOSE", "False")
    for name in (
        "LiteLLM",
        "litellm",
        "litellm.utils",
        "litellm.litellm_core_utils",
        "httpx",
        "httpcore",
    ):
        logging.getLogger(name).setLevel(logging.ERROR if name.lower().startswith("litellm") else logging.WARNING)


_pre_suppress_litellm_import_logs()

try:  # pragma: no cover - optional import exercised in integration use
    import litellm
except Exception:  # pragma: no cover
    litellm = None


_log = get_logger("llm_client")
DEFAULT_ROUTING_CONFIG_PATH = config_file_path("model_routing.yaml")


def suppress_litellm_info_logs() -> None:
    """Keep LiteLLM INFO/debug chatter out of CLI and researchos.log."""

    if litellm is not None:
        try:
            litellm.suppress_debug_info = True
        except Exception:
            pass
        try:
            litellm.set_verbose = False
        except Exception:
            pass
    for name in (
        "LiteLLM",
        "litellm",
        "litellm.utils",
        "litellm.litellm_core_utils",
        "httpx",
        "httpcore",
    ):
        logging.getLogger(name).setLevel(logging.ERROR if name.lower().startswith("litellm") else logging.WARNING)


def _dedupe_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


@dataclass
class Endpoint:
    """一个具体 API endpoint 的连接信息。"""

    name: str
    provider: str
    api_key_env: str | None = None
    api_key: str | None = None  # 直接从配置文件读取的 API key
    api_base: str | None = None
    api_base_env: str | None = None
    api_version: str | None = None
    extra_headers: dict[str, Any] = field(default_factory=dict)
    rate_limit: dict[str, Any] = field(default_factory=dict)
    # 用于从配置文件中读取 api_keys 的引用
    _config_api_keys: dict[str, str] = field(default_factory=dict, repr=False)

    def litellm_provider(self) -> str:
        """返回传给 LiteLLM 的 provider 前缀。"""

        return self.provider

    def _get_api_key(self) -> str | None:
        """获取 API key。

        优先级：
        1. 直接配置的 api_key（来自配置文件 api_keys 部分）
        2. 环境变量（api_key_env）
        3. 常见备选环境变量
        """
        # 1. 优先使用配置文件中的 api_keys
        if self.api_key_env and self.api_key_env in self._config_api_keys:
            key = self._config_api_keys[self.api_key_env]
            if key and key != "your-siliconflow-key-here":
                return key

        # 2. 回退到环境变量
        key = os.environ.get(self.api_key_env) if self.api_key_env else None
        if key:
            return key

        # 3. 尝试同 provider 的常见备选名称。不要跨 provider 随便借 key，
        # 否则 deepseek endpoint 可能误拿 SiliconFlow/OpenAI key，错误会变得很难诊断。
        fallback_names = self._fallback_api_key_names()
        for fallback in fallback_names:
            key = os.environ.get(fallback)
            if key:
                _log.info(
                    "using_fallback_api_key",
                    requested=self.api_key_env,
                    fallback=fallback,
                )
                return key
        return None

    def _get_api_base(self) -> str | None:
        """获取 API base URL。

        优先级：
        1. 直接配置的 api_base（来自配置文件）
        2. 环境变量（api_base_env）
        3. 常见备选环境变量
        """
        if self.api_base:
            return self.api_base

        if self.api_base_env:
            # 先检查配置文件中的 api_keys
            if self.api_base_env in self._config_api_keys:
                base = self._config_api_keys[self.api_base_env]
                if base:
                    return base

            # 回退到环境变量
            base = os.environ.get(self.api_base_env)
            if base:
                return base

            # 尝试同 endpoint/provider 的常见备选 base URL。不要把 SiliconFlow
            # base URL 用到 DeepSeek endpoint 上。
            fallback_names = self._fallback_api_base_names()
            for fallback in fallback_names:
                base = os.environ.get(fallback)
                if base:
                    _log.info(
                        "using_fallback_api_base",
                        requested=self.api_base_env,
                        fallback=fallback,
                    )
                    return base
        return None

    def to_litellm_kwargs(self) -> dict[str, Any]:
        """把 endpoint 配置转换成 litellm 需要的关键字参数。

        支持多种配置方式：
        1. 配置文件中的 api_keys 部分（最高优先级）
        2. api_key_env / api_base_env 环境变量引用
        3. 常见备选环境变量
        """
        kwargs: dict[str, Any] = {}

        key = self._get_api_key()
        if key:
            kwargs["api_key"] = key
        elif self.api_key_env:
            # 给出友好提示
            _log.warning(
                "no_api_key_for_endpoint",
                endpoint=self.name,
                api_key_env=self.api_key_env,
                hint="在 config/model_routing.yaml 的 api_keys 部分配置，或设置环境变量",
            )

        api_base = self._get_api_base()
        if api_base:
            kwargs["api_base"] = api_base

        if self.api_version:
            kwargs["api_version"] = self.api_version
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        return kwargs

    def _fallback_api_key_names(self) -> list[str]:
        names: list[str] = []
        provider = self.provider.lower()
        endpoint = self.name.lower()
        if "deepseek" in endpoint:
            names.extend(["DEEPSEEK_API_KEY"])
        if "siliconflow" in endpoint:
            names.extend(["SILICONFLOW_API_KEY"])
        if provider == "openai":
            names.extend(["OPENAI_API_KEY"])
        elif provider == "anthropic":
            names.extend(["ANTHROPIC_API_KEY"])
        elif provider == "openrouter":
            names.extend(["OPENROUTER_API_KEY"])
        return _dedupe_nonempty(names)

    def _fallback_api_base_names(self) -> list[str]:
        names: list[str] = []
        endpoint = self.name.lower()
        if "deepseek" in endpoint:
            names.extend(["DEEPSEEK_BASE_URL", "DEEPSEEK_API_BASE"])
        if "siliconflow" in endpoint:
            names.extend(["SILICONFLOW_BASE_URL", "SILICONFLOW_API_BASE"])
        if self.provider.lower() == "openai":
            names.extend(["OPENAI_BASE_URL", "OPENAI_API_BASE"])
        return _dedupe_nonempty(names)


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
        # 例如 `deepseek-ai/DeepSeek-V4-Flash`。这类名字仍然需要
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
        suppress_litellm_info_logs()
        if not routing_config_path.exists():
            raise ConfigurationError(f"Routing config not found: {routing_config_path}")
        self._load_env_file(routing_config_path)
        self.routing_config_path = routing_config_path
        raw = yaml.safe_load(routing_config_path.read_text(encoding="utf-8")) or {}
        if should_apply_default_user_settings(routing_config_path, DEFAULT_ROUTING_CONFIG_PATH):
            raw = apply_model_routing_overrides(raw, load_user_settings())
        self.raw = raw
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
        # 获取配置文件中直接设置的 api_keys
        api_keys = raw.get("api_keys") or {}

        endpoints = {}
        for name, cfg in (raw.get("endpoints") or {}).items():
            # 将 api_keys 传递给每个 endpoint
            endpoint = Endpoint(name=name, **cfg)
            endpoint._config_api_keys = api_keys
            endpoints[name] = endpoint

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
        endpoint_override: str | None = None,
        max_context_override: int | None = None,
    ) -> list[tuple[ModelBinding, Endpoint]]:
        """解析某次调用的候选模型链路。

        返回值按优先级排序：
        - 第一个是 primary；
        - 后续是 fallback。
        """
        # 只有“改模型”或“改 endpoint”时，才需要把候选链压缩成单条绑定。
        # 单独覆盖 max_context 时，fallback 仍然应该保留；否则像 experimenter
        # 这类在 YAML 里显式设置 llm.max_context 的 agent，会被错误地降成
        # “只有 primary、没有 fallback”的单模型调用。
        if model_override is not None or endpoint_override is not None:
            binding, endpoint = self._resolve_override_binding(
                profile=profile,
                tier=tier,
                model_override=model_override,
                endpoint_override=endpoint_override,
                max_context_override=max_context_override,
            )
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
        if max_context_override is not None:
            # 这里需要给整条候选链统一套用上下文上限，而不是只保留 primary。
            out = [
                (
                    ModelBinding(
                        model=binding.model,
                        endpoint=binding.endpoint,
                        max_context=int(max_context_override),
                    ),
                    endpoint,
                )
                for binding, endpoint in out
            ]
        return out

    def _resolve_override_binding(
        self,
        *,
        profile: str | None,
        tier: str,
        model_override: str | None,
        endpoint_override: str | None,
        max_context_override: int | None,
    ) -> tuple[ModelBinding, Endpoint]:
        if model_override is not None:
            binding, endpoint = self._find_binding_for_override(model_override)
        else:
            binding, endpoint = self.resolve(
                profile=profile,
                tier=tier,
                model_override=None,
            )[0]

        if endpoint_override is not None:
            endpoint = self.endpoints.get(endpoint_override)
            if endpoint is None:
                raise ConfigurationError(f"Unknown endpoint override: {endpoint_override}")

        return (
            ModelBinding(
                model=model_override if model_override is not None else binding.model,
                endpoint=endpoint.name,
                max_context=(
                    max_context_override if max_context_override is not None else binding.max_context
                ),
            ),
            endpoint,
        )

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
        await self.aclose()
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
        endpoint_override: str | None = None,
        max_context_override: int | None = None,
        timeout: int = 120,
        max_retries_per_model: int = 2,
        retry_base_delay: float = 2.0,
    ) -> LLMResponse:
        """执行一次模型调用。

        注意这里有两层保护：
        - 每一轮会把 primary/fallback 候选都试一遍，避免第一个 provider 忙时长时间卡死；
        - 如果这一整轮候选都失败，才进入下一轮重试。
        """
        if litellm is None:
            raise LLMProviderError("litellm is not installed")

        errors: list[str] = []
        candidates = self.resolve(
            profile=profile,
            tier=tier,
            model_override=model_override,
            endpoint_override=endpoint_override,
            max_context_override=max_context_override,
        )
        for attempt in range(max_retries_per_model):
            for binding, endpoint in candidates:
                qualified = binding.qualified(endpoint)
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
                    # 对 LiteLLM 再包一层 runtime 级硬超时，避免 provider/SDK
                    # 未按预期尊重 timeout 时单次调用悬挂过久。
                    try:
                        raw = await asyncio.wait_for(
                            litellm.acompletion(**kwargs),
                            timeout=max(float(timeout), 0.001),
                        )
                    finally:
                        await self.aclose()
                    choices = getattr(raw, "choices", None)
                    if not choices:
                        raise RuntimeError("LLM provider returned an empty choices list")
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
                    errors.append(
                        f"{qualified}@{endpoint.name} attempt {attempt + 1}: {exc!r} "
                        f"({self._endpoint_debug_hint(endpoint, qualified)})"
                    )
            if attempt < max_retries_per_model - 1:
                await asyncio.sleep(min(retry_base_delay * (2**attempt), 8))
        raise LLMProviderError(
            f"All candidates failed (profile={profile or self.default_profile_name}, "
            f"tier={tier}). Errors: {errors}"
        )

    @staticmethod
    def _endpoint_debug_hint(endpoint: Endpoint, qualified_model: str) -> str:
        kwargs = endpoint.to_litellm_kwargs()
        base = kwargs.get("api_base") or "<unset>"
        has_key = bool(kwargs.get("api_key"))
        return (
            f"endpoint={endpoint.name}, provider={endpoint.provider}, model={qualified_model}, "
            f"api_base={base}, api_key={'set' if has_key else 'missing'}; "
            "OpenAI-compatible DeepSeek endpoints usually require model=openai/<model>, "
            "DEEPSEEK_API_KEY, and DEEPSEEK_BASE_URL such as https://api.deepseek.com"
        )

    async def aclose(self) -> None:
        """Close LiteLLM async HTTP clients created by this process."""

        if litellm is None:
            return
        closer = getattr(litellm, "close_litellm_async_clients", None)
        try:
            if closer is not None:
                maybe_awaitable = closer()
                if inspect.isawaitable(maybe_awaitable):
                    await maybe_awaitable
            for attr_name in ("client_session", "aclient_session"):
                session = getattr(litellm, attr_name, None)
                if session is None:
                    continue
                await self._close_async_client_like(session)
                try:
                    setattr(litellm, attr_name, None)
                except Exception:
                    pass
        except Exception as exc:  # pragma: no cover - cleanup failure should not mask LLM error
            _log.warning("litellm_async_client_cleanup_failed", error=repr(exc))

    @staticmethod
    async def _close_async_client_like(client: Any) -> None:
        """Best-effort close for aiohttp/httpx-style async clients."""

        if getattr(client, "closed", False):
            return
        close = getattr(client, "aclose", None) or getattr(client, "close", None)
        if not callable(close):
            return
        maybe_awaitable = close()
        if inspect.isawaitable(maybe_awaitable):
            await maybe_awaitable

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
