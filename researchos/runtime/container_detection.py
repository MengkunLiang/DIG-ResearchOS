"""容器环境检测工具

提供统一的容器环境检测逻辑，避免代码重复。
"""

import os
from pathlib import Path


def is_running_in_container() -> bool:
    """检测当前是否在 Docker 容器内运行

    检查以下标志：
    1. /.dockerenv 文件（Docker 标准标志）
    2. /run/.containerenv 文件（Podman 标志）
    3. CONTAINER_ID 环境变量（自定义标志）

    Returns:
        bool: 如果在容器内返回 True，否则返回 False
    """
    # 检查 Docker 标准标志文件
    if Path("/.dockerenv").exists():
        return True

    # 检查 Podman 标志文件
    if Path("/run/.containerenv").exists():
        return True

    # 检查自定义环境变量
    if os.getenv("CONTAINER_ID") is not None:
        return True

    return False
