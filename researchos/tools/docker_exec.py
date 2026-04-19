from __future__ import annotations

"""Docker 隔离执行工具。

这个工具主要服务于后续 T5/T7/T9：
- 训练/推理/批处理代码不直接污染宿主机；
- 通过镜像 allowlist、网络开关、GPU 开关把执行边界固定下来；
- 把 stdout/stderr/exit_code 结构化回填给 runtime。
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
_DEFAULT_ALLOWED_IMAGES = [
    "researchos/python:3.11-ml",
    "researchos/latex:texlive-2024",
]


class DockerExecParams(BaseModel):
    image: str = Field(..., description="Docker 镜像，如 researchos/python:3.11-ml")
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
        docker_cfg = self.project_config.get("docker", {})
        allowed_images = docker_cfg.get("allowed_images", _DEFAULT_ALLOWED_IMAGES)
        if params.image not in allowed_images:
            return ToolResult(
                ok=False,
                content=f"Image '{params.image}' not in allowlist: {allowed_images}",
                error="image_not_allowed",
            )
        if not params.cwd.startswith("/workspace"):
            return ToolResult(
                ok=False,
                content="Docker cwd must stay within /workspace",
                error="invalid_cwd",
            )
        if params.gpu and not self.project_config.get("compute_budget", {}).get("gpu_enabled", False):
            return ToolResult(
                ok=False,
                content="Project config does not allow GPU execution",
                error="gpu_not_allowed",
            )
        try:
            for mount in params.extra_mounts:
                self._normalize_mount(mount)
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="mount_denied")
        return None

    def _build_docker_command(self, params: DockerExecParams) -> list[str]:
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
            "docker": {"allowed_images": list(_DEFAULT_ALLOWED_IMAGES)},
            "compute_budget": {"gpu_enabled": False},
        }
    try:
        return yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {
            "docker": {"allowed_images": list(_DEFAULT_ALLOWED_IMAGES)},
            "compute_budget": {"gpu_enabled": False},
        }
