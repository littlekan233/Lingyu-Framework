from __future__ import annotations

import inspect, logging
from os import environ
from sys import stdout

from loguru import logger


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logger(show_startup_log: bool = True) -> None:
    loglevel = (environ.get("LOGLEVEL") or environ.get("LOG_LEVEL") or "INFO").upper()
    logger.remove()
    logger.add(
        stdout,
        format="[{time:HH:mm:ss}] [<c>{name}</c> | <lvl>{level}</lvl>] {message}",
        level=loglevel,
    )
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
    if show_startup_log:
        logger.success(f"已初始化 logger。日志等级：{loglevel}")
