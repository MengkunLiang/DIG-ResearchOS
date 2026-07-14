from __future__ import annotations

"""LaTeX compilation with native-first, Docker-backed execution.

``auto`` prefers a local TeX installation, then uses the configured, allowlisted
Docker image when the host has no usable TeX toolchain.  The container receives
only the current workspace bind mount, no network, and the same compile report
contract as native execution.  This keeps T3.6, T8, and T9 runnable on a slim
host without pretending that a PDF was compiled.
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import signal
import subprocess
import re
from typing import Any

from pydantic import BaseModel, Field

from ..runtime.errors import ToolRuntimeError
from ..runtime.config import LatexSettings
from ..runtime.environment import detect_latex_backends
from ..runtime.logger import get_logger
from .base import Tool, ToolResult
from .docker_exec import DockerExecTool, check_docker_environment

_LOG = get_logger("latex_compile")


def _new_process_group_kwargs() -> dict[str, bool]:
    """Create each native compiler in its own process group on POSIX.

    ``latexmk`` can spawn one or more TeX children.  Killing only the parent
    on timeout leaves those children alive and makes a later CLI resume appear
    stuck.  A separate session gives the timeout path one group to terminate.
    """

    return {"start_new_session": True} if os.name == "posix" else {}


async def _terminate_process_group(proc: asyncio.subprocess.Process) -> None:
    """Terminate a compiler and all of its descendants without orphaning TeX."""

    if proc.returncode is not None:
        return
    pid = getattr(proc, "pid", None)
    if os.name == "posix" and isinstance(pid, int) and pid > 0:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
            return
        except asyncio.TimeoutError:
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            await proc.wait()
            return
    proc.kill()
    await proc.wait()


def latex_backend_preflight(latex_settings: LatexSettings) -> dict[str, Any]:
    """Probe the backend that ``auto`` will use before an LLM writing stage.

    The probe is intentionally stricter than merely finding a ``docker`` binary:
    for Docker fallback it validates daemon access, the configured allowlisted
    image, and the TeX commands needed by both survey and submission paths.
    """

    if shutil.which("latexmk"):
        return {
            "ok": True,
            "selected_backend": "latexmk",
            "reason": "latexmk_found_on_current_path",
            "image": "",
        }
    if shutil.which("tectonic"):
        return {
            "ok": True,
            "selected_backend": "tectonic",
            "reason": "tectonic_found_on_current_path",
            "image": "",
        }
    if not latex_settings.allow_docker_fallback:
        return {
            "ok": False,
            "selected_backend": "none",
            "reason": "no_local_tex_and_docker_fallback_disabled",
            "image": latex_settings.docker_image,
        }

    from ..runtime.container_detection import is_running_in_container

    image = latex_settings.docker_image
    if is_running_in_container():
        return {
            "ok": False,
            "selected_backend": "none",
            "reason": "container_missing_local_tex_toolchain",
            "image": image,
        }
    docker_ok, docker_error, docker_details = check_docker_environment(image=image)
    if not docker_ok:
        return {
            "ok": False,
            "selected_backend": "docker",
            "reason": str((docker_details or {}).get("error") or "docker_unavailable"),
            "message": docker_error or "Docker TeX backend is unavailable.",
            "image": image,
            "details": docker_details,
        }

    try:
        probe = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                "sh",
                image,
                "-lc",
                "command -v latexmk && command -v pdflatex && command -v xelatex && command -v bibtex",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "selected_backend": "docker",
            "reason": "docker_tex_probe_timeout",
            "image": image,
            "details": docker_details,
        }
    if probe.returncode != 0:
        return {
            "ok": False,
            "selected_backend": "docker",
            "reason": "docker_tex_commands_missing",
            "message": (probe.stderr or probe.stdout or "").strip()[:1000],
            "image": image,
            "details": docker_details,
        }
    return {
        "ok": True,
        "selected_backend": "docker",
        "reason": "docker_tex_image_verified",
        "image": image,
        "details": docker_details,
    }


class LatexCompileParams(BaseModel):
    tex_path: str = Field(..., description="相对 workspace 的 .tex 文件路径")
    engine: str = Field("pdflatex", pattern="^(pdflatex|xelatex|lualatex)$")
    bibtex: bool = Field(True, description="是否运行 bibtex")
    output_dir: str | None = Field(None, description="可选输出目录，相对 tex 文件目录")
    backend: str = Field(
        "auto",
        pattern="^(auto|latexmk|tectonic|docker|export_only)$",
        description="LaTeX backend；auto 优先本机 latexmk/tectonic，缺失时可回退到允许的 Docker TeX 镜像",
    )
    allow_docker_fallback: bool = Field(False, description="是否允许 Docker TeX fallback；可由 runtime.yaml 默认开启")
    auto_fit_wide_tables: bool = Field(
        False,
        description=(
            "Opt in to rewriting structurally wide standard tabular blocks with resizebox. "
            "Disabled by default because compilation must not mutate a previously audited source artifact."
        ),
    )


class LatexCompileTool(Tool):
    name = "latex_compile"
    description = "编译 .tex 并生成 PDF；auto 优先本机 TeX，宿主缺失时可安全回退到配置的 Docker TeX 镜像。"
    parameters_schema = LatexCompileParams
    timeout_seconds = 1800.0

    def __init__(self, docker_tool: DockerExecTool, latex_settings: LatexSettings | None = None):
        self.docker = docker_tool
        self.latex_settings = latex_settings or LatexSettings()

    def _is_running_in_container(self) -> bool:
        """检测是否在容器内运行（使用共享工具）"""
        from researchos.runtime.container_detection import is_running_in_container

        return is_running_in_container()

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = LatexCompileParams(**kwargs)
        if params.backend == "auto" and self.latex_settings.default_backend != "auto":
            params = params.model_copy(update={"backend": self.latex_settings.default_backend})
        if not params.allow_docker_fallback and self.latex_settings.allow_docker_fallback:
            params = params.model_copy(update={"allow_docker_fallback": True})
        tex_abs = self.docker.policy.resolve_read(params.tex_path)
        if params.auto_fit_wide_tables:
            # The opt-in table transform writes the TeX source. Require an
            # explicit write grant instead of bypassing the workspace policy.
            self.docker.policy.resolve_write(params.tex_path)
        table_layout = _apply_table_layout_policy(tex_abs, enabled=params.auto_fit_wide_tables)
        started_at = _now_iso()
        report_base = _compile_report_base(
            tex_abs=tex_abs,
            workspace=self.docker.policy.workspace_dir,
            params=params,
            started_at=started_at,
        )
        report_base["table_layout"] = table_layout
        backend_checks = detect_latex_backends(allow_docker=params.allow_docker_fallback or params.backend == "docker")
        report_base["requested_backend"] = params.backend
        report_base["selected_backend"] = _select_backend(params, backend_checks)
        report_base["detected_backends"] = backend_checks
        cached = _cached_compile_result_if_redundant(
            self.docker.policy.workspace_dir,
            params=params,
            report_base=report_base,
        )
        if cached is not None:
            return cached

        if params.backend == "export_only":
            report = _finalize_compile_report(
                report_base,
                success=False,
                engine="export_only",
                exit_code=None,
                error="export_only_requested",
            )
            _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
            return ToolResult(
                ok=False,
                content=(
                    "WAITING_ENVIRONMENT: PDF compilation skipped because backend=export_only.\n"
                    f"TeX source is available at {params.tex_path}."
                ),
                error="export_only_requested",
                data={"error": "export_only_requested", "compile_report": report},
            )
        if report_base["selected_backend"] == "latexmk" and shutil.which("latexmk") is None:
            report = _finalize_compile_report(
                report_base,
                success=False,
                engine="latexmk",
                exit_code=None,
                error="waiting_environment_latexmk_missing",
            )
            _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
            return ToolResult(
                ok=False,
                content=(
                    "WAITING_ENVIRONMENT: latexmk is not installed in the current environment.\n"
                    "Install a local TeX distribution and latexmk, or enable/configure the Docker TeX backend, then resume.\n"
                    "Ubuntu/Debian: sudo apt-get install texlive-latex-base texlive-latex-extra "
                    "texlive-fonts-recommended texlive-xetex texlive-lang-chinese latexmk\n"
                    "macOS: install MacTeX or BasicTeX plus latexmk.\n"
                    "Windows: install MiKTeX or TeX Live and ensure latexmk is on PATH.\n"
                    "If a Python import error says `No module named researchos`, do not run "
                    "`pip install researchos` from PyPI; run from the repository root with "
                    "`PYTHONPATH=/path/to/DIG-ResearchOS python -m researchos.cli ...` or install "
                    "this local checkout with `pip install -e .`."
                ),
                error="waiting_environment_latexmk_missing",
                data={"error": "waiting_environment_latexmk_missing", "compile_report": report},
            )
        if report_base["selected_backend"] == "tectonic":
            if shutil.which("tectonic") is None:
                report = _finalize_compile_report(
                    report_base,
                    success=False,
                    engine="tectonic",
                    exit_code=None,
                    error="waiting_environment_tectonic_missing",
                )
                _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
                return ToolResult(
                    ok=False,
                    content=(
                        "WAITING_ENVIRONMENT: tectonic is not installed in the current environment.\n"
                        f"TeX source is available at {params.tex_path}."
                    ),
                    error="waiting_environment_tectonic_missing",
                    data={"error": "waiting_environment_tectonic_missing", "compile_report": report},
                )
            return await self._compile_tectonic(params, report_base=report_base)
        if report_base["selected_backend"] == "docker":
            return await self._compile_docker(params, report_base=report_base)
        return await self._compile_native(params, report_base=report_base)

    async def _compile_tectonic(self, params: LatexCompileParams, *, report_base: dict[str, Any]) -> ToolResult:
        """Use the current environment's tectonic backend."""

        tex_abs = self.docker.policy.resolve_read(params.tex_path)
        tex_dir = tex_abs.parent
        tex_name = tex_abs.name
        cmd = ["tectonic", "--keep-logs", "--keep-intermediates", tex_name]

        _LOG.info("latex_compile_tectonic", tex_path=params.tex_path, cwd=str(tex_dir))
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=tex_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **_new_process_group_kwargs(),
            )
        except OSError as exc:
            raise ToolRuntimeError(self.name, exc) from exc

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            await _terminate_process_group(proc)
            report = _finalize_compile_report(report_base, success=False, engine="tectonic", exit_code=None, error="timeout")
            _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
            return ToolResult(ok=False, content=f"Tectonic compilation timed out after {self.timeout_seconds}s", error="timeout", data={"compile_report": report})

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        content_parts = []
        if out:
            content_parts.append(f"STDOUT:\n{out}")
        if err:
            content_parts.append(f"STDERR:\n{err}")
        content_parts.append(f"EXIT: {proc.returncode}")

        if proc.returncode != 0:
            report = _finalize_compile_report(report_base, success=False, engine="tectonic", exit_code=proc.returncode, error="nonzero_exit")
            _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
            return ToolResult(ok=False, content="\n\n".join(content_parts), error="nonzero_exit", data={"compile_report": report})

        pdf_path = self._expected_pdf_path(tex_abs, params.output_dir)
        if not pdf_path.exists():
            report = _finalize_compile_report(report_base, success=False, engine="tectonic", exit_code=proc.returncode, error="pdf_missing")
            _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
            return ToolResult(ok=False, content=f"Tectonic finished but PDF was not generated: {pdf_path}", error="pdf_missing", data={"compile_report": report})

        pdf_rel = pdf_path.relative_to(self.docker.policy.workspace_dir).as_posix()
        content_parts.append(f"\nPDF: {pdf_rel}")
        report = _finalize_compile_report(report_base, success=True, engine="tectonic", exit_code=proc.returncode, pdf_path=pdf_path)
        _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
        return ToolResult(ok=True, content="\n\n".join(content_parts), data={"pdf_path": pdf_rel, "exit_code": proc.returncode, "compile_report": report})

    async def _compile_native(self, params: LatexCompileParams, *, report_base: dict[str, Any]) -> ToolResult:
        """Use the current environment's latexmk."""
        tex_abs = self.docker.policy.resolve_read(params.tex_path)
        tex_dir = tex_abs.parent
        tex_name = tex_abs.name

        # 构建 latexmk 命令
        cmd = [
            "latexmk",
            f"-{params.engine}",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
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
                **_new_process_group_kwargs(),
            )
        except OSError as exc:
            raise ToolRuntimeError(self.name, exc) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            await _terminate_process_group(proc)
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

    async def _compile_docker(self, params: LatexCompileParams, *, report_base: dict[str, Any]) -> ToolResult:
        """Compile through the configured TeX image and persist normal artifacts.

        ``DockerExecTool`` handles the allowlist, daemon/image checks, network
        isolation and workspace bind mount.  In container-native deployments it
        deliberately executes the current container's TeX toolchain instead of
        attempting Docker-in-Docker, so a slim application image gets a clear
        actionable error instead of an opaque nested-Docker failure.
        """

        tex_abs = self.docker.policy.resolve_read(params.tex_path)
        workspace = self.docker.policy.workspace_dir.resolve()
        try:
            tex_dir_rel = tex_abs.parent.resolve().relative_to(workspace).as_posix()
        except ValueError:
            report = _finalize_compile_report(
                report_base,
                success=False,
                engine="docker",
                exit_code=None,
                error="tex_path_outside_workspace",
            )
            _write_compile_report_for_known_target(workspace, params.tex_path, report)
            return ToolResult(
                ok=False,
                content="LaTeX source must remain within the active workspace for Docker compilation.",
                error="tex_path_outside_workspace",
                data={"compile_report": report},
            )

        if self._is_running_in_container() and shutil.which("latexmk") is None:
            report = _finalize_compile_report(
                report_base,
                success=False,
                engine="docker",
                exit_code=None,
                error="waiting_environment_container_tex_missing",
            )
            _write_compile_report_for_known_target(workspace, params.tex_path, report)
            return ToolResult(
                ok=False,
                content=(
                    "WAITING_ENVIRONMENT: ResearchOS is already running in a container, but that "
                    "container has no latexmk. Rebuild/run the configured ResearchOS image with its TeX "
                    "toolchain, or run ResearchOS on the host so it can start the configured Docker TeX image."
                ),
                error="waiting_environment_container_tex_missing",
                data={"compile_report": report},
            )

        command = [
            "latexmk",
            f"-{params.engine}",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            "-bibtex" if params.bibtex else "-bibtex-",
        ]
        if params.output_dir:
            command.extend(["-outdir", params.output_dir])
        command.append(tex_abs.name)
        container_cwd = "/workspace" if tex_dir_rel in {"", "."} else f"/workspace/{tex_dir_rel}"
        image = self.latex_settings.docker_image
        _LOG.info(
            "latex_compile_docker",
            tex_path=params.tex_path,
            engine=params.engine,
            image=image,
            cwd=container_cwd,
        )
        docker_result = await self.docker.execute(
            image=image,
            command=shlex.join(command),
            cwd=container_cwd,
            timeout_seconds=int(self.timeout_seconds),
            allow_network=False,
            gpu=False,
            env={},
            extra_mounts=[],
        )
        exit_code = docker_result.data.get("exit_code") if isinstance(docker_result.data, dict) else None
        if not docker_result.ok:
            error = str(docker_result.error or "docker_compile_failed")
            report = _finalize_compile_report(
                report_base,
                success=False,
                engine="docker",
                exit_code=exit_code if isinstance(exit_code, int) else None,
                error=error,
            )
            _write_compile_report_for_known_target(workspace, params.tex_path, report)
            return ToolResult(
                ok=False,
                content=docker_result.content,
                error=error,
                data={"compile_report": report, "docker": docker_result.data},
            )

        pdf_path = self._expected_pdf_path(tex_abs, params.output_dir)
        if not pdf_path.exists():
            report = _finalize_compile_report(
                report_base,
                success=False,
                engine="docker",
                exit_code=exit_code if isinstance(exit_code, int) else 0,
                error="pdf_missing",
            )
            _write_compile_report_for_known_target(workspace, params.tex_path, report)
            return ToolResult(
                ok=False,
                content=(
                    "Docker LaTeX command completed but did not create the expected PDF: "
                    f"{pdf_path.relative_to(workspace)}\n\n{docker_result.content}"
                ),
                error="pdf_missing",
                data={"compile_report": report, "docker": docker_result.data},
            )

        pdf_rel = pdf_path.relative_to(workspace).as_posix()
        report = _finalize_compile_report(
            report_base,
            success=True,
            engine="docker",
            exit_code=exit_code if isinstance(exit_code, int) else 0,
            pdf_path=pdf_path,
        )
        _write_compile_report_for_known_target(workspace, params.tex_path, report)
        return ToolResult(
            ok=True,
            content=docker_result.content + f"\n\nPDF: {pdf_rel}",
            data={
                "pdf_path": pdf_rel,
                "exit_code": exit_code if isinstance(exit_code, int) else 0,
                "compile_report": report,
                "docker": docker_result.data,
            },
        )

    @staticmethod
    def _expected_pdf_path(tex_abs: Path, output_dir: str | None) -> Path:
        pdf_name = tex_abs.with_suffix(".pdf").name
        if output_dir:
            return tex_abs.parent / output_dir / pdf_name
        return tex_abs.with_suffix(".pdf")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_TABLE_ENV_RE = re.compile(r"\\begin\{table\*?\}(?P<body>.*?)\\end\{table\*?\}", re.DOTALL)
_TABULAR_RE = re.compile(
    r"\\begin\{tabular\}(?:\[[^\]]*\])?\{(?P<spec>[^}]*)\}(?P<body>.*?)\\end\{tabular\}",
    re.DOTALL,
)


def inspect_latex_table_layout(tex: str) -> dict[str, Any]:
    """Inspect standard tables without changing LaTeX source."""

    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    if _template_disallows_resizebox(tex):
        return {
            "template_allows_resizebox": False,
            "wide_table_count": 0,
            "candidates": [],
            "skipped": [{"reason": "template_disallows_resizebox"}],
        }
    for table_index, table_match in enumerate(_TABLE_ENV_RE.finditer(tex), start=1):
        table_body = table_match.group("body")
        if "\\resizebox" in table_body or "\\adjustbox" in table_body:
            skipped.append({"table": str(table_index), "reason": "already_scaled"})
            continue
        if any(marker in table_body for marker in ("\\begin{tabularx}", "\\begin{longtable}", "\\begin{supertabular}")):
            skipped.append({"table": str(table_index), "reason": "nonstandard_table_environment"})
            continue
        tabular_match = _TABULAR_RE.search(table_body)
        if tabular_match is None:
            continue
        column_count = _tabular_column_count(tabular_match.group("spec"))
        row_width = _max_tabular_row_columns(tabular_match.group("body"))
        if max(column_count, row_width) >= 6:
            candidates.append(
                {
                    "table": table_index,
                    "column_count": column_count,
                    "row_column_count": row_width,
                }
            )
        else:
            skipped.append({"table": str(table_index), "reason": "not_structurally_wide"})
    return {
        "template_allows_resizebox": True,
        "wide_table_count": len(candidates),
        "candidates": candidates,
        "skipped": skipped,
    }


def apply_safe_resizebox_to_wide_tables(tex: str) -> tuple[str, dict[str, Any]]:
    """Wrap only wide ordinary ``tabular`` blocks in ``\\resizebox``.

    The transform intentionally avoids ``tabularx``, ``longtable``, existing
    wrappers, and templates that explicitly prohibit table resizing.  It is a
    layout fallback, not a general LaTeX rewriter.
    """

    inspection = inspect_latex_table_layout(tex)
    report: dict[str, Any] = {
        "auto_fit_enabled": True,
        "template_allows_resizebox": inspection["template_allows_resizebox"],
        "wide_table_count": inspection["wide_table_count"],
        "resizebox_inserted": 0,
        "skipped": inspection["skipped"],
    }
    if not inspection["template_allows_resizebox"] or not inspection["candidates"]:
        return tex, report

    candidate_numbers = {int(item["table"]) for item in inspection["candidates"]}
    pieces: list[str] = []
    cursor = 0
    for table_index, table_match in enumerate(_TABLE_ENV_RE.finditer(tex), start=1):
        pieces.append(tex[cursor:table_match.start()])
        whole = table_match.group(0)
        if table_index in candidate_numbers:
            tabular_match = _TABULAR_RE.search(whole)
            if tabular_match is not None:
                tabular = tabular_match.group(0)
                wrapped = "\\resizebox{\\textwidth}{!}{%\n" + tabular + "%\n}"
                whole = whole[:tabular_match.start()] + wrapped + whole[tabular_match.end():]
                report["resizebox_inserted"] = int(report["resizebox_inserted"]) + 1
        pieces.append(whole)
        cursor = table_match.end()
    pieces.append(tex[cursor:])
    transformed = "".join(pieces)
    if report["resizebox_inserted"] and not _has_graphicx_package(transformed):
        transformed = _ensure_graphicx_package(transformed)
        report["graphicx_added"] = True
    else:
        report["graphicx_added"] = False
    return transformed, report


def _apply_table_layout_policy(tex_path: Path, *, enabled: bool) -> dict[str, Any]:
    """Apply the opt-out-safe table transform and return its audit record."""

    if not enabled:
        return {
            "auto_fit_enabled": False,
            "wide_table_count": 0,
            "resizebox_inserted": 0,
            "skipped": [{"reason": "disabled_by_request"}],
        }
    try:
        original = tex_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {
            "auto_fit_enabled": True,
            "wide_table_count": 0,
            "resizebox_inserted": 0,
            "skipped": [{"reason": f"source_unreadable:{exc}"}],
        }
    transformed, report = apply_safe_resizebox_to_wide_tables(original)
    if transformed != original:
        tex_path.write_text(transformed, encoding="utf-8")
        report["source_updated"] = True
    else:
        report["source_updated"] = False
    return report


def _template_disallows_resizebox(tex: str) -> bool:
    lowered = tex.casefold()
    return "aaai" in lowered and ("\\usepackage{aaai" in lowered or "\\documentclass{aaai" in lowered)


def _tabular_column_count(spec: str) -> int:
    cleaned = re.sub(r"@[^{]*\{[^}]*\}|>[^{]*\{[^}]*\}|<[^{]*\{[^}]*\}", "", spec or "")
    count = 0
    index = 0
    while index < len(cleaned):
        char = cleaned[index]
        if char in "lcrXSD":
            count += 1
        elif char in "pmb" and index + 1 < len(cleaned) and cleaned[index + 1] == "{":
            count += 1
        index += 1
    return count


def _max_tabular_row_columns(body: str) -> int:
    rows = re.split(r"\\\\(?:\[[^\]]*\])?", body or "")
    return max((row.count("&") + 1 for row in rows if row.strip()), default=0)


def _has_graphicx_package(tex: str) -> bool:
    return bool(re.search(r"\\usepackage(?:\[[^\]]*\])?\{[^}]*graphicx[^}]*\}", tex))


def _ensure_graphicx_package(tex: str) -> str:
    documentclass = re.search(r"\\documentclass(?:\[[^\]]*\])?\{[^}]+\}", tex)
    if documentclass is None:
        return "\\usepackage{graphicx}\n" + tex
    return tex[:documentclass.end()] + "\n\\usepackage{graphicx}" + tex[documentclass.end():]


def _select_backend(params: LatexCompileParams, backend_checks: list[dict[str, Any]]) -> str:
    available = {
        str(item.get("name")): bool(item.get("available"))
        for item in backend_checks
        if isinstance(item, dict)
    }
    if params.backend != "auto":
        return params.backend
    if available.get("latexmk"):
        return "latexmk"
    if available.get("tectonic"):
        return "tectonic"
    if params.allow_docker_fallback and available.get("docker"):
        return "docker"
    return "latexmk"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


_COMPILE_GENERATED_SUFFIXES = {
    ".dvi",
    ".aux",
    ".bbl",
    ".bcf",
    ".blg",
    ".fls",
    ".fdb_latexmk",
    ".ilg",
    ".ind",
    ".lof",
    ".log",
    ".lot",
    ".nav",
    ".out",
    ".run.xml",
    ".snm",
    ".synctex.gz",
    ".toc",
    ".vrb",
    ".xdv",
}

_COMPILE_SOURCE_SUFFIXES = {
    ".bbx",
    ".bib",
    ".bst",
    ".cbx",
    ".cfg",
    ".cls",
    ".clo",
    ".csv",
    ".dat",
    ".def",
    ".eps",
    ".fd",
    ".jpeg",
    ".jpg",
    ".ldf",
    ".pdf",
    ".png",
    ".sty",
    ".svg",
    ".tex",
    ".tikz",
    ".txt",
}


def _compile_dependency_fingerprint(workspace: Path, tex_abs: Path) -> dict[str, Any]:
    """Fingerprint non-generated files next to the TeX entry point.

    LaTeX output depends on more than `main.tex`: bibliography, local style
    files, included section files, and figures can all change without the main
    source hash changing. For a submission bundle, hashing every non-generated
    file in the bundle is conservative and cheap.
    """

    base_dir = tex_abs.parent
    files: list[dict[str, Any]] = []
    report_rel = _compile_report_target_for_tex(_rel_to_workspace(workspace, tex_abs))
    report_abs = (workspace / report_rel).resolve() if report_rel else None
    if base_dir.exists():
        for path in sorted(item for item in base_dir.rglob("*") if item.is_file()):
            if report_abs is not None and path.resolve() == report_abs:
                continue
            if _is_generated_compile_artifact(path, tex_abs):
                continue
            if not _is_latex_source_dependency(path):
                continue
            try:
                rel = path.relative_to(workspace).as_posix()
            except ValueError:
                rel = path.as_posix()
            files.append(
                {
                    "path": rel,
                    "sha256": _sha256_file(path),
                    "size": path.stat().st_size,
                }
            )
    digest_payload = json.dumps(
        [{"path": item["path"], "sha256": item["sha256"], "size": item["size"]} for item in files],
        ensure_ascii=False,
        sort_keys=True,
    )
    return {
        "semantics": "latex_compile_dependency_fingerprint",
        "scope": _rel_to_workspace(workspace, base_dir),
        "hash": hashlib.sha256(digest_payload.encode("utf-8")).hexdigest(),
        "file_count": len(files),
        "files": files,
    }


def _is_generated_compile_artifact(path: Path, tex_abs: Path | None = None) -> bool:
    name = path.name
    suffix = path.suffix.lower()
    if tex_abs is not None and suffix == ".pdf" and path.parent == tex_abs.parent and path.stem == tex_abs.stem:
        return True
    if suffix in _COMPILE_GENERATED_SUFFIXES:
        return True
    return any(name.lower().endswith(item) for item in _COMPILE_GENERATED_SUFFIXES if item.startswith("."))


def _is_latex_source_dependency(path: Path) -> bool:
    return path.suffix.lower() in _COMPILE_SOURCE_SUFFIXES


def _rel_to_workspace(workspace: Path, path: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return path.as_posix()


def _compile_report_base(
    *,
    tex_abs: Path,
    workspace: Path,
    params: LatexCompileParams,
    started_at: str,
) -> dict[str, Any]:
    tex_rel = tex_abs.relative_to(workspace).as_posix()
    log_path = tex_abs.with_suffix(".log")
    dependency_fingerprint = _compile_dependency_fingerprint(workspace, tex_abs)
    return {
        "_workspace": workspace.as_posix(),
        "version": "1.0",
        "semantics": "latex_compile_attempt_report",
        "tex_path": tex_rel,
        "requested_engine": params.engine,
        "requested_backend": params.backend,
        "selected_backend": params.backend,
        "detected_backends": [],
        "bibtex": params.bibtex,
        "output_dir": params.output_dir,
        "started_at": started_at,
        "main_tex_sha256": _sha256_file(tex_abs) if tex_abs.exists() else "",
        "main_tex_mtime": tex_abs.stat().st_mtime if tex_abs.exists() else 0,
        "dependency_fingerprint": dependency_fingerprint,
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
                    "selected_backend": base.get("selected_backend") or engine,
                    "requested_engine": base.get("requested_engine"),
                    "requested_backend": base.get("requested_backend"),
                    "bibtex": base.get("bibtex"),
                    "output_dir": base.get("output_dir"),
                    "main_tex_sha256": base.get("main_tex_sha256"),
                    "dependency_fingerprint_hash": (base.get("dependency_fingerprint") or {}).get("hash"),
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
    report["attempt_count"] = len(report.get("attempts") or [])
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
    report = _merge_compile_report_attempts(report_path, report)
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


def _load_compile_report(workspace: Path, tex_path: str) -> tuple[Path | None, dict[str, Any]]:
    report_rel = _compile_report_target_for_tex(tex_path)
    if not report_rel:
        return None, {}
    report_path = workspace / report_rel
    if not report_path.exists() or report_path.stat().st_size <= 0:
        return report_path, {}
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return report_path, {}
    return report_path, data if isinstance(data, dict) else {}


def _cached_compile_result_if_redundant(
    workspace: Path,
    *,
    params: LatexCompileParams,
    report_base: dict[str, Any],
) -> ToolResult | None:
    """Avoid rerunning identical LaTeX compiles.

    If the source hash and compile options are unchanged, a previous successful
    PDF can be reused and a previous source-level failure should not be repeated
    until the TeX changes. Environment wait failures are not cached because the
    user may install TeX/latexmk without editing the file.
    """

    _, existing = _load_compile_report(workspace, params.tex_path)
    if not existing:
        return None
    if not _same_compile_input(existing, report_base):
        return None

    pdf_rel = str(existing.get("pdf_path") or "").strip()
    if bool(existing.get("success")) and pdf_rel and (workspace / pdf_rel).exists():
        cache_ok, cache_err = _cached_success_artifacts_match(workspace, existing, report_base)
        if not cache_ok:
            _LOG.info("latex_cached_success_invalidated", reason=cache_err, tex_path=params.tex_path)
            return None
        return ToolResult(
            ok=True,
            content=(
                "LaTeX compile skipped: existing PDF matches unchanged main.tex. "
                f"PDF: {pdf_rel}"
            ),
            data={
                "pdf_path": pdf_rel,
                "cached": True,
                "compile_report": existing,
            },
        )

    attempts_for_hash = _attempts_for_compile_input(existing, report_base)
    max_attempts = _max_compile_attempts_for_tex(params.tex_path)
    if len(attempts_for_hash) >= max_attempts:
        return ToolResult(
            ok=False,
            content=(
                "LaTeX compile attempt limit reached for unchanged main.tex "
                f"({len(attempts_for_hash)}/{max_attempts}). Edit the TeX source before retrying."
            ),
            error="compile_attempt_limit_exceeded",
            data={
                "cached": True,
                "compile_report": existing,
                "attempt_count_for_current_tex": len(attempts_for_hash),
                "max_compile_attempts": max_attempts,
            },
        )

    error = str(existing.get("error") or "")
    if error in {"nonzero_exit", "pdf_missing", "timeout"} and not _existing_pdf_can_be_revalidated(workspace, existing, report_base):
        return ToolResult(
            ok=False,
            content=(
                "LaTeX compile skipped: previous compile failed for the same main.tex hash "
                f"with error={error}. Edit the TeX source before retrying."
            ),
            error="cached_compile_failure_same_tex",
            data={
                "cached": True,
                "compile_report": existing,
                "attempt_count_for_current_tex": len(attempts_for_hash),
                "max_compile_attempts": max_attempts,
            },
        )
    return None


def _existing_pdf_can_be_revalidated(workspace: Path, report: dict[str, Any], base: dict[str, Any]) -> bool:
    tex_rel = str(report.get("tex_path") or base.get("tex_path") or "").strip()
    if not tex_rel:
        return False
    tex_path = workspace / tex_rel
    pdf_rel = str(report.get("pdf_path") or "").strip()
    pdf_path = workspace / pdf_rel if pdf_rel else tex_path.with_suffix(".pdf")
    return tex_path.exists() and pdf_path.exists() and pdf_path.stat().st_mtime >= tex_path.stat().st_mtime


def _cached_success_artifacts_match(
    workspace: Path,
    report: dict[str, Any],
    base: dict[str, Any],
) -> tuple[bool, str | None]:
    if report.get("semantics") != "latex_compile_attempt_report":
        return False, "semantics"
    tex_rel = str(report.get("tex_path") or "").strip()
    pdf_rel = str(report.get("pdf_path") or "").strip()
    log_rel = str(report.get("log_path") or "").strip()
    if not tex_rel or not pdf_rel or not log_rel:
        return False, "missing tex/pdf/log path"
    tex_path = workspace / tex_rel
    pdf_path = workspace / pdf_rel
    log_path = workspace / log_rel
    for path, label in ((tex_path, "tex"), (pdf_path, "pdf"), (log_path, "log")):
        if not path.exists():
            return False, f"{label} missing"
    if str(report.get("main_tex_sha256") or "") != str(base.get("main_tex_sha256") or ""):
        return False, "main_tex_sha256"
    if _dependency_hash(report) != _dependency_hash(base):
        return False, "dependency_fingerprint"
    if str(report.get("pdf_sha256") or "") != _sha256_file(pdf_path):
        return False, "pdf_sha256"
    log_hash = str(report.get("log_sha256") or "")
    if not log_hash or log_hash != _sha256_file(log_path):
        return False, "log_sha256"
    if int(report.get("pdf_size") or 0) != pdf_path.stat().st_size:
        return False, "pdf_size"
    if int(report.get("log_size") or 0) != log_path.stat().st_size:
        return False, "log_size"
    if float(report.get("pdf_mtime") or 0) < tex_path.stat().st_mtime:
        return False, "pdf_mtime"
    if float(report.get("log_mtime") or 0) < tex_path.stat().st_mtime:
        return False, "log_mtime"
    attempts = report.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return False, "attempts"
    last_attempt = attempts[-1]
    if not isinstance(last_attempt, dict) or last_attempt.get("success") is not True:
        return False, "last_attempt"
    return True, None


def _same_compile_input(report: dict[str, Any], base: dict[str, Any]) -> bool:
    return (
        str(report.get("main_tex_sha256") or "") == str(base.get("main_tex_sha256") or "")
        and _dependency_hash(report) == _dependency_hash(base)
        and str(report.get("requested_engine") or "") == str(base.get("requested_engine") or "")
        and str(report.get("selected_backend") or report.get("engine") or "") == str(
            base.get("selected_backend") or base.get("engine") or ""
        )
        and bool(report.get("bibtex")) == bool(base.get("bibtex"))
        and str(report.get("output_dir") or "") == str(base.get("output_dir") or "")
    )


def _dependency_hash(report: dict[str, Any]) -> str:
    fingerprint = report.get("dependency_fingerprint")
    if isinstance(fingerprint, dict):
        return str(fingerprint.get("hash") or "")
    return str(report.get("dependency_fingerprint_hash") or "")


def _attempts_for_compile_input(report: dict[str, Any], base: dict[str, Any]) -> list[dict[str, Any]]:
    attempts = report.get("attempts") if isinstance(report.get("attempts"), list) else []
    current_hash = str(base.get("main_tex_sha256") or "")
    current_dependency_hash = _dependency_hash(base)
    current_engine = str(base.get("requested_engine") or "")
    current_bibtex = bool(base.get("bibtex"))
    current_output_dir = str(base.get("output_dir") or "")
    matches: list[dict[str, Any]] = []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        attempt_hash = str(attempt.get("main_tex_sha256") or report.get("main_tex_sha256") or "")
        attempt_dependency_hash = str(
            attempt.get("dependency_fingerprint_hash")
            or _dependency_hash(report)
            or ""
        )
        attempt_engine = str(attempt.get("requested_engine") or attempt.get("engine") or report.get("requested_engine") or "")
        attempt_backend = str(attempt.get("selected_backend") or attempt.get("engine") or report.get("selected_backend") or report.get("engine") or "")
        current_backend = str(base.get("selected_backend") or base.get("engine") or "")
        attempt_bibtex = bool(attempt.get("bibtex", report.get("bibtex")))
        attempt_output_dir = str(attempt.get("output_dir") or report.get("output_dir") or "")
        if _is_environment_compile_error(str(attempt.get("error") or report.get("error") or "")):
            continue
        if (
            attempt_hash == current_hash
            and attempt_dependency_hash == current_dependency_hash
            and attempt_engine == current_engine
            and attempt_backend == current_backend
            and attempt_bibtex == current_bibtex
            and attempt_output_dir == current_output_dir
        ):
            matches.append(attempt)
    return matches


def _is_environment_compile_error(error: str) -> bool:
    return error in {
        "waiting_environment",
        "waiting_environment_latexmk_missing",
    } or error.startswith("waiting_environment_")


def _max_compile_attempts_for_tex(tex_path: str) -> int:
    default = 10 if tex_path.strip().lstrip("./") == "submission/bundle/main.tex" else 4
    try:
        from ..runtime.agent_params import get_agent_params

        params = get_agent_params("submission")
        return max(1, int(params.get("max_compile_attempts") or default))
    except Exception:
        return default


def _merge_compile_report_attempts(report_path: Path, report: dict[str, Any]) -> dict[str, Any]:
    if not report_path.exists() or report_path.stat().st_size <= 0:
        return report
    try:
        existing = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return report
    if not isinstance(existing, dict):
        return report
    old_attempts = existing.get("attempts") if isinstance(existing.get("attempts"), list) else []
    new_attempts = report.get("attempts") if isinstance(report.get("attempts"), list) else []
    merged = dict(report)
    merged["attempts"] = [*old_attempts, *new_attempts]
    merged["attempt_count"] = len(merged["attempts"])
    return merged
