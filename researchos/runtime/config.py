from __future__ import annotations

"""ResearchOS runtime 共享配置。

这个模块的目标很明确：
- 让 `config/system_config/runtime.yaml` 不再只是 README 里的摆设；
- 把 workspace 运行目录、日志格式、人机接口后端等配置集中在一处解析；
- 给 CLI、runner、workspace helper 提供同一套路径与默认值来源。

当前只接入已经在仓库里真实生效、且不会破坏兼容性的字段：
- `workspace.default_root`
- `workspace.runtime_dir`
- `logging.level`
- `logging.json`
- `human_interface.backend`
- `debug.enable_trace`
- `ui.no_banner`
- `web_fetch.allowed_schemes`
- `web_fetch.allowed_hosts`

未来如果要继续扩展 runtime 级共享配置，优先在这里加字段，再逐步把调用点接过来。
"""

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any

import yaml

from .system_config import system_config_path


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
class AgentBehaviorSettings:
    """Agent运行时行为配置。"""

    max_empty_reply: int = 2
    max_nudge_finish: int = 2
    max_validation_retries: int = 3
    # T4 normally uses the controller-owned evolutionary workflow.  This
    # migration switch is deliberately opt-in: it permits the historical
    # prompt only when a workspace has no native T4 artifacts to protect.
    allow_legacy_t4_fallback: bool = False


@dataclass(frozen=True)
class DebugSettings:
    """调试与追踪配置。"""

    enable_trace: bool = True


@dataclass(frozen=True)
class UISettings:
    """CLI 展示相关配置。"""

    no_banner: bool = False
    quiet: bool = False
    verbose: bool = False
    verbosity: str = "normal"
    no_color: bool = False
    json_events: bool = False


@dataclass(frozen=True)
class WebFetchSettings:
    """网页抓取 allowlist 配置。"""

    allowed_schemes: tuple[str, ...] = ("http", "https")
    allowed_hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class LatexSettings:
    """LaTeX backend selection."""

    default_backend: str = "auto"
    allow_docker_fallback: bool = True
    docker_image: str = "researchos/system:latest"


@dataclass(frozen=True)
class RuntimeSettings:
    """Runtime 共享配置的内存表示。"""

    workspace: WorkspaceSettings = field(default_factory=WorkspaceSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    human_interface: HumanInterfaceSettings = field(default_factory=HumanInterfaceSettings)
    agent_behavior: AgentBehaviorSettings = field(default_factory=AgentBehaviorSettings)
    debug: DebugSettings = field(default_factory=DebugSettings)
    ui: UISettings = field(default_factory=UISettings)
    web_fetch: WebFetchSettings = field(default_factory=WebFetchSettings)
    latex: LatexSettings = field(default_factory=LatexSettings)

    def runtime_root(self, workspace_dir: Path) -> Path:
        """返回某个 workspace 下 runtime 私有目录的根路径。"""

        return workspace_dir / self.workspace.runtime_dir

    def traces_dir(self, workspace_dir: Path) -> Path:
        """返回 trace 目录。"""

        return self.runtime_root(workspace_dir) / "traces"

    def logs_dir(self, workspace_dir: Path) -> Path:
        """返回日志目录。"""

        return self.runtime_root(workspace_dir) / "logs"


def resolve_runtime_config_path(config_path: Path | None = None) -> Path:
    """Return the runtime config path that will actually be loaded.

    Environment deployment overrides must apply consistently across Native and
    Docker Mode. CLI callers historically passed ``Path("config/runtime.yaml")``
    explicitly, which accidentally bypassed ``RESEARCHOS_RUNTIME_CONFIG``.
    This helper centralizes the precedence:

    environment variable > caller-provided path > checked-in default.
    """

    configured = os.getenv("RESEARCHOS_RUNTIME_CONFIG", "").strip()
    if configured:
        return Path(configured)
    if config_path and config_path.exists():
        return config_path
    return system_config_path("runtime.yaml")


def load_runtime_settings(config_path: Path | None = None) -> RuntimeSettings:
    """从 `config/system_config/runtime.yaml` 读取共享配置。

    约束：
    - 配置文件缺失时回退到安全默认值；
    - 对未知字段保持忽略，避免用户在 YAML 中提前放未来字段时把当前 runtime 弄崩；
    - 这里只做轻量 schema 解析，不引入额外配置依赖。
    """

    path = resolve_runtime_config_path(config_path)
    if not path.exists():
        return _apply_runtime_env_overrides(RuntimeSettings())

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return _apply_runtime_env_overrides(RuntimeSettings())

    workspace_block = _as_mapping(raw.get("workspace"))
    logging_block = _as_mapping(raw.get("logging"))
    human_block = _as_mapping(raw.get("human_interface"))
    agent_behavior_block = _as_mapping(raw.get("agent_behavior"))
    debug_block = _as_mapping(raw.get("debug"))
    ui_block = _as_mapping(raw.get("ui"))
    web_fetch_block = _as_mapping(raw.get("web_fetch"))
    latex_block = _as_mapping(raw.get("latex"))

    settings = RuntimeSettings(
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
        agent_behavior=AgentBehaviorSettings(
            max_empty_reply=int(agent_behavior_block.get("max_empty_reply", 2)),
            max_nudge_finish=int(agent_behavior_block.get("max_nudge_finish", 2)),
            max_validation_retries=int(agent_behavior_block.get("max_validation_retries", 3)),
            allow_legacy_t4_fallback=bool(
                agent_behavior_block.get("allow_legacy_t4_fallback", False)
            ),
        ),
        debug=DebugSettings(
            enable_trace=bool(debug_block.get("enable_trace", True)),
        ),
        ui=UISettings(
            no_banner=bool(ui_block.get("no_banner", False)),
            quiet=bool(ui_block.get("quiet", False)),
            verbose=bool(ui_block.get("verbose", False)),
            verbosity=str(ui_block.get("verbosity", "normal")),
            no_color=bool(ui_block.get("no_color", False)),
            json_events=bool(ui_block.get("json_events", False)),
        ),
        web_fetch=WebFetchSettings(
            allowed_schemes=_normalize_csv_like_list(
                web_fetch_block.get("allowed_schemes"),
                default=("http", "https"),
            ),
            allowed_hosts=_normalize_csv_like_list(
                web_fetch_block.get("allowed_hosts"),
                default=(),
            ),
        ),
        latex=LatexSettings(
            default_backend=str(latex_block.get("default_backend", "auto")),
            allow_docker_fallback=bool(latex_block.get("allow_docker_fallback", True)),
            docker_image=str(latex_block.get("docker_image", "researchos/system:latest")),
        ),
    )
    return _apply_runtime_env_overrides(settings)


def _apply_runtime_env_overrides(settings: RuntimeSettings) -> RuntimeSettings:
    """Apply deployment-friendly environment overrides."""

    workspace_root = os.getenv("RESEARCHOS_WORKSPACE_ROOT", "").strip()
    if not workspace_root:
        return settings
    return RuntimeSettings(
        workspace=WorkspaceSettings(
            default_root=workspace_root,
            runtime_dir=settings.workspace.runtime_dir,
        ),
        logging=settings.logging,
        human_interface=settings.human_interface,
        agent_behavior=settings.agent_behavior,
        debug=settings.debug,
        ui=settings.ui,
        web_fetch=settings.web_fetch,
        latex=settings.latex,
    )


def validate_runtime_config(settings: RuntimeSettings, config_dir: Path) -> list[str]:
    """验证runtime配置，返回错误列表。

    检查：
    - 必需的配置文件存在
    - 配置值在有效范围内
    - 引用的路径可访问
    """
    errors = []

    # ``model_settings.yaml`` is local and intentionally may not exist before
    # the first interactive setup. Validate its shape when present, but let the
    # runtime guide the user through missing provider credentials.
    model_settings = config_dir / "model_settings.yaml"
    if model_settings.exists():
        try:
            configured = yaml.safe_load(model_settings.read_text(encoding="utf-8")) or {}
            if not isinstance(configured, dict):
                errors.append("model_settings.yaml: 根节点必须是字典")
            elif any(key in configured for key in ("endpoints", "profiles")):
                errors.append("model_settings.yaml: 不支持 legacy endpoint/profile 路由；请保留 provider、api_base、api_key、model、fallback、context_window_fallback 和 truncation")
            elif "truncation" in configured and not isinstance(configured.get("truncation"), dict):
                errors.append("model_settings.yaml: truncation 必须是字典")
        except Exception as exc:
            errors.append(f"model_settings.yaml: 解析错误: {exc}")

    # 检查runtime.yaml值
    if settings.agent_behavior.max_empty_reply < 1:
        errors.append("agent_behavior.max_empty_reply必须>=1")
    if settings.agent_behavior.max_nudge_finish < 1:
        errors.append("agent_behavior.max_nudge_finish必须>=1")
    if settings.agent_behavior.max_validation_retries < 0:
        errors.append("agent_behavior.max_validation_retries必须>=0")

    # 检查logging level有效
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    if settings.logging.level.upper() not in valid_levels:
        errors.append(f"logging.level必须是{valid_levels}之一")

    if not settings.web_fetch.allowed_schemes:
        errors.append("web_fetch.allowed_schemes 至少需要一个协议")

    return errors


def _as_mapping(value: Any) -> dict[str, Any]:
    """把 YAML 段落安全地收敛成字典。"""

    return value if isinstance(value, dict) else {}


def _normalize_csv_like_list(value: Any, *, default: tuple[str, ...]) -> tuple[str, ...]:
    """把 YAML 里的字符串/列表规范化为去重后的字符串元组。"""

    if value is None:
        return default

    if isinstance(value, str):
        items = [part.strip().lower() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(part).strip().lower() for part in value]
    else:
        return default

    normalized = tuple(dict.fromkeys(item for item in items if item))
    return normalized or default
