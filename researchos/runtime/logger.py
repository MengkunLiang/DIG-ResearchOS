from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

try:
    import structlog
except ModuleNotFoundError:  # pragma: no cover - 是否安装取决于环境
    structlog = None


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    # ResearchOS 的结构化日志首选 structlog；但测试环境或最小运行环境里如果没有装，
    # runtime 也不应因此直接不可导入，所以这里提供标准库 logging 的降级路径。
    if structlog is None:
        logging.basicConfig(
            level=getattr(logging, level.upper(), logging.INFO),
            format="%(levelname)s %(name)s %(message)s",
        )
        return

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared = [
        structlog.stdlib.add_log_level,
        timestamper,
    ]
    renderer: structlog.types.Processor
    if json_logs:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            *shared,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(message)s")


def get_logger(name: str) -> Any:
    if structlog is None:
        return logging.getLogger(name)
    return structlog.get_logger(name)


def configure_file_logging(log_path: Path, level: str = "INFO") -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(handler)
