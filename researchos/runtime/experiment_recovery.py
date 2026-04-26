from __future__ import annotations

"""T5/T7 运行期恢复辅助。

目标：
1. 在 Experimenter 任务中显式识别“已有代码/已有结果”的状态；
2. 把续跑所需的最小恢复信息写成 artifact，方便 prompt 和调试复用；
3. 避免 pilot/full 在重跑时盲目把已存在的代码和结果全部重写。
"""

import json
from pathlib import Path
from typing import Any


def _rel(path: Path, workspace_dir: Path) -> str:
    return str(path.relative_to(workspace_dir))


def _list_relative_files(root: Path, workspace_dir: Path) -> list[str]:
    """递归列出目录中的相对文件路径。"""

    if not root.exists():
        return []
    return sorted(
        _rel(path, workspace_dir)
        for path in root.rglob("*")
        if path.is_file()
    )


def prepare_experiment_resume_artifacts(workspace_dir: Path, *, mode: str) -> dict[str, Any]:
    """为 T5/T7 生成恢复摘要。

    mode:
    - pilot: 对应 T5
    - full: 对应 T7
    """

    if mode not in {"pilot", "full"}:
        return {}

    workspace_dir = workspace_dir.resolve()
    resume_state_path = workspace_dir / (
        "pilot/pilot_resume_state.json" if mode == "pilot" else "experiments/full_resume_state.json"
    )

    if mode == "pilot":
        outputs = {
            "pilot_plan": workspace_dir / "pilot" / "pilot_plan.yaml",
            "pilot_results": workspace_dir / "pilot" / "pilot_results.json",
            "motivation_validation": workspace_dir / "pilot" / "motivation_validation.md",
            "smoke_marker": workspace_dir / "pilot" / "smoke_test_passed.marker",
            "docker_digests": workspace_dir / "pilot" / "docker_digests.txt",
        }
        code_dir = workspace_dir / "pilot" / "pilot_code"
    else:
        outputs = {
            "results_summary": workspace_dir / "experiments" / "results_summary.json",
            "iteration_log": workspace_dir / "experiments" / "iteration_log.md",
            "ablations": workspace_dir / "experiments" / "ablations.csv",
            "seed_ensemble_summary": workspace_dir / "experiments" / "seed_ensemble_summary.json",
            "iteration_diversity_check": workspace_dir / "experiments" / "iteration_diversity_check.md",
            "docker_digests": workspace_dir / "experiments" / "docker_digests.txt",
        }
        code_dir = workspace_dir / "experiments" / "code"

    existing_outputs = {
        key: _rel(path, workspace_dir)
        for key, path in outputs.items()
        if path.exists()
    }
    missing_outputs = [key for key, path in outputs.items() if not path.exists()]
    existing_code_files = _list_relative_files(code_dir, workspace_dir)

    payload = {
        "mode": mode,
        "existing_output_keys": sorted(existing_outputs.keys()),
        "existing_outputs": existing_outputs,
        "missing_output_keys": missing_outputs,
        "code_dir": _rel(code_dir, workspace_dir) if code_dir.exists() else "",
        "existing_code_files": existing_code_files,
        "has_existing_code": bool(existing_code_files),
    }

    resume_state_path.parent.mkdir(parents=True, exist_ok=True)
    resume_state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "resume_state_path": _rel(resume_state_path, workspace_dir),
        "resume_mode": bool(existing_outputs or existing_code_files),
        "resume_existing_outputs": sorted(existing_outputs.keys()),
        "resume_missing_outputs": missing_outputs,
        "resume_existing_code_files": existing_code_files[:20],
        "resume_has_existing_code": bool(existing_code_files),
    }
