from __future__ import annotations

"""ResearchOS runtime 共享配置。

这个模块的目标很明确：
- 让 `config/runtime.yaml` 不再只是 README 里的摆设；
- 把 workspace 运行目录、日志格式、人机接口后端等配置集中在一处解析；
- 给 CLI、runner、workspace helper 提供同一套路径与默认值来源。

当前只接入已经在仓库里真实生效、且不会破坏兼容性的字段：
- `workspace.default_root`
- `workspace.runtime_dir`
- `logging.level`
- `logging.json`
- `human_interface.backend`

未来如果要继续扩展 runtime 级共享配置，优先在这里加字段，再逐步把调用点接过来。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class WorkspaceSettings:
    """与 workspace 布局相关的配置。"""

    default_root: str = "./workspace"
    runtime_dir: str = "_runtime"


@dataclass(frozen=True)
class LoggingSettings:
    """结构化日志的默认配置。"""

    level: str = "INFO"
    json: bool = True


@dataclass(frozen=True)
class HumanInterfaceSettings:
    """人机接口后端配置。

    当前 runtime 只实现了 CLI backend，但先把抽象固定下来，
    后续接 Web UI / API gate 时可以复用这个入口。
    """

    backend: str = "cli"


@dataclass(frozen=True)
class RuntimeSettings:
    """Runtime 共享配置的内存表示。"""

    workspace: WorkspaceSettings = field(default_factory=WorkspaceSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    human_interface: HumanInterfaceSettings = field(default_factory=HumanInterfaceSettings)

    def runtime_root(self, workspace_dir: Path) -> Path:
        """返回某个 workspace 下 runtime 私有目录的根路径。"""

        return workspace_dir / self.workspace.runtime_dir

    def traces_dir(self, workspace_dir: Path) -> Path:
        """返回 trace 目录。"""

        return self.runtime_root(workspace_dir) / "traces"

    def logs_dir(self, workspace_dir: Path) -> Path:
        """返回日志目录。"""

        return self.runtime_root(workspace_dir) / "logs"


def load_runtime_settings(config_path: Path | None = None) -> RuntimeSettings:
    """从 `config/runtime.yaml` 读取共享配置。

    约束：
    - 配置文件缺失时回退到安全默认值；
    - 对未知字段保持忽略，避免用户在 YAML 中提前放未来字段时把当前 runtime 弄崩；
    - 这里只做轻量 schema 解析，不引入额外配置依赖。
    """

    path = config_path or Path("config/runtime.yaml")
    if not path.exists():
        return RuntimeSettings()

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return RuntimeSettings()

    workspace_block = _as_mapping(raw.get("workspace"))
    logging_block = _as_mapping(raw.get("logging"))
    human_block = _as_mapping(raw.get("human_interface"))

    return RuntimeSettings(
        workspace=WorkspaceSettings(
            default_root=str(workspace_block.get("default_root", "./workspace")),
            runtime_dir=str(workspace_block.get("runtime_dir", "_runtime")),
        ),
        logging=LoggingSettings(
            level=str(logging_block.get("level", "INFO")),
            json=bool(logging_block.get("json", True)),
        ),
        human_interface=HumanInterfaceSettings(
            backend=str(human_block.get("backend", "cli")),
        ),
    )


def _as_mapping(value: Any) -> dict[str, Any]:
    """把 YAML 段落安全地收敛成字典。"""

    return value if isinstance(value, dict) else {}
