import types

import pytest

from researchos.runtime.llm_client import LLMClient


class _FakeUsage:
    prompt_tokens = 11
    completion_tokens = 13


class _FakeResponse:
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


@pytest.mark.asyncio
async def test_llm_client_selftest_uses_profile_endpoints(tmp_path, monkeypatch):
    routing = tmp_path / "model_routing.yaml"
    _write_routing(routing)
    monkeypatch.setenv("TEST_API_KEY", "secret")
    monkeypatch.setattr(
        "researchos.runtime.llm_client.litellm",
        types.SimpleNamespace(acompletion=_fake_acompletion, token_counter=lambda **_: 42),
    )

    client = LLMClient(routing)
    result = await client.selftest()

    assert result["relay"]["ok"] is True
    assert result["relay"]["latency_ms"] >= 0


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
