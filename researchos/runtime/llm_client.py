from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import os
from pathlib import Path
import time
from typing import Any

import yaml

from .errors import ConfigurationError, LLMProviderError
from .logger import get_logger

try:  # pragma: no cover - optional import exercised in integration use
    import litellm
except Exception:  # pragma: no cover
    litellm = None


_log = get_logger("llm_client")


@dataclass
class Endpoint:
    name: str
    provider: str
    api_key_env: str | None = None
    api_base: str | None = None
    api_version: str | None = None
    extra_headers: dict[str, Any] = field(default_factory=dict)

    def to_litellm_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self.api_key_env:
            key = os.environ.get(self.api_key_env)
            if not key:
                raise ConfigurationError(
                    f"Endpoint '{self.name}' requires env var {self.api_key_env}"
                )
            kwargs["api_key"] = key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_version:
            kwargs["api_version"] = self.api_version
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        return kwargs


@dataclass
class ModelBinding:
    model: str
    endpoint: str
    max_context: int = 100_000

    def qualified(self, endpoint_obj: Endpoint) -> str:
        if "/" in self.model:
            return self.model
        return f"{endpoint_obj.provider}/{self.model}"


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
    def __init__(self, routing_config_path: Path):
        if not routing_config_path.exists():
            raise ConfigurationError(f"Routing config not found: {routing_config_path}")
        self.routing_config_path = routing_config_path
        self.raw = yaml.safe_load(routing_config_path.read_text(encoding="utf-8")) or {}
        self.endpoints = self._parse_endpoints(self.raw)
        self.profiles = self._parse_profiles(self.raw)
        self.default_profile_name = self.raw.get("default_profile", "default")
        self.truncation_cfg = self.raw.get("truncation", {})
        self._validate()

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
        if litellm is None:
            raise LLMProviderError("litellm is not installed")

        errors: list[str] = []
        candidates = self.resolve(profile=profile, tier=tier, model_override=model_override)
        for binding, endpoint in candidates:
            qualified = binding.qualified(endpoint)
            for attempt in range(max_retries_per_model):
                started = time.time()
                try:
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

