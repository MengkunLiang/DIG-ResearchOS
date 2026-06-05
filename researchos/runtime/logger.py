from __future__ import annotations

import logging
from pathlib import Path
import json
from typing import Any

try:
    import structlog
except ModuleNotFoundError:  # pragma: no cover - 是否安装取决于环境
    structlog = None


class _StdlibStructuredLogger:
    """在没有 structlog 时模拟最小 structured logger 接口。"""

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def debug(self, event: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        self._log(logging.INFO, event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, event, **kwargs)

    def exception(self, event: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, event, exc_info=True, **kwargs)

    def _log(self, level: int, event: str, *, exc_info: bool = False, **kwargs: Any) -> None:
        if kwargs:
            payload = json.dumps(kwargs, ensure_ascii=False, default=str)
            message = f"{event} {payload}"
        else:
            message = event
        self._logger.log(level, message, exc_info=exc_info)


def _suppress_noisy_library_loggers() -> None:
    """Keep provider SDK INFO logs out of CLI and human timeline logs."""

    for name in (
        "LiteLLM",
        "litellm",
        "litellm.utils",
        "litellm.litellm_core_utils",
        "httpx",
        "httpcore",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    _suppress_noisy_library_loggers()
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
        return _StdlibStructuredLogger(logging.getLogger(name))
    return structlog.get_logger(name)


def configure_file_logging(log_path: Path, level: str = "INFO") -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    resolved = log_path.resolve()
    root = logging.getLogger()
    for existing in root.handlers:
        if not isinstance(existing, logging.FileHandler):
            continue
        try:
            if Path(existing.baseFilename).resolve() == resolved:
                return
        except Exception:
            continue
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.addHandler(handler)
