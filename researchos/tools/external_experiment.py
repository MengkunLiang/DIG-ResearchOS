from __future__ import annotations

"""External experiment handoff and evidence tools.

ResearchOS owns protocol, provenance, integrity checks, and claim mapping.
External executors such as Codex CLI, Claude Code, or a manual runner own code
implementation and experiment execution in an isolated workspace. These tools
only read/write workspace artifacts and provide a mock dry-run path for tests.
"""

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from .base import Tool, ToolResult
from .workspace_policy import ToolAccessDenied, WorkspaceAccessPolicy


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _artifact_record(workspace: Path, rel_path: str, *, role: str, kind: str = "file") -> dict[str, Any]:
    path = workspace / rel_path
    record: dict[str, Any] = {"path": rel_path, "kind": kind, "role": role, "exists": path.exists()}
    if path.exists() and path.is_file():
        record.update({"sha256": _sha256(path), "bytes": path.stat().st_size})
    return record


def _extract_exp_plan_metrics(exp_plan: dict[str, Any]) -> list[str]:
    metrics: list[str] = []
    for exp in exp_plan.get("experiments", []) or []:
        if not isinstance(exp, dict):
            continue
        for metric in exp.get("metrics", []) or []:
            if isinstance(metric, dict) and metric.get("name"):
                metrics.append(str(metric["name"]))
            elif isinstance(metric, str):
                metrics.append(metric)
    return list(dict.fromkeys(metrics)) or ["task_score"]


def _extract_exp_plan_seeds(project: dict[str, Any]) -> list[int]:
    seed_ensemble = project.get("seed_ensemble") if isinstance(project, dict) else {}
    seeds: list[int] = []
    if isinstance(seed_ensemble, dict):
        for key in ("tier1_seeds", "tier2_seeds", "tier3_seeds"):
            for value in seed_ensemble.get(key, []) or []:
                try:
                    seeds.append(int(value))
                except Exception:
                    continue
    return list(dict.fromkeys(seeds)) or [42]


def _infer_experiment_intent(project: dict[str, Any], hypotheses: str) -> str:
    topic = str(project.get("topic") or project.get("research_direction") or project.get("project_id") or "").strip()
    for line in hypotheses.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:240]
    if topic:
        return f"Evaluate the ResearchOS hypothesis for: {topic}"
    return "Evaluate the selected ResearchOS hypothesis under an auditable external experiment protocol."


def _extract_required_baselines(workspace: Path) -> list[dict[str, Any]]:
    """Extract user/LLM-written required baselines without inventing knowledge."""

    existing = _read_json(workspace / "novelty" / "required_baselines.json")
    if isinstance(existing.get("required_baselines"), list):
        return [item for item in existing["required_baselines"] if isinstance(item, dict)]

    text = ""
    for rel in ("ideation/novelty_audit.md", "novelty/must_add_baselines.md"):
        path = workspace / rel
        if path.exists():
            text += "\n" + path.read_text(encoding="utf-8", errors="replace")
    if "Required Baselines" not in text and "Baseline:" not in text:
        return []

    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    baseline_index = 1
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        baseline_match = re.match(r"^-?\s*(?:Baseline|基线)\s*[:：]\s*(.+)$", line, re.IGNORECASE)
        if baseline_match:
            if current and current.get("baseline_name"):
                items.append(current)
            name = baseline_match.group(1).strip()
            current = {
                "baseline_id": f"required_baseline_{baseline_index}",
                "baseline_name": name,
                "source": "novelty_audit",
                "reason_required": "",
                "priority": "must_run",
                "acceptable_substitute": None,
                "cannot_claim_without_it": ["outperforms prior work", "state-of-the-art", "strong empirical advantage"],
            }
            baseline_index += 1
            continue
        if current is None:
            continue
        field_match = re.match(r"^-?\s*([^:：]+)\s*[:：]\s*(.+)$", line)
        if not field_match:
            continue
        key = field_match.group(1).strip().lower().replace(" ", "_")
        value = field_match.group(2).strip()
        if key in {"reason", "reason_required", "must_run_because", "why"}:
            current["reason_required"] = value
        elif key in {"acceptable_substitute", "substitute", "可替代"}:
            current["acceptable_substitute"] = value
        elif key in {"claims_blocked_if_missing", "cannot_claim_without_it", "blocked_claims"}:
            current["cannot_claim_without_it"] = [part.strip() for part in re.split(r"[,;；，]", value) if part.strip()]
    if current and current.get("baseline_name"):
        items.append(current)
    return items


def build_executor_selection_payload(
    *,
    selected_executor: str,
    selected_by: str = "human",
    notes: str = "",
) -> dict[str, Any]:
    real_allowed = selected_executor != "mock_dry_run"
    requires_copy = selected_executor in {"claude_code_window", "manual"}
    next_state = "T5-DRY-RUN" if selected_executor == "mock_dry_run" else "T5-EXTERNAL-WAIT"
    payload: dict[str, Any] = {
        "version": "1.0",
        "semantics": "external_executor_selection",
        "selected_executor": selected_executor,
        "real_experiment_allowed": real_allowed,
        "requires_user_copy_paste": requires_copy,
        "selected_by": selected_by,
        "selected_at": _now_iso(),
        "next_state": next_state,
        "fallback_order": [item for item in ["mock_dry_run", "claude_code_window", "manual"] if item != selected_executor],
        "notes": notes or _default_executor_selection_note(selected_executor),
    }
    if selected_executor == "claude_code_window":
        payload["prompt_to_copy"] = "external_executor/claude_code_prompt.md"
    if selected_executor == "codex_cli":
        payload["prompt_file"] = "external_executor/codex_prompt.md"
        payload["allowed_workdir"] = "external_executor/workdir"
    if selected_executor == "manual":
        payload["prompt_to_copy"] = "external_executor/manual_instructions.md"
    return payload


def _default_executor_selection_note(selected_executor: str) -> str:
    if selected_executor == "mock_dry_run":
        return "Protocol-only dry run selected; no real experiment evidence will be produced."
    if selected_executor == "codex_cli":
        return "Codex CLI selected for external real execution inside allowed workdir."
    if selected_executor == "claude_code_window":
        return "Claude Code window selected; user must copy prompt and resume after result_pack.json exists."
    return "Manual external execution selected; ResearchOS waits for result_pack.json."


def patch_external_executor_files_with_selection(workspace: Path, selection: dict[str, Any]) -> None:
    dry_run = "true" if selection.get("selected_executor") == "mock_dry_run" else "false"
    mock_only = "true" if selection.get("selected_executor") == "mock_dry_run" else "false"
    real_allowed = "true" if selection.get("real_experiment_allowed") else "false"
    selected = str(selection.get("selected_executor") or "mock_dry_run")
    execution_mode = "dry_run" if selected == "mock_dry_run" else "external"
    handoff_path = workspace / "external_executor" / "handoff_pack.json"
    handoff = _read_json(handoff_path)
    if handoff:
        handoff["executor"] = selected
        handoff["execution_mode"] = execution_mode
        _write_json(handoff_path, handoff)
    for rel in (
        "external_executor/AGENTS.md",
        "external_executor/CLAUDE.md",
        "external_executor/README.md",
        "external_executor/executor_prompt.md",
        "external_executor/codex_prompt.md",
        "external_executor/claude_code_prompt.md",
        "external_executor/manual_instructions.md",
    ):
        path = workspace / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        text = text.replace("dry_run: UNSET", f"dry_run: {dry_run}")
        text = text.replace("mock_only: UNSET", f"mock_only: {mock_only}")
        text = text.replace("real_experiment_allowed: UNSET", f"real_experiment_allowed: {real_allowed}")
        text = text.replace('"executor": "UNSET"', f'"executor": "{selected}"')
        text = text.replace('"execution_mode": "unselected"', f'"execution_mode": "{execution_mode}"')
        text = text.replace('"selected_executor": "UNSET"', f'"selected_executor": "{selected}"')
        text = text.replace(
            "EXECUTION MODE NOT YET SELECTED",
            f"EXECUTION MODE SELECTED: {selection.get('selected_executor')}",
        )
        path.write_text(text, encoding="utf-8")
    job_state = _read_json(workspace / "external_executor" / "job_state.json")
    if job_state:
        job_state.update(
            {
                "executor_type": selection.get("selected_executor"),
                "dry_run": selection.get("selected_executor") == "mock_dry_run",
                "mock_only": selection.get("selected_executor") == "mock_dry_run",
                "current_state": "CREATED",
                "selection": selection,
            }
        )
        _write_json(workspace / "external_executor" / "job_state.json", job_state)


def validate_external_executor_ready(workspace: Path, result_pack_rel: str, status_rel: str) -> dict[str, Any]:
    missing = [rel for rel in (result_pack_rel, status_rel) if not (workspace / rel).exists()]
    if missing:
        report = {
            "version": "1.0",
            "semantics": "external_executor_wait_acceptance_report",
            "ok": False,
            "message": (
                "WAITING_EXTERNAL: external executor has not produced required files: "
                + ", ".join(missing)
                + ". Run the selected executor, then resume ResearchOS."
            ),
            "missing": missing,
        }
        _write_wait_rejection_report(workspace, report)
        return report
    result_pack = _read_json(workspace / result_pack_rel)
    status = _read_json(workspace / status_rel)
    manifest_rel = str(result_pack.get("run_manifest") or status.get("run_manifest") or "external_executor/run_manifest.json")
    manifest = _read_json(workspace / manifest_rel)
    allowed_paths_path = workspace / "external_executor" / "allowed_paths.txt"
    allowed_rules = _parse_allowed_paths(allowed_paths_path)
    issues: list[str] = []
    if not allowed_paths_path.exists() or not allowed_rules:
        issues.append("external_executor/allowed_paths.txt missing or empty")
    if result_pack.get("semantics") != "external_executor_result_pack":
        issues.append("result_pack semantics invalid")
    if status.get("semantics") != "external_executor_status":
        issues.append("executor_status semantics invalid")
    current_state = status.get("current_state") or status.get("status")
    if current_state not in {"done", "COMPLETED", "PARTIAL_RESULTS_READY"}:
        issues.append("executor_status current_state/status is not done/COMPLETED/PARTIAL_RESULTS_READY")
    job_state = _read_json(workspace / "external_executor" / "job_state.json")
    allowed_states = job_state.get("allowed_states") if isinstance(job_state, dict) else None
    if allowed_states:
        state_value = status.get("current_state") or status.get("status")
        if state_value not in allowed_states and state_value not in {"done"}:
            issues.append(f"executor_status state not declared in job_state.allowed_states: {state_value}")
        job_current = job_state.get("current_state")
        if job_current and state_value in allowed_states and job_current in allowed_states:
            if allowed_states.index(state_value) < allowed_states.index(job_current):
                issues.append(f"executor_status state regressed from job_state current_state: {job_current} -> {state_value}")
    metrics = result_pack.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        issues.append("result_pack.metrics missing")
    if manifest.get("semantics") != "external_executor_run_manifest":
        issues.append("run_manifest semantics invalid or missing")
    for rel_path in _referenced_executor_paths(result_pack, manifest):
        if not _path_allowed(rel_path, allowed_rules):
            issues.append(f"path not allowed by allowed_paths.txt: {rel_path}")
            continue
        path = workspace / rel_path
        if not path.exists():
            issues.append(f"referenced artifact missing: {rel_path}")
    for artifact in result_pack.get("artifacts", []) or []:
        if not isinstance(artifact, dict):
            continue
        rel_path = str(artifact.get("path") or "")
        expected_hash = str(artifact.get("sha256") or "")
        if not rel_path or not expected_hash:
            issues.append(f"artifact missing path/hash: {artifact}")
            continue
        path = workspace / rel_path
        if path.exists() and path.is_file() and _sha256(path) != expected_hash:
            issues.append(f"artifact hash mismatch: {rel_path}")
    if not result_pack.get("mock_only"):
        runs = result_pack.get("runs")
        manifest_runs = manifest.get("runs")
        if not isinstance(runs, list) or not runs:
            issues.append("real result_pack must include non-empty runs")
        if manifest_runs is not None and (not isinstance(manifest_runs, list) or not manifest_runs):
            issues.append("real run_manifest.runs must be non-empty when present")
    if issues:
        report = {
            "version": "1.0",
            "semantics": "external_executor_wait_acceptance_report",
            "ok": False,
            "message": "WAITING_EXTERNAL: external result pack exists but is not valid: " + "; ".join(issues),
            "issues": issues,
        }
        _write_wait_rejection_report(workspace, report)
        return report
    report = {
        "version": "1.0",
        "semantics": "external_executor_wait_acceptance_report",
        "ok": True,
        "message": "External executor result pack is present and schema-compatible.",
        "result_pack": result_pack_rel,
        "executor_status": status_rel,
        "run_manifest": manifest_rel,
        "dry_run": bool(result_pack.get("dry_run")),
        "mock_only": bool(result_pack.get("mock_only")),
    }
    return report


def _parse_allowed_paths(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    rules: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        mode, prefix = parts[0].strip(), parts[1].strip()
        if mode in {"rw", "ro", "no"}:
            rules.append((mode, prefix))
    return rules


def _path_allowed(rel_path: str, rules: list[tuple[str, str]]) -> bool:
    normalized = rel_path.strip().lstrip("./")
    matched: str | None = None
    for mode, prefix in rules:
        prefix_norm = prefix.strip().lstrip("./")
        if normalized == prefix_norm or normalized.startswith(prefix_norm):
            matched = mode
    return matched == "rw"


def _referenced_executor_paths(result_pack: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("raw_result_files", "config_files", "log_files"):
        value = result_pack.get(key)
        if isinstance(value, list):
            paths.extend(str(item) for item in value if isinstance(item, str))
    for key in ("raw_results", "configs", "logs"):
        value = manifest.get(key)
        if isinstance(value, list):
            paths.extend(str(item) for item in value if isinstance(item, str))
    for artifact in list(result_pack.get("artifacts", []) or []) + list(manifest.get("artifacts", []) or []):
        if isinstance(artifact, dict) and artifact.get("path"):
            paths.append(str(artifact["path"]))
    for metric in result_pack.get("metrics", []) or []:
        if isinstance(metric, dict) and metric.get("source_artifact"):
            paths.append(str(metric["source_artifact"]))
    return list(dict.fromkeys(path for path in paths if path))


def _write_wait_rejection_report(workspace: Path, report: dict[str, Any]) -> None:
    path = workspace / "external_executor" / "wait_rejection_report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    issues = report.get("issues") or report.get("missing") or []
    lines = [
        "# External Executor Wait Rejection Report",
        "",
        str(report.get("message") or "External executor result is not ready."),
        "",
        "## Issues",
    ]
    lines.extend(f"- {issue}" for issue in issues)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_external_executor_guides(
    policy: WorkspaceAccessPolicy,
    handoff: dict[str, Any],
    *,
    selection: dict[str, Any],
) -> None:
    ext_dir = policy.workspace_dir / "external_executor"
    ext_dir.mkdir(parents=True, exist_ok=True)
    project_id = str(handoff.get("project_id") or handoff.get("project", {}).get("project_id") or "unknown")
    metrics_block = "\n".join(f"- {metric.get('name')}" for metric in handoff.get("metrics", []) if isinstance(metric, dict)) or "- task_score"
    baselines = handoff.get("required_baselines") or []
    baselines_block = _format_required_baselines_block(baselines)
    allowed_paths = "\n".join(f"- {path}" for path in handoff.get("allowed_paths", []))
    required_outputs = "\n".join(f"- `{path}`" for path in (handoff.get("executor_outputs_contract") or {}).get("must_write", []))
    seeds = ", ".join(str(seed) for seed in handoff.get("seeds", []) or [42])
    common_header = (
        "> EXECUTION MODE NOT YET SELECTED - see executor_selection.json after T5-EXECUTOR-GATE\n\n"
        f"- dry_run: UNSET\n- mock_only: UNSET\n- real_experiment_allowed: UNSET\n\n"
    )
    agents = (
        f"# External Executor Instructions for ResearchOS - project {project_id}\n\n"
        + common_header
        + "## Role\n"
        "You are an external experiment executor for ResearchOS. You are not the paper writer and not the ResearchOS runtime.\n\n"
        "## This experiment in one line\n"
        f"{handoff.get('experiment_intent_oneliner')}\n\n"
        "## Read first\n"
        "1. external_executor/handoff_pack.json\n"
        "2. external_executor/expected_outputs_schema.json\n"
        "3. external_executor/allowed_paths.txt\n"
        "4. external_executor/executor_selection.json\n"
        "5. novelty/required_baselines.json\n"
        "6. resources/baseline_candidates.jsonl\n"
        "7. literature/baseline_map.json\n"
        "8. ideation/novelty_audit.md\n\n"
        "## Metrics you must report\n"
        f"{metrics_block}\n\n"
        "## Required baselines\n"
        f"{baselines_block}\n\n"
        "## Seeds\n"
        f"Run required configurations over seeds: {seeds}\n\n"
        "## Hard boundaries\n"
        f"{allowed_paths}\n\n"
        "Do not fabricate datasets, baselines, metrics, or results. Every metric must trace to a raw file, config, run id, log, and sha256.\n\n"
        "## Required outputs\n"
        f"{required_outputs}\n\n"
        "Write external_executor/result_pack.json last. Do not write paper text or final claims.\n"
    )
    claude = (
        f"# Claude Code External Execution Guide - project {project_id}\n\n"
        + common_header
        + "You are used as an external coding executor for ResearchOS via a Claude Code window.\n\n"
        "## Steps\n"
        "1. Read handoff_pack.json, expected_outputs_schema.json, allowed_paths.txt.\n"
        "2. Read novelty/required_baselines.json and resources/baseline_candidates.jsonl.\n"
        "3. If mock_only=true, emit schema-valid mock artifacts with mock_only=true and dry_run=true.\n"
        "4. If real, clone/inspect baseline repos only inside external_executor/workdir.\n"
        f"5. Run required configs over seeds {seeds}.\n"
        "6. Write all required outputs and stop after result_pack.json.\n\n"
        "## Metrics\n"
        f"{metrics_block}\n\n"
        "## Required baselines\n"
        f"{baselines_block}\n\n"
        "## Required outputs\n"
        f"{required_outputs}\n"
    )
    readme = (
        f"# External Executor Workspace - {project_id}\n\n"
        + common_header
        + "ResearchOS writes experiment contracts here. External executors write auditable result artifacts here.\n\n"
        "Key files: handoff_pack.json, expected_outputs_schema.json, allowed_paths.txt, AGENTS.md, CLAUDE.md, result_pack.json.\n"
    )
    dir_guide = (
        "# Workspace Directory Guide\n\n"
        "| 项目 | 说明 |\n"
        "|---|---|\n"
        "| 目录用途 | ResearchOS 与 Codex/Claude/manual 外部实验执行器的边界目录。 |\n"
        "| 生成阶段/来源 | T5-HANDOFF, T5-EXECUTOR-GATE, external executor, T5-DRY-RUN. |\n"
        "| 下游使用方 | T5-EXTERNAL-WAIT, T7-INGEST, T7-AUDIT, T7-POST-NOVELTY, T7-CLAIMS, T8-RESOURCE. |\n"
        "| 人工可编辑范围 | Manual executor outputs only. |\n"
        "| Agent 可写范围 | External executor may write only paths allowed by allowed_paths.txt. |\n"
        "| 不应放入 | Paper text, unsupported claims, API keys, or unrelated notebooks. |\n"
        "| 校验/恢复规则 | Every metric must have source artifact, config/log linkage, run id, and hash provenance. |\n\n"
        "## Key Files\n\n"
        "| 文件/子目录 | 内容与用途 |\n"
        "|---|---|\n"
        "| `AGENTS.md` | Codex/agent 外部执行约束。 |\n"
        "| `CLAUDE.md` | Claude Code 外部执行约束。 |\n"
        "| `handoff_pack.json` | T5 编译的实验任务、协议、证据契约和 allowed paths。 |\n"
        "| `expected_outputs_schema.json` | 外部执行器必须写回的 result pack/status/manifest schema。 |\n"
        "| `allowed_paths.txt` | 外部执行器可读写路径边界。 |\n"
        "| `result_pack.json` | 外部执行器写回的核心结果包，T7 只从这里摄取实验结果。 |\n"
        "| `executor_status.json` | 外部执行器状态、accepted/mock/dry-run 标记。 |\n"
        "| `run_manifest.json` | 运行记录、raw/config/log 路径和 provenance。 |\n\n"
        "Generated by ResearchOS workspace initialization.\n"
    )
    _write_text(policy.resolve_write("external_executor/AGENTS.md"), agents)
    _write_text(policy.resolve_write("external_executor/CLAUDE.md"), claude)
    _write_text(policy.resolve_write("external_executor/README.md"), readme)
    _write_text(policy.resolve_write("external_executor/_DIR_GUIDE.md"), dir_guide)
    _write_json(
        policy.resolve_write("external_executor/job_state.json"),
        {
            "version": "1.0",
            "semantics": "external_executor_job_state",
            "job_id": f"external_{project_id}",
            "executor_type": selection.get("selected_executor", "UNSET"),
            "current_state": "CREATED",
            "allowed_states": [
                "CREATED",
                "CLAIMED_BY_EXECUTOR",
                "ENV_PREPARED",
                "BASELINE_REPO_CLONED",
                "DATASET_PREPARED",
                "BASELINE_SMOKE_PASSED",
                "METHOD_IMPLEMENTED",
                "EXPERIMENT_RUNNING",
                "PARTIAL_RESULTS_READY",
                "FAILED_RECOVERABLE",
                "FAILED_FINAL",
                "COMPLETED",
                "INGESTED",
                "AUDITED",
            ],
            "last_heartbeat_at": None,
            "dry_run": None,
            "mock_only": None,
            "result_pack_path": "external_executor/result_pack.json",
        },
    )
    events = policy.resolve_write("external_executor/executor_events.jsonl")
    if not events.exists():
        events.write_text(
            json.dumps({"time": _now_iso(), "state": "CREATED", "message": "Handoff files generated."}, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )


def _format_required_baselines_block(baselines: list[Any]) -> str:
    if not baselines:
        return "No mandatory baselines were extracted. Executor should still report any baseline limitations."
    lines = []
    for item in baselines:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- {item.get('baseline_name') or item.get('name')}: "
            f"{item.get('reason_required') or 'required by novelty audit'}"
        )
    return "\n".join(lines) or "No mandatory baselines were extracted."


def _baseline_coverage_from_metrics(
    required_baselines: list[dict[str, Any]],
    metrics: list[Any],
    *,
    mock_only: bool,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if existing and existing.get("status") in {"complete", "incomplete", "missing", "no_required_baselines", "mock_only"}:
        return existing
    required_names = [
        str(item.get("baseline_name") or item.get("name") or item.get("baseline_id") or "").strip()
        for item in required_baselines
        if isinstance(item, dict)
    ]
    required_names = [name for name in required_names if name]
    if not required_names:
        return {
            "status": "no_required_baselines",
            "required": [],
            "completed": [],
            "missing_baselines": [],
            "substituted_baselines": [],
            "claim_blocks": [],
        }
    metric_text = "\n".join(json.dumps(metric, ensure_ascii=False).lower() for metric in metrics if isinstance(metric, dict))
    completed = [name for name in required_names if name.lower() in metric_text]
    missing = [name for name in required_names if name not in completed]
    status = "mock_only" if mock_only else ("complete" if not missing else "incomplete")
    return {
        "status": status,
        "required": required_names,
        "completed": completed,
        "missing_baselines": missing,
        "substituted_baselines": [],
        "claim_blocks": ["outperforms prior work", "state-of-the-art", "strong empirical advantage"] if missing else [],
    }


def _format_fairness_review(audit: dict[str, Any]) -> str:
    coverage = audit.get("required_baseline_coverage") or {}
    return (
        "# Experiment Fairness Review\n\n"
        "This is a deterministic scaffold. LLM/human reviewers should inspect fairness before strong claims.\n\n"
        f"- integrity_status: {audit.get('status')}\n"
        f"- evidence_grade: {audit.get('evidence_grade')}\n"
        f"- baseline_coverage_status: {coverage.get('status')}\n"
        f"- missing_baselines: {', '.join(coverage.get('missing_baselines', []) or []) or 'none'}\n"
    )


def _forbidden_wording(summary: dict[str, Any], baseline_missing: bool) -> list[str]:
    forbidden = ["proves"]
    if summary.get("mock_only"):
        forbidden.extend(["validated", "empirically validates", "state-of-the-art", "outperforms"])
    if baseline_missing:
        forbidden.extend(["outperforms prior work", "state-of-the-art", "strong empirical advantage"])
    return list(dict.fromkeys(forbidden))


def _claim_limitations(summary: dict[str, Any], baseline_missing: bool, coverage: dict[str, Any]) -> list[str]:
    limitations = []
    if summary.get("mock_only"):
        limitations.append("mock_only")
    if baseline_missing:
        limitations.append("required_baselines_missing:" + ",".join(coverage.get("missing_baselines", []) or []))
    return limitations


def _global_must_not_claim(summary: dict[str, Any], baseline_missing: bool, coverage: dict[str, Any]) -> list[str]:
    claims = []
    if summary.get("mock_only"):
        claims.append("Do not present dry-run/mock metrics as empirical evidence.")
    if baseline_missing:
        claims.append("Do not claim superiority over prior work until required baselines are covered.")
        for block in coverage.get("claim_blocks", []) or []:
            claims.append(f"Do not claim: {block}")
    return claims


def _format_must_not_claim(result: dict[str, Any]) -> str:
    lines = ["# Must Not Claim", ""]
    items = result.get("global_must_not_claim") or []
    if not items:
        lines.append("- No global claim block generated by deterministic audit.")
    else:
        lines.extend(f"- {item}" for item in items)
    return "\n".join(lines) + "\n"


def _format_claim_support_matrix(result: dict[str, Any]) -> str:
    rows = [["claim_id", "support_status", "claim_strength", "metric_refs", "evidence_refs", "limitations"]]
    for item in result.get("claim_mappings", []) or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            [
                str(item.get("claim_id") or ""),
                str(item.get("support_status") or ""),
                str(item.get("claim_strength") or ""),
                ";".join(str(x) for x in item.get("metric_refs", []) or []),
                ";".join(str(x) for x in item.get("evidence_refs", []) or []),
                ";".join(str(x) for x in item.get("limitations", []) or []),
            ]
        )
    return "\n".join(",".join(_csv_cell(cell) for cell in row) for row in rows) + "\n"


def _csv_cell(value: str) -> str:
    if any(ch in value for ch in [",", "\n", '"']):
        return '"' + value.replace('"', '""') + '"'
    return value


def _format_limitations_from_experiments(result: dict[str, Any]) -> str:
    lines = ["# Limitations From Experiments", ""]
    seen: set[str] = set()
    for item in result.get("claim_mappings", []) or []:
        if isinstance(item, dict):
            for limitation in item.get("limitations", []) or []:
                seen.add(str(limitation))
    if not seen:
        lines.append("- No deterministic limitation generated; still require reviewer inspection.")
    else:
        lines.extend(f"- {item}" for item in sorted(seen))
    return "\n".join(lines) + "\n"


class BuildExperimentHandoffPackParams(BaseModel):
    executor: Literal["UNSET", "mock_dry_run", "codex_cli", "claude_code_window", "manual"] = Field(
        default="UNSET",
        description="Initial executor mode. T5-EXECUTOR-GATE patches the real selection later.",
    )
    output_path: str = Field(default="external_executor/handoff_pack.json")
    prompt_output_path: str = Field(default="external_executor/executor_prompt.md")
    expected_schema_path: str = Field(default="external_executor/expected_outputs_schema.json")
    allowed_paths_path: str = Field(default="external_executor/allowed_paths.txt")
    executor_selection_path: str = Field(default="external_executor/executor_selection.json")
    input_manifest_path: str = Field(default="external_executor/input_manifest.json")
    codex_prompt_path: str = Field(default="external_executor/codex_prompt.md")
    claude_prompt_path: str = Field(default="external_executor/claude_code_prompt.md")
    manual_instructions_path: str = Field(default="external_executor/manual_instructions.md")


class SelectExternalExecutorParams(BaseModel):
    selected_executor: Literal["mock_dry_run", "codex_cli", "claude_code_window", "manual"] = Field(
        default="mock_dry_run",
        description="Executor selected by the T5-EXECUTOR-GATE human decision.",
    )
    executor_selection_path: str = Field(default="external_executor/executor_selection.json")
    selected_by: str = Field(default="human")
    notes: str = Field(default="")


class WaitForExternalExecutorResultParams(BaseModel):
    result_pack_path: str = Field(default="external_executor/result_pack.json")
    status_path: str = Field(default="external_executor/executor_status.json")
    output_path: str = Field(default="external_executor/wait_acceptance_report.json")


class BuildPostExperimentNoveltyCheckParams(BaseModel):
    results_summary_path: str = Field(default="experiments/results_summary.json")
    integrity_audit_path: str = Field(default="experiments/integrity_audit.json")
    novelty_audit_path: str = Field(default="ideation/novelty_audit.md")
    required_baselines_path: str = Field(default="novelty/required_baselines.json")
    output_path: str = Field(default="novelty/post_experiment_novelty_check.json")
    collision_output_path: str = Field(default="novelty/post_experiment_collision_cases.md")


class MockExternalDryRunParams(BaseModel):
    handoff_pack_path: str = Field(default="external_executor/handoff_pack.json")
    output_path: str = Field(default="external_executor/result_pack.json")
    status_path: str = Field(default="external_executor/executor_status.json")


class IngestExternalResultsParams(BaseModel):
    result_pack_path: str = Field(default="external_executor/result_pack.json")
    status_path: str = Field(default="external_executor/executor_status.json")
    results_summary_path: str = Field(default="experiments/results_summary.json")
    run_records_path: str = Field(default="experiments/run_records.jsonl")
    evidence_index_path: str = Field(default="experiments/evidence_index.json")
    ingest_report_path: str = Field(default="experiments/ingest_report.json")


class AuditExperimentIntegrityParams(BaseModel):
    results_summary_path: str = Field(default="experiments/results_summary.json")
    evidence_index_path: str = Field(default="experiments/evidence_index.json")
    output_path: str = Field(default="experiments/integrity_audit.json")


class MapResultsToClaimsParams(BaseModel):
    results_summary_path: str = Field(default="experiments/results_summary.json")
    integrity_audit_path: str = Field(default="experiments/integrity_audit.json")
    output_path: str = Field(default="experiments/experimental_claims.json")
    draft_output_path: str = Field(default="drafts/result_to_claim.json")
    must_not_claim_path: str = Field(default="drafts/must_not_claim.md")
    claim_support_matrix_path: str = Field(default="drafts/claim_support_matrix.csv")
    limitations_path: str = Field(default="drafts/limitations_from_experiments.md")
    figure_table_evidence_map_path: str = Field(default="drafts/figure_table_evidence_map.json")


class BuildExperimentEvidencePackParams(BaseModel):
    results_summary_path: str = Field(default="experiments/results_summary.json")
    integrity_audit_path: str = Field(default="experiments/integrity_audit.json")
    experimental_claims_path: str = Field(default="experiments/experimental_claims.json")
    evidence_index_path: str = Field(default="experiments/evidence_index.json")
    output_path: str = Field(default="drafts/experiment_evidence_pack.json")


class AuditPaperClaimsParams(BaseModel):
    paper_path: str = Field(default="drafts/paper.tex")
    evidence_pack_path: str = Field(default="drafts/experiment_evidence_pack.json")
    result_to_claim_path: str = Field(default="drafts/result_to_claim.json")
    output_path: str = Field(default="drafts/paper_claim_audit.md")


class BuildExperimentHandoffPackTool(Tool):
    name = "build_experiment_handoff_pack"
    description = "Compile a protocol pack and executor prompt for external experiment execution."
    parameters_schema = BuildExperimentHandoffPackParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BuildExperimentHandoffPackParams(**kwargs)
        try:
            ws = self.policy.workspace_dir
            project = _read_yaml(ws / "project.yaml")
            exp_plan = _read_yaml(ws / "ideation" / "exp_plan.yaml")
            hypotheses = (ws / "ideation" / "hypotheses.md").read_text(encoding="utf-8", errors="replace") if (ws / "ideation" / "hypotheses.md").exists() else ""
            metrics = _extract_exp_plan_metrics(exp_plan)
            seeds = _extract_exp_plan_seeds(project)
            required_baselines = _extract_required_baselines(ws)
            if required_baselines:
                _write_json(
                    self.policy.resolve_write("novelty/required_baselines.json"),
                    {
                        "version": "1.0",
                        "semantics": "required_baselines_from_novelty_audit",
                        "source": "ideation/novelty_audit.md",
                        "required_baselines": required_baselines,
                    },
                )
            source_artifacts = [
                _artifact_record(ws, "project.yaml", role="project"),
                _artifact_record(ws, "ideation/hypotheses.md", role="hypotheses"),
                _artifact_record(ws, "ideation/exp_plan.yaml", role="experiment_plan"),
                _artifact_record(ws, "ideation/novelty_audit.md", role="novelty_audit"),
                _artifact_record(ws, "novelty/required_baselines.json", role="required_baselines"),
                _artifact_record(ws, "resources/baseline_candidates.jsonl", role="baseline_candidates"),
                _artifact_record(ws, "literature/baseline_map.json", role="baseline_map"),
                _artifact_record(ws, "literature/synthesis.md", role="literature_synthesis"),
                _artifact_record(ws, "literature/comparison_table.csv", role="comparison_table"),
            ]
            handoff = {
                "version": "1.0",
                "schema_version": "v3.1",
                "semantics": "external_experiment_handoff_contract",
                "legacy_semantics": "external_experiment_handoff_pack_not_execution_result",
                "created_at": _now_iso(),
                "executor": params.executor,
                "execution_mode": "unselected" if params.executor == "UNSET" else ("dry_run" if params.executor == "mock_dry_run" else "external"),
                "accepted": False,
                "status": "handoff_compiled",
                "project": {
                    "project_id": project.get("project_id") or project.get("name") or "unknown",
                    "target_venue": project.get("target_venue", ""),
                },
                "project_id": project.get("project_id") or project.get("name") or "unknown",
                "experiment_intent_oneliner": _infer_experiment_intent(project, hypotheses),
                "executor_special_notes": "Use structure and provenance from this contract; do not write paper claims.",
                "experiment_contract": {
                    "metrics": metrics,
                    "seeds": seeds,
                    "experiments": exp_plan.get("experiments", []) if isinstance(exp_plan, dict) else [],
                    "required_baselines": required_baselines,
                    "acceptance": {
                        "must_write_result_pack": True,
                        "dry_run_results_must_be_marked_mock_only": True,
                    },
                },
                "metrics": [
                    {
                        "metric_id": f"metric_{idx}",
                        "name": name,
                        "direction": "unknown_requires_executor_or_llm_annotation",
                        "primary": idx == 1,
                    }
                    for idx, name in enumerate(metrics, start=1)
                ],
                "required_baselines": required_baselines,
                "seeds": seeds,
                "source_artifacts": source_artifacts,
                "executor_outputs": {
                    "result_pack": "external_executor/result_pack.json",
                    "status": "external_executor/executor_status.json",
                    "run_manifest": "external_executor/run_manifest.json",
                    "raw_results": "external_executor/raw_results/",
                    "configs": "external_executor/configs/",
                    "logs_dir": "external_executor/logs",
                },
                "allowed_paths": [
                    "rw  external_executor/workdir/",
                    "rw  external_executor/raw_results/",
                    "rw  external_executor/configs/",
                    "rw  external_executor/logs/",
                    "rw  external_executor/patches/",
                    "rw  external_executor/result_pack.json",
                    "rw  external_executor/executor_status.json",
                    "rw  external_executor/run_manifest.json",
                    "rw  external_executor/job_state.json",
                    "ro  external_executor/handoff_pack.json",
                    "ro  external_executor/expected_outputs_schema.json",
                    "ro  novelty/required_baselines.json",
                    "ro  resources/",
                    "ro  literature/",
                    "ro  ideation/",
                    "no  researchos/",
                    "no  config/",
                    "no  drafts/",
                    "no  submission/",
                    "no  _runtime/",
                ],
                "executor_outputs_contract": {
                    "must_write": [
                        "external_executor/result_pack.json",
                        "external_executor/executor_status.json",
                        "external_executor/run_manifest.json",
                        "external_executor/raw_results/",
                        "external_executor/configs/",
                        "external_executor/logs/",
                    ],
                    "result_pack_semantics": "external_executor_result_pack",
                },
                "hypotheses_preview": hypotheses[:1200],
            }
            output_path = self.policy.resolve_write(params.output_path)
            _write_json(output_path, handoff)
            input_manifest = {
                "version": "1.0",
                "semantics": "external_executor_input_manifest",
                "handoff_pack": params.output_path,
                "source_artifacts": source_artifacts,
                "required_executor_outputs": handoff["executor_outputs"],
            }
            _write_json(self.policy.resolve_write(params.input_manifest_path), input_manifest)
            executor_selection = {
                "version": "1.0",
                "semantics": "external_executor_selection",
                "selected_executor": params.executor,
                "real_experiment_allowed": False,
                "requires_user_copy_paste": False,
                "selected_by": "system_placeholder",
                "selected_at": None,
                "next_state": "T5-EXECUTOR-GATE",
                "fallback_order": ["mock_dry_run", "claude_code_window", "manual"],
                "notes": "Execution mode is intentionally UNSET until T5-EXECUTOR-GATE.",
            }
            _write_json(self.policy.resolve_write(params.executor_selection_path), executor_selection)
            schema = {
                "version": "1.0",
                "semantics": "expected_external_executor_outputs_schema",
                "required": ["semantics", "run_id", "executor", "dry_run", "metrics", "artifacts", "baseline_coverage"],
                "required_files": [
                    "external_executor/result_pack.json",
                    "external_executor/executor_status.json",
                    "external_executor/run_manifest.json",
                    "external_executor/raw_results/",
                    "external_executor/configs/",
                    "external_executor/logs/",
                ],
                "metric_required": ["metric_id", "name", "value", "source_artifact", "dataset", "seed"],
                "artifact_required": ["path", "kind", "role", "sha256"],
                "status_required": ["semantics", "run_id", "status", "accepted", "dry_run"],
                "run_manifest_required": ["semantics", "run_id", "executor", "raw_results", "configs", "logs"],
            }
            _write_json(self.policy.resolve_write(params.expected_schema_path), schema)
            _write_text(
                self.policy.resolve_write(params.allowed_paths_path),
                "\n".join(handoff["allowed_paths"]) + "\n",
            )
            _write_external_executor_guides(self.policy, handoff, selection=executor_selection)
            prompt = _render_executor_prompt(handoff, executor=params.executor)
            _write_text(self.policy.resolve_write(params.prompt_output_path), prompt)
            _write_text(self.policy.resolve_write(params.codex_prompt_path), _render_executor_prompt(handoff, executor="codex_cli"))
            _write_text(self.policy.resolve_write(params.claude_prompt_path), _render_executor_prompt(handoff, executor="claude_code_window"))
            _write_text(self.policy.resolve_write(params.manual_instructions_path), _render_manual_instructions(handoff))
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"handoff pack failed: {exc}", error="handoff_failed")
        return ToolResult(
            ok=True,
            content=f"Wrote external experiment handoff pack to {params.output_path}.",
            data={"path": params.output_path, "executor": params.executor},
        )


class SelectExternalExecutorTool(Tool):
    name = "select_external_executor"
    description = "Persist the T5 executor gate selection and patch external executor instructions."
    parameters_schema = SelectExternalExecutorParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = SelectExternalExecutorParams(**kwargs)
        try:
            selection = build_executor_selection_payload(
                selected_executor=params.selected_executor,
                selected_by=params.selected_by,
                notes=params.notes,
            )
            _write_json(self.policy.resolve_write(params.executor_selection_path), selection)
            patch_external_executor_files_with_selection(self.policy.workspace_dir, selection)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"executor selection failed: {exc}", error="executor_selection_failed")
        return ToolResult(ok=True, content=f"Selected executor {params.selected_executor}.", data=selection)


class WaitForExternalExecutorResultTool(Tool):
    name = "wait_for_external_executor_result"
    description = "Validate that an external executor result pack exists before T7 ingest."
    parameters_schema = WaitForExternalExecutorResultParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = WaitForExternalExecutorResultParams(**kwargs)
        try:
            report = validate_external_executor_ready(self.policy.workspace_dir, params.result_pack_path, params.status_path)
            if not report["ok"]:
                return ToolResult(ok=False, content=report["message"], error="external_executor_not_ready", data=report)
            _write_json(self.policy.resolve_write(params.output_path), report)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"external wait failed: {exc}", error="external_wait_failed")
        return ToolResult(ok=True, content=report["message"], data=report)


class BuildPostExperimentNoveltyCheckTool(Tool):
    name = "build_post_experiment_novelty_check"
    description = "Build a conservative post-experiment novelty/collision check artifact for T7-POST-NOVELTY."
    parameters_schema = BuildPostExperimentNoveltyCheckParams
    timeout_seconds = 10.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BuildPostExperimentNoveltyCheckParams(**kwargs)
        try:
            summary = _read_json(self.policy.resolve_read(params.results_summary_path))
            audit = _read_json(self.policy.resolve_read(params.integrity_audit_path))
            required = _read_json(self.policy.workspace_dir / params.required_baselines_path)
            baseline_status = ((audit.get("required_baseline_coverage") or {}).get("status") or "unknown")
            claim_downgrades: list[str] = []
            if summary.get("mock_only"):
                claim_downgrades.append("mock_only_results_cannot_support_empirical_novelty")
            if audit.get("status") == "fail":
                claim_downgrades.append("integrity_audit_failed")
            if baseline_status in {"missing", "incomplete"}:
                claim_downgrades.append("required_baselines_missing_or_incomplete")
            novelty_after = "weak" if claim_downgrades else ("moderate" if audit.get("status") == "pass" else "collision_risk")
            check = {
                "version": "1.0",
                "semantics": "post_experiment_novelty_check",
                "implementation_matches_original_idea": "partial" if summary.get("mock_only") else "unknown_requires_llm_review",
                "novelty_after_implementation": novelty_after,
                "collision_risks": [],
                "claim_downgrades_required": claim_downgrades,
                "additional_baselines_required": (audit.get("required_baseline_coverage") or {}).get("missing_baselines", []),
                "required_baselines": required.get("required_baselines", []),
                "recommended_next_task": "T7-CLAIMS",
                "notes": (
                    "This tool performs deterministic evidence-status checks only. "
                    "LLM novelty interpretation should read this artifact before T7.5/T8."
                ),
            }
            _write_json(self.policy.resolve_write(params.output_path), check)
            collision_lines = [
                "# Post-Experiment Collision Cases",
                "",
                "No deterministic collision case was proven by this tool.",
                "Review implementation diffs, baseline reuse, and novelty_audit.md before strong claims.",
                "",
                "## Claim Downgrades",
            ]
            collision_lines.extend(f"- {item}" for item in claim_downgrades)
            _write_text(self.policy.resolve_write(params.collision_output_path), "\n".join(collision_lines) + "\n")
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"post novelty check failed: {exc}", error="post_novelty_failed")
        return ToolResult(ok=True, content=f"Wrote post-experiment novelty check to {params.output_path}.", data=check)


class MockExternalDryRunTool(Tool):
    name = "mock_external_dry_run"
    description = "Generate a schema-compatible mock external result pack without running real experiments."
    parameters_schema = MockExternalDryRunParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = MockExternalDryRunParams(**kwargs)
        try:
            ws = self.policy.workspace_dir
            handoff_path = self.policy.resolve_read(params.handoff_pack_path)
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            selection = _read_json(ws / "external_executor" / "executor_selection.json")
            executor_type = str(selection.get("selected_executor") or handoff.get("executor") or "mock_dry_run")
            if executor_type == "UNSET":
                executor_type = "mock_dry_run"
            raw_result_rel = "external_executor/raw_results/mock_results.json"
            config_rel = "external_executor/configs/mock_config.json"
            log_rel = "external_executor/logs/mock_dry_run.log"
            manifest_rel = "external_executor/run_manifest.json"
            heartbeat_rel = "external_executor/heartbeat.json"
            raw_result = {
                "version": "1.0",
                "semantics": "mock_raw_result_file_not_scientific_evidence",
                "run_id": "mock_dry_run",
                "metrics": [],
            }
            metrics = []
            for idx, name in enumerate((handoff.get("experiment_contract") or {}).get("metrics", []) or ["task_score"], start=1):
                metric = (
                    {
                        "metric_id": f"mock_metric_{idx}",
                        "experiment_id": "mock_dry_run",
                        "name": str(name),
                        "value": round(0.7 + idx * 0.01, 4),
                        "unit": "score",
                        "dataset": "mock_dataset",
                        "seed": ((handoff.get("experiment_contract") or {}).get("seeds") or [42])[0],
                        "source_artifact": raw_result_rel,
                        "mock_only": True,
                    }
                )
                metrics.append(metric)
                raw_result["metrics"].append(metric)
            _write_json(self.policy.resolve_write(raw_result_rel), raw_result)
            config = {
                "version": "1.0",
                "semantics": "mock_external_executor_config",
                "run_id": "mock_dry_run",
                "executor": executor_type,
                "seeds": (handoff.get("experiment_contract") or {}).get("seeds") or [42],
                "mock_only": True,
            }
            _write_json(self.policy.resolve_write(config_rel), config)
            log_path = self.policy.resolve_write(log_rel)
            _write_text(log_path, "mock dry-run completed; no real experiment executed\n")
            heartbeat = {
                "version": "1.0",
                "semantics": "external_executor_heartbeat",
                "run_id": "mock_dry_run",
                "status": "done",
                "dry_run": True,
                "mock_only": True,
            }
            _write_json(self.policy.resolve_write(heartbeat_rel), heartbeat)
            artifacts = [
                _artifact_record(ws, raw_result_rel, role="mock_raw_results", kind="raw_results"),
                _artifact_record(ws, config_rel, role="mock_config", kind="config"),
                _artifact_record(ws, log_rel, role="mock_log", kind="log"),
            ]
            required_baselines = (handoff.get("experiment_contract") or {}).get("required_baselines", []) or handoff.get("required_baselines", []) or []
            baseline_coverage = _baseline_coverage_from_metrics(required_baselines, metrics, mock_only=True)
            run_manifest = {
                "version": "1.0",
                "semantics": "external_executor_run_manifest",
                "run_id": "mock_dry_run",
                "executor": executor_type,
                "dry_run": True,
                "mock_only": True,
                "raw_results": [raw_result_rel],
                "configs": [config_rel],
                "logs": [log_rel],
                "artifacts": artifacts,
            }
            _write_json(self.policy.resolve_write(manifest_rel), run_manifest)
            result_pack = {
                "version": "1.0",
                "semantics": "external_executor_result_pack",
                "run_id": "mock_dry_run",
                "executor": executor_type,
                "dry_run": True,
                "mock_only": True,
                "evidence_grade": "mock_only",
                "metrics": metrics,
                "artifacts": artifacts,
                "baseline_coverage": baseline_coverage,
                "run_manifest": manifest_rel,
                "logs": [{"path": log_rel, "level": "info"}],
                "limitations": ["mock_dry_run: not evidence for paper claims"],
            }
            _write_json(self.policy.resolve_write(params.output_path), result_pack)
            status = {
                "version": "1.0",
                "semantics": "external_executor_status",
                "run_id": "mock_dry_run",
                "status": "done",
                "accepted": False,
                "dry_run": True,
                "mock_only": True,
                "heartbeat": heartbeat_rel,
                "run_manifest": manifest_rel,
            }
            _write_json(self.policy.resolve_write(params.status_path), status)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"mock dry-run failed: {exc}", error="mock_dry_run_failed")
        return ToolResult(ok=True, content=f"Wrote mock result pack to {params.output_path}.", data={"path": params.output_path})


class IngestExternalResultsTool(Tool):
    name = "ingest_external_results"
    description = "Normalize an external executor result pack into ResearchOS result artifacts."
    parameters_schema = IngestExternalResultsParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = IngestExternalResultsParams(**kwargs)
        try:
            readiness = validate_external_executor_ready(
                self.policy.workspace_dir,
                params.result_pack_path,
                params.status_path,
            )
            if not readiness.get("ok"):
                return ToolResult(
                    ok=False,
                    content=str(readiness.get("message") or "external executor result is not ready"),
                    error="external_result_not_ready",
                    data=readiness,
                )
            result_pack_path = self.policy.resolve_read(params.result_pack_path)
            result_pack = json.loads(result_pack_path.read_text(encoding="utf-8"))
            if result_pack.get("semantics") != "external_executor_result_pack":
                return ToolResult(ok=False, content="result_pack semantics invalid", error="invalid_result_pack")
            metrics = result_pack.get("metrics")
            if not isinstance(metrics, list) or not metrics:
                return ToolResult(ok=False, content="result_pack metrics missing", error="missing_metrics")
            experiments = [
                {
                    "experiment_id": str(metric.get("experiment_id") or result_pack.get("run_id") or "external_run"),
                    "metrics": {str(metric.get("name")): metric.get("value")},
                    "seed": metric.get("seed"),
                    "source_artifact": metric.get("source_artifact"),
                    "mock_only": bool(metric.get("mock_only") or result_pack.get("mock_only")),
                    "baseline_id": metric.get("baseline_id"),
                    "method_role": metric.get("method_role"),
                }
                for metric in metrics
                if isinstance(metric, dict) and metric.get("name") and metric.get("value") is not None
            ]
            summary = {
                "version": "1.0",
                "semantics": "external_executor_results_summary",
                "source": "external_executor",
                "run_id": result_pack.get("run_id"),
                "dry_run": bool(result_pack.get("dry_run")),
                "mock_only": bool(result_pack.get("mock_only")),
                "evidence_grade": str(result_pack.get("evidence_grade") or ("mock_only" if result_pack.get("mock_only") else "external_unverified")),
                "ingest_report_ref": params.ingest_report_path,
                "experiments": experiments,
                "metrics": metrics,
                "baseline_coverage": result_pack.get("baseline_coverage") or {},
                "quality_status": "mock_only" if result_pack.get("mock_only") else "ingested_unverified",
            }
            _write_json(self.policy.resolve_write(params.results_summary_path), summary)
            run_records = self.policy.resolve_write(params.run_records_path)
            run_records.parent.mkdir(parents=True, exist_ok=True)
            run_records.write_text(json.dumps(result_pack, ensure_ascii=False) + "\n", encoding="utf-8")
            evidence_index = {
                "version": "1.0",
                "semantics": "external_experiment_evidence_index",
                "result_pack": params.result_pack_path,
                "run_manifest": result_pack.get("run_manifest"),
                "metrics": metrics,
                "baseline_coverage": result_pack.get("baseline_coverage") or {},
                "artifacts": result_pack.get("artifacts", []),
                "logs": result_pack.get("logs", []),
            }
            _write_json(self.policy.resolve_write(params.evidence_index_path), evidence_index)
            report = {
                "version": "1.0",
                "semantics": "external_result_ingest_report",
                "ok": True,
                "dry_run": bool(result_pack.get("dry_run")),
                "mock_only": bool(result_pack.get("mock_only")),
                "evidence_grade": summary["evidence_grade"],
                "metric_count": len(metrics),
                "results_summary": params.results_summary_path,
            }
            _write_json(self.policy.resolve_write(params.ingest_report_path), report)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"ingest failed: {exc}", error="ingest_failed")
        return ToolResult(ok=True, content=f"Ingested result pack to {params.results_summary_path}.", data=report)


class AuditExperimentIntegrityTool(Tool):
    name = "audit_experiment_integrity"
    description = "Audit ingested external results for provenance, dry-run status, seed and metric issues."
    parameters_schema = AuditExperimentIntegrityParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = AuditExperimentIntegrityParams(**kwargs)
        try:
            summary = _read_json(self.policy.resolve_read(params.results_summary_path))
            evidence = _read_json(self.policy.resolve_read(params.evidence_index_path))
            issues: list[dict[str, Any]] = []
            if summary.get("mock_only"):
                issues.append({"level": "WARN", "code": "mock_only", "detail": "Dry-run result is not publishable evidence."})
            if not summary.get("metrics"):
                issues.append({"level": "FAIL", "code": "missing_metrics", "detail": "No metrics in results summary."})
            evidence_artifacts = [
                item for item in (evidence.get("artifacts", []) or []) if isinstance(item, dict)
            ]
            artifact_by_path = {str(item.get("path")): item for item in evidence_artifacts if item.get("path")}
            seen_metric_ids: set[str] = set()
            for metric in summary.get("metrics", []) or []:
                if not isinstance(metric, dict):
                    continue
                metric_id = str(metric.get("metric_id") or "")
                if metric_id in seen_metric_ids:
                    issues.append({"level": "WARN", "code": "duplicate_metric_id", "detail": metric_id})
                seen_metric_ids.add(metric_id)
                value = metric.get("value")
                if not isinstance(value, (int, float)):
                    issues.append({"level": "FAIL", "code": "non_numeric_metric", "detail": metric_id})
                source_artifact = str(metric.get("source_artifact") or "")
                if not source_artifact:
                    issues.append({"level": "FAIL", "code": "missing_source_artifact", "detail": metric_id})
                elif source_artifact not in artifact_by_path:
                    issues.append({"level": "FAIL", "code": "metric_source_not_indexed", "detail": f"{metric_id}: {source_artifact}"})
            for artifact in evidence_artifacts:
                rel_path = str(artifact.get("path") or "")
                if not rel_path:
                    issues.append({"level": "FAIL", "code": "artifact_missing_path", "detail": str(artifact)})
                    continue
                path = self.policy.workspace_dir / rel_path
                if not path.exists():
                    issues.append({"level": "FAIL", "code": "artifact_missing_on_disk", "detail": rel_path})
                    continue
                expected_hash = artifact.get("sha256")
                if expected_hash and path.is_file() and expected_hash != _sha256(path):
                    issues.append({"level": "FAIL", "code": "artifact_hash_mismatch", "detail": rel_path})
            manifest_rel = evidence.get("run_manifest")
            if manifest_rel:
                manifest_path = self.policy.workspace_dir / str(manifest_rel)
                if not manifest_path.exists():
                    issues.append({"level": "FAIL", "code": "missing_run_manifest", "detail": str(manifest_rel)})
                else:
                    manifest = _read_json(manifest_path)
                    if manifest.get("semantics") != "external_executor_run_manifest":
                        issues.append({"level": "FAIL", "code": "run_manifest_semantics", "detail": str(manifest_rel)})
            required_baselines = _extract_required_baselines(self.policy.workspace_dir)
            baseline_coverage = _baseline_coverage_from_metrics(
                required_baselines,
                summary.get("metrics", []) or [],
                mock_only=bool(summary.get("mock_only")),
                existing=summary.get("baseline_coverage") if isinstance(summary.get("baseline_coverage"), dict) else None,
            )
            if baseline_coverage.get("status") in {"missing", "incomplete"}:
                issues.append(
                    {
                        "level": "FAIL" if not summary.get("mock_only") else "WARN",
                        "code": "required_baseline_coverage",
                        "detail": ", ".join(baseline_coverage.get("missing_baselines", []) or []),
                    }
                )
            audit = {
                "version": "1.0",
                "semantics": "external_experiment_integrity_audit",
                "status": "fail" if any(item["level"] == "FAIL" for item in issues) else ("mock_only" if summary.get("mock_only") else "pass"),
                "dry_run": bool(summary.get("dry_run")),
                "mock_only": bool(summary.get("mock_only")),
                "evidence_grade": str(summary.get("evidence_grade") or ("mock_only" if summary.get("mock_only") else "audited_external")),
                "issues": issues,
                "evidence_index": params.evidence_index_path,
                "artifact_count": len(evidence.get("artifacts", []) or []),
                "checked_artifacts": len(evidence_artifacts),
                "required_baseline_coverage": baseline_coverage,
            }
            _write_json(self.policy.resolve_write(params.output_path), audit)
            _write_text(
                self.policy.resolve_write("experiments/experiment_fairness_review.md"),
                _format_fairness_review(audit),
            )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"integrity audit failed: {exc}", error="integrity_audit_failed")
        return ToolResult(ok=True, content=f"Wrote experiment integrity audit to {params.output_path}.", data=audit)


class MapResultsToClaimsTool(Tool):
    name = "map_results_to_claims"
    description = "Map audited result metrics to conservative experimental claims for T8 writing."
    parameters_schema = MapResultsToClaimsParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = MapResultsToClaimsParams(**kwargs)
        try:
            summary = _read_json(self.policy.resolve_read(params.results_summary_path))
            audit = _read_json(self.policy.resolve_read(params.integrity_audit_path))
            mappings = []
            claims = []
            baseline_coverage = audit.get("required_baseline_coverage") if isinstance(audit.get("required_baseline_coverage"), dict) else {}
            baseline_missing = baseline_coverage.get("status") in {"missing", "incomplete"}
            for metric in summary.get("metrics", []) or []:
                if not isinstance(metric, dict):
                    continue
                status = "unsupported_mock_only" if summary.get("mock_only") else ("supported" if audit.get("status") == "pass" and not baseline_missing else "weak")
                claim_strength = "unsupported" if summary.get("mock_only") else ("strong" if status == "supported" else "weak")
                if baseline_missing and claim_strength == "strong":
                    claim_strength = "weak"
                claim_id = f"claim_{metric.get('metric_id') or len(mappings)+1}"
                mappings.append(
                    {
                        "claim_id": claim_id,
                        "support_status": status,
                        "claim_strength": claim_strength,
                        "metric_refs": [metric.get("metric_id")],
                        "evidence_refs": [metric.get("source_artifact")],
                        "allowed_wording": (
                            "Dry-run only; do not use as a paper result."
                            if summary.get("mock_only")
                            else f"Reports {metric.get('name')}={metric.get('value')} under audited external execution."
                        ),
                        "forbidden_wording": _forbidden_wording(summary, baseline_missing),
                        "limitations": _claim_limitations(summary, baseline_missing, baseline_coverage),
                    }
                )
                claims.append(
                    {
                        "claim_id": claim_id,
                        "claim_text_conservative": (
                            "Protocol-only dry-run result; not empirical evidence."
                            if summary.get("mock_only")
                            else f"Audited external result reports {metric.get('name')}={metric.get('value')}."
                        ),
                        "claim_strength": claim_strength,
                        "supported_by": [metric.get("metric_id"), metric.get("source_artifact")],
                        "blocked_by": baseline_coverage.get("claim_blocks", []) if baseline_missing else [],
                        "must_not_say": _forbidden_wording(summary, baseline_missing),
                        "paper_sections": ["experiments", "analysis"],
                    }
                )
            result = {
                "version": "1.0",
                "semantics": "mechanical_result_to_claim_map_not_final_scientific_judgment",
                "schema_semantics": "result_to_claim_mapping_not_paper_text",
                "source": "external_executor",
                "dry_run": bool(summary.get("dry_run")),
                "mock_only": bool(summary.get("mock_only")),
                "evidence_grade": str(summary.get("evidence_grade") or audit.get("evidence_grade") or ""),
                "integrity_audit": params.integrity_audit_path,
                "required_baseline_coverage": baseline_coverage,
                "claim_mappings": mappings,
                "claims": claims,
                "global_must_not_claim": _global_must_not_claim(summary, baseline_missing, baseline_coverage),
            }
            _write_json(self.policy.resolve_write(params.output_path), result)
            _write_json(self.policy.resolve_write(params.draft_output_path), result)
            _write_text(self.policy.resolve_write(params.must_not_claim_path), _format_must_not_claim(result))
            _write_text(self.policy.resolve_write(params.claim_support_matrix_path), _format_claim_support_matrix(result))
            _write_text(self.policy.resolve_write(params.limitations_path), _format_limitations_from_experiments(result))
            _write_json(
                self.policy.resolve_write(params.figure_table_evidence_map_path),
                {
                    "version": "1.0",
                    "semantics": "figure_table_evidence_map_from_result_to_claim",
                    "tables": [
                        {
                            "table_id": "tab:main_results",
                            "claim_ids": [claim.get("claim_id") for claim in claims],
                            "metric_refs": [metric.get("metric_id") for metric in summary.get("metrics", []) or [] if isinstance(metric, dict)],
                        }
                    ],
                    "figures": [],
                },
            )
            iteration_log = self.policy.workspace_dir / "experiments" / "iteration_log.md"
            iteration_log.parent.mkdir(parents=True, exist_ok=True)
            iteration_log.write_text(_format_iteration_log(summary, audit, result), encoding="utf-8")
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"result-to-claim failed: {exc}", error="result_to_claim_failed")
        return ToolResult(ok=True, content=f"Wrote result-to-claim map to {params.output_path}.", data={"claim_count": len(mappings)})


class BuildExperimentEvidencePackTool(Tool):
    name = "build_experiment_evidence_pack"
    description = "Build a normalized experiment evidence pack for manuscript writing."
    parameters_schema = BuildExperimentEvidencePackParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = BuildExperimentEvidencePackParams(**kwargs)
        try:
            summary = _read_json(self.policy.resolve_read(params.results_summary_path))
            audit = _read_json(self.policy.resolve_read(params.integrity_audit_path))
            claims = _read_json(self.policy.resolve_read(params.experimental_claims_path))
            evidence = _read_json(self.policy.resolve_read(params.evidence_index_path))
            pack = {
                "version": "1.0",
                "semantics": "normalized_experiment_evidence_pack",
                "source": "external_executor",
                "dry_run": bool(summary.get("dry_run")),
                "mock_only": bool(summary.get("mock_only")),
                "evidence_grade": str(summary.get("evidence_grade") or audit.get("evidence_grade") or ""),
                "source_packs": [{"path": params.results_summary_path}, {"path": params.integrity_audit_path}],
                "artifacts": evidence.get("artifacts", []),
                "metrics": summary.get("metrics", []),
                "claims": claims.get("claim_mappings", []),
                "required_baseline_coverage": audit.get("required_baseline_coverage") or {},
                "must_not_claim": claims.get("global_must_not_claim", []),
                "integrity": {
                    "status": audit.get("status"),
                    "issues": audit.get("issues", []),
                },
                "limitations": ["mock_only"] if summary.get("mock_only") else [],
            }
            _write_json(self.policy.resolve_write(params.output_path), pack)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"evidence pack failed: {exc}", error="evidence_pack_failed")
        return ToolResult(ok=True, content=f"Wrote experiment evidence pack to {params.output_path}.", data={"metric_count": len(pack.get("metrics", []))})


class AuditPaperClaimsTool(Tool):
    name = "audit_paper_claims"
    description = "Audit manuscript numeric claims against normalized experiment evidence pack."
    parameters_schema = AuditPaperClaimsParams
    timeout_seconds = 20.0

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = AuditPaperClaimsParams(**kwargs)
        try:
            paper = self.policy.resolve_read(params.paper_path).read_text(encoding="utf-8", errors="replace")
            pack = _read_json(self.policy.resolve_read(params.evidence_pack_path))
            result_to_claim = _read_json(self.policy.resolve_read(params.result_to_claim_path))
            known_values = {
                str(metric.get("value"))
                for metric in pack.get("metrics", []) or []
                if isinstance(metric, dict) and metric.get("value") is not None
            }
            issues = []
            for number in _extract_substantive_numbers(paper):
                if number not in known_values:
                    issues.append({"level": "WARN", "number": number, "issue": "number_not_in_evidence_pack"})
            if pack.get("mock_only"):
                issues.append({"level": "FAIL", "issue": "mock_only_evidence_pack", "detail": "Dry-run evidence cannot support paper claims."})
            forbidden_violations = _detect_forbidden_wording_violations(paper, result_to_claim)
            for violation in forbidden_violations:
                issues.append({"level": "FAIL", "issue": "forbidden_wording_violation", **violation})
            unsupported_strong_claims = _detect_unsupported_strong_claims(result_to_claim)
            for item in unsupported_strong_claims:
                issues.append({"level": "FAIL", "issue": "unsupported_strong_claim", **item})
            audit = {
                "version": "1.0",
                "semantics": "paper_claim_audit_against_experiment_evidence_pack",
                "summary": {
                    "fail_count": sum(1 for item in issues if item["level"] == "FAIL"),
                    "warn_count": sum(1 for item in issues if item["level"] == "WARN"),
                },
                "mock_only": bool(pack.get("mock_only")),
                "known_metric_values": sorted(known_values),
                "claim_mappings_count": len(result_to_claim.get("claim_mappings", []) or []),
                "unsupported_strong_claims": unsupported_strong_claims,
                "forbidden_wording_violations": forbidden_violations,
                "global_must_not_claim": result_to_claim.get("global_must_not_claim", []) or [],
                "issues": issues,
            }
            output_path = self.policy.resolve_write(params.output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(_format_paper_claim_audit_markdown(audit), encoding="utf-8")
            output_path.with_suffix(".json").write_text(
                json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")
        except Exception as exc:
            return ToolResult(ok=False, content=f"paper claim audit failed: {exc}", error="paper_claim_audit_failed")
        return ToolResult(ok=True, content=f"Wrote paper claim audit to {params.output_path}.", data=audit["summary"])


def _render_executor_prompt(handoff: dict[str, Any], *, executor: str) -> str:
    executor_label = {
        "codex_cli": "Codex CLI",
        "claude_code_window": "Claude Code",
        "manual": "Manual External Executor",
        "mock_dry_run": "Mock Dry-Run Executor",
        "UNSET": "Unselected External Executor",
    }.get(executor, executor)
    return (
        f"# External Experiment Executor Prompt ({executor_label})\n\n"
        "> EXECUTION MODE NOT YET SELECTED - see executor_selection.json after T5-EXECUTOR-GATE\n\n"
        "- dry_run: UNSET\n"
        "- mock_only: UNSET\n"
        "- real_experiment_allowed: UNSET\n\n"
        "You are an external executor for ResearchOS. Do not modify the ResearchOS main repo outside allowed paths.\n\n"
        "## Required Output\n"
        "- Write `external_executor/result_pack.json` matching `external_executor/expected_outputs_schema.json`.\n"
        "- Write `external_executor/run_manifest.json` with raw result/config/log artifact paths and hashes.\n"
        "- Write `external_executor/executor_status.json` with `status: done` and `accepted: false`.\n"
        "- Write raw logs under `external_executor/logs/`.\n"
        "- Mark dry-run outputs with `mock_only: true`.\n\n"
        "## Integrity Rules\n"
        "- Do not summarize results without raw files.\n"
        "- Do not write outside `external_executor/` or other allowed paths listed in the handoff pack.\n"
        "- Executor completion is only `done`; ResearchOS ingest/audit decides whether evidence is accepted.\n\n"
        "## Handoff Pack\n\n"
        "```json\n"
        + json.dumps(handoff, ensure_ascii=False, indent=2)
        + "\n```\n"
    )


def _render_manual_instructions(handoff: dict[str, Any]) -> str:
    return (
        "# Manual External Executor Instructions\n\n"
        "Use this file when a human or non-integrated external agent runs the experiment outside ResearchOS.\n\n"
        "1. Read `external_executor/handoff_pack.json` and `external_executor/expected_outputs_schema.json`.\n"
        "2. Implement/run only inside the allowed paths.\n"
        "3. Place raw metrics in `external_executor/raw_results/` and configs in `external_executor/configs/`.\n"
        "4. Write `external_executor/run_manifest.json`, `external_executor/result_pack.json`, and "
        "`external_executor/executor_status.json`.\n"
        "5. Keep `executor_status.accepted=false`; ResearchOS will set acceptance through ingest/audit.\n\n"
        "Allowed paths:\n\n"
        + "\n".join(f"- `{path}`" for path in handoff.get("allowed_paths", []))
        + "\n"
    )


def _format_iteration_log(summary: dict[str, Any], audit: dict[str, Any], claims: dict[str, Any]) -> str:
    lines = [
        "# External Experiment Iteration Log",
        "",
        f"- source: {summary.get('source')}",
        f"- run_id: {summary.get('run_id')}",
        f"- dry_run: {summary.get('dry_run')}",
        f"- mock_only: {summary.get('mock_only')}",
        f"- integrity_status: {audit.get('status')}",
        f"- claim_mappings: {len(claims.get('claim_mappings', []) or [])}",
        "",
        "Dry-run results are protocol tests only and must not be used as paper evidence.",
    ]
    return "\n".join(lines) + "\n"


def _extract_substantive_numbers(text: str) -> list[str]:
    import re

    numbers = re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", text)
    result: list[str] = []
    for num in numbers:
        try:
            value = float(num)
        except Exception:
            continue
        if value in {0.0, 1.0} or 1900 <= value <= 2100:
            continue
        if value.is_integer() and 1 <= value <= 20:
            continue
        result.append(num)
    return result


def _format_paper_claim_audit_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# Paper Claim Audit",
        "",
        f"- semantics: `{audit.get('semantics')}`",
        f"- fail_count: {audit.get('summary', {}).get('fail_count')}",
        f"- warn_count: {audit.get('summary', {}).get('warn_count')}",
        f"- mock_only: {audit.get('mock_only')}",
        "",
        "## Issues",
    ]
    for issue in audit.get("issues", []) or []:
        lines.append(f"- **{issue.get('level')}** {issue.get('issue')}: {issue.get('number', '')} {issue.get('detail', '')}".strip())
    return "\n".join(lines) + "\n"


def _detect_forbidden_wording_violations(paper: str, result_to_claim: dict[str, Any]) -> list[dict[str, str]]:
    paper_lc = paper.lower()
    forbidden: list[str] = []
    for item in result_to_claim.get("global_must_not_claim", []) or []:
        text = str(item or "").strip()
        match = re.search(r"Do not claim:\s*(.+)$", text, re.IGNORECASE)
        forbidden.append(match.group(1).strip() if match else text)
    for mapping in result_to_claim.get("claim_mappings", []) or []:
        if isinstance(mapping, dict):
            forbidden.extend(str(item) for item in mapping.get("forbidden_wording", []) or [])
    violations: list[dict[str, str]] = []
    for phrase in sorted({item.strip() for item in forbidden if item and len(item.strip()) >= 4}):
        phrase_lc = phrase.lower()
        if _forbidden_phrase_present(phrase_lc, paper_lc):
            violations.append({"phrase": phrase, "detail": f"paper contains forbidden wording: {phrase}"})
    return violations


def _forbidden_phrase_present(phrase_lc: str, paper_lc: str) -> bool:
    if phrase_lc.startswith("do not present"):
        for token in ("empirical evidence", "validated", "outperforms", "state-of-the-art"):
            if token in paper_lc:
                return True
        return False
    if phrase_lc.startswith("do not "):
        return False
    return phrase_lc in paper_lc


def _detect_unsupported_strong_claims(result_to_claim: dict[str, Any]) -> list[dict[str, str]]:
    unsupported: list[dict[str, str]] = []
    coverage = result_to_claim.get("required_baseline_coverage")
    baseline_incomplete = isinstance(coverage, dict) and coverage.get("status") in {"missing", "incomplete", "mock_only"}
    for mapping in result_to_claim.get("claim_mappings", []) or []:
        if not isinstance(mapping, dict):
            continue
        strength = str(mapping.get("claim_strength") or "").lower()
        support_status = str(mapping.get("support_status") or "").lower()
        if strength == "strong" and (support_status.startswith("unsupported") or baseline_incomplete):
            unsupported.append(
                {
                    "claim_id": str(mapping.get("claim_id") or ""),
                    "detail": f"claim_strength=strong with support_status={support_status or 'unknown'}",
                }
            )
    for claim in result_to_claim.get("claims", []) or []:
        if not isinstance(claim, dict):
            continue
        strength = str(claim.get("claim_strength") or "").lower()
        blocked = claim.get("blocked_by") or claim.get("must_not_say") or []
        if strength == "strong" and blocked:
            unsupported.append(
                {
                    "claim_id": str(claim.get("claim_id") or ""),
                    "detail": "claim_strength=strong but claim has blocked_by/must_not_say entries",
                }
            )
    return unsupported
