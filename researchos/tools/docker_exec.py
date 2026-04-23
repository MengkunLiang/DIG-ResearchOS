from __future__ import annotations

"""Docker 隔离执行工具。

这个工具主要服务于后续 T5/T7/T9：
- 训练/推理/批处理代码不直接污染宿主机；
- 通过镜像 allowlist、网络开关、GPU 开关把执行边界固定下来；
- 把 stdout/stderr/exit_code 结构化回填给 runtime。

容器内模式（方案 D1）：
- 当检测到运行在 Docker 容器内时，直接使用 subprocess 执行命令
- 不再嵌套启动新的 Docker 容器（避免 Docker-in-Docker 复杂度）
- 保持工具接口不变，确保向后兼容
"""

import asyncio
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
import yaml

from ..runtime.errors import ToolAccessDenied, ToolRuntimeError
from ..runtime.logger import get_logger
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy


_LOG = get_logger("docker_exec")

# 默认允许的 Docker 镜像列表（统一镜像策略）
_DEFAULT_ALLOWED_IMAGES = [
    "researchos/system:latest",  # 统一镜像
]


def get_default_image() -> str:
    """获取默认 Docker 镜像。

    优先从 config/runtime.yaml 读取，失败时使用内置默认值。
    与 Dockerfile 构建的镜像名保持一致。

    Returns:
        str: 默认镜像名，如 "researchos/system:latest"
    """
    # 尝试从 runtime.yaml 读取
    config_paths = [
        Path(__file__).parent.parent.parent / "config" / "runtime.yaml",
        Path(__file__).parent.parent.parent.parent / "config" / "runtime.yaml",
    ]

    for config_path in config_paths:
        if config_path.exists():
            try:
                runtime_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
                if runtime_config:
                    docker_cfg = runtime_config.get("docker", {})
                    default_image = docker_cfg.get("default_image")
                    if default_image:
                        return default_image
            except Exception:
                pass

    # 回退到内置默认值
    return "researchos/system:latest"


# 动态获取的默认镜像（延迟加载）
_default_image_cache: str | None = None


def get_default_allowed_images() -> list[str]:
    """获取允许的镜像列表。

    从 runtime.yaml 读取 allowed_images 配置，否则使用内置列表。

    Returns:
        list[str]: 允许的镜像列表
    """
    config_paths = [
        Path(__file__).parent.parent.parent / "config" / "runtime.yaml",
        Path(__file__).parent.parent.parent.parent / "config" / "runtime.yaml",
    ]

    for config_path in config_paths:
        if config_path.exists():
            try:
                runtime_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
                if runtime_config:
                    docker_cfg = runtime_config.get("docker", {})
                    allowed = docker_cfg.get("allowed_images")
                    if allowed:
                        return allowed
            except Exception:
                pass

    return list(_DEFAULT_ALLOWED_IMAGES)


class DockerExecParams(BaseModel):
    image: str = Field("researchos/system:latest", description="Docker 镜像，默认 researchos/system:latest")
    command: str = Field(..., description="容器内执行的命令")
    cwd: str = Field("/workspace", description="容器内工作目录，默认 /workspace")
    timeout_seconds: int = Field(600, ge=10, le=7200, description="容器执行超时")
    allow_network: bool = Field(False, description="是否允许容器访问外网")
    gpu: bool = Field(False, description="是否挂载 GPU")
    env: dict[str, str] = Field(default_factory=dict, description="额外环境变量")
    memory_limit: str | None = Field(None, description="内存上限，如 16g")
    extra_mounts: list[str] = Field(
        default_factory=list,
        description="额外挂载，格式为 host_path:container_path[:ro]",
    )


class DockerExecTool(Tool):
    name = "docker_exec"
    description = (
        "在 Docker 容器中隔离执行命令。workspace 会挂载到 /workspace。"
        "适合训练、评测、LaTeX 编译等需要较强环境隔离的场景。"
    )
    parameters_schema = DockerExecParams
    timeout_seconds = 7200.0
    requires_human_approval = True
    idempotent = False

    def __init__(
        self,
        policy: WorkspaceAccessPolicy,
        *,
        project_config: dict[str, Any] | None = None,
        max_output_bytes: int = 64 * 1024,
    ) -> None:
        self.policy = policy
        self.project_config = project_config or load_project_config(policy.workspace_dir)
        self.max_output_bytes = max_output_bytes
        # 检测是否在容器内运行（方案 D1）
        self._container_mode = self._is_running_in_container()

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = DockerExecParams(**kwargs)

        # 1. 先做执行边界校验，尽量在真的起容器前就把错误拦下来。
        validation_error = self._validate_params(params)
        if validation_error is not None:
            return validation_error

        docker_cmd = self._build_docker_command(params)
        _LOG.info(
            "docker_exec_start",
            image=params.image,
            cwd=params.cwd,
            allow_network=params.allow_network,
            gpu=params.gpu,
            timeout_seconds=params.timeout_seconds,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise ToolRuntimeError(self.name, exc) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=params.timeout_seconds,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult(
                ok=False,
                content=f"Docker exec timed out after {params.timeout_seconds}s",
                data={"timeout_seconds": params.timeout_seconds},
                error="timeout",
            )

        out = stdout[: self.max_output_bytes].decode("utf-8", errors="replace")
        err = stderr[: self.max_output_bytes].decode("utf-8", errors="replace")
        truncated = len(stdout) > self.max_output_bytes or len(stderr) > self.max_output_bytes

        # GPU 降级处理：检测 GPU 挂载失败错误，自动重试（不加 --gpus）
        _GPU_ERROR_MARKERS = [
            "could not select device driver",
            "no device drivers found",
            "nvidia runtime is not configured",
            "NV Runtime Error",
        ]
        _GPU_CAPABILITY_MSG = "Capabilities: [[gpu]]"

        if params.gpu and proc.returncode != 0:
            stderr_lower = err.lower()
            if any(marker.lower() in stderr_lower for marker in _GPU_ERROR_MARKERS):
                _LOG.warning(
                    "gpu_not_available_fallback_to_cpu",
                    original_error=err[:500],
                    hint="GPU 不可用，任务将在 CPU 模式下执行",
                )
                # 不带 --gpus 重试
                params_no_gpu = DockerExecParams(
                    image=params.image,
                    command=params.command,
                    cwd=params.cwd,
                    timeout_seconds=params.timeout_seconds,
                    allow_network=params.allow_network,
                    gpu=False,
                    env=params.env,
                    memory_limit=params.memory_limit,
                    extra_mounts=params.extra_mounts,
                )
                docker_cmd_no_gpu = self._build_docker_command(params_no_gpu)
                _LOG.info("docker_exec_retry_cpu", command_parts=docker_cmd_no_gpu[:5])
                try:
                    proc2 = await asyncio.create_subprocess_exec(
                        *docker_cmd_no_gpu,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout2, stderr2 = await asyncio.wait_for(
                        proc2.communicate(),
                        timeout=params.timeout_seconds,
                    )
                    out = stdout2[: self.max_output_bytes].decode("utf-8", errors="replace")
                    err = stderr2[: self.max_output_bytes].decode("utf-8", errors="replace")
                    truncated = len(stdout2) > self.max_output_bytes or len(stderr2) > self.max_output_bytes
                    proc = proc2  # 用于后续 content 组装
                except OSError as exc2:
                    raise ToolRuntimeError(self.name, exc2) from exc2

        content_parts: list[str] = []
        if out:
            content_parts.append(f"STDOUT:\n{out}")
        if err:
            content_parts.append(f"STDERR:\n{err}")
        content_parts.append(f"EXIT: {proc.returncode}")
        if truncated:
            content_parts.append(f"[OUTPUT TRUNCATED at {self.max_output_bytes} bytes]")

        _LOG.info(
            "docker_exec_done",
            exit_code=proc.returncode,
            stdout_bytes=len(stdout),
            stderr_bytes=len(stderr),
        )
        return ToolResult(
            ok=(proc.returncode == 0),
            content="\n\n".join(content_parts),
            data={
                "exit_code": proc.returncode,
                "truncated": truncated,
                "stdout_bytes": len(stdout),
                "stderr_bytes": len(stderr),
            },
            error="nonzero_exit" if proc.returncode != 0 else None,
        )

    def _validate_params(self, params: DockerExecParams) -> ToolResult | None:
        """验证执行参数。

        容器内模式：
        - 镜像白名单检查被跳过（因为已经在容器内）
        - 其他安全检查保持不变

        宿主机模式：
        - 完整的参数校验
        """
        # 优先从 runtime.yaml 读取配置，其次从 project.yaml，再次回退到内置默认值
        allowed_images = get_default_allowed_images()

        # 尝试从 project.yaml 覆盖（如果有的话）
        docker_cfg = self.project_config.get("docker", {})
        if docker_cfg.get("allowed_images"):
            allowed_images = docker_cfg["allowed_images"]

        # 容器内模式：跳过镜像白名单检查（image 参数被忽略）
        if not self._container_mode:
            if params.image not in allowed_images:
                return ToolResult(
                    ok=False,
                    content=f"Image '{params.image}' not in allowlist: {allowed_images}",
                    error="image_not_allowed",
                )

        # 工作目录检查（两种模式都需要）
        if not params.cwd.startswith("/workspace"):
            return ToolResult(
                ok=False,
                content="Docker cwd must stay within /workspace",
                error="invalid_cwd",
            )

        # GPU 权限检查（两种模式都需要）
        if params.gpu and not self.project_config.get("compute_budget", {}).get("gpu_enabled", False):
            return ToolResult(
                ok=False,
                content="Project config does not allow GPU execution",
                error="gpu_not_allowed",
            )

        # 挂载路径检查（仅宿主机模式需要）
        if not self._container_mode:
            try:
                for mount in params.extra_mounts:
                    self._normalize_mount(mount)
            except ToolAccessDenied as exc:
                return ToolResult(ok=False, content=str(exc), error="mount_denied")

        return None

    def _is_running_in_container(self) -> bool:
        """检测是否在 Docker 容器内运行。

        使用共享的容器检测工具。

        Returns:
            bool: 如果在容器内运行返回 True，否则返回 False
        """
        from researchos.runtime.container_detection import is_running_in_container

        return is_running_in_container()

    def _build_docker_command(self, params: DockerExecParams) -> list[str]:
        """构建执行命令。

        容器内模式（方案 D1）：
        - 直接返回 bash 命令，不包装 docker run
        - 环境变量通过 bash 前缀设置
        - 工作目录通过 cd 切换

        宿主机模式（方案 A）：
        - 构建完整的 docker run 命令
        - 挂载 workspace、设置资源限制等
        """
        # 容器内模式：直接执行命令
        if self._container_mode:
            _LOG.info(
                "docker_exec_container_native_mode",
                command=params.command,
                cwd=params.cwd,
                env=params.env,
            )

            # 构建命令：设置环境变量 + 切换目录 + 执行命令
            cmd_parts = []

            # 添加环境变量
            if params.env:
                env_exports = " ".join(f"export {k}={v};" for k, v in params.env.items())
                cmd_parts.append(env_exports)

            # 切换工作目录（如果不是默认的 /workspace）
            if params.cwd != "/workspace":
                cmd_parts.append(f"cd {params.cwd};")

            # 添加实际命令
            cmd_parts.append(params.command)

            full_command = " ".join(cmd_parts)
            return ["bash", "-lc", full_command]

        # 宿主机模式：构建 docker run 命令
        docker_cfg = self.project_config.get("docker", {})
        workspace_dir = self.policy.workspace_dir.resolve()
        memory_limit = params.memory_limit or docker_cfg.get("default_memory_limit", "16g")

        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{workspace_dir}:/workspace:rw",
            "-w",
            params.cwd,
            "--stop-timeout",
            "30",
            "--memory",
            memory_limit,
        ]
        if not params.allow_network:
            cmd.extend(["--network", "none"])
        if params.gpu:
            cmd.extend(["--gpus", "all"])

        for mount in params.extra_mounts:
            cmd.extend(["-v", self._normalize_mount(mount)])
        for key, value in params.env.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.append(params.image)
        cmd.extend(["bash", "-lc", params.command])
        return cmd

    def _normalize_mount(self, mount: str) -> str:
        host_part, _, remainder = mount.partition(":")
        if not host_part or not remainder:
            raise ToolAccessDenied(f"Invalid mount format: {mount}")
        host_path = Path(host_part)
        if host_path.is_absolute():
            candidate = host_path.resolve()
            try:
                candidate.relative_to(self.policy.workspace_dir)
            except ValueError as exc:
                raise ToolAccessDenied(
                    f"Mount host path must stay within workspace: {host_part}"
                ) from exc
        else:
            candidate = self.policy.resolve_read(host_part)
        return f"{candidate}:{remainder}"


def load_project_config(workspace_dir: Path) -> dict[str, Any]:
    """从 workspace/project.yaml 读取项目级配置。

    如果文件不存在，则返回一个足以让 runtime 工作的保守默认值。
    """
    project_path = workspace_dir / "project.yaml"
    if not project_path.exists():
        return {
            "docker": {"allowed_images": get_default_allowed_images()},
            "compute_budget": {"gpu_enabled": False},
        }
    try:
        return yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {
            "docker": {"allowed_images": get_default_allowed_images()},
            "compute_budget": {"gpu_enabled": False},
        }
