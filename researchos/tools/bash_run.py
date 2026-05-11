from __future__ import annotations

import asyncio
from contextlib import suppress
import os
from pathlib import Path
import signal

from pydantic import BaseModel, Field

from ..runtime.errors import ToolAccessDenied, ToolRuntimeError
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy


class BashRunParams(BaseModel):
    command: str = Field(..., description="要执行的 bash 命令")
    cwd: str | None = Field(
        default=None,
        description="可选工作目录。必须位于 workspace 或 skill_dir 内。",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="附加环境变量，会合并到当前进程环境中。",
    )
    timeout_seconds: int = Field(
        default=60,
        ge=1,
        le=600,
        description="命令超时时间，单位秒，最大 600。",
    )


class BashRunTool(Tool):
    name = "bash_run"
    description = "在受限工作目录中执行 bash 命令，返回 stdout、stderr 和退出码"
    parameters_schema = BashRunParams
    timeout_seconds = 610.0
    idempotent = False

    def __init__(
        self,
        policy: WorkspaceAccessPolicy,
        *,
        skill_dir: Path | None = None,
        max_output_bytes: int = 1_000_000,
    ) -> None:
        self.policy = policy
        self.skill_dir = skill_dir.resolve() if skill_dir else None
        self.max_output_bytes = max_output_bytes

    async def execute(self, **kwargs) -> ToolResult:
        command = kwargs["command"]
        timeout_seconds = kwargs.get("timeout_seconds", 60)
        env = kwargs.get("env", {})

        try:
            cwd = self._resolve_cwd(kwargs.get("cwd"))
        except ToolAccessDenied as exc:
            return ToolResult(ok=False, content=str(exc), error="access_denied")

        if not cwd.exists():
            return ToolResult(ok=False, content=f"CWD not found: {cwd}", error="not_found")
        if not cwd.is_dir():
            return ToolResult(ok=False, content=f"CWD is not a directory: {cwd}", error="not_directory")

        merged_env = os.environ.copy()
        merged_env.update(env)
        merged_env["PWD"] = str(cwd)

        try:
            proc = await asyncio.create_subprocess_exec(
                "bash",
                "-lc",
                command,
                cwd=str(cwd),
                env=merged_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise ToolRuntimeError(self.name, exc) from exc

        communicate_task = asyncio.create_task(proc.communicate())
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(
                asyncio.shield(communicate_task),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            if proc.returncode is not None:
                # The process finished at the timeout boundary; give asyncio a
                # short grace period to drain the already-closed pipes.
                try:
                    stdout, stderr = await asyncio.wait_for(communicate_task, timeout=1)
                except asyncio.TimeoutError:
                    stdout, stderr = b"", b""
            else:
                with suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                try:
                    stdout, stderr = await asyncio.wait_for(communicate_task, timeout=2)
                except asyncio.TimeoutError:
                    communicate_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await communicate_task
                    stdout, stderr = b"", b""
                timed_out = True
        if timed_out:
            return ToolResult(
                ok=False,
                content=f"Command timed out after {timeout_seconds}s",
                data={"cwd": str(cwd), "timeout_seconds": timeout_seconds},
                error="timeout",
            )

        out = stdout[: self.max_output_bytes].decode("utf-8", errors="replace")
        err = stderr[: self.max_output_bytes].decode("utf-8", errors="replace")
        truncated = len(stdout) > self.max_output_bytes or len(stderr) > self.max_output_bytes
        suffix = f"\n[output truncated at {self.max_output_bytes} bytes]" if truncated else ""

        content_parts: list[str] = []
        if out:
            content_parts.append(f"STDOUT:\n{out}")
        if err:
            content_parts.append(f"STDERR:\n{err}")
        content_parts.append(f"EXIT: {proc.returncode}{suffix}")

        return ToolResult(
            ok=(proc.returncode == 0),
            content="\n\n".join(content_parts),
            data={
                "exit_code": proc.returncode,
                "cwd": str(cwd),
                "timeout_seconds": timeout_seconds,
                "truncated": truncated,
            },
            error="nonzero_exit" if proc.returncode != 0 else None,
        )

    def _resolve_cwd(self, raw_cwd: str | None) -> Path:
        if raw_cwd in (None, "", "."):
            return self.policy.resolve_read("")

        cwd_path = Path(raw_cwd)
        if cwd_path.is_absolute():
            candidate = cwd_path.resolve()
            if self._is_within(candidate, self.policy.workspace_dir):
                return candidate
            if self.skill_dir and self._is_within(candidate, self.skill_dir):
                return candidate
            raise ToolAccessDenied(
                f"bash_run cwd must stay within workspace or skill_dir: '{raw_cwd}'"
            )

        try:
            workspace_candidate = self.policy.resolve_read(raw_cwd)
        except ToolAccessDenied as workspace_exc:
            if self.skill_dir is None:
                raise workspace_exc
            candidate = (self.skill_dir / raw_cwd).resolve()
            if self._is_within(candidate, self.skill_dir):
                return candidate
            raise ToolAccessDenied(
                f"bash_run cwd must stay within workspace or skill_dir: '{raw_cwd}'"
            ) from workspace_exc
        if workspace_candidate.exists() or self.skill_dir is None:
            return workspace_candidate
        candidate = (self.skill_dir / raw_cwd).resolve()
        if self._is_within(candidate, self.skill_dir) and candidate.exists():
            return candidate
        return workspace_candidate

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False
