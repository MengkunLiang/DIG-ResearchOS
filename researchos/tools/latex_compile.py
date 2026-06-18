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
        cached = _cached_compile_result_if_redundant(
            self.docker.policy.workspace_dir,
            params=params,
            report_base=report_base,
        )
        if cached is not None:
            return cached

        # 容器内模式或宿主机已有 TeX：直接调用 latexmk。
        if self._is_running_in_container() or shutil.which("latexmk"):
            if shutil.which("latexmk") is None:
                report = _finalize_compile_report(report_base, success=False, engine="native", exit_code=None)
                _write_compile_report_for_known_target(self.docker.policy.workspace_dir, params.tex_path, report)
                return ToolResult(
                    ok=False,
                    content=(
                        "WAITING_ENVIRONMENT: latexmk is not installed in the current container/native environment.\n"
                        "Fix by installing TeX/latexmk in the active environment or by using the ResearchOS Docker image. "
                        "If a Python import error says `No module named researchos`, do not run `pip install researchos` "
                        "from PyPI; run from the repository root with `PYTHONPATH=/path/to/DIG-ResearchOS python -m researchos.cli ...` "
                        "or install this local checkout with `pip install -e .`."
                    ),
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
            error_code = _classify_docker_compile_error(result)
            if error_code in {
                "docker_command_not_found",
                "docker_daemon_unavailable",
                "docker_image_missing",
                "image_not_allowed",
                "docker_entrypoint_misconfigured",
                "researchos_module_missing",
            }:
                content = (
                    "WAITING_ENVIRONMENT: Docker/LaTeX compile environment unavailable.\n"
                    "If the Docker image reports `No module named researchos`, rebuild/install the local ResearchOS checkout "
                    "in the image; do not install an unrelated PyPI package named researchos.\n\n"
                    + result.content
                )
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
                    "requested_engine": base.get("requested_engine"),
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
    user may install TeX/Docker without editing the file.
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


def _classify_docker_compile_error(result: ToolResult) -> str:
    error = str(result.error or "")
    content = str(result.content or "")
    lowered = content.casefold()
    if "no module named researchos" in lowered:
        return "researchos_module_missing"
    if "invalid choice: 'bash'" in lowered or "argument command: invalid choice" in lowered:
        return "docker_entrypoint_misconfigured"
    return error


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
        attempt_bibtex = bool(attempt.get("bibtex", report.get("bibtex")))
        attempt_output_dir = str(attempt.get("output_dir") or report.get("output_dir") or "")
        if _is_environment_compile_error(str(attempt.get("error") or report.get("error") or "")):
            continue
        if (
            attempt_hash == current_hash
            and attempt_dependency_hash == current_dependency_hash
            and attempt_engine == current_engine
            and attempt_bibtex == current_bibtex
            and attempt_output_dir == current_output_dir
        ):
            matches.append(attempt)
    return matches


def _is_environment_compile_error(error: str) -> bool:
    return error in {
        "waiting_environment",
        "waiting_environment_docker",
        "waiting_environment_latexmk_missing",
        "docker_command_not_found",
        "docker_daemon_unavailable",
        "docker_image_missing",
        "image_not_allowed",
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
