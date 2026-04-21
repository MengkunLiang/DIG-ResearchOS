from __future__ import annotations

"""种子材料上传工具。

支持用户上传本地 PDF、代码、数据文件到 workspace，
用于提供种子论文、基线代码、数据集等外部资源。
"""

import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..runtime.errors import ToolRuntimeError
from ..runtime.logger import get_logger
from .base import Tool, ToolResult
from .workspace_policy import WorkspaceAccessPolicy

_LOG = get_logger("upload_seed_materials")


class UploadSeedPdfParams(BaseModel):
    source_path: str = Field(..., description="本地 PDF 文件的绝对路径")
    paper_id: str = Field(..., description="论文标识符（用于命名，如 arxiv_2401.12345）")
    metadata: dict[str, Any] | None = Field(None, description="可选的论文元数据（标题、作者等）")


class UploadSeedDataParams(BaseModel):
    source_path: str = Field(..., description="本地数据文件或目录的绝对路径")
    dataset_name: str = Field(..., description="数据集名称（用于命名）")
    description: str | None = Field(None, description="数据集描述")


class UploadSeedCodeParams(BaseModel):
    source_path: str = Field(..., description="本地代码文件或目录的绝对路径")
    repo_name: str = Field(..., description="代码仓库名称（用于命名）")
    entry_point: str | None = Field(None, description="主入口文件（如 main.py）")


class UploadSeedPdfTool(Tool):
    name = "upload_seed_pdf"
    description = "上传本地 PDF 论文到 workspace/user_seeds/pdfs/ 目录"
    parameters_schema = UploadSeedPdfParams

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = UploadSeedPdfParams(**kwargs)

        # 验证源文件
        source = Path(params.source_path)
        if not source.exists():
            return ToolResult(
                ok=False,
                content=f"源文件不存在: {params.source_path}",
                error="file_not_found",
            )

        if not source.is_file():
            return ToolResult(
                ok=False,
                content=f"源路径不是文件: {params.source_path}",
                error="not_a_file",
            )

        if source.suffix.lower() != ".pdf":
            return ToolResult(
                ok=False,
                content=f"文件不是 PDF 格式: {source.suffix}",
                error="invalid_format",
            )

        # 创建目标目录
        target_dir = self.policy.workspace_dir / "user_seeds" / "pdfs"
        target_dir.mkdir(parents=True, exist_ok=True)

        # 复制文件
        target_file = target_dir / f"{params.paper_id}.pdf"
        try:
            shutil.copy2(source, target_file)
        except Exception as exc:
            raise ToolRuntimeError(self.name, exc) from exc

        _LOG.info(
            "upload_seed_pdf",
            source=str(source),
            target=str(target_file.relative_to(self.policy.workspace_dir)),
            paper_id=params.paper_id,
        )

        # 保存元数据（如果提供）
        if params.metadata:
            import json

            metadata_file = target_dir / f"{params.paper_id}.json"
            metadata_file.write_text(json.dumps(params.metadata, indent=2, ensure_ascii=False))

        return ToolResult(
            ok=True,
            content=f"PDF 已上传: user_seeds/pdfs/{params.paper_id}.pdf",
            data={
                "pdf_path": f"user_seeds/pdfs/{params.paper_id}.pdf",
                "paper_id": params.paper_id,
                "size_bytes": target_file.stat().st_size,
            },
        )


class UploadSeedDataTool(Tool):
    name = "upload_seed_data"
    description = "上传本地数据集到 workspace/user_seeds/data/ 目录"
    parameters_schema = UploadSeedDataParams

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = UploadSeedDataParams(**kwargs)

        # 验证源路径
        source = Path(params.source_path)
        if not source.exists():
            return ToolResult(
                ok=False,
                content=f"源路径不存在: {params.source_path}",
                error="path_not_found",
            )

        # 创建目标目录
        target_dir = self.policy.workspace_dir / "user_seeds" / "data" / params.dataset_name
        target_dir.mkdir(parents=True, exist_ok=True)

        # 复制文件或目录
        try:
            if source.is_file():
                target_file = target_dir / source.name
                shutil.copy2(source, target_file)
                copied_items = [target_file.name]
            else:
                # 复制目录内容
                copied_items = []
                for item in source.rglob("*"):
                    if item.is_file():
                        rel_path = item.relative_to(source)
                        target_file = target_dir / rel_path
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, target_file)
                        copied_items.append(str(rel_path))
        except Exception as exc:
            raise ToolRuntimeError(self.name, exc) from exc

        _LOG.info(
            "upload_seed_data",
            source=str(source),
            target=str(target_dir.relative_to(self.policy.workspace_dir)),
            dataset_name=params.dataset_name,
            file_count=len(copied_items),
        )

        # 保存描述（如果提供）
        if params.description:
            readme_file = target_dir / "README.txt"
            readme_file.write_text(params.description, encoding="utf-8")

        return ToolResult(
            ok=True,
            content=f"数据集已上传: user_seeds/data/{params.dataset_name}/ ({len(copied_items)} 个文件)",
            data={
                "data_path": f"user_seeds/data/{params.dataset_name}",
                "dataset_name": params.dataset_name,
                "file_count": len(copied_items),
                "files": copied_items[:10],  # 最多显示前10个文件
            },
        )


class UploadSeedCodeTool(Tool):
    name = "upload_seed_code"
    description = "上传本地代码到 workspace/user_seeds/code/ 目录"
    parameters_schema = UploadSeedCodeParams

    def __init__(self, policy: WorkspaceAccessPolicy):
        self.policy = policy

    async def execute(self, **kwargs: Any) -> ToolResult:
        params = UploadSeedCodeParams(**kwargs)

        # 验证源路径
        source = Path(params.source_path)
        if not source.exists():
            return ToolResult(
                ok=False,
                content=f"源路径不存在: {params.source_path}",
                error="path_not_found",
            )

        # 创建目标目录
        target_dir = self.policy.workspace_dir / "user_seeds" / "code" / params.repo_name
        target_dir.mkdir(parents=True, exist_ok=True)

        # 复制文件或目录
        try:
            if source.is_file():
                target_file = target_dir / source.name
                shutil.copy2(source, target_file)
                copied_items = [target_file.name]
            else:
                # 复制目录内容（排除常见的临时文件和缓存）
                exclude_patterns = {
                    "__pycache__",
                    ".git",
                    ".pytest_cache",
                    ".mypy_cache",
                    "*.pyc",
                    ".DS_Store",
                }
                copied_items = []
                for item in source.rglob("*"):
                    # 跳过排除的模式
                    if any(pattern in item.parts for pattern in exclude_patterns):
                        continue
                    if item.suffix == ".pyc":
                        continue

                    if item.is_file():
                        rel_path = item.relative_to(source)
                        target_file = target_dir / rel_path
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, target_file)
                        copied_items.append(str(rel_path))
        except Exception as exc:
            raise ToolRuntimeError(self.name, exc) from exc

        _LOG.info(
            "upload_seed_code",
            source=str(source),
            target=str(target_dir.relative_to(self.policy.workspace_dir)),
            repo_name=params.repo_name,
            file_count=len(copied_items),
        )

        # 保存入口点信息（如果提供）
        if params.entry_point:
            readme_file = target_dir / "ENTRY_POINT.txt"
            readme_file.write_text(params.entry_point, encoding="utf-8")

        return ToolResult(
            ok=True,
            content=f"代码已上传: user_seeds/code/{params.repo_name}/ ({len(copied_items)} 个文件)",
            data={
                "code_path": f"user_seeds/code/{params.repo_name}",
                "repo_name": params.repo_name,
                "file_count": len(copied_items),
                "entry_point": params.entry_point,
                "files": copied_items[:10],  # 最多显示前10个文件
            },
        )
