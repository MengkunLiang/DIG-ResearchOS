"""CLI runner implementations.

当前 runtime 暴露两种模式：
- `CompletePipelineRunner`：走完整状态机；
- `SingleTaskRunner`：只跑单个 task，主要服务于 agent/debug。
"""

from .complete_pipeline import CompletePipelineRunner
from .single_task import SingleTaskRunner


__all__ = ["CompletePipelineRunner", "SingleTaskRunner"]
