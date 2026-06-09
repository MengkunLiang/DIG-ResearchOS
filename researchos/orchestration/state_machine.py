from __future__ import annotations

"""ResearchOS 状态机解释器。

本模块负责三件事：
1. 把 `config/system_config/state_machine.yaml` 解析成 task 节点；
2. 基于 `AgentResult` 推进 `state.yaml`；
3. 在 gate / resume / iteration 这些跨 task 语义上做统一处理。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid
from typing import Any

import yaml

from ..pydantic_compat import model_dump
from ..runtime.agent import (
    AgentResult,
    BudgetOverride,
    ExecutionContext,
    LLMConfigOverride,
    ToolPolicyOverride,
)
from ..runtime.task_recovery import prepare_task_resume_artifacts
from ..runtime.artifact_fingerprints import (
    build_input_fingerprints,
    validate_input_fingerprints,
    validate_t45_fingerprint_report,
)
from ..schemas.state import BudgetCumulative, GateState, StateYaml, TaskHistoryEntry
from .gate_presenter import build_presentation
from .task_io_contract import get_task_io
from ..tools.external_experiment import (
    build_executor_selection_payload,
    patch_external_executor_files_with_selection,
    validate_external_executor_ready,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json_fingerprint(payload: dict[str, Any]) -> str:
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


_T36_SURVEY_GATE_INPUT_PATHS = {
    "project": "project.yaml",
    "synthesis": "literature/synthesis.md",
    "synthesis_workbench": "literature/synthesis_workbench.json",
    "domain_map": "literature/domain_map.json",
    "comparison_table": "literature/comparison_table.csv",
    "seed_outline_profile": "user_seeds/seed_outline_profile.json",
    "seed_ideas": "user_seeds/seed_ideas.md",
    "seed_constraints": "user_seeds/seed_constraints.md",
    "seed_external_resources": "user_seeds/seed_external_resources.jsonl",
}


_T36_CORPUS_GATE_INPUT_PATHS = {
    "survey_plan": "drafts/survey/survey_plan.json",
    "survey_state": "drafts/survey/survey_state.json",
    "synthesis": "literature/synthesis.md",
    "synthesis_workbench": "literature/synthesis_workbench.json",
    "domain_map": "literature/domain_map.json",
    "comparison_table": "literature/comparison_table.csv",
    "paper_notes": "literature/paper_notes",
    "paper_notes_abstract": "literature/paper_notes_abstract",
    "metadata_triage": "literature/metadata_triage.md",
    "related_work_bib": "literature/related_work.bib",
}


_T2_LITERATURE_PARAM_GATE_INPUT_PATHS = {
    "project": "project.yaml",
    "seed_outline_profile": "user_seeds/seed_outline_profile.json",
    "bridge_domain_plan": "literature/bridge_domain_plan.json",
}


def _t4_gate1_candidate_pool_fingerprints(workspace_dir: Path) -> dict[str, dict[str, Any]]:
    paths = {
        "pass1_forward_candidates": "ideation/_pass1_forward_candidates.json",
        "pass2_grounding_review": "ideation/_pass2_grounding_review.json",
        "candidate_directions": "ideation/_candidate_directions.json",
        "gate1_selection_brief": "ideation/_gate1_selection_brief.md",
        "bridge_coverage_review": "ideation/bridge_coverage_review.json",
    }
    fingerprints: dict[str, dict[str, Any]] = {}
    for label, rel in paths.items():
        path = workspace_dir / rel
        item: dict[str, Any] = {"path": rel, "exists": path.exists()}
        if path.exists() and path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            item["sha256"] = digest.hexdigest()
            item["size"] = path.stat().st_size
        fingerprints[label] = item
    return fingerprints


def _gate1_pool_fingerprint_changed(
    stored: object,
    current: dict[str, dict[str, Any]],
) -> list[str]:
    if not isinstance(stored, dict):
        return []
    changed: list[str] = []
    for label, item in current.items():
        previous = stored.get(label)
        if not isinstance(previous, dict):
            changed.append(label)
            continue
        if bool(previous.get("exists")) != bool(item.get("exists")):
            changed.append(label)
            continue
        if item.get("exists") and str(previous.get("sha256") or "") != str(item.get("sha256") or ""):
            changed.append(label)
    return changed


def build_literature_param_payload(
    *,
    selected_option: str,
    captured: dict[str, Any] | None = None,
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    """Build the workspace-local literature coverage parameters for T2/T3."""

    option = _normalize_literature_param_option(selected_option)
    presets: dict[str, dict[str, Any]] = {
        "standard_research": {
            "profile": "research_article",
            "t2_finalize": {"active_pool_max": 120},
            "reader": {
                "deep_read_min": 35,
                "deep_read_target": 35,
                "deep_read_max": 45,
                "require_deep_read_target": True,
                "abstract_sweep": {
                    "lite_paper_num": 120,
                    "sources": ["papers_verified", "papers_dedup"],
                    "include_metadata_only": True,
                    "metadata_replacement_policy": "replace_metadata_only_with_readable_backlog_when_available",
                },
            },
        },
        "survey_balanced": {
            "profile": "survey",
            "t2_finalize": {"active_pool_max": 180},
            "reader": {
                "deep_read_min": 50,
                "deep_read_target": 60,
                "deep_read_max": 70,
                "require_deep_read_target": True,
                "abstract_sweep": {
                    "lite_paper_num": "all_readable",
                    "sources": ["papers_verified", "papers_dedup", "papers_backlog"],
                    "include_metadata_only": True,
                    "metadata_replacement_policy": "replace_metadata_only_with_readable_backlog_when_available",
                },
            },
        },
        "survey_exhaustive": {
            "profile": "survey",
            "t2_finalize": {"active_pool_max": 240},
            "reader": {
                "deep_read_min": 70,
                "deep_read_target": 80,
                "deep_read_max": 95,
                "require_deep_read_target": True,
                "abstract_sweep": {
                    "lite_paper_num": "all_readable",
                    "sources": ["papers_verified", "papers_dedup", "papers_backlog"],
                    "include_metadata_only": True,
                    "metadata_replacement_policy": "replace_metadata_only_with_readable_backlog_when_available",
                },
            },
        },
    }
    payload = json.loads(json.dumps(presets.get(option, presets["survey_balanced"]), ensure_ascii=False))
    captured = captured or {}
    if option == "custom":
        active_pool = _safe_int(captured.get("active_pool_max"), default=180, minimum=30)
        deep_target = _safe_int(captured.get("deep_read_target"), default=60, minimum=1)
        deep_min = max(1, min(deep_target, int(round(deep_target * 0.8))))
        abstract_target: str | int = str(captured.get("abstract_sweep_target") or "all_readable").strip()
        if abstract_target.casefold() not in {"all", "all_readable", "unlimited", "全部"}:
            abstract_target = _safe_int(abstract_target, default=active_pool, minimum=1)
        require_target = _safe_bool(captured.get("require_deep_read_target"), default=True)
        payload = {
            "profile": "custom",
            "t2_finalize": {"active_pool_max": active_pool},
            "reader": {
                "deep_read_min": deep_min,
                "deep_read_target": deep_target,
                "deep_read_max": max(deep_target, int(round(deep_target * 1.15))),
                "require_deep_read_target": require_target,
                "abstract_sweep": {
                    "lite_paper_num": abstract_target,
                    "sources": ["papers_verified", "papers_dedup", "papers_backlog"],
                    "include_metadata_only": True,
                    "metadata_replacement_policy": "replace_metadata_only_with_readable_backlog_when_available",
                },
            },
        }

    payload.update(
        {
            "semantics": "workspace_literature_coverage_parameters_for_t2_t3",
            "selected_option": option,
            "captured": captured,
            "resource_backfill_policy": {
                "retained_candidates": "attempt all reasonable metadata, abstract, DOI, OpenAlex, Crossref, Semantic Scholar, arXiv, and PDF-hint backfill",
                "user_visible_budget_semantics": "coverage targets, not network attempt caps",
                "metadata_only": (
                    "metadata-only records receive batch LLM triage but do not count as abstract-note evidence; "
                    "when possible, readable backlog records should replace metadata-only slots for coverage."
                ),
            },
            "parameter_meanings": {
                "active_pool_max": "保留候选数：T2 从检索结果里保留多少篇进入后续阅读处置；不是精读篇数，也不是最终引用篇数。",
                "deep_read_target": "精读目标：正常完成 T3 前应完成多少篇结构化深读笔记。",
                "deep_read_min": "最低精读：预算或资源异常时的最低可接受线；正常运行由 require_deep_read_target 决定是否必须读满 target。",
                "abstract_sweep.lite_paper_num": "摘要轻读数量：T3 后对未精读但有摘要的论文做 LLM 摘要级轻读；all_readable 表示尽量读完可读摘要。",
                "metadata_replacement_policy": "metadata-only 只做批量 triage，并尽量用 backlog 中有摘要/PDF 的候选补足可读覆盖。",
            },
        }
    )
    if workspace_dir is not None:
        payload["detected_profile_before_gate"] = _detect_literature_profile_hint(workspace_dir)
    return payload


def _normalize_literature_param_option(option: str) -> str:
    normalized = str(option or "").strip().casefold()
    aliases = {
        "standard": "standard_research",
        "research": "standard_research",
        "默认": "standard_research",
        "研究": "standard_research",
        "survey": "survey_balanced",
        "review": "survey_balanced",
        "综述": "survey_balanced",
        "均衡": "survey_balanced",
        "exhaustive": "survey_exhaustive",
        "full": "survey_exhaustive",
        "全量": "survey_exhaustive",
        "强覆盖": "survey_exhaustive",
        "自定义": "custom",
    }
    return aliases.get(normalized, normalized if normalized in {"standard_research", "survey_balanced", "survey_exhaustive", "custom"} else "survey_balanced")


def _safe_int(value: Any, *, default: int, minimum: int) -> int:
    try:
        result = int(float(str(value).strip()))
    except (TypeError, ValueError):
        result = default
    return max(minimum, result)


def _safe_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "y", "是", "需要", "require", "target"}:
        return True
    if text in {"0", "false", "no", "n", "否", "不", "min"}:
        return False
    return default


def _detect_literature_profile_hint(workspace_dir: Path) -> str:
    texts: list[str] = []
    for rel in ("project.yaml", "user_seeds/seed_outline_profile.json"):
        path = workspace_dir / rel
        if path.exists():
            texts.append(path.read_text(encoding="utf-8", errors="replace")[:4000])
    joined = " ".join(texts).casefold()
    if any(token in joined for token in ("survey", "综述", "review", "taxonomy-driven")):
        return "survey"
    return "research_article"


def _file_newer_than_existing_inputs(output: Path, inputs: list[Path]) -> bool:
    """Return true when an output can safely route against existing inputs."""

    if not output.exists() or output.stat().st_size <= 0:
        return False
    output_mtime = output.stat().st_mtime
    for path in inputs:
        if path.exists() and path.stat().st_size > 0 and path.stat().st_mtime > output_mtime:
            return False
    return True


def _normalized_tags(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list | tuple | set):
        values = list(value)
    else:
        return set()
    return {str(item).strip().lower().replace("-", "_") for item in values if str(item).strip()}


def _config_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_")
        if normalized in {"1", "true", "yes", "y", "on", "unlimited", "unlimited_budget"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "limited", ""}:
            return False
    return bool(value)


def _budget_has_unlimited_tag(
    budget_block: dict[str, Any],
    node_tags: list[str] | None = None,
) -> bool | None:
    """Return explicit unlimited budget override from state-machine config.

    `None` means the node did not express an override, so the AgentSpec default
    should continue to apply.
    """

    if "unlimited_budget" in budget_block:
        return _config_bool(budget_block.get("unlimited_budget"))
    tags = (
        _normalized_tags(budget_block.get("tags"))
        | _normalized_tags(budget_block.get("budget_tags"))
        | _normalized_tags(node_tags)
    )
    if {"unlimited_budget", "unlimited"} & tags:
        return True
    return None


def _valid_writing_style_file(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return isinstance(data, dict) and data.get("venue_style") in {"is", "ccf_a", "both"}


def _extract_t45_final_gate_verdict(text: str) -> str:
    """Extract the T4.5 Final Gate Verdict without interpreting scientific quality."""

    match = re.search(
        r"(?im)^\s*(?:#+\s*)?(?:\*\*)?\s*Final\s+Gate\s+Verdict\s*(?:\*\*)?\s*[:：]\s*(.+?)\s*$",
        text,
    )
    if match:
        return match.group(1).strip()

    heading = re.search(r"(?im)^\s*#+\s*Final\s+Gate\s+Verdict\s*$", text)
    if heading:
        tail = text[heading.end() :].splitlines()
        for line in tail[:8]:
            stripped = line.strip().strip("*")
            if not stripped or stripped.startswith("#"):
                continue
            return stripped
    return ""


@dataclass
class TaskNode:
    """一个 FSM 节点的运行期表示。"""

    task_id: str
    agent: str | None = None
    skill: str | None = None
    description: str | None = None
    inputs: dict[str, str] | None = None
    outputs: dict[str, str] | None = None
    optional_outputs: dict[str, str] | None = None
    next_on_success: str | None = None
    next_on_failure: str | None = None
    terminal: bool = False
    llm: dict[str, Any] | None = None
    budget: dict[str, Any] | None = None
    tools: dict[str, Any] | None = None
    tags: list[str] | None = None
    mode: str | None = None
    gate: str | dict[str, Any] | None = None
    branches: dict[str, str] | None = None
    max_iterations: int | None = None
    round: int | None = None
    extra: dict[str, Any] | None = None


class StateMachine:
    """ResearchOS runtime 层的状态机。

    设计原则：
    - 状态推进要尽量声明式：节点配置决定下一跳，代码只解释语义；
    - `state.yaml` 是唯一真相来源，CLI 每次操作都可重建状态；
    - runtime 只管理“如何推进”，不管理 agent 内部如何恢复历史工作。
    """

    def __init__(self, config_path: Path, gates_config_path: Path | None = None):
        self.config_path = config_path
        self.gates_config_path = gates_config_path
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        self.initial_state = raw["initial_state"]
        self.nodes = self._parse_nodes(raw)
        self.gates = self._load_gates(gates_config_path)

    def _parse_nodes(self, raw: dict[str, Any]) -> dict[str, TaskNode]:
        """同时兼容两种配置风格：

        - `states: {T1: {...}}`
        - `nodes: [{id: T1, ...}]`
        """
        source = raw.get("states") or raw.get("nodes") or {}
        if isinstance(source, list):
            return {
                item["id"]: TaskNode(
                    task_id=item["id"],
                    **{key: value for key, value in item.items() if key != "id"},
                )
                for item in source
            }
        return {task_id: TaskNode(task_id=task_id, **cfg) for task_id, cfg in source.items()}

    def _load_gates(self, gates_config_path: Path | None) -> dict[str, dict[str, Any]]:
        if gates_config_path is None or not gates_config_path.exists():
            return {}
        raw = yaml.safe_load(gates_config_path.read_text(encoding="utf-8")) or {}
        gates = raw.get("gates", raw)
        if isinstance(gates, list):
            return {gate["id"]: gate for gate in gates}
        if not isinstance(gates, dict):
            raise ValueError("gates config must be a mapping or list")
        return gates

    def create_initial_state(self, project_id: str) -> StateYaml:
        """创建项目首次运行时的状态。"""
        return StateYaml(project_id=project_id, current_task=self.initial_state)

    def validate_definition(self) -> list[str]:
        """对状态机配置做启动前静态校验。

        这里专注于 runtime 自己有能力判断的结构问题：
        - 初始节点是否存在；
        - 非 terminal 节点是否声明了且只声明了一种执行体(agent 或 skill)；
        - 所有 next/branch 是否都指向已知节点或特殊占位符；
        - gate 引用是否存在；
        - 与 task I/O 契约是否明显不一致。
        """

        errors: list[str] = []
        if self.initial_state not in self.nodes:
            errors.append(f"initial_state '{self.initial_state}' not found in state machine nodes")

        for task_id, node in self.nodes.items():
            if node.terminal:
                continue

            if bool(node.agent) == bool(node.skill):
                errors.append(
                    f"{task_id}: non-terminal node must declare exactly one of 'agent' or 'skill'"
                )

            for field_name, target in (
                ("next_on_success", node.next_on_success),
                ("next_on_failure", node.next_on_failure),
            ):
                self._validate_target(task_id, field_name, target, errors)

            self._validate_gate(task_id, node, errors)
            self._validate_task_contract(task_id, node, errors)
        return errors

    def build_execution_context(self, workspace_dir: Path, state: StateYaml) -> ExecutionContext:
        """把当前状态翻译成 AgentRunner 可执行的 ExecutionContext。"""
        node = self.nodes[state.current_task]

        # Phase 2.3: 检查迭代死锁（相同参数重复3次以上）
        self._check_iteration_deadlock(state, node)

        run_id = f"{state.current_task.lower()}_{uuid.uuid4().hex[:8]}"
        outputs = {name: workspace_dir / rel for name, rel in (node.outputs or {}).items()}
        inputs = {name: workspace_dir / rel for name, rel in (node.inputs or {}).items()}

        # ctx.extra 的来源分三层：
        # 1. state.task_context：上一个 gate 决策附带的上下文
        # 2. node.extra：节点自己声明的静态额外信息
        # 3. runtime 自动注入的 resume / iteration 标志
        extra = dict(state.task_context)
        extra.update(node.extra or {})
        if node.description:
            extra.setdefault("task_description", node.description)
        if node.agent:
            extra.setdefault("agent_id", node.agent)
        if node.skill:
            extra.setdefault("skill_id", node.skill)
        if node.mode is not None:
            extra.setdefault("phase", node.mode)
        if node.round is not None:
            extra.setdefault("round", node.round)

        # P0-9 修复: 设置 skill_dir（如果是 skill 节点）
        if node.skill:
            extra["skill_name"] = node.skill
            # 设置实际skill_dir路径供bash_run等工具使用
            skill_dir = workspace_dir / "skills" / node.skill
            extra["skill_dir"] = str(skill_dir)

        iteration = state.iteration_count.get(state.current_task, 0)
        if iteration:
            extra["iteration_count"] = iteration

        # P0-2 修复: 检测 resume 场景并设置 extra 字段
        resumed_from = None
        resume_reason = None

        for history in reversed(state.history):
            if history.task != state.current_task:
                continue

            # 场景1: INTERRUPTED（用户Ctrl+C）
            if history.status == "INTERRUPTED":
                resumed_from = history.run_id
                resume_reason = "interrupted"
                break

            # 场景2: FAILED重试（验证失败，将重试）
            if history.status == "FAILED":
                # 检查是否为重试场景（非终止失败）
                # 注意：STOP_HUMAN_REJECT表示用户拒绝，不应重试
                if hasattr(history, 'stop_reason') and history.stop_reason == "human_reject":
                    break
                # 检查是否配置了失败后重试
                if node.next_on_failure and node.next_on_failure == state.current_task:
                    resumed_from = history.run_id
                    resume_reason = "retry_after_failure"
                    break

            # 场景3: 迭代（通过gate返回同一任务）
            if history.status == "DONE" and iteration > 0:
                resumed_from = history.run_id
                resume_reason = "iteration"
                break

            # 只检查该任务的最近一次运行
            break

        if resumed_from:
            # 设计文档 §13.5 要求的字段
            extra["resumed_from_run_id"] = resumed_from
            extra["resume_mode"] = True
            extra["resume_reason"] = resume_reason
            # 保留旧字段以兼容现有代码
            extra["is_resume"] = True
            extra["resumed_from"] = resumed_from

        # 所有 task 都统一生成恢复快照，让 pipeline resume 与单任务续跑共享同一语义。
        recovery_info = prepare_task_resume_artifacts(
            workspace_dir,
            task_id=node.task_id,
            outputs_expected=outputs,
            base_extra=extra,
        )
        extra.update(recovery_info)

        ctx = ExecutionContext(
            workspace_dir=workspace_dir,
            project_id=state.project_id,
            task_id=node.task_id,
            run_id=run_id,
            inputs=inputs,
            outputs_expected=outputs,
            mode=node.mode,
            extra=extra,
        )
        llm_ov, budget_ov, tool_ov = self._build_overrides(node)
        ctx.llm_override = llm_ov
        ctx.budget_override = budget_ov
        ctx.tool_policy_override = tool_ov
        return ctx

    def should_pause_for_immediate_gate(self, state: StateYaml) -> bool:
        """Return true when the current node is a gate-only node that should not run an LLM."""

        node = self.nodes[state.current_task]
        return bool(node.gate and (node.extra or {}).get("immediate_gate"))

    def pause_for_immediate_gate(
        self,
        state: StateYaml,
        *,
        workspace_dir: Path | None = None,
    ) -> StateYaml:
        """Present a gate-only node directly and pause without starting an agent run."""

        node = self.nodes[state.current_task]
        if not node.gate:
            raise ValueError(f"{state.current_task} has no gate")
        gate_id = self._gate_id_for_node(node)
        gate_spec = self._find_gate(gate_id)
        presentation = build_presentation(
            gate_spec,
            model_dump(state, mode="json"),
            workspace_dir or Path("."),
        )
        if node.task_id == "T4-GATE1" and workspace_dir is not None:
            presentation["candidate_pool_fingerprints"] = _t4_gate1_candidate_pool_fingerprints(workspace_dir)
        state.pending_gate = GateState(
            gate_id=gate_id,
            presented_at=_now_iso(),
            presentation=presentation,
            options=list(gate_spec.get("options", [])),
        )
        state.status = "WAITING_HUMAN"
        state.paused_at = _now_iso()
        return state

    def start_task(self, state: StateYaml, run_id: str) -> StateYaml:
        """task 开始执行前，先写入一条 RUNNING history。"""
        state.status = "RUNNING"
        state.pending_gate = None
        state.paused_at = None
        state.history.append(
            TaskHistoryEntry(
                task=state.current_task,
                run_id=run_id,
                status="RUNNING",
                started_at=_now_iso(),
            )
        )

        # Phase 2.3: 记录迭代历史（用于死锁检测）
        self._record_iteration_attempt(state, self.nodes[state.current_task])

        return state

    def mark_interrupted(self, state: StateYaml, *, reason: str | None = None) -> StateYaml:
        """收到 SIGINT / SIGTERM 后，把项目置为 PAUSED。"""
        if state.history:
            if state.history[-1].status not in {"DONE", "FAILED", "INTERRUPTED"}:
                state.history[-1].status = "INTERRUPTED"
            state.history[-1].finished_at = _now_iso()
            state.history[-1].stop_reason = state.history[-1].stop_reason or AgentResult.STOP_INTERRUPTED
            if reason and not state.history[-1].error:
                state.history[-1].error = reason
        state.status = "PAUSED"
        state.paused_at = _now_iso()
        return state

    def advance(
        self,
        state: StateYaml,
        result: AgentResult,
        *,
        workspace_dir: Path | None = None,
    ) -> StateYaml:
        """根据一次 agent run 的结果推进状态机。"""
        history = state.history[-1]
        history.finished_at = _now_iso()
        history.stop_reason = result.stop_reason
        history.tokens = result.tokens_in + result.tokens_out
        history.tokens_in = result.tokens_in
        history.tokens_out = result.tokens_out
        history.cost_usd = result.cost_usd
        history.llm_profile = result.llm_profile
        history.llm_tier = result.llm_tier
        history.llm_model = result.llm_model_used
        history.llm_endpoint = result.llm_endpoint_used
        history.completion_mode = (result.metadata or {}).get("completion_mode")
        history.error = result.error
        recoverable_pause = result.stop_reason in {
            AgentResult.STOP_INTERRUPTED,
            AgentResult.STOP_MAX_STEPS,
            AgentResult.STOP_BUDGET,
        }
        history.status = "DONE" if result.ok else "INTERRUPTED" if recoverable_pause else "FAILED"

        state.budget_cumulative = BudgetCumulative(
            tokens_total=state.budget_cumulative.tokens_total + history.tokens,
            cost_usd_total=state.budget_cumulative.cost_usd_total + result.cost_usd,
            gpu_hours_used=state.budget_cumulative.gpu_hours_used,
        )

        # Budget drift warning (§7.1)
        if workspace_dir:
            self._check_budget_drift(state, workspace_dir)

        if result.stop_reason in {
            AgentResult.STOP_INTERRUPTED,
            AgentResult.STOP_MAX_STEPS,
            AgentResult.STOP_BUDGET,
        }:
            state.last_error = result.error
            return self.mark_interrupted(state)

        node = self.nodes[state.current_task]
        if not result.ok:
            state.last_error = result.error
            next_task = node.next_on_failure
            if next_task and next_task in self.nodes and not self.nodes[next_task].terminal:
                state.current_task = next_task
                state.status = "RUNNING"
            else:
                if next_task and next_task in self.nodes:
                    state.current_task = next_task
                state.status = "FAILED"
            return state

        if (
            state.current_task == "T4"
            and (result.metadata or {}).get("completion_mode") == "t4_gate1_ready"
            and "T4-GATE1" in self.nodes
        ):
            return self._transition_to_next(state, "T4-GATE1", workspace_dir=workspace_dir)

        if node.gate:
            gate_id = self._gate_id_for_node(node)
            gate_spec = self._find_gate(gate_id)
            presentation = build_presentation(
                gate_spec,
                model_dump(state, mode="json"),
                workspace_dir or Path("."),
            )
            if state.current_task == "T4-GATE1" and workspace_dir is not None:
                presentation["candidate_pool_fingerprints"] = _t4_gate1_candidate_pool_fingerprints(workspace_dir)
            state.pending_gate = GateState(
                gate_id=gate_id,
                presented_at=_now_iso(),
                presentation=presentation,
                options=list(gate_spec.get("options", [])),
            )
            state.status = "WAITING_HUMAN"
            return state

        return self._transition_to_next(state, node.next_on_success, workspace_dir=workspace_dir)

    def resolve_pending_gate(
        self,
        state: StateYaml,
        gate_result: dict[str, Any],
        *,
        workspace_dir: Path | None = None,
    ) -> StateYaml:
        """处理一个已挂起 gate 的用户选择。"""
        if state.pending_gate is None:
            raise ValueError("No pending gate to resolve")
        node = self.nodes[state.current_task]
        if node.task_id == "T4-GATE1" and workspace_dir is not None:
            current_pool = _t4_gate1_candidate_pool_fingerprints(workspace_dir)
            previous_pool = (state.pending_gate.presentation or {}).get("candidate_pool_fingerprints")
            changed = _gate1_pool_fingerprint_changed(previous_pool, current_pool)
            if changed:
                gate_spec = self._find_gate(self._gate_id_for_node(node))
                presentation = build_presentation(
                    gate_spec,
                    model_dump(state, mode="json"),
                    workspace_dir,
                )
                presentation["candidate_pool_fingerprints"] = current_pool
                presentation["stale_reason"] = (
                    "T4-GATE1 candidate pool changed while waiting for human selection: "
                    + ", ".join(changed[:8])
                )
                state.pending_gate = GateState(
                    gate_id=self._gate_id_for_node(node),
                    presented_at=_now_iso(),
                    presentation=presentation,
                    options=list(gate_spec.get("options", [])),
                )
                state.status = "WAITING_HUMAN"
                state.paused_at = _now_iso()
                state.last_error = presentation["stale_reason"]
                return state
        next_task = self._resolve_branch(node, gate_result, state, workspace_dir=workspace_dir)
        self._persist_immediate_gate_result(node, gate_result, next_task, workspace_dir)
        if node.task_id == "T5-EXTERNAL-WAIT" and workspace_dir is not None and next_task == "T7-INGEST":
            readiness = validate_external_executor_ready(
                workspace_dir,
                "external_executor/result_pack.json",
                "external_executor/executor_status.json",
            )
            if not readiness.get("ok"):
                if state.pending_gate is not None:
                    state.pending_gate.presentation["external_executor_wait_status"] = readiness.get("message")
                state.status = "WAITING_HUMAN"
                state.paused_at = _now_iso()
                state.last_error = str(readiness.get("message") or "external executor result is not ready")
                return state
        state.pending_gate = None
        return self._transition_to_next(state, next_task, workspace_dir=workspace_dir)

    def _transition_to_next(
        self,
        state: StateYaml,
        next_task: str | None,
        *,
        workspace_dir: Path | None = None,
    ) -> StateYaml:
        """统一处理正常 next / terminal next / 特殊占位 next。"""
        if next_task is None or next_task == "__terminal__":
            state.status = "COMPLETED"
            return state
        if next_task == "__fail__":
            state.status = "FAILED"
            return state

        next_task = self._resolve_special_target(
            current_task=state.current_task,
            next_task=next_task,
            workspace_dir=workspace_dir,
        )

        target = self.nodes[next_task]
        state.current_task = next_task
        if target.terminal:
            state.status = "FAILED" if next_task.lower().startswith("fail") else "COMPLETED"
        else:
            state.status = "RUNNING"
        return state

    def _gate_id_for_node(self, node: TaskNode) -> str:
        if isinstance(node.gate, dict):
            return str(node.gate.get("id") or node.gate.get("ref") or node.gate.get("type"))
        return str(node.gate)

    def _find_gate(self, gate_id: str) -> dict[str, Any]:
        gate = self.gates.get(gate_id)
        if gate is None:
            raise KeyError(f"Gate '{gate_id}' not found in gates config")
        return gate

    def _resolve_branch(
        self,
        node: TaskNode,
        gate_result: dict[str, Any],
        state: StateYaml,
        *,
        workspace_dir: Path | None = None,
    ) -> str:
        """根据 gate 选择计算下一跳。

        支持两类配置：
        - `gate.options[*].next`
        - `branches: {option_id: next_task}`
        """
        option_id = gate_result.get("option_id") or gate_result.get("key")
        gate_spec = self.gates.get(self._gate_id_for_node(node), {})
        option = self._find_option(gate_spec, option_id) or self._find_option_from_node(node, option_id)
        next_state = None
        if option is not None:
            next_state = option.get("next")
            if option.get("extra"):
                state.task_context.update(option["extra"])

        branches = dict(node.branches or {})
        if isinstance(node.gate, dict):
            branches.update(node.gate.get("branches", {}))
        branches.update(gate_spec.get("branches", {}))
        if next_state is None:
            next_state = branches.get(option_id)
        if next_state is None:
            raise KeyError(f"Gate option '{option_id}' has no branch mapping")

        next_state = self._resolve_special_target(
            current_task=state.current_task,
            next_task=next_state,
            workspace_dir=workspace_dir,
        )

        # 如果 next_state 指向一个之前成功跑过的 task，则记为一次“正常迭代”。
        if next_state in self.nodes and self._is_iteration(next_state, state):
            state.iteration_count[next_state] = state.iteration_count.get(next_state, 0) + 1

        if next_state in self.nodes:
            limit = self.nodes[next_state].max_iterations
            if limit is not None and state.iteration_count.get(next_state, 0) >= limit:
                if "ITER_LIMIT_GATE" in self.nodes:
                    return "ITER_LIMIT_GATE"
        return next_state

    def _persist_immediate_gate_result(
        self,
        node: TaskNode,
        gate_result: dict[str, Any],
        next_task: str,
        workspace_dir: Path | None,
    ) -> None:
        """Persist the user decision for gate-only nodes that declare a JSON output."""

        if workspace_dir is None or not (node.extra or {}).get("immediate_gate"):
            return
        if node.task_id == "T2-PARAM-GATE":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "survey_balanced")
            captured = gate_result.get("captured") or {}
            payload = build_literature_param_payload(
                selected_option=option_id,
                captured=captured if isinstance(captured, dict) else {},
                workspace_dir=workspace_dir,
            )
            payload["task_id"] = node.task_id
            payload["gate_id"] = self._gate_id_for_node(node)
            payload["next_task"] = next_task
            payload["input_fingerprints"] = build_input_fingerprints(
                workspace_dir,
                _T2_LITERATURE_PARAM_GATE_INPUT_PATHS,
            )
            payload["decided_at"] = _now_iso()
            path = workspace_dir / "literature" / "literature_params.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return
        if node.task_id == "T3.6-GATE-SURVEY":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "")
            write_survey = option_id in {"yes", "write_survey", "survey", "撰写综述"}
            payload = {
                "write_survey": write_survey,
                "user_answer": option_id,
                "selected_option": option_id,
                "note": (
                    "taxonomy-driven survey, not synthesis-to-tex"
                    if write_survey
                    else "skip survey branch and continue T4"
                ),
                "input_fingerprints": build_input_fingerprints(workspace_dir, _T36_SURVEY_GATE_INPUT_PATHS),
                "decided_at": _now_iso(),
            }
            path = workspace_dir / "drafts" / "survey" / "decision.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return
        if node.task_id == "T3.6-GATE-CORPUS":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "")
            scope = "complete" if option_id in {"complete", "full", "expand", "补检", "完整"} else "conservative"
            payload = {
                "scope": scope,
                "selected_option": option_id,
                "note": (
                    "one-shot targeted survey expansion plan"
                    if scope == "complete"
                    else "use existing T2/T3 corpus only"
                ),
                "input_fingerprints": build_input_fingerprints(workspace_dir, _T36_CORPUS_GATE_INPUT_PATHS),
                "decided_at": _now_iso(),
            }
            path = workspace_dir / "drafts" / "survey" / "corpus_decision.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return
        if node.task_id == "T4-GATE1":
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "")
            captured = gate_result.get("captured") or {}
            candidate_pool_fingerprints = _t4_gate1_candidate_pool_fingerprints(workspace_dir)
            fingerprint_payload = {
                "semantics": "t4_gate1_selection_fingerprint",
                "gate_id": self._gate_id_for_node(node),
                "selected_option": option_id,
                "captured": captured,
                "candidate_pool_fingerprints": candidate_pool_fingerprints,
            }
            payload = {
                "semantics": "t4_gate1_user_selection_for_candidate_pool",
                "task_id": node.task_id,
                "gate_id": self._gate_id_for_node(node),
                "selected_option": option_id,
                "captured": captured,
                "candidate_pool_fingerprints": candidate_pool_fingerprints,
                "selection_fingerprint": _stable_json_fingerprint(fingerprint_payload),
                "next_task": next_task,
                "decided_at": _now_iso(),
            }
            path = workspace_dir / "ideation" / "_gate1_user_selection.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return
        if node.task_id == "T5-EXECUTOR-GATE":
            if next_task == "T5-HANDOFF":
                outputs = node.outputs or {}
                for rel_path in outputs.values():
                    path = workspace_dir / rel_path
                    if path.suffix.lower() != ".json":
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    payload = {
                        "semantics": "external_executor_selection_deferred_for_handoff_rebuild",
                        "task_id": node.task_id,
                        "gate_id": self._gate_id_for_node(node),
                        "selected_option": gate_result.get("option_id") or gate_result.get("key"),
                        "next_task": next_task,
                        "decided_at": _now_iso(),
                    }
                    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                    return
            option_id = str(gate_result.get("option_id") or gate_result.get("key") or "mock_dry_run")
            aliases = {
                "mock": "mock_dry_run",
                "dry": "mock_dry_run",
                "dry_run": "mock_dry_run",
                "external_ready_later": "claude_code_window",
                "claude": "claude_code_window",
                "manual_external": "manual",
            }
            selected_executor = aliases.get(option_id, option_id)
            if selected_executor not in {"mock_dry_run", "codex_cli", "claude_code_window", "manual"}:
                selected_executor = "mock_dry_run"
            captured = gate_result.get("captured") or {}
            notes = str(captured.get("notes") or captured.get("note") or "")
            if captured.get("downgraded_from"):
                downgrade_note = (
                    f"downgraded_from={captured.get('downgraded_from')}; "
                    f"reason={captured.get('downgrade_reason') or 'not specified'}"
                )
                notes = f"{notes}; {downgrade_note}".strip("; ")
            selection = build_executor_selection_payload(
                selected_executor=selected_executor,
                selected_by="human",
                notes=notes,
            )
            selection["next_state"] = next_task
            path = workspace_dir / "external_executor" / "executor_selection.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            patch_external_executor_files_with_selection(workspace_dir, selection)
            return
        outputs = node.outputs or {}
        for rel_path in outputs.values():
            path = workspace_dir / rel_path
            if path.suffix.lower() != ".json":
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "semantics": "human_decision_over_agent_recommendation",
                "task_id": node.task_id,
                "gate_id": self._gate_id_for_node(node),
                "selected_option": gate_result.get("option_id") or gate_result.get("key"),
                "captured": gate_result.get("captured") or {},
                "next_task": next_task,
                "decided_at": _now_iso(),
            }
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return

    def _resolve_special_target(
        self,
        *,
        current_task: str,
        next_task: str,
        workspace_dir: Path | None,
    ) -> str:
        """解析状态机里的特殊占位目标。"""
        if next_task != "__parse_from_output__":
            return next_task
        if workspace_dir is None:
            raise ValueError("workspace_dir is required for __parse_from_output__ targets")

        if current_task == "T7.5":
            return self._parse_t75_decision(workspace_dir)
        if current_task == "T4.5":
            return self._parse_t45_verdict(workspace_dir)
        if current_task == "T3.6-GATE-SURVEY":
            return self._parse_t36_survey_decision(workspace_dir)
        if current_task == "T3.6-GATE-CORPUS":
            return self._parse_t36_corpus_decision(workspace_dir)

        raise ValueError(f"Unsupported __parse_from_output__ task: {current_task}")

    def _parse_t45_verdict(self, workspace_dir: Path) -> str:
        """Route T4.5 according to the explicit Final Gate Verdict in novelty_audit.md."""

        human_review = "T4.5-HUMAN-REVIEW" if "T4.5-HUMAN-REVIEW" in self.nodes else "failed"
        audit_path = workspace_dir / "ideation" / "novelty_audit.md"
        if not audit_path.exists():
            return human_review
        if not _file_newer_than_existing_inputs(
            audit_path,
            [
                workspace_dir / "ideation" / "hypotheses.md",
                workspace_dir / "ideation" / "idea_scorecard.yaml",
                workspace_dir / "ideation" / "gate_decisions.json",
                workspace_dir / "literature" / "synthesis.md",
                workspace_dir / "literature" / "synthesis_workbench.json",
                workspace_dir / "literature" / "comparison_table.csv",
            ],
        ):
            return human_review
        ok, _err = validate_t45_fingerprint_report(workspace_dir)
        if not ok:
            return human_review

        text = audit_path.read_text(encoding="utf-8", errors="replace")
        verdict_text = _extract_t45_final_gate_verdict(text)
        if not verdict_text:
            return human_review

        normalized = verdict_text.lower().replace("-", "_").replace(" ", "_")
        if any(token in normalized for token in ("return_to_t4", "return_tot4", "reframe", "回到t4", "回退t4")):
            return human_review
        if any(token in normalized for token in ("drop_due_to_collision", "drop", "collision", "reject", "fail")):
            return human_review
        verdict_token = re.split(r"[^a-z0-9_]+", normalized, maxsplit=1)[0]
        pass_tokens = {
            "pass",
            "passed",
            "pass_to_experiment",
            "pass_with_required_baselines",
            "go_t7",
            "continue_to_t7",
            "continue_to_experiment",
        }
        if verdict_token in pass_tokens:
            if "T5-HANDOFF" in self.nodes:
                return "T5-HANDOFF"
            return "T7" if "T7" in self.nodes else "failed"
        return human_review

    def _parse_t36_survey_decision(self, workspace_dir: Path) -> str:
        """Route the optional T3.6 survey branch from drafts/survey/decision.json."""

        path = workspace_dir / "drafts" / "survey" / "decision.json"
        data = self._read_json_dict(path)
        if data is None:
            return "T3.6-GATE-SURVEY" if "T3.6-GATE-SURVEY" in self.nodes else "failed"
        fingerprints = data.get("input_fingerprints")
        if fingerprints is not None:
            ok, _ = validate_input_fingerprints(
                workspace_dir,
                fingerprints,
                _T36_SURVEY_GATE_INPUT_PATHS,
                label_for_error="T3.6 survey gate decision",
            )
            if not ok:
                return "T3.6-GATE-SURVEY" if "T3.6-GATE-SURVEY" in self.nodes else "failed"
        decision = data.get("write_survey")
        if isinstance(decision, str):
            decision = decision.strip().lower() in {"yes", "true", "1", "write", "survey", "撰写", "是"}
        if decision:
            return "T3.6-PLAN" if "T3.6-PLAN" in self.nodes else "T4"
        return "T4" if "T4" in self.nodes else "failed"

    def _parse_t36_corpus_decision(self, workspace_dir: Path) -> str:
        """Route the survey corpus-scope gate from drafts/survey/corpus_decision.json."""

        path = workspace_dir / "drafts" / "survey" / "corpus_decision.json"
        data = self._read_json_dict(path)
        if data is None:
            return "T3.6-GATE-CORPUS" if "T3.6-GATE-CORPUS" in self.nodes else "failed"
        fingerprints = data.get("input_fingerprints")
        if fingerprints is not None:
            ok, _ = validate_input_fingerprints(
                workspace_dir,
                fingerprints,
                _T36_CORPUS_GATE_INPUT_PATHS,
                label_for_error="T3.6 corpus gate decision",
            )
            if not ok:
                return "T3.6-GATE-CORPUS" if "T3.6-GATE-CORPUS" in self.nodes else "failed"
        scope = str(data.get("scope") or data.get("corpus_scope") or "").strip().lower()
        if scope in {"complete", "full", "expand", "完整", "补检", "定向补检"}:
            return "T3.6-EXPAND" if "T3.6-EXPAND" in self.nodes else "T3.6-STATE"
        return "T3.6-STATE" if "T3.6-STATE" in self.nodes else "T4"

    @staticmethod
    def _read_json_dict(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def _parse_t75_decision(self, workspace_dir: Path) -> str:
        """T7.5 完成后，解析 evaluation_decision.md 的推荐下一步。"""

        default_t8_entry = self._default_t8_entry(workspace_dir)
        decision_path = workspace_dir / "evaluation" / "evaluation_decision.md"
        if not decision_path.exists():
            return default_t8_entry

        text = decision_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"next_task:\s*([A-Za-z0-9_.-]+)", text, re.DOTALL)
        if match is None:
            return default_t8_entry

        raw_target = match.group(1).strip()
        aliases = {
            "T5": self._default_experiment_entry(),
            "T6": self._default_experiment_entry(),
            "T7": self._default_experiment_entry(),
            "T8": self._default_t8_entry(workspace_dir),
            "T8-WRITE": self._default_t8_entry(workspace_dir),
            "T8-SEC-LIMITATIONS": "T8-SEC-CONCLUSION",
            "terminate": "done",
            "terminal": "done",
            "stop": "done",
            "end": "done",
        }
        target = aliases.get(raw_target, raw_target)
        if target not in self.nodes and raw_target in self.nodes:
            return raw_target
        if target not in self.nodes:
            return default_t8_entry
        return target

    def _default_experiment_entry(self) -> str:
        if "T5-HANDOFF" in self.nodes:
            return "T5-HANDOFF"
        if "T7" in self.nodes:
            return "T7"
        if "T5" in self.nodes:
            return "T5"
        return "failed"

    def _default_t8_entry(self, workspace_dir: Path | None = None) -> str:
        if "T8-STYLE-GATE" in self.nodes:
            if workspace_dir is not None and _valid_writing_style_file(workspace_dir / "drafts" / "writing_style.json"):
                if "T8-RESOURCE" in self.nodes:
                    return "T8-RESOURCE"
            return "T8-STYLE-GATE"
        if "T8-RESOURCE" in self.nodes:
            return "T8-RESOURCE"
        if "T8-WRITE" in self.nodes:
            return "T8-WRITE"
        return "done"

    @staticmethod
    def _find_option(gate_spec: dict[str, Any], option_id: str | None) -> dict[str, Any] | None:
        for option in gate_spec.get("options", []):
            key = option.get("id") or option.get("key")
            if key == option_id:
                return option
        return None

    @staticmethod
    def _find_option_from_node(node: TaskNode, option_id: str | None) -> dict[str, Any] | None:
        if not isinstance(node.gate, dict):
            return None
        for option in node.gate.get("options", []):
            key = option.get("id") or option.get("key")
            if key == option_id:
                return option
        return None

    def _is_iteration(self, next_state: str, state: StateYaml) -> bool:
        return any(history.task == next_state and history.status == "DONE" for history in state.history)

    @staticmethod
    def _build_overrides(
        node: TaskNode,
    ) -> tuple[LLMConfigOverride, BudgetOverride, ToolPolicyOverride]:
        """把节点里的 llm/budget/tools 块转换成 ExecutionContext override。"""
        llm_block = node.llm or {}
        llm_ov = LLMConfigOverride(
            profile=llm_block.get("profile"),
            tier=llm_block.get("tier"),
            model=llm_block.get("model"),
            endpoint=llm_block.get("endpoint"),
            max_context=llm_block.get("max_context"),
            temperature=llm_block.get("temperature"),
        )

        budget_block = node.budget or {}
        budget_ov = BudgetOverride(
            max_steps=budget_block.get("max_steps"),
            max_tokens=budget_block.get("max_tokens"),
            max_wall_seconds=budget_block.get("max_wall_seconds"),
            unlimited_budget=_budget_has_unlimited_tag(budget_block, node.tags),
        )

        tools_block = node.tools or {}
        tool_ov = ToolPolicyOverride(
            allowed_read_prefixes=tools_block.get("allowed_read_prefixes"),
            allowed_write_prefixes=tools_block.get("allowed_write_prefixes"),
            extra_tool_names=tools_block.get("extra_tool_names", tools_block.get("extra", [])),
        )
        return llm_ov, budget_ov, tool_ov

    def _validate_target(
        self,
        task_id: str,
        field_name: str,
        target: str | None,
        errors: list[str],
    ) -> None:
        if target is None:
            return
        if target in {"__terminal__", "__fail__", "__parse_from_output__"}:
            return
        if target not in self.nodes:
            errors.append(f"{task_id}: {field_name} points to unknown node '{target}'")

    def _validate_gate(self, task_id: str, node: TaskNode, errors: list[str]) -> None:
        if not node.gate:
            return

        gate_id = self._gate_id_for_node(node)
        inline_gate = node.gate if isinstance(node.gate, dict) else {}
        gate_spec = self.gates.get(gate_id, {})
        if gate_id not in self.gates and not inline_gate.get("options"):
            errors.append(f"{task_id}: gate '{gate_id}' not found in gates config")

        for option in list(gate_spec.get("options", [])) + list(inline_gate.get("options", [])):
            next_target = option.get("next")
            if next_target is not None:
                self._validate_target(task_id, f"gate option '{option.get('id') or option.get('key')}'", next_target, errors)

        branch_maps = [
            ("branches", node.branches or {}),
            ("gate.branches", inline_gate.get("branches", {})),
            (f"gates.{gate_id}.branches", gate_spec.get("branches", {})),
        ]
        for field_name, mapping in branch_maps:
            if not isinstance(mapping, dict):
                continue
            for option_id, target in mapping.items():
                self._validate_target(task_id, f"{field_name}.{option_id}", target, errors)

    def _validate_task_contract(self, task_id: str, node: TaskNode, errors: list[str]) -> None:
        """检查节点与 task I/O 契约是否一致。

        这里只对 ResearchOS 已定义 contract 的正式 task 生效；像 `done`/`failed` 这类
        控制节点或自定义调试节点，不做额外限制。
        """

        try:
            contract = get_task_io(task_id)
        except KeyError:
            return

        declared_inputs = dict(node.inputs or {})
        declared_outputs = dict(node.outputs or {})
        declared_outputs.update(dict(node.optional_outputs or {}))
        contract_inputs = dict(contract.get("inputs", {}))
        contract_outputs = dict(contract.get("outputs", {}))

        if declared_inputs != contract_inputs:
            errors.append(
                f"{task_id}: node.inputs does not match task_io_contract "
                f"(declared={declared_inputs}, contract={contract_inputs})"
            )
        if declared_outputs != contract_outputs:
            errors.append(
                f"{task_id}: node.outputs does not match task_io_contract "
                f"(declared={declared_outputs}, contract={contract_outputs})"
            )

    def _check_budget_drift(self, state: StateYaml, workspace_dir: Path) -> None:
        """检查预算漂移并发出警告（§7.1）。

        如果累计花费超过预算的70%，记录警告；
        如果超过90%，记录严重警告。
        """
        from ..runtime.logger import get_logger

        logger = get_logger("state_machine.budget")

        # 读取project.yaml获取预算上限
        project_file = workspace_dir / "project.yaml"
        if not project_file.exists():
            return

        try:
            project_data = yaml.safe_load(project_file.read_text(encoding="utf-8"))
            max_budget = project_data.get("constraints", {}).get("max_budget_usd")
            if max_budget is None or max_budget <= 0:
                return

            spent = state.budget_cumulative.cost_usd_total
            ratio = spent / max_budget

            if ratio >= 0.9:
                logger.warning(
                    f"预算严重超支警告: 已花费 ${spent:.2f} / ${max_budget:.2f} ({ratio*100:.1f}%)"
                )
                # 写入预算警告文件
                warning_file = workspace_dir / ".researchos" / "budget_warning.txt"
                warning_file.parent.mkdir(parents=True, exist_ok=True)
                warning_file.write_text(
                    f"预算严重超支警告 (90%+)\n"
                    f"已花费: ${spent:.2f}\n"
                    f"预算上限: ${max_budget:.2f}\n"
                    f"使用比例: {ratio*100:.1f}%\n"
                    f"当前任务: {state.current_task}\n"
                    f"时间: {_now_iso()}\n",
                    encoding="utf-8"
                )
            elif ratio >= 0.7:
                logger.warning(
                    f"预算警告: 已花费 ${spent:.2f} / ${max_budget:.2f} ({ratio*100:.1f}%)"
                )
        except Exception as e:
            logger.debug(f"预算检查失败: {e}")

    def _check_iteration_deadlock(self, state: StateYaml, node: TaskNode) -> None:
        """检查迭代死锁：相同参数组合尝试3次以上时快速失败。

        Phase 2.3: 防止 Agent 在相同参数上无限迭代。
        """
        from ..runtime.logger import get_logger

        logger = get_logger("state_machine.deadlock")

        task_id = state.current_task
        task_history = state.iteration_history.get(task_id, [])

        if not task_history:
            return

        # 计算当前参数哈希
        current_params = self._extract_task_params(node)
        current_hash = self._compute_param_hash(current_params)

        # 统计相同参数哈希出现次数
        same_param_count = sum(1 for entry in task_history if entry.get("param_hash") == current_hash)

        if same_param_count >= 3:
            error_msg = (
                f"检测到迭代死锁：任务 '{task_id}' 使用相同参数已尝试 {same_param_count} 次。"
                f"\n参数哈希: {current_hash}"
                f"\n参数内容: {current_params}"
                f"\n建议：检查任务配置或修改参数以避免无限循环。"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        if same_param_count >= 2:
            logger.warning(
                f"迭代警告：任务 '{task_id}' 使用相同参数已尝试 {same_param_count} 次，"
                f"再次尝试将触发死锁保护。"
            )

    def _record_iteration_attempt(self, state: StateYaml, node: TaskNode) -> None:
        """记录本次迭代尝试到 iteration_history。

        Phase 2.3: 用于后续死锁检测。
        """
        task_id = state.current_task
        params = self._extract_task_params(node)
        param_hash = self._compute_param_hash(params)

        if task_id not in state.iteration_history:
            state.iteration_history[task_id] = []

        state.iteration_history[task_id].append(
            {
                "param_hash": param_hash,
                "timestamp": _now_iso(),
                "params": params,
            }
        )

    @staticmethod
    def _extract_task_params(node: TaskNode) -> dict[str, Any]:
        """提取任务的关键参数用于死锁检测。

        包括：inputs, outputs, llm配置, budget配置等影响任务行为的参数。
        """
        params = {}

        if node.inputs:
            params["inputs"] = dict(node.inputs)
        if node.outputs:
            params["outputs"] = dict(node.outputs)
        if node.llm:
            params["llm"] = dict(node.llm)
        if node.budget:
            params["budget"] = dict(node.budget)
        if node.mode:
            params["mode"] = node.mode
        if node.extra:
            params["extra"] = dict(node.extra)

        return params

    @staticmethod
    def _compute_param_hash(params: dict[str, Any]) -> str:
        """计算参数字典的哈希值。

        使用 frozenset 处理嵌套字典，确保参数顺序不影响哈希结果。
        """
        import json

        # 将参数转换为规范化的JSON字符串，然后计算哈希
        # 使用 sort_keys 确保字典顺序一致
        normalized = json.dumps(params, sort_keys=True, default=str)
        return str(hash(normalized))
