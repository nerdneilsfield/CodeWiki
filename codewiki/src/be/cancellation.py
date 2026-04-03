"""Cooperative cancellation primitives for async generation flows."""

from __future__ import annotations

import threading

from codewiki.src.be.errors import CancellationError


class CancellationToken:
    """Thread-safe cooperative cancellation token."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check(self) -> None:
        if self._cancelled.is_set():
            raise CancellationError("Operation cancelled")
