from __future__ import annotations

"""LaTeX 编译工具。

容器内模式（方案 D1）：
- 直接调用系统的 latexmk 命令（容器内已安装 texlive-full）
- 不再通过 docker_exec 嵌套启动容器

宿主机模式（方案 A）：
- 通过 docker_exec 在 LaTeX 镜像中执行 latexmk
- 保持原有行为
"""

import asyncio
import os
from pathlib import Path
import shutil
from typing import Any

from pydantic import BaseModel, Field

from ..runtime.errors import ToolRuntimeError
from ..runtime.logger import get_logger
from .base import Tool, ToolResult
from .docker_exec import DockerExecTool

_LOG = get_logger("latex_compile")


class LatexCompileParams(BaseModel):
    tex_path: str = Field(..., description="相对 workspace 的 .tex 文件路径")
    engine: str = Field("pdflatex", pattern="^(pdflatex|xelatex|lualatex)$")
    bibtex: bool = Field(True, description="是否运行 bibtex")
    output_dir: str | None = Field(None, description="可选输出目录，相对 tex 文件目录")


class LatexCompileTool(Tool):
    name = "latex_compile"
    description = "使用本机 latexmk 或统一 Docker 镜像编译 .tex 文件并生成 PDF。"
    parameters_schema = LatexCompileParams
    timeout_seconds = 1800.0

    def __init__(self, docker_tool: DockerExecTool):
        self.docker = docker_tool

    def _is_running_in_container(self) -> bool:
        """检测是否在容器内运行（使用共享工具）"""
        from researchos.runtime.container_detection import is_running_in_container

        return is_running_in_container()

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = LatexCompileParams(**kwargs)

        # 容器内模式或宿主机已有 TeX：直接调用 latexmk。
        if self._is_running_in_container() or shutil.which("latexmk"):
            return await self._compile_native(params)

        # 宿主机模式：通过 docker_exec
        return await self._compile_via_docker(params)

    async def _compile_native(self, params: LatexCompileParams) -> ToolResult:
        """容器内直接编译（方案 D1）"""
        tex_abs = self.docker.policy.resolve_read(params.tex_path)
        tex_dir = tex_abs.parent
        tex_name = tex_abs.name

        # 构建 latexmk 命令
        cmd = [
            "latexmk",
            f"-{params.engine}",
            "-interaction=nonstopmode",
            "-bibtex" if params.bibtex else "-bibtex-",
        ]

        if params.output_dir:
            cmd.extend(["-outdir", params.output_dir])

        cmd.append(tex_name)

        _LOG.info(
            "latex_compile_native",
            tex_path=params.tex_path,
            engine=params.engine,
            cwd=str(tex_dir),
        )

        # 执行编译
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=tex_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise ToolRuntimeError(self.name, exc) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult(
                ok=False,
                content=f"LaTeX compilation timed out after {self.timeout_seconds}s",
                error="timeout",
            )

        # 处理输出
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")

        content_parts = []
        if out:
            content_parts.append(f"STDOUT:\n{out}")
        if err:
            content_parts.append(f"STDERR:\n{err}")
        content_parts.append(f"EXIT: {proc.returncode}")

        if proc.returncode != 0:
            return ToolResult(
                ok=False,
                content="\n\n".join(content_parts),
                error="nonzero_exit",
            )

        # 检查 PDF 是否生成
        pdf_path = self._expected_pdf_path(tex_abs, params.output_dir)
        if not pdf_path.exists():
            return ToolResult(
                ok=False,
                content=(
                    f"LaTeX command finished but PDF was not generated: "
                    f"{pdf_path.relative_to(self.docker.policy.workspace_dir)}\n\n"
                    + "\n\n".join(content_parts)
                ),
                error="pdf_missing",
            )

        pdf_rel = pdf_path.relative_to(self.docker.policy.workspace_dir).as_posix()
        content_parts.append(f"\nPDF: {pdf_rel}")

        return ToolResult(
            ok=True,
            content="\n\n".join(content_parts),
            data={"pdf_path": pdf_rel, "exit_code": proc.returncode},
        )

    async def _compile_via_docker(self, params: LatexCompileParams) -> ToolResult:
        """宿主机模式：通过 docker_exec 编译（方案 A）"""
        tex_abs = self.docker.policy.resolve_read(params.tex_path)
        tex_dir_rel = tex_abs.parent.relative_to(self.docker.policy.workspace_dir).as_posix()
        tex_name = tex_abs.name

        output_cmd = ""
        if params.output_dir:
            output_cmd = f"-outdir {params.output_dir}"

        command = (
            f"cd /workspace/{tex_dir_rel} && "
            f"latexmk -{params.engine} -interaction=nonstopmode "
            f"{'-bibtex' if params.bibtex else '-bibtex-'} {output_cmd} {tex_name}"
        ).strip()

        result = await self.docker.execute(
            image="researchos/system:latest",
            command=command,
            cwd=f"/workspace/{tex_dir_rel}",
            timeout_seconds=int(self.timeout_seconds),
            allow_network=False,
            gpu=False,
            env={},
            extra_mounts=[],
        )
        if not result.ok:
            return result

        pdf_path = self._expected_pdf_path(tex_abs, params.output_dir)
        if not pdf_path.exists():
            return ToolResult(
                ok=False,
                content=(
                    f"LaTeX command finished but PDF was not generated: "
                    f"{pdf_path.relative_to(self.docker.policy.workspace_dir)}"
                ),
                error="pdf_missing",
            )

        result.data["pdf_path"] = pdf_path.relative_to(self.docker.policy.workspace_dir).as_posix()
        result.content += (
            f"\n\nPDF: {pdf_path.relative_to(self.docker.policy.workspace_dir).as_posix()}"
        )
        return result

    @staticmethod
    def _expected_pdf_path(tex_abs: Path, output_dir: str | None) -> Path:
        pdf_name = tex_abs.with_suffix(".pdf").name
        if output_dir:
            return tex_abs.parent / output_dir / pdf_name
        return tex_abs.with_suffix(".pdf")
