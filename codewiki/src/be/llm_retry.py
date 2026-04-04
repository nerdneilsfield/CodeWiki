"""Retry helpers for LLM operations."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Awaitable, Callable, TypeVar

from codewiki.src.be.errors import CancellationError, ErrorCategory, LLMError

_logger = logging.getLogger(__name__)
T = TypeVar("T")
_BASE_DELAY = 0.5
_MAX_DELAY = 32.0


class LLMRetryExhausted(Exception):
    def __init__(self, last_error: Exception, attempts: int):
        self.last_error = last_error
        self.attempts = attempts
        super().__init__(f"Exhausted {attempts} attempts: {last_error}")


def _get_retry_after(error: LLMError) -> float | None:
    cause = error.__cause__
    if cause is None:
        return None
    headers = getattr(getattr(cause, "response", None), "headers", None)
    if headers is None:
        return None
    val = headers.get("retry-after") or headers.get("Retry-After")
    if val:
        try:
            return min(float(val), 120.0)
        except (ValueError, TypeError):
            pass
    return None


def _compute_delay(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return retry_after
    base = min(_BASE_DELAY * (2 ** (attempt - 1)), _MAX_DELAY)
    return base + random.random() * 0.25 * base


def _is_timeout(error: LLMError) -> bool:
    cause = error.__cause__
    try:
        import openai

        if isinstance(cause, openai.APITimeoutError):
            return True
    except ImportError:
        pass
    msg = str(error).lower()
    return "timeout" in msg or "524" in msg or "cloudflare" in msg or "stream disconnected" in msg


async def with_retry(
    operation: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 3,
    cancel_token: Any = None,
    on_timeout_use_stream: bool = False,
    **kwargs: Any,
) -> T:
    last_error: Exception | None = None
    auth_retried = False
    total_attempts = max_retries + 1

    for attempt in range(1, total_attempts + 1):
        if cancel_token and cancel_token.is_cancelled:
            raise CancellationError("Cancelled before retry")
        try:
            return await operation(*args, **kwargs)
        except CancellationError:
            raise
        except LLMError as exc:
            last_error = exc
            if not exc.is_retryable:
                raise
            if exc.category == ErrorCategory.RETRYABLE_AUTH:
                if auth_retried:
                    raise
                auth_retried = True
                _logger.warning("Auth error, retrying once...")
                continue
            if attempt >= total_attempts:
                break
            if on_timeout_use_stream and _is_timeout(exc):
                kwargs["stream"] = True
                _logger.info("Timeout detected, switching to streaming for next retry")
            retry_after = _get_retry_after(exc)
            delay = _compute_delay(attempt, retry_after)
            _logger.warning("LLM retry %d/%d in %.1fs: %s", attempt, total_attempts, delay, exc)
            if cancel_token:
                cancel_token.check()
            await asyncio.sleep(delay)
            if cancel_token:
                cancel_token.check()
        except Exception:
            raise

    if last_error is None:
        last_error = RuntimeError("retry loop exhausted without capturing an error")
    raise LLMRetryExhausted(last_error, total_attempts)


def with_retry_sync(
    operation: Callable[..., T],
    *args: Any,
    max_retries: int = 3,
    cancel_token: Any = None,
    **kwargs: Any,
) -> T:
    """Synchronous retry helper for existing sync direct-call sites."""

    last_error: Exception | None = None
    auth_retried = False
    total_attempts = max_retries + 1

    for attempt in range(1, total_attempts + 1):
        if cancel_token and cancel_token.is_cancelled:
            raise CancellationError("Cancelled before retry")
        try:
            return operation(*args, **kwargs)
        except CancellationError:
            raise
        except LLMError as exc:
            last_error = exc
            if not exc.is_retryable:
                raise
            if exc.category == ErrorCategory.RETRYABLE_AUTH:
                if auth_retried:
                    raise
                auth_retried = True
                _logger.warning("Auth error, retrying once...")
                continue
            if attempt >= total_attempts:
                break
            delay = _compute_delay(attempt, _get_retry_after(exc))
            _logger.warning("LLM retry %d/%d in %.1fs: %s", attempt, total_attempts, delay, exc)
            if cancel_token:
                cancel_token.check()
            time.sleep(delay)
            if cancel_token:
                cancel_token.check()
        except Exception:
            raise

    if last_error is None:
        last_error = RuntimeError("retry loop exhausted without capturing an error")
    raise LLMRetryExhausted(last_error, total_attempts)
