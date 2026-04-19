from __future__ import annotations

"""workspace 初始化与说明辅助。

Runtime Spec 明确要求 workspace 是 artifact-first 的唯一事实来源。
因此除了 `_runtime/` 本身，这里还把后续 T1-T9 常用目录的“标准树”固定下来，
便于：
- CLI 从 0 初始化一个可调试 workspace；
- README 给出稳定目录结构；
- 后续 agent 开发在同一套路径约定上协作，而不是每个人各建一套目录。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


STANDARD_WORKSPACE_DIRS = [
    "user_seeds",
    "literature",
    "literature/pdfs",
    "literature/paper_notes",
    "ideation",
    "pilot",
    "pilot/pilot_code",
    "novelty",
    "experiments",
    "experiments/runs",
    "experiments/configs",
    "evaluation",
    "drafts",
    "reviews",
    "reviews/review_rounds",
    "submission",
    "submission/bundle",
    "skills",
]


def build_standard_workspace_dirs(runtime_dir_name: str = "_runtime") -> list[str]:
    """返回标准 workspace 目录列表。

    `runtime_dir_name` 默认仍是 `_runtime`，这样对已有 workspace 和测试保持兼容；
    但如果团队想统一改成 `.runtime` 一类名字，也只需要改 `config/runtime.yaml`。
    """

    return [
        f"{runtime_dir_name}/traces",
        f"{runtime_dir_name}/logs",
        *STANDARD_WORKSPACE_DIRS,
    ]


@dataclass
class WorkspaceInitResult:
    """初始化 workspace 后返回的摘要。"""

    workspace_dir: Path
    created_dirs: list[str]
    project_file: Path | None


def initialize_workspace(
    workspace_dir: Path,
    *,
    create_project_file: bool = True,
    project_id: str | None = None,
    topic: str | None = None,
    force_project_file: bool = False,
    runtime_dir_name: str = "_runtime",
) -> WorkspaceInitResult:
    """创建标准 workspace 树。

    约定：
    - 永远不会删除已有文件；
    - `project.yaml` 仅在不存在或显式 `force_project_file=True` 时写入；
    - 目录初始化是幂等操作，适合在 CLI / 测试 / 脚本里反复调用。
    """

    workspace_dir = workspace_dir.resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    created_dirs: list[str] = []

    for rel_dir in build_standard_workspace_dirs(runtime_dir_name):
        candidate = workspace_dir / rel_dir
        if not candidate.exists():
            created_dirs.append(rel_dir)
        candidate.mkdir(parents=True, exist_ok=True)

    project_file: Path | None = None
    if create_project_file:
        project_file = write_project_stub(
            workspace_dir,
            project_id=project_id or "demo-project",
            topic=topic or "",
            force=force_project_file,
        )

    return WorkspaceInitResult(
        workspace_dir=workspace_dir,
        created_dirs=created_dirs,
        project_file=project_file,
    )


def write_project_stub(
    workspace_dir: Path,
    *,
    project_id: str,
    topic: str,
    force: bool = False,
) -> Path:
    """写入最小 `project.yaml` 模板。"""

    project_path = workspace_dir / "project.yaml"
    if project_path.exists() and not force:
        return project_path

    payload: dict[str, Any] = {
        "project_id": project_id,
        "topic": topic,
        "created_at": _now_iso(),
        "status": "draft",
        "notes": (
            "该文件是由 runtime 初始化生成的最小模板。"
            "后续 T1/T7.5 等 agent 落地后，可在此基础上补业务字段。"
        ),
    }
    project_path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return project_path


def render_workspace_tree(runtime_dir_name: str = "_runtime") -> str:
    """返回 README / CLI 可复用的标准 workspace 树说明。"""

    return "\n".join(
        [
            "workspace/",
            "|-- project.yaml",
            "|-- state.yaml",
            "|-- user_seeds/",
            "|-- literature/",
            "|   |-- pdfs/",
            "|   `-- paper_notes/",
            "|-- ideation/",
            "|-- pilot/",
            "|   `-- pilot_code/",
            "|-- novelty/",
            "|-- experiments/",
            "|   |-- runs/",
            "|   `-- configs/",
            "|-- evaluation/",
            "|-- drafts/",
            "|-- reviews/",
            "|   `-- review_rounds/",
            "|-- submission/",
            "|   `-- bundle/",
            "|-- skills/",
            f"`-- {runtime_dir_name}/",
            "    |-- traces/",
            "    `-- logs/",
        ]
    )
