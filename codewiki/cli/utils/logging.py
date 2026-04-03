"""Logging utilities for CLI output built on the shared structlog setup."""

from __future__ import annotations

from datetime import datetime

import structlog


class CLILogger:
    """Thin CLI facade over structlog with the existing call sites' API."""

    def __init__(self, verbose: bool = False, name: str = "codewiki.cli"):
        self.verbose = verbose
        self.start_time = datetime.now()
        self._logger = structlog.get_logger(name)

    def debug(self, message: str) -> None:
        if self.verbose:
            self._logger.debug(message)

    def info(self, message: str) -> None:
        self._logger.info(message)

    def success(self, message: str) -> None:
        self._logger.info(f"SUCCESS {message}")

    def warning(self, message: str) -> None:
        self._logger.warning(f"WARNING {message}")

    def error(self, message: str) -> None:
        self._logger.error(f"ERROR {message}")

    def step(self, message: str, step: int | None = None, total: int | None = None) -> None:
        prefix = f"[{step}/{total}] " if step is not None and total is not None else ""
        self._logger.info(f"{prefix}{message}")

    def elapsed_time(self) -> str:
        elapsed = datetime.now() - self.start_time
        minutes = int(elapsed.total_seconds() // 60)
        seconds = int(elapsed.total_seconds() % 60)
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"


def create_logger(verbose: bool = False, name: str = "codewiki.cli") -> CLILogger:
    """Return a CLI logger facade backed by structlog."""

    return CLILogger(verbose=verbose, name=name)
