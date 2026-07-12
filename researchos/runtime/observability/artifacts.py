from __future__ import annotations

"""Read-only artifact inspection and stage-before/stage-after comparison."""

from dataclasses import asdict, dataclass
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ArtifactInfo:
    path: str
    status: str
    kind: str
    size_bytes: int = 0
    record_count: int | None = None
    detail: str = ""
    digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def snapshot_artifacts(workspace: Path, paths: Iterable[Path]) -> dict[str, ArtifactInfo]:
    return {relative_path(workspace, path): inspect_artifact(workspace, path) for path in paths}


def relative_path(workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def inspect_artifact(workspace: Path, path: Path, *, required: bool = False) -> ArtifactInfo:
    """Return conservative metadata without asserting semantic validity."""

    rel = relative_path(workspace, path)
    if not path.exists():
        return ArtifactInfo(
            path=rel,
            status="missing" if required else "optional_missing",
            kind=_kind_for(path),
            detail="未找到" if required else "可选输入未提供",
        )
    try:
        if path.is_dir():
            files = [item for item in path.rglob("*") if item.is_file() and not item.name.startswith("_DIR_GUIDE")]
            return ArtifactInfo(
                path=rel,
                status="available",
                kind="directory",
                record_count=len(files),
                detail=f"{len(files)} 个文件",
                digest=_directory_digest(files),
            )
        size = path.stat().st_size
    except OSError as exc:
        return ArtifactInfo(path=rel, status="invalid", kind=_kind_for(path), detail=f"无法读取: {type(exc).__name__}")

    kind = _kind_for(path)
    try:
        record_count, detail = _inspect_by_kind(path, kind)
        return ArtifactInfo(
            path=rel,
            status="available",
            kind=kind,
            size_bytes=size,
            record_count=record_count,
            detail=detail,
            digest=_file_digest(path),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, csv.Error, ValueError) as exc:
        return ArtifactInfo(
            path=rel,
            status="invalid",
            kind=kind,
            size_bytes=size,
            detail=f"无法解析: {type(exc).__name__}",
        )


def compare_artifact(before: ArtifactInfo | None, after: ArtifactInfo) -> str:
    if after.status in {"missing", "optional_missing", "invalid"}:
        return after.status
    if before is None or before.status in {"missing", "optional_missing", "invalid"}:
        return "created"
    if before.digest and after.digest and before.digest == after.digest:
        return "reused"
    return "updated"


def _kind_for(path: Path) -> str:
    if path.is_dir():
        return "directory"
    suffix = path.suffix.lower()
    if path.name.endswith(".jsonl"):
        return "jsonl"
    return {
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".csv": "csv",
        ".pdf": "pdf",
        ".md": "markdown",
        ".tex": "latex",
        ".bib": "bibtex",
    }.get(suffix, "file")


def _inspect_by_kind(path: Path, kind: str) -> tuple[int | None, str]:
    if kind == "jsonl":
        count = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        return count, f"{count} 条记录"
    if kind == "json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return len(data), f"JSON 数组，{len(data)} 项"
        if isinstance(data, dict):
            count = _best_collection_count(data)
            keys = ", ".join(str(key) for key in list(data)[:4])
            suffix = f"；关键字段: {keys}" if keys else ""
            return count, (f"JSON 对象，{count} 项" if count is not None else "JSON 对象") + suffix
        return None, "JSON 标量"
    if kind == "csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader, [])
            count = sum(1 for _ in reader)
        columns = ", ".join(header[:4])
        return count, f"{count} 行；列: {columns}" if columns else f"{count} 行"
    if kind in {"markdown", "latex", "bibtex", "yaml", "file"}:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = len(text.splitlines())
        return lines, f"{lines} 行"
    if kind == "pdf":
        try:
            import fitz  # PyMuPDF is a runtime dependency.

            document = fitz.open(path)
            pages = document.page_count
            document.close()
            return pages, f"{pages} 页"
        except Exception:
            return None, "PDF 可用，页数未读取"
    return None, "可用"


def _best_collection_count(data: dict[str, Any]) -> int | None:
    for key in (
        "papers",
        "candidates",
        "records",
        "runs",
        "claims",
        "rows",
        "items",
        "artifacts",
        "checks",
        "sections",
        "attempts",
    ):
        value = data.get(key)
        if isinstance(value, (list, dict)):
            return len(value)
    return None


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()[:16]


def _directory_digest(files: list[Path]) -> str:
    hasher = hashlib.sha256()
    for path in sorted(files):
        try:
            stat = path.stat()
        except OSError:
            continue
        hasher.update(str(path).encode("utf-8", errors="replace"))
        hasher.update(str(stat.st_size).encode("ascii"))
        hasher.update(str(stat.st_mtime_ns).encode("ascii"))
    return hasher.hexdigest()[:16]
