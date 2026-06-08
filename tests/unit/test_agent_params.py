import json
from pathlib import Path

from researchos.runtime.agent_params import (
    build_agent_spec,
    clear_cache,
    get_agent_mode_params,
    get_agent_params,
    get_budget_escalation_policy,
    get_global_budget,
    get_global_timeout,
    get_retry_policy,
)
from researchos.runtime.t2_config import (
    detect_manuscript_profile,
    get_effective_reader_read_params,
    load_deep_read_queue_config,
    load_t2_finalize_config,
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


def test_t2_finalize_config_is_single_source_and_clamps_active_pool(tmp_path, monkeypatch):
    config_path = tmp_path / "agent_params.yaml"
    config_path.write_text(
        """
agents:
  scout:
    behavior:
      t2_finalize:
        finish_finalize_min_raw: 3
        active_pool_max: 5
        screened_active_pool_cap: 2
        bridge_active_pool_cap_per_bridge: 1
        snowball_active_pool_cap: 0
        dedup_title_threshold: 1.2
        access_audit_top_n: 7
        metadata_backfill_max_concurrency: 3
        abstract_backfill_title_match_threshold: -0.5
        abstract_backfill_max_concurrency: 4
        snowball_max_sources: 2
        snowball_refs_per_source: 3
        snowball_max_candidates: 9
        snowball_max_concurrency: 2
        snowball_title_match_threshold: 0.91
      progress:
        enabled: false
        file: literature/custom_progress.md
        update_on_tool_results: false
        update_on_finalize: false
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCHOS_AGENT_PARAMS", str(config_path))
    clear_cache()

    cfg = load_t2_finalize_config()

    assert cfg.finish_finalize_min_raw == 10
    assert cfg.active_pool_max == 10
    assert cfg.screened_active_pool_cap == 2
    assert cfg.bridge_active_pool_cap_per_bridge == 1
    assert cfg.snowball_active_pool_cap == 0
    assert cfg.dedup_title_threshold == 1.0
    assert cfg.access_audit_top_n == 7
    assert cfg.metadata_backfill_max_concurrency == 3
    assert cfg.abstract_backfill_title_match_threshold == 0.0
    assert cfg.abstract_backfill_max_concurrency == 4
    assert cfg.snowball_max_sources == 2
    assert cfg.snowball_refs_per_source == 3
    assert cfg.snowball_max_candidates == 9
    assert cfg.snowball_max_concurrency == 2
    assert cfg.snowball_title_match_threshold == 0.91
    assert cfg.progress_enabled is False
    assert cfg.progress_file == "literature/custom_progress.md"

    clear_cache()


def test_survey_behavior_profile_overrides_t2_and_t3_from_workspace(tmp_path, monkeypatch):
    config_path = tmp_path / "agent_params.yaml"
    workspace = tmp_path / "workspace"
    (workspace / "user_seeds").mkdir(parents=True)
    (workspace / "project.yaml").write_text(
        "project_id: p\nmetadata:\n  manuscript_type: survey\n",
        encoding="utf-8",
    )
    (workspace / "user_seeds" / "seed_outline_profile.json").write_text(
        json.dumps({"manuscript_type": "survey", "project_type": "survey"}, ensure_ascii=False),
        encoding="utf-8",
    )
    config_path.write_text(
        """
agents:
  scout:
    behavior:
      t2_finalize:
        active_pool_max: 120
        screened_active_pool_cap: 60
        snowball_max_sources: 12
      behavior_profiles:
        survey:
          t2_finalize:
            active_pool_max: 180
            screened_active_pool_cap: 90
            snowball_max_sources: 18
  reader:
    modes:
      read:
        behavior:
          deep_read_min: 35
          deep_read_target: 35
          deep_read_max: 45
          abstract_sweep:
            lite_paper_num: 120
            sources:
              - papers_verified
              - papers_dedup
          behavior_profiles:
            survey:
              deep_read_min: 45
              deep_read_target: 55
              deep_read_max: 65
              abstract_sweep:
                lite_paper_num: 180
                sources:
                  - papers_verified
                  - papers_dedup
                  - papers_backlog
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCHOS_AGENT_PARAMS", str(config_path))
    clear_cache()

    assert detect_manuscript_profile(workspace) == "survey"
    default_t2 = load_t2_finalize_config()
    survey_t2 = load_t2_finalize_config(workspace)
    default_t3 = load_deep_read_queue_config()
    survey_t3 = load_deep_read_queue_config(workspace)
    survey_reader_params = get_agent_mode_params("reader", "read")
    effective_reader_params = get_effective_reader_read_params(workspace)

    assert default_t2.active_pool_max == 120
    assert survey_t2.active_pool_max == 180
    assert survey_t2.screened_active_pool_cap == 90
    assert survey_t2.snowball_max_sources == 18
    assert default_t3.deep_read_min == 35
    assert survey_t3.deep_read_min == 45
    assert survey_t3.deep_read_target == 55
    assert survey_t3.deep_read_max == 65
    assert survey_reader_params["abstract_sweep"]["lite_paper_num"] == 120
    assert effective_reader_params["abstract_sweep"]["lite_paper_num"] == 180
    assert "papers_backlog" in effective_reader_params["abstract_sweep"]["sources"]

    clear_cache()


def test_agent_params_support_unlimited_budget_tags_and_explicit_false(tmp_path, monkeypatch):
    config_path = tmp_path / "agent_params.yaml"
    config_path.write_text(
        """
agents:
  longrun:
    llm:
      tier: heavy
    budget:
      max_steps: 1
      max_tokens_total: 1
      max_wall_seconds: 1
      tags:
        - unlimited-budget
    tools:
      tool_names:
        - finish_task
      allowed_read_prefixes:
        - ""
      allowed_write_prefixes:
        - ""
    modes:
      limited:
        budget:
          unlimited_budget: "false"
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("RESEARCHOS_AGENT_PARAMS", str(config_path))
    clear_cache()

    defaults = {
        "model_tier": "medium",
        "tool_names": [],
        "max_steps": 30,
        "max_tokens_total": 200_000,
        "max_wall_seconds": 1800,
        "temperature": 0.3,
        "allowed_read_prefixes": [""],
        "allowed_write_prefixes": [""],
    }
    base = build_agent_spec("longrun", defaults=defaults)
    limited = build_agent_spec("longrun", mode="limited", defaults=defaults)

    assert base.unlimited_budget is True
    assert limited.unlimited_budget is False

    clear_cache()


def test_user_settings_overlay_supports_separated_llm_and_budget_tables(tmp_path, monkeypatch):
    config_path = tmp_path / "agent_params.yaml"
    settings_path = tmp_path / "user_settings.yaml"
    config_path.write_text(
        """
agents:
  writer:
    llm:
      profile: old_profile
      tier: medium
      temperature: 0.4
    budget:
      max_steps: 10
      max_tokens_total: 100
      max_wall_seconds: 60
      unlimited_budget: false
    tools:
      tool_names:
        - finish_task
      allowed_read_prefixes:
        - ""
      allowed_write_prefixes:
        - drafts/
    modes:
      revise:
        budget:
          max_steps: 3
""".strip(),
        encoding="utf-8",
    )
    settings_path.write_text(
        """
llm:
  defaults:
    profile: deepseek
    tier: heavy
  agents:
    writer:
      temperature: 0.7
      max_context: 128000
budget:
  defaults:
    unlimited_budget: true
    max_tokens: 999
  agents:
    writer:
      max_steps: 42
      modes:
        revise:
          max_steps: 11
          max_tokens: 1234
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("RESEARCHOS_AGENT_PARAMS", str(config_path))
    monkeypatch.setenv("RESEARCHOS_USER_SETTINGS", str(settings_path))
    clear_cache()

    base = get_agent_params("writer")
    revise = get_agent_mode_params("writer", "revise")

    assert base["llm"]["profile"] == "deepseek"
    assert base["llm"]["tier"] == "heavy"
    assert base["llm"]["temperature"] == 0.7
    assert base["llm"]["max_context"] == 128000
    assert base["max_steps"] == 42
    assert base["max_tokens_total"] == 999
    assert base["unlimited_budget"] is True
    assert revise["max_steps"] == 11
    assert revise["max_tokens_total"] == 1234

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
        },
    )
    assert spec.llm_profile == "deepseek"
    assert spec.model_tier == "heavy"
    assert spec.temperature == 0.7
    assert spec.llm_max_context == 128000
    assert spec.max_steps == 11
    assert spec.max_tokens_total == 1234
    assert spec.unlimited_budget is True

    clear_cache()


def test_user_settings_overlay_keeps_legacy_concise_agent_table_compatible(tmp_path, monkeypatch):
    config_path = tmp_path / "agent_params.yaml"
    settings_path = tmp_path / "user_settings.yaml"
    config_path.write_text(
        """
agents:
  writer:
    llm:
      profile: old_profile
      tier: medium
      temperature: 0.4
    budget:
      max_steps: 10
      max_tokens_total: 100
      max_wall_seconds: 60
      unlimited_budget: false
    tools:
      tool_names:
        - finish_task
      allowed_read_prefixes:
        - ""
      allowed_write_prefixes:
        - drafts/
""".strip(),
        encoding="utf-8",
    )
    settings_path.write_text(
        """
defaults:
  unlimited_budget: true
  max_tokens: 999
agents:
  writer:
    profile: deepseek
    tier: heavy
    temperature: 0.7
    max_steps: 42
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("RESEARCHOS_AGENT_PARAMS", str(config_path))
    monkeypatch.setenv("RESEARCHOS_USER_SETTINGS", str(settings_path))
    clear_cache()

    base = get_agent_params("writer")

    assert base["llm"]["profile"] == "deepseek"
    assert base["llm"]["tier"] == "heavy"
    assert base["llm"]["temperature"] == 0.7
    assert base["max_steps"] == 42
    assert base["max_tokens_total"] == 999
    assert base["unlimited_budget"] is True

    clear_cache()


def test_user_settings_overlay_controls_runtime_budget_timeout_and_retry(tmp_path, monkeypatch):
    config_path = tmp_path / "agent_params.yaml"
    settings_path = tmp_path / "user_settings.yaml"
    config_path.write_text(
        """
agents:
  hello:
    tools:
      tool_names:
        - finish_task
      allowed_read_prefixes:
        - ""
      allowed_write_prefixes:
        - ""
""".strip(),
        encoding="utf-8",
    )
    settings_path.write_text(
        """
runtime:
  global_budget:
    default_max_budget_usd: 321.0
    warning_threshold: 0.5
  timeouts:
    max_agent_runtime: 1234
    max_tool_call: 55
    llm_call: 66
  retry_policy:
    llm_retries: 7
    llm_retry_delay: 0.5
    llm_timeout_cooldown_seconds: 90
  budget_escalation:
    enabled: true
    max_extensions_per_run: 4
    validation_retry_increase: 3
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("RESEARCHOS_AGENT_PARAMS", str(config_path))
    monkeypatch.setenv("RESEARCHOS_USER_SETTINGS", str(settings_path))
    clear_cache()

    assert get_global_budget()["default_max_budget_usd"] == 321.0
    assert get_global_budget()["warning_threshold"] == 0.5
    assert get_global_timeout()["max_agent_runtime"] == 1234
    assert get_global_timeout()["max_tool_call"] == 55
    assert get_global_timeout()["llm_call"] == 66
    assert get_retry_policy()["llm_retries"] == 7
    assert get_retry_policy()["llm_retry_delay"] == 0.5
    assert get_retry_policy()["llm_timeout_cooldown_seconds"] == 90
    assert get_budget_escalation_policy()["enabled"] is True
    assert get_budget_escalation_policy()["max_extensions_per_run"] == 4
    assert get_budget_escalation_policy()["validation_retry_increase"] == 3

    clear_cache()
