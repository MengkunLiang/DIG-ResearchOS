from __future__ import annotations

"""LaTeX 编译工具。

它是 docker_exec 的薄封装：
- 对外暴露更符合论文场景的参数；
- 内部统一落到 LaTeX 镜像里执行 latexmk；
- 编译结束后检查 PDF 是否真的生成。
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .base import Tool, ToolResult
from .docker_exec import DockerExecTool


class LatexCompileParams(BaseModel):
    tex_path: str = Field(..., description="相对 workspace 的 .tex 文件路径")
    engine: str = Field("pdflatex", pattern="^(pdflatex|xelatex|lualatex)$")
    bibtex: bool = Field(True, description="是否运行 bibtex")
    output_dir: str | None = Field(None, description="可选输出目录，相对 tex 文件目录")


class LatexCompileTool(Tool):
    name = "latex_compile"
    description = "在 LaTeX Docker 镜像中编译 .tex 文件并生成 PDF。"
    parameters_schema = LatexCompileParams
    timeout_seconds = 300.0

    def __init__(self, docker_tool: DockerExecTool):
        self.docker = docker_tool

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = LatexCompileParams(**kwargs)
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
            image="researchos/latex:texlive-2024",
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
