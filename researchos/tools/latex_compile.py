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
from datetime import datetime, timezone
import hashlib
import json
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
        tex_abs = self.docker.policy.resolve_read(params.tex_path)
        started_at = _now_iso()
        report_base = _compile_report_base(
            tex_abs=tex_abs,
            workspace=self.docker.policy.workspace_dir,
            params=params,
            started_at=started_at,
        )

        # 容器内模式或宿主机已有 TeX：直接调用 latexmk。
        if self._is_running_in_container() or shutil.which("latexmk"):
            if shutil.which("latexmk") is None:
                report = _finalize_compile_report(report_base, success=False, engine="native", exit_code=None)
                _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
                return ToolResult(
                    ok=False,
                    content="WAITING_ENVIRONMENT: latexmk is not installed in the current container/native environment.",
                    error="waiting_environment_latexmk_missing",
                    data={"error": "waiting_environment_latexmk_missing", "compile_report": report},
                )
            result = await self._compile_native(params, report_base=report_base)
            return result

        # 宿主机模式：通过 docker_exec
        return await self._compile_via_docker(params, report_base=report_base)

    async def _compile_native(self, params: LatexCompileParams, *, report_base: dict[str, Any]) -> ToolResult:
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
            report = _finalize_compile_report(report_base, success=False, engine="native", exit_code=None, error="timeout")
            _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
            return ToolResult(
                ok=False,
                content=f"LaTeX compilation timed out after {self.timeout_seconds}s",
                error="timeout",
                data={"compile_report": report},
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
            report = _finalize_compile_report(
                report_base,
                success=False,
                engine="native",
                exit_code=proc.returncode,
                error="nonzero_exit",
            )
            _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
            return ToolResult(
                ok=False,
                content="\n\n".join(content_parts),
                error="nonzero_exit",
                data={"compile_report": report},
            )

        # 检查 PDF 是否生成
        pdf_path = self._expected_pdf_path(tex_abs, params.output_dir)
        if not pdf_path.exists():
            report = _finalize_compile_report(
                report_base,
                success=False,
                engine="native",
                exit_code=proc.returncode,
                error="pdf_missing",
            )
            _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
            return ToolResult(
                ok=False,
                content=(
                    f"LaTeX command finished but PDF was not generated: "
                    f"{pdf_path.relative_to(self.docker.policy.workspace_dir)}\n\n"
                    + "\n\n".join(content_parts)
                ),
                error="pdf_missing",
                data={"compile_report": report},
            )

        pdf_rel = pdf_path.relative_to(self.docker.policy.workspace_dir).as_posix()
        content_parts.append(f"\nPDF: {pdf_rel}")

        report = _finalize_compile_report(
            report_base,
            success=True,
            engine="native",
            exit_code=proc.returncode,
            pdf_path=pdf_path,
        )
        _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
        return ToolResult(
            ok=True,
            content="\n\n".join(content_parts),
            data={"pdf_path": pdf_rel, "exit_code": proc.returncode, "compile_report": report},
        )

    async def _compile_via_docker(self, params: LatexCompileParams, *, report_base: dict[str, Any]) -> ToolResult:
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
            error_code = result.error or ""
            if error_code in {
                "docker_command_not_found",
                "docker_daemon_unavailable",
                "docker_image_missing",
                "image_not_allowed",
            }:
                content = "WAITING_ENVIRONMENT: Docker/LaTeX compile environment unavailable.\n\n" + result.content
                report = _finalize_compile_report(
                    report_base,
                    success=False,
                    engine="docker",
                    exit_code=result.data.get("exit_code") if isinstance(result.data, dict) else None,
                    error=error_code or "waiting_environment",
                )
                _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
                return ToolResult(
                    ok=False,
                    content=content,
                    error=f"waiting_environment_{error_code or 'docker'}",
                    data={"error": "waiting_environment", "compile_report": report},
                )
            report = _finalize_compile_report(
                report_base,
                success=False,
                engine="docker",
                exit_code=result.data.get("exit_code") if isinstance(result.data, dict) else None,
                error=result.error,
            )
            _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
            result.data["compile_report"] = report
            return result

        pdf_path = self._expected_pdf_path(tex_abs, params.output_dir)
        if not pdf_path.exists():
            report = _finalize_compile_report(
                report_base,
                success=False,
                engine="docker",
                exit_code=result.data.get("exit_code") if isinstance(result.data, dict) else None,
                error="pdf_missing",
            )
            _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
            return ToolResult(
                ok=False,
                content=(
                    f"LaTeX command finished but PDF was not generated: "
                    f"{pdf_path.relative_to(self.docker.policy.workspace_dir)}"
                ),
                error="pdf_missing",
                data={"compile_report": report},
            )

        report = _finalize_compile_report(
            report_base,
            success=True,
            engine="docker",
            exit_code=result.data.get("exit_code") if isinstance(result.data, dict) else 0,
            pdf_path=pdf_path,
        )
        _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
        result.data["pdf_path"] = pdf_path.relative_to(self.docker.policy.workspace_dir).as_posix()
        result.data["compile_report"] = report
        result.content += (
            f"\n\nPDF: {pdf_path.relative_to(self.docker.policy.workspace_dir).as_posix()}"
            f"\nCompile report: {_compile_report_target_for_tex(params.tex_path) or 'not persisted for this tex_path'}"
        )
        return result

    @staticmethod
    def _expected_pdf_path(tex_abs: Path, output_dir: str | None) -> Path:
        pdf_name = tex_abs.with_suffix(".pdf").name
        if output_dir:
            return tex_abs.parent / output_dir / pdf_name
        return tex_abs.with_suffix(".pdf")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _compile_report_base(
    *,
    tex_abs: Path,
    workspace: Path,
    params: LatexCompileParams,
    started_at: str,
) -> dict[str, Any]:
    tex_rel = tex_abs.relative_to(workspace).as_posix()
    log_path = tex_abs.with_suffix(".log")
    return {
        "_workspace": workspace.as_posix(),
        "version": "1.0",
        "semantics": "latex_compile_attempt_report",
        "tex_path": tex_rel,
        "requested_engine": params.engine,
        "bibtex": params.bibtex,
        "output_dir": params.output_dir,
        "started_at": started_at,
        "main_tex_sha256": _sha256_file(tex_abs) if tex_abs.exists() else "",
        "main_tex_mtime": tex_abs.stat().st_mtime if tex_abs.exists() else 0,
        "log_path": log_path.relative_to(workspace).as_posix(),
    }


def _finalize_compile_report(
    base: dict[str, Any],
    *,
    success: bool,
    engine: str,
    exit_code: int | None,
    pdf_path: Path | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    workspace = Path(base.get("_workspace", "")) if base.get("_workspace") else None
    finished_at = _now_iso()
    report = dict(base)
    report.pop("_workspace", None)
    report.update(
        {
            "engine": engine,
            "exit_code": exit_code,
            "success": success,
            "finished_at": finished_at,
            "error": error,
            "attempts": [
                {
                    "engine": engine,
                    "exit_code": exit_code,
                    "success": success,
                    "started_at": base.get("started_at"),
                    "finished_at": finished_at,
                    "error": error,
                }
            ],
        }
    )
    if pdf_path is not None and pdf_path.exists():
        if workspace is not None:
            try:
                report["pdf_path"] = pdf_path.relative_to(workspace).as_posix()
            except ValueError:
                report["pdf_path"] = str(pdf_path)
        else:
            report["pdf_path"] = str(pdf_path)
        report["pdf_sha256"] = _sha256_file(pdf_path)
        report["pdf_size"] = pdf_path.stat().st_size
        report["pdf_mtime"] = pdf_path.stat().st_mtime
    else:
        report["pdf_path"] = ""
        report["pdf_sha256"] = ""
        report["pdf_size"] = 0
        report["pdf_mtime"] = 0
    if workspace is not None:
        log_rel = report.get("log_path")
        if isinstance(log_rel, str) and log_rel:
            log_path = workspace / log_rel
            if log_path.exists():
                report["log_sha256"] = _sha256_file(log_path)
                report["log_mtime"] = log_path.stat().st_mtime
                report["log_size"] = log_path.stat().st_size
            else:
                report["log_sha256"] = ""
                report["log_mtime"] = 0
                report["log_size"] = 0
    return report


def _write_compile_report_for_known_target(workspace: Path, tex_path: str, report: dict[str, Any]) -> None:
    """Persist compile reports for task-level TeX targets that validators expect."""

    report_rel = _compile_report_target_for_tex(tex_path)
    if not report_rel:
        return
    report_path = workspace / report_rel
    pdf_rel = report.get("pdf_path")
    if isinstance(pdf_rel, str) and pdf_rel and pdf_rel.startswith(str(workspace)):
        try:
            report["pdf_path"] = Path(pdf_rel).relative_to(workspace).as_posix()
        except ValueError:
            pass
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _compile_report_target_for_tex(tex_path: str) -> str:
    """Return the workspace-relative compile report path expected by validators."""

    normalized = tex_path.strip().lstrip("./")
    if normalized == "submission/bundle/main.tex":
        return "submission/compile_report.json"
    if normalized == "drafts/survey/survey.tex":
        return "drafts/survey/survey_compile_report.json"
    return ""
