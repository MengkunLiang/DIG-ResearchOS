from __future__ import annotations

"""LLM 路由与 provider 调用层。"""

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field, replace
import inspect
import logging
import os
from pathlib import Path
import time
from typing import Any, Awaitable
from urllib.parse import quote

import httpx
import yaml

from .errors import ConfigurationError, LLMProviderError
from .logger import get_logger
from .rate_limiter import EndpointRateLimiter
from .model_settings import (
    DEFAULT_MODEL_SETTINGS_PATH as DEFAULT_USER_MODEL_SETTINGS_PATH,
    build_single_model_runtime_config,
    load_dotenv_for_model_settings,
    provider_requires_api_base,
)


def _pre_suppress_litellm_import_logs() -> None:
    """Suppress LiteLLM import-time network work before importing litellm.

    LiteLLM can emit Bedrock/SageMaker preload warnings during module import,
    before ResearchOS has a chance to call `configure_logging`.  Those warnings
    are not actionable for normal OpenAI-compatible routes and they make even
    config-only CLI commands look broken, so suppress them at the boundary.
    LiteLLM can also fetch a model-cost map synchronously during import.  Cost
    metadata must never delay a workspace command or prevent Ctrl+C from being
    installed, so use LiteLLM's bundled map unless an operator explicitly
    overrides this environment variable before starting ResearchOS.
    """

    os.environ.setdefault("LITELLM_LOG", "ERROR")
    os.environ.setdefault("LITELLM_VERBOSE", "False")
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
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
DEFAULT_MODEL_SETTINGS_PATH = DEFAULT_USER_MODEL_SETTINGS_PATH


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
        # 1. The public single-model config stores the key directly on the
        # endpoint. Legacy configs may still reference the api_keys mapping.
        if self.api_key:
            return self.api_key

        # 2. Legacy api_keys mapping.
        if self.api_key_env and self.api_key_env in self._config_api_keys:
            key = self._config_api_keys[self.api_key_env]
            if key and key != "your-siliconflow-key-here":
                return key

        # 3. Environment-variable fallback.
        key = os.environ.get(self.api_key_env) if self.api_key_env else None
        if key:
            return key

        # 4. 尝试同 provider 的常见备选名称。不要跨 provider 随便借 key，
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
                hint="在 config/model_settings.yaml 配置 api_key，或设置 provider 环境变量",
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


@dataclass(frozen=True)
class ContextWindowInfo:
    """The effective context window and the evidence used to obtain it.

    ``configured_fallback`` deliberately remains a first-class source.  A
    number of OpenAI-compatible relays expose a ``/models`` list but do not
    publish a context length, so inventing a larger capacity would make the
    caller less reliable rather than more capable.
    """

    max_context: int
    source: str
    detail: str | None = None


class LLMClient:
    """ResearchOS 对 LiteLLM 的薄封装。

    New runs load one connection from ``model_settings.yaml``.  The legacy
    endpoint/profile format is still readable only so existing deployments and
    historical test fixtures do not fail during migration.
    """

    def __init__(self, model_settings_path: Path | None = None):
        suppress_litellm_info_logs()
        self.model_settings_path = (model_settings_path or DEFAULT_MODEL_SETTINGS_PATH).resolve()
        self.routing_config_path = self.model_settings_path  # legacy diagnostic alias
        load_dotenv_for_model_settings(self.model_settings_path)
        source = self._load_source_config(self.model_settings_path)
        if self._is_legacy_routing_config(source):
            self.raw = source
        else:
            self.raw = build_single_model_runtime_config(self.model_settings_path)
        self.single_model_mode = bool(self.raw.get("_simple_llm_mode"))
        self.endpoints = self._parse_endpoints(self.raw)
        self.profiles = self._parse_profiles(self.raw)
        self.default_profile_name = self.raw.get("default_profile", "default")
        self.truncation_cfg = self.raw.get("truncation", {})
        self.rate_limiter = EndpointRateLimiter(self.raw.get("endpoints") or {})
        # Cache is intentionally scoped to one LLMClient/run.  Provider model
        # metadata can change after an endpoint deployment; persistent caching
        # would make a previous deployment's context window look authoritative.
        self._context_window_cache: dict[tuple[str, str], ContextWindowInfo] = {}
        self._context_discovery_failures: set[tuple[str, str]] = set()
        # An invalid credential cannot recover from a ten-second cooldown.
        # Keep this cache only for the current client/run: a later `resume`
        # constructs a fresh client and may legitimately use repaired config.
        self._disabled_auth_endpoints: set[str] = set()
        self._validate()

    @staticmethod
    def _load_source_config(path: Path) -> dict[str, Any]:
        if not path.exists() or path.name == "__researchos_model_settings_disabled__":
            return {}
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"Invalid model settings YAML: {path}: {exc}") from exc
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _is_legacy_routing_config(raw: dict[str, Any]) -> bool:
        return isinstance(raw.get("endpoints"), dict) and isinstance(raw.get("profiles"), dict)

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
            raise ConfigurationError("LLM configuration has no endpoint")
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
            raise ConfigurationError("LLM configuration has no model binding")
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
        if self.single_model_mode:
            # The public configuration deliberately has one model. Historical
            # AgentSpec tier/profile/direct overrides remain readable for old
            # workspaces but cannot silently route a new run elsewhere.
            tier_cfg = self.profiles[self.default_profile_name].tiers["standard"]
            binding = tier_cfg.primary
            endpoint = self.endpoints[binding.endpoint]
            return [(self._binding_with_discovered_context(binding, endpoint), endpoint)]

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
            if max_context_override is not None:
                return [(binding, endpoint)]
            return [(self._binding_with_discovered_context(binding, endpoint), endpoint)]
        profile_name = profile or self.default_profile_name
        profile_obj = self.profiles.get(profile_name)
        if profile_obj is None:
            raise ConfigurationError(f"Unknown profile: {profile_name}")
        tier_cfg = profile_obj.tiers.get(tier) or profile_obj.tiers.get("standard")
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
        return [
            (self._binding_with_discovered_context(binding, endpoint), endpoint)
            for binding, endpoint in out
        ]

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
            # Resolve the configured primary directly rather than through
            # ``resolve()``.  The latter may already carry a discovered
            # context value for its original endpoint; carrying that value to
            # a different endpoint override would make one provider's model
            # metadata look like another provider's fallback capacity.
            profile_name = profile or self.default_profile_name
            profile_obj = self.profiles.get(profile_name)
            if profile_obj is None:
                raise ConfigurationError(f"Unknown profile: {profile_name}")
            tier_cfg = profile_obj.tiers.get(tier) or profile_obj.tiers.get("standard")
            if tier_cfg is None:
                raise ConfigurationError(f"Profile '{profile_name}' has no tier '{tier}'")
            binding = tier_cfg.primary
            endpoint = self.endpoints[binding.endpoint]

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

    def get_context_window_info(
        self,
        binding: ModelBinding,
        endpoint: Endpoint | None = None,
        *,
        explicit_override: bool = False,
    ) -> ContextWindowInfo:
        """Report why a binding has the context value it currently has."""

        if explicit_override:
            return ContextWindowInfo(
                max_context=binding.max_context,
                source="explicit_override",
                detail="由 task/CLI 的显式 max_context 上限指定",
            )
        if endpoint is not None:
            cached = self._context_window_cache.get(self._context_cache_key(binding, endpoint))
            if cached is not None:
                return cached
        return ContextWindowInfo(
            max_context=binding.max_context,
            source="configured_fallback",
            detail="服务端模型元数据未提供可验证的上下文窗口时使用的保守回退值",
        )

    async def discover_context_window(
        self,
        binding: ModelBinding,
        endpoint: Endpoint,
    ) -> ContextWindowInfo:
        """Best-effort discover a model context window from provider metadata.

        The method never makes a research task fail.  It asks the configured
        endpoint's OpenAI-compatible model metadata API first and only changes
        the effective window when the response names the routed model and
        exposes a plausible capacity.  A failed/missing endpoint is cached for
        this client instance so repeated agent turns do not repeatedly wait on
        a metadata request that the relay cannot serve.
        """

        key = self._context_cache_key(binding, endpoint)
        cached = self._context_window_cache.get(key)
        if cached is not None:
            return cached
        if key in self._context_discovery_failures:
            return self.get_context_window_info(binding, endpoint)

        try:
            payloads = await self._fetch_model_metadata(binding=binding, endpoint=endpoint)
            for payload, source_url in payloads:
                context_window = self._extract_context_window(payload, binding)
                if context_window is None:
                    continue
                info = ContextWindowInfo(
                    max_context=context_window,
                    source="provider_metadata",
                    detail=f"provider metadata: {source_url}",
                )
                self._context_window_cache[key] = info
                _log.info(
                    "llm_context_window_discovered",
                    endpoint=endpoint.name,
                    model=binding.model,
                    max_context=context_window,
                    source="provider_metadata",
                )
                return info
            reason = "metadata_missing_context_length"
        except Exception as exc:  # Discovery must never block a normal model call.
            reason = f"metadata_unavailable:{type(exc).__name__}"

        self._context_discovery_failures.add(key)
        _log.info(
            "llm_context_window_fallback",
            endpoint=endpoint.name,
            model=binding.model,
            max_context=binding.max_context,
            reason=reason,
        )
        return self.get_context_window_info(binding, endpoint)

    def _binding_with_discovered_context(
        self,
        binding: ModelBinding,
        endpoint: Endpoint,
    ) -> ModelBinding:
        cached = self._context_window_cache.get(self._context_cache_key(binding, endpoint))
        if cached is None:
            return binding
        return replace(binding, max_context=cached.max_context)

    @staticmethod
    def _context_cache_key(binding: ModelBinding, endpoint: Endpoint) -> tuple[str, str]:
        return endpoint.name, binding.model.strip().casefold()

    async def _fetch_model_metadata(
        self,
        *,
        binding: ModelBinding,
        endpoint: Endpoint,
    ) -> list[tuple[Any, str]]:
        """Fetch direct and collection metadata without exposing credentials."""

        api_base = endpoint._get_api_base() or self._default_metadata_api_base(endpoint)
        if not api_base:
            return []
        base = api_base.rstrip("/")
        headers: dict[str, str] = {"Accept": "application/json"}
        api_key = endpoint._get_api_key()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        headers.update(
            {
                str(key): str(value)
                for key, value in endpoint.extra_headers.items()
                if value is not None
            }
        )
        raw_model_id = binding.model.strip().strip("/")
        model_ids = [raw_model_id]
        if "/" in raw_model_id:
            model_ids.append(raw_model_id.rsplit("/", 1)[-1])

        # OpenAI-compatible deployments are inconsistent about whether the
        # configured base already contains ``/v1``. Try both forms and both
        # the direct-record and collection endpoints. All requests are tiny,
        # concurrent, cached for this client, and never block a model call.
        api_bases = [base]
        if base.endswith("/v1"):
            api_bases.append(base[:-3].rstrip("/"))
        else:
            api_bases.append(f"{base}/v1")
        urls: list[str] = []
        for candidate_base in dict.fromkeys(api_bases):
            for candidate_model_id in dict.fromkeys(model_ids):
                urls.append(f"{candidate_base}/models/{quote(candidate_model_id, safe='')}")
            urls.append(f"{candidate_base}/models")
        timeout = httpx.Timeout(connect=1.0, read=1.5, write=1.0, pool=1.0)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            async def _fetch_one(url: str) -> tuple[Any, str] | None:
                try:
                    response = await client.get(url)
                    if response.status_code >= 400:
                        return None
                    return response.json(), url
                except (httpx.HTTPError, ValueError):
                    return None

            results = await asyncio.gather(*(_fetch_one(url) for url in urls))
        return [result for result in results if result is not None]

    @staticmethod
    def _default_metadata_api_base(endpoint: Endpoint) -> str | None:
        """Known provider base only when the endpoint config leaves it implicit."""

        if endpoint.provider.casefold() == "openrouter":
            return "https://openrouter.ai/api/v1"
        return None

    @classmethod
    def _extract_context_window(cls, payload: Any, binding: ModelBinding) -> int | None:
        """Read standard context-capacity fields from a matched model record."""

        records = cls._model_records(payload)
        if not records and isinstance(payload, dict):
            records = [payload]
        matched = [record for record in records if cls._record_matches_binding(record, binding)]
        # A direct ``/models/<model>`` response need not repeat the model id.
        # It is safe to inspect it only when there is exactly one returned
        # record, rather than guessing from a large collection response.
        candidates = matched or (records if len(records) == 1 else [])
        for record in candidates:
            context_window = cls._context_from_record(record)
            if context_window is not None:
                return context_window
        return None

    @staticmethod
    def _model_records(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("data", "models", "result"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                return [value]
        return [payload]

    @classmethod
    def _record_matches_binding(cls, record: dict[str, Any], binding: ModelBinding) -> bool:
        model_id = record.get("id") or record.get("model") or record.get("name")
        if not isinstance(model_id, str) or not model_id.strip():
            return False
        return bool(cls._model_aliases(model_id) & cls._model_aliases(binding.model))

    @staticmethod
    def _model_aliases(model: str) -> set[str]:
        value = model.strip().casefold().strip("/")
        if not value:
            return set()
        prefixes = {"openai", "openrouter", "anthropic", "deepseek"}
        aliases = {value}
        parts = value.split("/")
        while len(parts) > 1 and parts[0] in prefixes:
            parts = parts[1:]
            aliases.add("/".join(parts))
        aliases.add(parts[-1])
        return aliases

    @classmethod
    def _context_from_record(cls, record: dict[str, Any]) -> int | None:
        for field_name in (
            "context_length",
            "context_window",
            "max_context",
            "max_context_length",
            "max_input_tokens",
            "context_size",
            "input_token_limit",
            "max_input_length",
        ):
            value = record.get(field_name)
            parsed = cls._plausible_context_window(value)
            if parsed is not None:
                return parsed
        for nested_key in (
            "capabilities",
            "architecture",
            "limits",
            "metadata",
            "top_provider",
            "model_info",
            "details",
            "parameters",
            "input",
        ):
            nested = record.get(nested_key)
            if isinstance(nested, dict):
                context_window = cls._context_from_record(nested)
                if context_window is not None:
                    return context_window
        return None

    @staticmethod
    def _plausible_context_window(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            numeric = int(float(value))
        except (TypeError, ValueError):
            return None
        # Smaller values are almost certainly output limits, prices, or an
        # unrelated capability field; values above this bound are malformed.
        if 4_096 <= numeric <= 100_000_000:
            return numeric
        return None

    def get_truncation_config(self) -> dict[str, Any]:
        return self.truncation_cfg

    def _any_binding_for(self, endpoint_name: str) -> ModelBinding | None:
        for profile in self.profiles.values():
            for tier in profile.tiers.values():
                for binding in [tier.primary, *tier.fallback]:
                    if binding.endpoint == endpoint_name:
                        return binding
        return None

    def configuration_status(self) -> dict[str, Any]:
        """Return a concise readiness report for the public one-model setup."""

        if not self.single_model_mode:
            return {"ready": True, "mode": "legacy_routing", "missing": []}
        endpoint = self.endpoints["default"]
        configured = self.raw.get("_simple_llm") or {}
        missing: list[str] = []
        provider = str(configured.get("provider") or "openai_compatible")
        if provider_requires_api_base(provider) and not str(configured.get("api_base") or "").strip():
            missing.append("api_base")
        if not str(configured.get("model") or "").strip():
            missing.append("model")
        if bool(configured.get("api_key_required", True)) and not endpoint._get_api_key():
            missing.append("api_key")
        return {
            "ready": not missing,
            "mode": "single_model",
            "missing": missing,
            "provider": provider,
            "api_base": str(configured.get("api_base") or ""),
            "model": str(configured.get("model") or ""),
            "settings_path": str(configured.get("settings_path") or self.model_settings_path),
            "dotenv_path": str(configured.get("dotenv_path") or ""),
            "fallback": dict(configured.get("fallback") or {}),
        }

    async def selftest(self, profiles_to_check: list[str] | None = None) -> dict[str, dict[str, Any]]:
        """对 profile 涉及的 endpoint 做最小连通性检查。"""
        if litellm is None:
            raise LLMProviderError("litellm is not installed")
        try:
            profiles = [self.default_profile_name] if self.single_model_mode else (profiles_to_check or [self.default_profile_name])
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
        finally:
            await self.aclose()

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
        max_retries_per_model: int | None = None,
        retry_base_delay: float | None = None,
    ) -> LLMResponse:
        """执行一次模型调用。

        注意这里有两层保护：
        - 每一轮会把 primary/fallback 候选都试一遍，避免第一个 provider 忙时长时间卡死；
        - 如果这一整轮候选都失败，才进入下一轮重试。
        """
        if litellm is None:
            raise LLMProviderError("litellm is not installed")

        recovery = self.raw.get("_simple_llm", {}).get("fallback") if self.single_model_mode else {}
        if not isinstance(recovery, dict):
            recovery = {}
        resolved_attempts = max_retries_per_model
        if resolved_attempts is None:
            resolved_attempts = int(recovery.get("max_attempts") or 2)
        resolved_attempts = max(1, min(int(resolved_attempts), 10))
        resolved_delay = retry_base_delay
        if resolved_delay is None:
            resolved_delay = float(recovery.get("initial_wait_seconds") or 2.0)
        resolved_delay = max(0.0, float(resolved_delay))
        resolved_max_delay = max(resolved_delay, float(recovery.get("max_wait_seconds") or 8.0))
        retry_after_timeout = bool(recovery.get("retry_after_timeout", False)) if self.single_model_mode else False

        try:
            errors: list[str] = []
            candidates = self.resolve(
                profile=profile,
                tier=tier,
                model_override=model_override,
                endpoint_override=endpoint_override,
                max_context_override=max_context_override,
            )
            candidates = [
                (binding, endpoint)
                for binding, endpoint in candidates
                if endpoint.name not in self._disabled_auth_endpoints
            ]
            if not candidates:
                raise LLMProviderError(
                    "All configured model endpoints are unavailable because authentication was rejected."
            )
            for attempt in range(resolved_attempts):
                round_timed_out = False
                active_candidate_seen = False
                for binding, endpoint in candidates:
                    if endpoint.name in self._disabled_auth_endpoints:
                        continue
                    active_candidate_seen = True
                    if max_context_override is None:
                        await self.discover_context_window(binding, endpoint)
                        binding = self._binding_with_discovered_context(binding, endpoint)
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
                        # Do not await a provider task after cancelling it.  Some SDKs
                        # suppress cancellation while they clean up a stuck connection;
                        # asyncio.wait_for then waits for that cleanup and defeats the
                        # configured timeout.
                        raw = await self._completion_with_hard_timeout(
                            litellm.acompletion(**kwargs),
                            timeout=float(timeout),
                        )
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
                        if self._is_authentication_failure(exc):
                            self._disabled_auth_endpoints.add(endpoint.name)
                        round_timed_out = round_timed_out or isinstance(exc, asyncio.TimeoutError)
                        errors.append(
                            f"{qualified}@{endpoint.name} attempt {attempt + 1}: {exc!r} "
                            f"({self._endpoint_debug_hint(endpoint, qualified)})"
                        )
                if not active_candidate_seen:
                    break
                # Legacy multi-provider routing stops after a hard timeout. A public
                # one-model configuration may explicitly retry the same provider after
                # waiting, which is useful for short-lived provider congestion.
                if round_timed_out and not retry_after_timeout:
                    break
                if attempt < resolved_attempts - 1:
                    await asyncio.sleep(min(resolved_delay * (2**attempt), resolved_max_delay))
            if self.single_model_mode:
                model = self.raw.get("_simple_llm", {}).get("model") or "configured model"
                raise LLMProviderError(f"Configured model {model} is unavailable. Errors: {errors}")
            raise LLMProviderError(
                f"All candidates failed (profile={profile or self.default_profile_name}, "
                f"tier={tier}). Errors: {errors}"
            )
        finally:
            await self.aclose()

    @staticmethod
    def _is_authentication_failure(exc: Exception) -> bool:
        """Identify credentials that must not be retried in this client run."""

        text = f"{type(exc).__name__}: {exc}".casefold()
        return any(
            marker in text
            for marker in (
                "authenticationerror",
                "authentication error",
                "invalid_api_key",
                "invalid api key",
                "unauthorized",
                "status code: 401",
                "status_code=401",
                "http 401",
            )
        )

    @staticmethod
    def _consume_detached_task_result(task: asyncio.Future[Any]) -> None:
        """Consume a cancelled provider task once it eventually settles."""

        with suppress(asyncio.CancelledError, Exception):
            task.result()

    async def _completion_with_hard_timeout(self, awaitable: Awaitable[Any], *, timeout: float) -> Any:
        """Return promptly when a provider ignores cancellation after a timeout.

        ``asyncio.wait_for`` waits for a cancelled coroutine to finish its cancellation
        cleanup.  That is normally desirable, but it lets a non-cooperative provider
        client hold the pipeline indefinitely.  This helper detaches the cancelled task
        after recording its eventual result, while the caller proceeds to fallback or a
        recoverable provider error.
        """

        task = asyncio.ensure_future(awaitable)
        try:
            done, _pending = await asyncio.wait(
                {task},
                timeout=max(float(timeout), 0.001),
            )
        except BaseException:
            task.add_done_callback(self._consume_detached_task_result)
            task.cancel()
            raise
        if task in done:
            return task.result()

        task.add_done_callback(self._consume_detached_task_result)
        task.cancel()
        raise asyncio.TimeoutError(f"LLM call exceeded {timeout:.0f}s")

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
            for attr_name in (
                "client_session",
                "aclient_session",
                "async_client",
                "_async_client",
                "httpx_async_client",
                "_httpx_async_client",
            ):
                session = getattr(litellm, attr_name, None)
                if session is None:
                    continue
                await self._close_async_client_like(session)
                try:
                    setattr(litellm, attr_name, None)
                except Exception:
                    pass
            cache = getattr(litellm, "in_memory_llm_clients_cache", None)
            if isinstance(cache, dict):
                for client in list(cache.values()):
                    await self._close_async_client_like(client)
                cache.clear()
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
