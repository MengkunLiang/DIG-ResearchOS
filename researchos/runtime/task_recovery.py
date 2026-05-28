from __future__ import annotations

"""通用 task 恢复辅助。

目标：
- 给所有 task 提供统一的“已有输出/缺失输出”快照；
- 为已有专项恢复逻辑（T3 / T5 / T7）提供统一接入口；
- 让 single-task 与完整 pipeline 两条执行链都能复用同一套恢复语义。
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .experiment_recovery import prepare_experiment_resume_artifacts
from .t3_recovery import prepare_t3_resume_artifacts


# `state.yaml` 由 runtime 统一管理；它的存在并不代表 Agent 已经产出过有效内容。
_RUNTIME_MANAGED_OUTPUTS = {"state.yaml"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_task_slug(task_id: str) -> str:
    """把 task_id 规范化为适合文件名的 slug。"""

    return re.sub(r"[^a-z0-9]+", "_", task_id.casefold()).strip("_") or "task"


def _rel(path: Path, workspace_dir: Path) -> str:
    try:
        return str(path.relative_to(workspace_dir))
    except ValueError:
        return str(path)


def _is_runtime_managed_output(path: Path, workspace_dir: Path) -> bool:
    """跳过 runtime 自己维护的输出，避免把它误判成 Agent 产出。"""

    return _rel(path, workspace_dir) in _RUNTIME_MANAGED_OUTPUTS


def _summarize_output(path: Path, workspace_dir: Path) -> dict[str, Any] | None:
    """把 file/dir 输出归一化成摘要；无有效内容时返回 None。"""

    if _is_runtime_managed_output(path, workspace_dir):
        return None

    if path.is_file():
        # 空文件通常意味着上次运行没有真正产出可复用内容，不作为恢复依据。
        size = path.stat().st_size
        if size <= 0:
            return None
        return {
            "kind": "file",
            "path": _rel(path, workspace_dir),
            "bytes": size,
        }

    if path.is_dir():
        files = sorted(item for item in path.rglob("*") if item.is_file())
        if not files:
            return None
        return {
            "kind": "dir",
            "path": _rel(path, workspace_dir),
            "file_count": len(files),
            "sample_files": [_rel(item, workspace_dir) for item in files[:20]],
        }

    return None


def prepare_generic_resume_artifacts(
    workspace_dir: Path,
    *,
    task_id: str,
    outputs_expected: dict[str, Path],
) -> dict[str, Any]:
    """为任意 task 生成统一的恢复快照。

    这个快照不做 task-specific 推理，只回答三个问题：
    1. 已有哪些输出已经存在且可复用；
    2. 还缺哪些声明产物；
    3. 若需要续跑，最小可用的恢复状态文件在哪里。
    """

    existing_outputs: list[str] = []
    missing_outputs: list[str] = []
    output_summaries: dict[str, dict[str, Any]] = {}
    existing_artifacts: list[str] = []

    for name, path in outputs_expected.items():
        summary = _summarize_output(path, workspace_dir)
        if summary is None:
            missing_outputs.append(name)
            continue

        existing_outputs.append(name)
        output_summaries[name] = summary
        if summary["kind"] == "file":
            existing_artifacts.append(str(summary["path"]))
        else:
            existing_artifacts.extend(summary.get("sample_files", []))

    resume_state_dir = workspace_dir / "_runtime" / "resume"
    resume_state_dir.mkdir(parents=True, exist_ok=True)
    resume_state_path = resume_state_dir / f"{_safe_task_slug(task_id)}_resume_state.json"

    payload = {
        "generated_at": _now_iso(),
        "task_id": task_id,
        "resume_mode": bool(existing_outputs),
        "existing_outputs": existing_outputs,
        "missing_outputs": missing_outputs,
        "output_summaries": output_summaries,
        "existing_artifacts": existing_artifacts[:40],
    }
    resume_state_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "resume_state_path": _rel(resume_state_path, workspace_dir),
        "resume_mode": bool(existing_outputs),
        "resume_existing_outputs": existing_outputs,
        "resume_missing_outputs": missing_outputs,
        "resume_output_summaries": output_summaries,
        "resume_existing_artifacts": existing_artifacts[:20],
    }


def prepare_task_resume_artifacts(
    workspace_dir: Path,
    *,
    task_id: str,
    outputs_expected: dict[str, Path],
    base_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """统一汇总 generic + task-specific 恢复信息。

    约定：
    - generic 恢复能力对所有 task 生效；
    - T3 / T5 / T7 继续叠加专项恢复信息；
    - 如果历史上已经被判定为 resume，则沿用原 resume_reason；
    - 如果只是检测到已有输出，则补一个通用 reason，提醒 Agent 走增量模式。
    """

    base_extra = dict(base_extra or {})
    recovery = prepare_generic_resume_artifacts(
        workspace_dir,
        task_id=task_id,
        outputs_expected=outputs_expected,
    )

    # T3 有“剩余精读队列”这种强 task-specific 语义，继续保留原专项恢复器。
    if task_id == "T3":
        recovery.update(
            prepare_t3_resume_artifacts(
                workspace_dir,
                refresh_reason=str(base_extra.get("resume_reason") or "context_build"),
            )
        )
    # T5 / T7 的“已有代码 + 待补实验产物”也继续沿用专项恢复器。
    elif task_id == "T5":
        recovery.update(prepare_experiment_resume_artifacts(workspace_dir, mode="pilot"))
    elif task_id == "T7":
        recovery.update(prepare_experiment_resume_artifacts(workspace_dir, mode="full"))

    resume_mode = bool(
        base_extra.get("resume_mode")
        or base_extra.get("is_resume")
        or recovery.get("resume_mode")
        or recovery.get("resume_queue_count")
    )
    recovery["resume_mode"] = resume_mode

    # 如果是“检测到已有输出但没有历史 resume 原因”，给一个通用原因，方便 prompt 明确表达。
    if resume_mode and not base_extra.get("resume_reason") and not recovery.get("resume_reason"):
        recovery["resume_reason"] = "existing_outputs_detected"

    return recovery
