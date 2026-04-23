from pathlib import Path

from researchos.runtime.agent_params import build_agent_spec, clear_cache


def test_build_agent_spec_supports_direct_llm_model_and_endpoint(tmp_path, monkeypatch):
    config_path = tmp_path / "agent_params.yaml"
    config_path.write_text(
        """
agents:
  hello:
    llm:
      tier: medium
      profile: hello_fast
      model: openrouter/openai/gpt-4o-mini
      endpoint: openrouter_main
      max_context: 128000
      temperature: 0.15
    max_steps: 9
    max_tokens_total: 12345
    max_wall_seconds: 77
    max_validation_retries: 4
    tool_names:
      - echo
      - finish_task
    allowed_read_prefixes:
      - ""
    allowed_write_prefixes:
      - outputs/
    prompt_template: hello_custom.j2
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("RESEARCHOS_AGENT_PARAMS", str(config_path))
    clear_cache()

    spec = build_agent_spec(
        "hello",
        defaults={
            "model_tier": "medium",
            "tool_names": ["echo"],
            "max_steps": 5,
            "max_tokens_total": 10000,
            "max_wall_seconds": 60,
            "temperature": 0.3,
            "allowed_read_prefixes": [""],
            "allowed_write_prefixes": [""],
            "prompt_template": "hello.j2",
        },
    )

    assert spec.model_tier == "medium"
    assert spec.llm_profile == "hello_fast"
    assert spec.model_override == "openrouter/openai/gpt-4o-mini"
    assert spec.llm_endpoint == "openrouter_main"
    assert spec.llm_max_context == 128000
    assert spec.temperature == 0.15
    assert spec.max_steps == 9
    assert spec.max_tokens_total == 12345
    assert spec.allowed_write_prefixes == ["outputs/"]
    assert spec.prompt_template == "hello_custom.j2"

    clear_cache()
