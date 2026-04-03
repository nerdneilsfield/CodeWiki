"""Structured logging configuration for CLI and web entry points."""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog

_THIRD_PARTY_LOGGERS = [
    "httpx",
    "openai",
    "httpcore",
    "pydantic_ai",
    "uvicorn",
    "fastapi",
    "watchfiles",
]


class _CurrentStderrProxy:
    """Resolve stderr at emit time so pytest stream capture doesn't go stale."""

    def _target(self):
        stream = sys.stderr
        if stream is None or getattr(stream, "closed", False):
            return sys.__stderr__
        return stream

    def write(self, data):
        return self._target().write(data)

    def flush(self):
        return self._target().flush()

    def isatty(self):
        return bool(getattr(self._target(), "isatty", lambda: False)())

    @property
    def encoding(self):
        return getattr(self._target(), "encoding", "utf-8")

    def fileno(self):
        return self._target().fileno()


def _shared_processors():
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def _configure_structlog(*, renderer) -> None:
    structlog.configure(
        processors=_shared_processors()
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _configure_root_handler(*, renderer, level: int, stream) -> None:
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=_shared_processors(),
    )
    root = logging.getLogger()
    root.setLevel(level)

    for handler in root.handlers:
        if getattr(handler, "_codewiki_structlog", False):
            stream_handler = cast(logging.StreamHandler, handler)
            stream_handler.setLevel(level)
            stream_handler.setFormatter(formatter)
            stream_handler.stream = stream
            break
    else:
        handler = logging.StreamHandler(stream)
        setattr(handler, "_codewiki_structlog", True)
        handler.setLevel(level)
        handler.setFormatter(formatter)
        root.addHandler(handler)


def configure_cli_logging(verbose: bool = False) -> None:
    """Configure structlog for CLI usage with colored console output."""
    level = logging.DEBUG if verbose else logging.INFO
    renderer = structlog.dev.ConsoleRenderer(colors=True)
    _configure_structlog(renderer=renderer)
    _configure_root_handler(renderer=renderer, level=level, stream=_CurrentStderrProxy())

    codewiki_logger = logging.getLogger("codewiki")
    codewiki_logger.setLevel(level)
    codewiki_logger.propagate = True

    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def configure_web_logging() -> None:
    """Configure structlog for web/worker usage with JSON output."""
    renderer = structlog.processors.JSONRenderer()
    _configure_structlog(renderer=renderer)
    _configure_root_handler(
        renderer=renderer,
        level=logging.INFO,
        stream=_CurrentStderrProxy(),
    )

    codewiki_logger = logging.getLogger("codewiki")
    codewiki_logger.setLevel(logging.INFO)
    codewiki_logger.propagate = True

    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
