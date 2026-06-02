from __future__ import annotations

"""配置接线审计。

用于回答两个问题：
1. 哪些全局配置当前真的被 runtime 读取并生效；
2. 哪些参数虽然声明在 YAML 中，但当前仍未接线或仅部分接线。
"""

from pathlib import Path
from typing import Any

import yaml


def build_config_audit_summary(config_dir: Path) -> dict[str, Any]:
    config_dir = config_dir.resolve()
    agent_params = _load_yaml(config_dir / "agent_params.yaml")

    return {
        "active_global_controls": {
            "runtime_yaml": [
                "workspace.default_root",
                "workspace.runtime_dir",
                "logging.level",
                "logging.json",
                "human_interface.backend",
                "agent_behavior.max_empty_reply",
                "agent_behavior.max_nudge_finish",
            ],
            "agent_params_yaml": [
                "global_timeout.max_agent_runtime",
                "global_timeout.max_tool_call",
                "global_timeout.llm_call",
                "retry_policy.llm_retries",
                "retry_policy.llm_retry_delay",
                "agents.<agent>.llm.profile/tier/model/endpoint/temperature",
                "agents.<agent>.budget.max_steps/max_tokens_total/max_wall_seconds/max_validation_retries",
                "agents.<agent>.tools.tool_names/allowed_read_prefixes/allowed_write_prefixes",
                "agents.<agent>.prompt.prompt_template/structured_outputs/expected_outputs",
                "agents.<agent>.behavior.*",
                "agents.<agent>.modes.<mode>.* section overrides",
            ],
            "model_routing_yaml": [
                "default_profile",
                "profiles.<name>.<tier>.primary/fallback",
                "endpoints.<name>.*",
                "truncation.trigger_ratio/target_ratio",
                "endpoints.<name>.rate_limit",
            ],
            "state_machine_yaml": [
                "states.<task>.llm",
                "states.<task>.budget",
                "states.<task>.tools",
                "states.<task>.gate/branches",
            ],
        },
        "partially_or_not_wired": {
            "runtime_yaml": [
                "agent_behavior.max_validation_retries",
            ],
            "agent_params_yaml": [
                "global_budget.default_max_budget_usd",
                "global_budget.warning_threshold",
                "global_budget.critical_threshold",
                "global_budget.stage_allocation",
                "retry_policy.tool_retries",
                "retry_policy.tool_retry_delay",
                "retry_policy.validation_retries",
                "retry_policy.no_retry_errors",
                "docker.requires_docker",
                "docker.requires_gpu",
                "docker.default_image",
                "logging.trace_enabled",
                "logging.trace_retention_days",
                "logging.trace_max_size_mb",
            ],
            "gates_yaml": [
                "gates.<id>.type",
                "gates.<id>.config.*",
            ],
        },
        "agents_disabling_profile_fallback": _scan_direct_llm_bindings(agent_params),
        "notes": [
            "若 Agent 同时配置 llm.model + llm.endpoint，则会绕过 profile fallback，只走单一候选模型。",
            "gates.yaml 当前主要用于展示与分支跳转；type/config 阈值本身没有统一执行器。",
            "tool 级 timeout 大多仍定义在各工具类里；global_timeout.max_tool_call 现在作为全局上限生效。",
        ],
    }


def _scan_direct_llm_bindings(agent_params: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    agents = agent_params.get("agents") or {}
    if not isinstance(agents, dict):
        return findings

    for agent_name, agent_cfg in agents.items():
        if not isinstance(agent_cfg, dict):
            continue
        base_llm = agent_cfg.get("llm") or {}
        if _has_direct_binding(base_llm):
            findings.append(f"{agent_name} (base)")

        modes = agent_cfg.get("modes") or {}
        if not isinstance(modes, dict):
            continue
        for mode_name, mode_cfg in modes.items():
            if not isinstance(mode_cfg, dict):
                continue
            mode_llm = mode_cfg.get("llm") or {}
            merged = dict(base_llm)
            merged.update(mode_llm)
            if _has_direct_binding(merged):
                findings.append(f"{agent_name}.{mode_name}")
    return findings


def _has_direct_binding(llm_cfg: Any) -> bool:
    if not isinstance(llm_cfg, dict):
        return False
    return bool(llm_cfg.get("model") and llm_cfg.get("endpoint"))


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}
