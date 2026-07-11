from __future__ import annotations

"""Runtime environment provenance for native and container execution."""

from datetime import datetime, timezone
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from .container_detection import is_running_in_container


def detect_runtime_mode() -> str:
    """Return the user-facing runtime mode."""

    return "docker" if is_running_in_container() else "native"


def collect_runtime_environment(workspace_dir: Path | None = None) -> dict[str, Any]:
    """Collect non-secret runtime provenance.

    This data is diagnostic only. It must not be used by validators to decide
    whether scientific artifacts are valid, because native and Docker mode must
    share the same artifact contracts.
    """

    image_reference = os.getenv("RESEARCHOS_IMAGE") or os.getenv("RESEARCHOS_IMAGE_REFERENCE") or ""
    image_digest = os.getenv("RESEARCHOS_IMAGE_DIGEST") or ""
    payload: dict[str, Any] = {
        "semantics": "researchos_runtime_environment_provenance",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "runtime_mode": detect_runtime_mode(),
        "containerized": is_running_in_container(),
        "researchos_version": _researchos_version(),
        "image_reference": image_reference,
        "image_digest": image_digest,
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "cwd": str(Path.cwd()),
        "workspace_absolute_path": str(workspace_dir.resolve()) if workspace_dir else "",
        "workspace_host_hint": workspace_host_hint(workspace_dir) if workspace_dir else "",
    }
    return payload


def write_runtime_environment(workspace_dir: Path, runtime_dir_name: str = "_runtime") -> Path:
    """Write runtime provenance under the runtime-private workspace directory."""

    runtime_dir = workspace_dir / runtime_dir_name
    runtime_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_dir / "runtime_environment.json"
    path.write_text(
        json.dumps(collect_runtime_environment(workspace_dir), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def workspace_host_hint(workspace_dir: Path | None) -> str:
    """Best-effort host path hint for Docker deployment bind mounts."""

    if workspace_dir is None:
        return ""
    explicit = os.getenv("RESEARCHOS_HOST_WORKSPACE")
    if explicit:
        return explicit
    root = os.getenv("RESEARCHOS_HOST_WORKSPACE_ROOT")
    if root:
        container_root = (os.getenv("RESEARCHOS_WORKSPACE_ROOT") or "/app/workspaces").rstrip("/")
        normalized = workspace_dir.as_posix().rstrip("/")
        if normalized == container_root:
            return str(Path(root))
        prefix = container_root + "/"
        if normalized.startswith(prefix):
            relative_workspace = normalized.removeprefix(prefix)
            return str(Path(root) / relative_workspace)
        try:
            project = workspace_dir.resolve().name
        except OSError:
            project = workspace_dir.name
        return str(Path(root) / project)

    normalized = workspace_dir.as_posix()
    if normalized.startswith("/app/workspaces/"):
        return "./workspaces/" + normalized.removeprefix("/app/workspaces/")
    if normalized == "/app/workspaces":
        return "./workspaces"
    if normalized.startswith("/workspace/"):
        return "./workspace/" + normalized.removeprefix("/workspace/")
    if normalized == "/workspace":
        return "./workspace"
    return str(workspace_dir)


def detect_latex_backends(*, allow_docker: bool = False) -> list[dict[str, Any]]:
    """Detect available LaTeX backends without failing on missing optional tools."""

    backends: list[dict[str, Any]] = []
    for name in ("latexmk", "tectonic"):
        executable = shutil.which(name)
        backends.append(
            {
                "name": name,
                "available": executable is not None,
                "path": executable or "",
                "reason": "found_on_path" if executable else "not_found_on_path",
            }
        )
    docker_path = shutil.which("docker")
    docker_available = False
    docker_reason = "not_allowed"
    if allow_docker:
        if docker_path:
            docker_available = True
            docker_reason = "docker_command_found_not_probed"
        else:
            docker_reason = "docker_not_found_on_path"
    backends.append(
        {
            "name": "docker",
            "available": docker_available,
            "path": docker_path or "",
            "reason": docker_reason,
        }
    )
    backends.append(
        {
            "name": "export_only",
            "available": True,
            "path": "",
            "reason": "tex_artifacts_can_be_written_without_pdf_compile",
        }
    )
    return backends


def command_version(command: str, *args: str, timeout: float = 3.0) -> str:
    """Return a short version string for an optional command."""

    executable = shutil.which(command)
    if not executable:
        return ""
    try:
        proc = subprocess.run(
            [executable, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return executable
    output = (proc.stdout or proc.stderr or "").strip().splitlines()
    if output:
        return output[0][:200]
    return executable


def _researchos_version() -> str:
    try:
        from researchos import __version__

        return str(__version__)
    except Exception:
        return ""
