from pathlib import Path

from researchos.runtime.agent_params import (
    build_agent_spec,
    clear_cache,
    get_agent_mode_params,
    get_agent_params,
)


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


def test_sectioned_agent_params_are_normalized_for_runtime(tmp_path, monkeypatch):
    config_path = tmp_path / "agent_params.yaml"
    config_path.write_text(
        """
agents:
  writer:
    llm:
      profile: writing_profile
      tier: heavy
      temperature: 0.55
    budget:
      max_steps: 33
      max_tokens_total: 98765
      max_wall_seconds: 456
      max_validation_retries: 7
    tools:
      tool_names:
        - read_file
        - write_file
        - finish_task
      allowed_read_prefixes:
        - ""
        - drafts/
      allowed_write_prefixes:
        - drafts/
    prompt:
      prompt_template: writer_custom.j2
      expected_outputs:
        paper_required: true
    behavior:
      max_compile_attempts: 9
    modes:
      revise:
        budget:
          max_steps: 11
        tools:
          allowed_write_prefixes:
            - drafts/
            - revisions/
        behavior:
          revision_mode: patch_only
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("RESEARCHOS_AGENT_PARAMS", str(config_path))
    clear_cache()

    base = get_agent_params("writer")
    assert base["max_steps"] == 33
    assert base["max_tokens_total"] == 98765
    assert base["max_wall_seconds"] == 456
    assert base["max_validation_retries"] == 7
    assert base["tool_names"] == ["read_file", "write_file", "finish_task"]
    assert base["allowed_read_prefixes"] == ["", "drafts/"]
    assert base["allowed_write_prefixes"] == ["drafts/"]
    assert base["prompt_template"] == "writer_custom.j2"
    assert base["expected_outputs"]["paper_required"] is True
    assert base["max_compile_attempts"] == 9

    revise = get_agent_mode_params("writer", "revise")
    assert revise["max_steps"] == 11
    assert revise["max_tokens_total"] == 98765
    assert revise["allowed_write_prefixes"] == ["drafts/", "revisions/"]
    assert revise["revision_mode"] == "patch_only"

    spec = build_agent_spec(
        "writer",
        mode="revise",
        defaults={
            "model_tier": "medium",
            "tool_names": [],
            "max_steps": 1,
            "max_tokens_total": 1,
            "max_wall_seconds": 1,
            "temperature": 0.3,
            "allowed_read_prefixes": [""],
            "allowed_write_prefixes": [""],
            "prompt_template": "fallback.j2",
        },
    )
    assert spec.model_tier == "heavy"
    assert spec.llm_profile == "writing_profile"
    assert spec.temperature == 0.55
    assert spec.max_steps == 11
    assert spec.max_tokens_total == 98765
    assert spec.allowed_write_prefixes == ["drafts/", "revisions/"]
    assert spec.prompt_template == "writer_custom.j2"

    clear_cache()
