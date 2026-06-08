from __future__ import annotations

"""配置接线审计。

用于回答两个问题：
1. 哪些全局配置当前真的被 runtime 读取并生效；
2. 哪些参数虽然声明在 YAML 中，但当前仍未接线或仅部分接线。
"""

from pathlib import Path
from typing import Any

import yaml

from .user_settings import (
    active_user_settings_summary,
    apply_agent_param_overrides,
    apply_model_routing_overrides,
    load_user_settings,
)


def build_config_audit_summary(config_dir: Path) -> dict[str, Any]:
    config_dir = config_dir.resolve()
    settings_path = config_dir / "user_settings.yaml"
    user_settings = load_user_settings(settings_path)
    agent_params = apply_agent_param_overrides(
        _load_yaml(config_dir / "agent_params.yaml"),
        user_settings,
    )
    model_routing = apply_model_routing_overrides(
        _load_yaml(config_dir / "model_routing.yaml"),
        user_settings,
    )
    state_machine = _load_yaml(config_dir / "state_machine.yaml")

    return {
        "active_global_controls": {
            "user_settings_yaml": [
                "llm.default_profile",
                "llm.endpoints.*",
                "llm.profiles.*",
                "llm.defaults.profile/tier/model/endpoint/max_context/temperature",
                "llm.agents.<agent>.profile/tier/model/endpoint/max_context/temperature",
                "llm.agents.<agent>.modes.<mode>.*",
                "budget.defaults.max_steps/max_tokens_total/max_wall_seconds/max_validation_retries/unlimited_budget",
                "budget.agents.<agent>.max_steps/max_tokens_total/max_wall_seconds/max_validation_retries/unlimited_budget",
                "budget.agents.<agent>.modes.<mode>.*",
                "runtime.global_budget.*",
                "runtime.timeouts.*",
                "runtime.retry_policy.*",
                "runtime.budget_escalation.*",
            ],
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
                "agents.<agent>.tools.tool_names/allowed_read_prefixes/allowed_write_prefixes",
                "agents.<agent>.prompt.prompt_template/structured_outputs/expected_outputs",
                "agents.scout.behavior.t2_finalize.*",
                "agents.scout.behavior.progress.*",
                "agents.reader.modes.read.behavior.abstract_sweep.*",
                "agents.reader.modes.read.deep_read_min/deep_read_target/deep_read_max/probe_pool/mainline_screened_cap/bridge_*/citation_hub_slots",
                "agents.<agent>.behavior.*",
                "agents.<agent>.modes.<mode>.description/prompt/behavior/tools",
            ],
            "model_routing_yaml": [
                "profiles.<name>.<tier>.primary/fallback",
                "endpoints.<name>.*",
                "truncation.trigger_ratio/target_ratio",
                "endpoints.<name>.rate_limit",
            ],
            "state_machine_yaml": [
                "states.<task>.agent/mode",
                "states.<task>.inputs/outputs",
                "states.<task>.gate/branches",
                "states.<task>.next_on_success/next_on_failure",
                "states.<task>.extra",
            ],
        },
        "configuration_layers": [
            "模型/预算/timeout/retry 日常只改 config/user_settings.yaml：llm.* 管模型，budget.* 管预算，runtime.* 管 timeout/retry/budget escalation。",
            "T2/T3 文献流程机械阈值只改 config/agent_params.yaml 的 scout.behavior.t2_finalize/progress 和 reader.modes.read.behavior。",
            "config/user_settings.yaml 会覆盖默认 agent_params.yaml 与 model_routing.yaml，但不改变状态机拓扑。",
            "state_machine.yaml 只定义拓扑、IO、gate 和少数 extra；默认配置不应写 llm/budget 强覆盖。",
            "agent_params.yaml 是 agent capability registry；T2/T3 文献流程阈值属于 behavior，不属于普通 LLM/budget 参数。",
            "model_routing.yaml 是 endpoint/profile/fallback 候选定义；不要在这里做日常默认 profile 切换。",
        ],
        "user_settings": active_user_settings_summary(settings_path),
        "effective_runtime": {
            "global_budget": agent_params.get("global_budget") or {},
            "global_timeout": agent_params.get("global_timeout") or {},
            "retry_policy": agent_params.get("retry_policy") or {},
            "budget_escalation": agent_params.get("budget_escalation") or {},
        },
        "effective_model_routing": {
            "default_profile": model_routing.get("default_profile"),
            "profiles": sorted((model_routing.get("profiles") or {}).keys()),
            "endpoints": sorted((model_routing.get("endpoints") or {}).keys()),
        },
        "effective_agent_llm": _summarize_agent_llm(agent_params),
        "state_machine_llm_overrides": _scan_state_machine_llm_overrides(state_machine),
        "partially_or_not_wired": {
            "runtime_yaml": [
                "agent_behavior.max_validation_retries",
            ],
            "agent_params_yaml": [
                "部分 behavior 字段只由对应 agent/validator 消费，不存在统一全局执行器。",
            ],
            "gates_yaml": [
                "gates.<id>.type",
                "gates.<id>.config.*",
            ],
        },
        "agents_disabling_profile_fallback": _scan_direct_llm_bindings(agent_params),
        "notes": [
            "最终 LLM 选择顺序：CLI/run-task override > state_machine task llm 强覆盖 > user_settings llm.agents/llm.defaults overlay > Python fallback；profile 名称再映射到 model_routing 候选链。",
            "若 Agent 同时配置 llm.model + llm.endpoint，则会绕过 profile fallback，只走单一候选模型。",
            "如果 user_settings 修改了 profile 但运行仍不生效，先看 state_machine_llm_overrides 是否列出了当前 task 的 profile/model/endpoint 强覆盖。",
            "gates.yaml 当前主要用于展示与分支跳转；type/config 阈值本身没有统一执行器。",
            "tool 级 timeout 大多仍定义在各工具类里；global_timeout.max_tool_call 现在作为全局上限生效。",
        ],
    }


def _summarize_agent_llm(agent_params: dict[str, Any]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    agents = agent_params.get("agents") or {}
    if not isinstance(agents, dict):
        return summary
    for agent_name, agent_cfg in sorted(agents.items()):
        if not isinstance(agent_cfg, dict):
            continue
        llm = agent_cfg.get("llm") or {}
        if not isinstance(llm, dict):
            llm = {}
        summary[agent_name] = {
            key: llm.get(key)
            for key in ("profile", "tier", "model", "endpoint", "max_context", "temperature")
            if llm.get(key) is not None
        }
    return summary


def _scan_state_machine_llm_overrides(state_machine: dict[str, Any]) -> dict[str, list[str]]:
    states = _state_items(state_machine)
    if not states:
        return {"profile_or_direct_model": [], "all_llm_overrides": []}
    profile_or_direct: list[str] = []
    all_overrides: list[str] = []
    for task_id, cfg in sorted(states):
        if not isinstance(cfg, dict):
            continue
        llm = cfg.get("llm") or {}
        if not isinstance(llm, dict) or not llm:
            continue
        keys = [key for key in ("profile", "model", "endpoint", "tier", "temperature", "max_context") if key in llm]
        if not keys:
            continue
        rendered = f"{task_id}: {', '.join(f'{key}={llm.get(key)}' for key in keys)}"
        all_overrides.append(rendered)
        if any(key in llm for key in ("profile", "model", "endpoint")):
            profile_or_direct.append(rendered)
    return {"profile_or_direct_model": profile_or_direct, "all_llm_overrides": all_overrides}


def _state_items(state_machine: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    source = state_machine.get("states") or state_machine.get("nodes") or {}
    if isinstance(source, dict):
        return [
            (str(task_id), cfg)
            for task_id, cfg in source.items()
            if isinstance(cfg, dict)
        ]
    if isinstance(source, list):
        out: list[tuple[str, dict[str, Any]]] = []
        for item in source:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            cfg = dict(item)
            task_id = str(cfg.pop("id"))
            out.append((task_id, cfg))
        return out
    return []


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
