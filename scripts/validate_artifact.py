#!/usr/bin/env python3
"""
Workspace 产物验证工具

用途：
    校验某个 workspace 中 task 产物的格式和内容。

用法：
    python scripts/validate_artifact.py <workspace_path>
    python scripts/validate_artifact.py /path/to/workspace

参数：
    workspace_path: 要验证的 workspace 路径

前置条件：
    无

示例：
    python scripts/validate_artifact.py ./workspace
    python scripts/validate_artifact.py /tmp/researchos_test
"""

from __future__ import annotations

from researchos.schemas.validator import main


if __name__ == "__main__":
    raise SystemExit(main())
