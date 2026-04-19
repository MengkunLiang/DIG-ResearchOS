from pathlib import Path

from researchos.runtime.llm_client import LLMClient


def test_llm_client_loads_project_env_file(tmp_path, monkeypatch):
    project_dir = tmp_path / "project"
    config_dir = project_dir / "config"
    config_dir.mkdir(parents=True)
    (project_dir / ".env").write_text(
        "TEST_API_KEY=test-key\nTEST_API_BASE=https://example.invalid/v1\n",
        encoding="utf-8",
    )
    routing = config_dir / "model_routing.yaml"
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
    light:
      primary:
        model: gpt-4o-mini
        endpoint: relay
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.delenv("TEST_API_KEY", raising=False)
    monkeypatch.delenv("TEST_API_BASE", raising=False)

    client = LLMClient(Path(routing))
    endpoint = client.endpoints["relay"]
    kwargs = endpoint.to_litellm_kwargs()

    assert kwargs["api_key"] == "test-key"
    assert kwargs["api_base"] == "https://example.invalid/v1"
