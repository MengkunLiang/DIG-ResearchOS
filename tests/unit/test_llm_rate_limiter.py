import asyncio
import types

import pytest

from researchos.runtime.errors import LLMProviderError
from researchos.runtime.llm_client import LLMClient


class _FakeUsage:
    prompt_tokens = 11
    completion_tokens = 13


class _FakeResponse:
    choices = [object()]
    usage = _FakeUsage()
    _hidden_params = {"response_cost": 0.02}


async def _fake_acompletion(**kwargs):
    return _FakeResponse()


def _write_routing(path, *, api_key_env="TEST_API_KEY"):
    path.write_text(
        f"""
default_profile: default

endpoints:
  relay:
    provider: openai
    api_key_env: {api_key_env}
    rate_limit:
      tokens_per_minute: 1000
      burst: 1000

profiles:
  default:
    medium:
      primary:
        model: gpt-4o-mini
        endpoint: relay
        max_context: 32000
""".strip(),
        encoding="utf-8",
    )


def test_model_binding_qualifies_openai_compatible_model_names_with_slashes(tmp_path):
    routing = tmp_path / "model_routing.yaml"
    routing.write_text(
        """
default_profile: default

endpoints:
  relay:
    provider: openai
    api_key_env: TEST_API_KEY
    api_base_env: TEST_API_BASE

profiles:
  default:
    medium:
      primary:
        model: deepseek-ai/DeepSeek-V4-Flash
        endpoint: relay
        max_context: 128000
""".strip(),
        encoding="utf-8",
    )

    client = LLMClient(routing)
    binding, endpoint = client.resolve(profile=None, tier="medium", model_override=None)[0]

    assert binding.qualified(endpoint) == "openai/deepseek-ai/DeepSeek-V4-Flash"


def test_profile_can_mix_providers_per_tier_and_fallback(tmp_path):
    routing = tmp_path / "model_routing.yaml"
    routing.write_text(
        """
default_profile: mixed

endpoints:
  siliconflow:
    provider: openai
    api_key_env: SILICONFLOW_API_KEY
    api_base_env: SILICONFLOW_BASE_URL
  anthropic_main:
    provider: anthropic
    api_key_env: ANTHROPIC_API_KEY

profiles:
  mixed:
    heavy:
      primary:
        model: deepseek-ai/DeepSeek-V4-Flash
        endpoint: siliconflow
        max_context: 128000
      fallback:
        - model: claude-3-5-sonnet-20241022
          endpoint: anthropic_main
          max_context: 200000
    medium:
      primary:
        model: claude-3-5-haiku-20241022
        endpoint: anthropic_main
        max_context: 200000
""".strip(),
        encoding="utf-8",
    )

    client = LLMClient(routing)
    heavy = client.resolve(profile="mixed", tier="heavy", model_override=None)
    medium = client.resolve(profile="mixed", tier="medium", model_override=None)

    assert [(binding.qualified(endpoint), endpoint.name) for binding, endpoint in heavy] == [
        ("openai/deepseek-ai/DeepSeek-V4-Flash", "siliconflow"),
        ("anthropic/claude-3-5-sonnet-20241022", "anthropic_main"),
    ]
    assert [(binding.qualified(endpoint), endpoint.name) for binding, endpoint in medium] == [
        ("anthropic/claude-3-5-haiku-20241022", "anthropic_main"),
    ]


def test_resolve_supports_direct_model_and_endpoint_override(tmp_path):
    routing = tmp_path / "model_routing.yaml"
    routing.write_text(
        """
default_profile: default

endpoints:
  siliconflow:
    provider: openai
    api_key_env: SILICONFLOW_API_KEY
    api_base_env: SILICONFLOW_BASE_URL
  openrouter_main:
    provider: openrouter
    api_key_env: OPENROUTER_API_KEY

profiles:
  default:
    medium:
      primary:
        model: gpt-4o-mini
        endpoint: siliconflow
        max_context: 32000
""".strip(),
        encoding="utf-8",
    )

    client = LLMClient(routing)
    binding, endpoint = client.resolve(
        profile="default",
        tier="medium",
        model_override="openrouter/openai/gpt-4o-mini",
        endpoint_override="openrouter_main",
        max_context_override=128000,
    )[0]

    assert endpoint.name == "openrouter_main"
    assert binding.model == "openrouter/openai/gpt-4o-mini"
    assert binding.max_context == 128000


@pytest.mark.asyncio
async def test_llm_client_selftest_uses_profile_endpoints(tmp_path, monkeypatch):
    routing = tmp_path / "model_routing.yaml"
    _write_routing(routing)
    monkeypatch.setenv("TEST_API_KEY", "secret")
    seen_models: list[str] = []

    async def fake_acompletion(**kwargs):
        seen_models.append(kwargs["model"])
        return await _fake_acompletion(**kwargs)

    monkeypatch.setattr(
        "researchos.runtime.llm_client.litellm",
        types.SimpleNamespace(acompletion=fake_acompletion, token_counter=lambda **_: 42),
    )

    client = LLMClient(routing)
    result = await client.selftest()

    assert result["relay"]["ok"] is True
    assert result["relay"]["latency_ms"] >= 0
    assert seen_models == ["openai/gpt-4o-mini"]


@pytest.mark.asyncio
async def test_llm_client_chat_waits_on_rate_limiter(tmp_path, monkeypatch):
    routing = tmp_path / "model_routing.yaml"
    _write_routing(routing)
    monkeypatch.setenv("TEST_API_KEY", "secret")
    monkeypatch.setattr(
        "researchos.runtime.llm_client.litellm",
        types.SimpleNamespace(acompletion=_fake_acompletion, token_counter=lambda **_: 12),
    )

    client = LLMClient(routing)
    calls: list[tuple[str, int]] = []

    async def fake_wait(endpoint_name: str, estimated_tokens: int) -> None:
        calls.append((endpoint_name, estimated_tokens))

    client.rate_limiter.wait = fake_wait

    response = await client.chat(
        messages=[{"role": "user", "content": "ping"}],
        tools=None,
        temperature=0.0,
        tier="medium",
    )

    assert response.endpoint_used == "relay"
    assert calls and calls[0][0] == "relay"
    assert calls[0][1] >= 4000


@pytest.mark.asyncio
async def test_llm_client_chat_enforces_runtime_hard_timeout(tmp_path, monkeypatch):
    routing = tmp_path / "model_routing.yaml"
    _write_routing(routing)
    monkeypatch.setenv("TEST_API_KEY", "secret")

    async def slow_acompletion(**kwargs):
        await asyncio.sleep(0.05)
        return _FakeResponse()

    cleanup_calls = 0

    async def fake_close_clients():
        nonlocal cleanup_calls
        cleanup_calls += 1

    monkeypatch.setattr(
        "researchos.runtime.llm_client.litellm",
        types.SimpleNamespace(
            acompletion=slow_acompletion,
            token_counter=lambda **_: 12,
            close_litellm_async_clients=fake_close_clients,
        ),
    )

    client = LLMClient(routing)
    async def noop_wait(*args, **kwargs):
        return None
    client.rate_limiter.wait = noop_wait

    with pytest.raises(LLMProviderError) as exc_info:
        await client.chat(
            messages=[{"role": "user", "content": "ping"}],
            tools=None,
            temperature=0.0,
            tier="medium",
            timeout=0.01,
            max_retries_per_model=1,
        )

    assert "TimeoutError" in str(exc_info.value)
    assert cleanup_calls == 1


@pytest.mark.asyncio
async def test_llm_client_chat_uses_configurable_retry_delay(tmp_path, monkeypatch):
    routing = tmp_path / "model_routing.yaml"
    _write_routing(routing)
    monkeypatch.setenv("TEST_API_KEY", "secret")

    async def failing_acompletion(**kwargs):
        raise RuntimeError("boom")

    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(
        "researchos.runtime.llm_client.litellm",
        types.SimpleNamespace(acompletion=failing_acompletion, token_counter=lambda **_: 12),
    )
    monkeypatch.setattr("researchos.runtime.llm_client.asyncio.sleep", fake_sleep)

    client = LLMClient(routing)
    async def noop_wait(*args, **kwargs):
        return None
    client.rate_limiter.wait = noop_wait

    with pytest.raises(LLMProviderError):
        await client.chat(
            messages=[{"role": "user", "content": "ping"}],
            tools=None,
            temperature=0.0,
            tier="medium",
            timeout=1,
            max_retries_per_model=2,
            retry_base_delay=0.25,
        )

    assert delays == [0.25]


@pytest.mark.asyncio
async def test_llm_client_chat_tries_fallback_before_repeating_primary(tmp_path, monkeypatch):
    routing = tmp_path / "model_routing.yaml"
    routing.write_text(
        """
default_profile: default

endpoints:
  siliconflow:
    provider: openai
    api_key_env: TEST_API_KEY
    api_base_env: TEST_API_BASE

profiles:
  default:
    medium:
      primary:
        model: deepseek-ai/DeepSeek-V4-Flash
        endpoint: siliconflow
        max_context: 128000
      fallback:
        - model: Pro/MiniMaxAI/MiniMax-M2.5
          endpoint: siliconflow
          max_context: 128000
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_API_KEY", "secret")

    calls: list[str] = []

    async def fake_acompletion(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "openai/deepseek-ai/DeepSeek-V4-Flash":
            raise TimeoutError("primary timeout")
        return _FakeResponse()

    monkeypatch.setattr(
        "researchos.runtime.llm_client.litellm",
        types.SimpleNamespace(acompletion=fake_acompletion, token_counter=lambda **_: 12),
    )

    client = LLMClient(routing)

    async def noop_wait(*args, **kwargs):
        return None

    client.rate_limiter.wait = noop_wait

    response = await client.chat(
        messages=[{"role": "user", "content": "ping"}],
        tools=None,
        temperature=0.0,
        tier="medium",
        timeout=1,
        max_retries_per_model=3,
        retry_base_delay=0.25,
    )

    assert response.model_used == "openai/Pro/MiniMaxAI/MiniMax-M2.5"
    assert calls[:2] == [
        "openai/deepseek-ai/DeepSeek-V4-Flash",
        "openai/Pro/MiniMaxAI/MiniMax-M2.5",
    ]


def test_resolve_keeps_fallback_chain_when_only_max_context_is_overridden(tmp_path):
    routing = tmp_path / "model_routing.yaml"
    routing.write_text(
        """
default_profile: default

endpoints:
  siliconflow:
    provider: openai
    api_key_env: TEST_API_KEY

profiles:
  default:
    medium:
      primary:
        model: deepseek-ai/DeepSeek-V4-Flash
        endpoint: siliconflow
        max_context: 64000
      fallback:
        - model: Pro/MiniMaxAI/MiniMax-M2.5
          endpoint: siliconflow
          max_context: 64000
""".strip(),
        encoding="utf-8",
    )

    client = LLMClient(routing)
    resolved = client.resolve(
        profile="default",
        tier="medium",
        model_override=None,
        endpoint_override=None,
        max_context_override=128000,
    )

    assert [binding.model for binding, _ in resolved] == [
        "deepseek-ai/DeepSeek-V4-Flash",
        "Pro/MiniMaxAI/MiniMax-M2.5",
    ]
    assert [binding.max_context for binding, _ in resolved] == [128000, 128000]
