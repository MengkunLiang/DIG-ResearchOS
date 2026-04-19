from __future__ import annotations

"""ResearchOS 当前可用 agent 的注册中心。"""

from .hello import HelloAgent


# 说明：
# - 现在仓库里只有 HelloAgent。
# - 后续 T1-T9 agent 落地后，统一在这里扩展映射，CLI 与启动检查都从这里读取。
AGENT_REGISTRY = {
    "hello": HelloAgent,
}

