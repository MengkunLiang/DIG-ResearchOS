"""Clone Repository Tool - 克隆外部代码仓库到 workspace。

支持的 URL 格式：
- github:owner/repo - GitHub 仓库
- gitlab:owner/repo - GitLab 仓库
- https://github.com/owner/repo.git - HTTPS URL
- git@github.com:owner/repo.git - SSH URL

安全性：
- 验证 URL 格式
- 限制克隆到 workspace 内
- 防止路径遍历攻击
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import structlog

from .base import Tool, ToolResult

logger = structlog.get_logger(__name__)


class CloneRepoTool(Tool):
    """克隆 Git 仓库到 workspace。"""

    name = "clone_repo"
    description = "Clone a git repository into workspace"

    def __init__(self, policy):
        """初始化工具。

        Args:
            policy: 访问策略，包含 workspace_dir
        """
        self.policy = policy

    async def execute(self, repo_url: str, target_dir: str, branch: str | None = None) -> ToolResult:
        """克隆仓库。

        Args:
            repo_url: 仓库 URL（支持 github:, gitlab:, https:, git@）
            target_dir: 目标目录（相对于 workspace）
            branch: 可选的分支名或 commit hash

        Returns:
            ToolResult 包含成功/失败信息
        """
        try:
            # 1. 验证和规范化 URL
            normalized_url, source_type = self._normalize_url(repo_url)
            if not normalized_url:
                return ToolResult(
                    ok=False,
                    content="",
                    error=f"Invalid repository URL: {repo_url}. "
                          f"Supported formats: github:owner/repo, gitlab:owner/repo, "
                          f"https://..., git@..."
                )

            # 2. 验证目标路径安全性
            workspace_dir = self.policy.workspace_dir
            target_path = self._validate_target_path(workspace_dir, target_dir)
            if not target_path:
                return ToolResult(
                    ok=False,
                    content="",
                    error=f"Invalid target directory: {target_dir}. "
                          f"Must be relative path within workspace."
                )

            # 3. 检查目标目录是否已存在
            if target_path.exists():
                return ToolResult(
                    ok=False,
                    content="",
                    error=f"Target directory already exists: {target_path}"
                )

            # 4. 创建父目录
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # 5. 执行 git clone
            clone_result = self._git_clone(normalized_url, target_path, branch)
            if not clone_result["success"]:
                return ToolResult(
                    ok=False,
                    content="",
                    error=f"Git clone failed: {clone_result['error']}"
                )

            # 6. 返回成功信息
            result_data = {
                "repo_url": repo_url,
                "normalized_url": normalized_url,
                "source_type": source_type,
                "target_path": str(target_path.relative_to(workspace_dir)),
                "branch": branch or "default",
                "commit": clone_result.get("commit", "unknown")
            }

            return ToolResult(
                ok=True,
                content=f"Successfully cloned {repo_url} to {target_path.relative_to(workspace_dir)}",
                data=result_data
            )

        except Exception as e:
            logger.exception("clone_repo failed", repo_url=repo_url, target_dir=target_dir)
            return ToolResult(ok=False, content="", error=f"Unexpected error: {e}")

    def _normalize_url(self, repo_url: str) -> tuple[str | None, str | None]:
        """规范化仓库 URL。

        Args:
            repo_url: 原始 URL

        Returns:
            (normalized_url, source_type) 或 (None, None) 如果无效
        """
        repo_url = repo_url.strip()

        # github:owner/repo
        if repo_url.startswith("github:"):
            path = repo_url[7:]
            if re.match(r'^[\w\-\.]+/[\w\-\.]+$', path):
                return f"https://github.com/{path}.git", "github"

        # gitlab:owner/repo
        elif repo_url.startswith("gitlab:"):
            path = repo_url[7:]
            if re.match(r'^[\w\-\.]+/[\w\-\.]+$', path):
                return f"https://gitlab.com/{path}.git", "gitlab"

        # https://github.com/owner/repo 或 https://github.com/owner/repo.git
        elif repo_url.startswith("https://"):
            if re.match(r'^https://(github\.com|gitlab\.com)/[\w\-\.]+/[\w\-\.]+', repo_url):
                # 确保以 .git 结尾
                if not repo_url.endswith(".git"):
                    repo_url = repo_url.rstrip("/") + ".git"
                source_type = "github" if "github.com" in repo_url else "gitlab"
                return repo_url, source_type

        # git@github.com:owner/repo.git
        elif repo_url.startswith("git@"):
            if re.match(r'^git@(github\.com|gitlab\.com):[\w\-\.]+/[\w\-\.]+\.git$', repo_url):
                source_type = "github" if "github.com" in repo_url else "gitlab"
                return repo_url, source_type

        return None, None

    def _validate_target_path(self, workspace_dir: Path, target_dir: str) -> Path | None:
        """验证目标路径安全性。

        Args:
            workspace_dir: workspace 根目录
            target_dir: 目标目录（相对路径）

        Returns:
            绝对路径 或 None 如果无效
        """
        try:
            # 移除前导斜杠（确保是相对路径）
            target_dir = target_dir.lstrip("/")

            # 解析路径
            target_path = (workspace_dir / target_dir).resolve()

            # 检查是否在 workspace 内（防止路径遍历）
            if not str(target_path).startswith(str(workspace_dir.resolve())):
                logger.warning(
                    "Path traversal attempt detected",
                    workspace_dir=workspace_dir,
                    target_dir=target_dir,
                    resolved=target_path
                )
                return None

            return target_path

        except Exception as e:
            logger.warning("Invalid target path", target_dir=target_dir, error=str(e))
            return None

    def _git_clone(
        self,
        repo_url: str,
        target_path: Path,
        branch: str | None = None
    ) -> dict[str, Any]:
        """执行 git clone。

        Args:
            repo_url: 仓库 URL
            target_path: 目标路径
            branch: 可选的分支名或 commit hash

        Returns:
            {"success": bool, "error": str | None, "commit": str | None}
        """
        try:
            # 构建 git clone 命令
            cmd = ["git", "clone", "--depth", "1"]

            if branch:
                cmd.extend(["--branch", branch])

            cmd.extend([repo_url, str(target_path)])

            # 执行命令
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 分钟超时
                check=False
            )

            if result.returncode != 0:
                return {
                    "success": False,
                    "error": result.stderr or result.stdout or "Unknown error",
                    "commit": None
                }

            # 获取当前 commit hash
            commit = self._get_current_commit(target_path)

            return {
                "success": True,
                "error": None,
                "commit": commit
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Git clone timeout (5 minutes)",
                "commit": None
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "commit": None
            }

    def _get_current_commit(self, repo_path: Path) -> str | None:
        """获取当前 commit hash。

        Args:
            repo_path: 仓库路径

        Returns:
            commit hash 或 None
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
                check=False
            )

            if result.returncode == 0:
                return result.stdout.strip()

        except Exception:
            pass

        return None
