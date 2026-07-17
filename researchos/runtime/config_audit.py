from __future__ import annotations

"""配置接线审计。

用于回答两个问题：
1. 哪些全局配置当前真的被 runtime 读取并生效；
2. 哪些参数虽然声明在 YAML 中，但当前仍未接线或仅部分接线。
"""

from pathlib import Path
from typing import Any

import yaml

from .model_settings import load_llm_runtime_defaults, load_model_settings
from .system_config import system_config_path_for


def build_config_audit_summary(config_dir: Path) -> dict[str, Any]:
    config_dir = config_dir.resolve()
    settings_path = config_dir / "model_settings.yaml"
    model_settings = load_model_settings(settings_path)
    agent_params = _load_yaml(system_config_path_for(config_dir, "agent_params.yaml"))
    state_machine_path = system_config_path_for(config_dir, "state_machine.yaml")
    gates_path = system_config_path_for(config_dir, "gates.yaml")
    cdr_schema_path = system_config_path_for(config_dir, "cdr_schema.yaml")
    venue_writing_profiles_path = system_config_path_for(config_dir, "venue_writing_profiles.yaml")
    state_machine = _load_yaml(state_machine_path)

    return {
        "system_config_contracts": {
            "state_machine_yaml": str(state_machine_path),
            "gates_yaml": str(gates_path),
            "cdr_schema_yaml": str(cdr_schema_path),
            "venue_writing_profiles_yaml": str(venue_writing_profiles_path),
            "llm_runtime_yaml": str(system_config_path_for(config_dir, "llm_runtime.yaml")),
            "purpose": (
                "系统契约配置：状态机拓扑、human gate 展示、CDR schema，以及统一的 venue writing profiles；"
                "普通用户日常不需要修改。"
            ),
        },
        "active_global_controls": {
            "model_settings_yaml": [
                "provider",
                "api_base",
                "api_key",
                "model",
                "fallback.max_attempts/initial_wait_seconds/max_wait_seconds/retry_after_timeout",
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
            "llm_runtime_yaml": [
                "context_window_fallback",
                "truncation.trigger_ratio/target_ratio",
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
            "日常只改 config/model_settings.yaml 中的 provider、api_base、api_key、model 和 fallback，或运行 `researchos configure-llm`；所有 Agent 使用同一模型。",
            "T2/T3 文献流程机械阈值默认来自 config/system_config/agent_params.yaml 的 scout.behavior.t2_finalize/progress/literature_quality 和 reader.modes.read.behavior；完整 run 会先经 T2-PARAM-GATE 写 workspace-local literature/literature_params.json，覆盖保留候选数、精读目标、摘要轻读目标和写作语言/中文文献策略。",
            "状态机、gate、CDR schema 和 venue writing profiles 属于 config/system_config/ 系统契约；CLI 默认读取新路径，并保留 config/*.yaml 旧路径 fallback。",
            "state_machine.yaml 只定义拓扑、IO、gate 和少数 extra；默认配置不应写 llm/budget 强覆盖。",
            "agent_params.yaml 是 agent capability registry；T2/T3 文献流程阈值属于 behavior，不属于普通 LLM/budget 参数。",
            "literature/literature_params.json 是单个 workspace 的运行决策文件，优先于全局 yaml；要改本次运行覆盖规模，优先看这个文件。",
            "config/system_config/llm_runtime.yaml 保存 context fallback 与 truncation 默认值；它不是日常用户配置。旧 endpoint/profile 文件仅为历史部署保留兼容读取。",
        ],
        "model_settings": {
            "path": str(settings_path),
            "configured": bool(settings_path.exists()),
            "provider": model_settings.get("provider"),
            "api_base": model_settings.get("api_base"),
            "model": model_settings.get("model"),
            "api_key_configured": bool(model_settings.get("api_key")),
            "fallback": model_settings.get("fallback"),
        },
        "effective_runtime": {
            "global_budget": agent_params.get("global_budget") or {},
            "global_timeout": agent_params.get("global_timeout") or {},
            "retry_policy": agent_params.get("retry_policy") or {},
            "runtime_recovery": _summarize_runtime_recovery_policy(
                agent_params.get("budget_escalation") or {}
            ),
        },
        "effective_llm_runtime": load_llm_runtime_defaults(),
        # Retained as a migration audit. These historical fields no longer
        # route a new run away from model_settings.yaml, but a maintainer can
        # still locate stale direct model declarations before removing them.
        "legacy_agent_model_overrides": _scan_direct_llm_bindings(agent_params),
        "agents_disabling_profile_fallback": _scan_direct_llm_bindings(agent_params),
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
        "notes": [
            "新默认配置只使用一个模型连接；历史 workspace 中保留的 profile/tier 记录仅用于审计，不会让新运行切换模型。",
            "`configure-llm` 保存设置后会立即做最小连通性检查；运行命令发现缺失配置时也会先提示配置。",
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


def _summarize_runtime_recovery_policy(policy: dict[str, Any]) -> dict[str, Any]:
    """Expose old budget_escalation config without reintroducing budget tuning.

    The YAML key is intentionally kept for compatibility, but validate-config is
    a researcher-facing command. It should describe what is active today rather
    than printing obsolete step/token increase ratios as if they were user
    controls.
    """

    if not isinstance(policy, dict):
        policy = {}
    return {
        "enabled": bool(policy.get("enabled", False)),
        "purpose": (
            "validation/tool recovery and legacy bounded-budget compatibility; "
            "ordinary ResearchOS step/token caps are not imposed by default"
        ),
        "validation_retry_increase": policy.get("validation_retry_increase", 3),
        "max_validation_extensions_per_run": policy.get("max_validation_extensions_per_run"),
        "legacy_bounded_budget_compatibility": {
            "enabled_only_when_a_bounded_budget_override_is_explicit": True,
            "max_extensions_per_run": policy.get("max_extensions_per_run"),
        },
    }


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
