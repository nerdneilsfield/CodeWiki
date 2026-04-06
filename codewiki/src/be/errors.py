"""Structured error classification for LLM and pipeline errors."""

from __future__ import annotations

from enum import Enum


class ErrorCategory(Enum):
    RETRYABLE_TRANSIENT = "retryable_transient"
    RETRYABLE_AUTH = "retryable_auth"
    NON_RETRYABLE_CLIENT = "non_retryable_client"
    NON_RETRYABLE_CONFIG = "non_retryable_config"
    RESOURCE_EXHAUSTED = "resource_exhausted"


class LLMError(Exception):
    """LLM API call error with retry-relevant classification."""

    def __init__(
        self,
        message: str,
        category: ErrorCategory,
        status_code: int | None = None,
    ) -> None:
        self.category = category
        self.status_code = status_code
        super().__init__(message)

    @property
    def is_retryable(self) -> bool:
        return self.category in {
            ErrorCategory.RETRYABLE_TRANSIENT,
            ErrorCategory.RETRYABLE_AUTH,
        }


class PipelineError(Exception):
    """Pipeline stage error with an explicit category."""

    def __init__(self, message: str, category: ErrorCategory, stage: str = "") -> None:
        self.category = category
        self.stage = stage
        super().__init__(message)

    @property
    def is_retryable(self) -> bool:
        return self.category in {
            ErrorCategory.RETRYABLE_TRANSIENT,
            ErrorCategory.RETRYABLE_AUTH,
        }


class CancellationError(Exception):
    """Operation cancelled by user or system."""


_RETRYABLE_STATUS = {429, 500, 502, 503, 529}
_AUTH_STATUS = {401, 403}
_CONFIG_INDICATORS = (
    "api key",
    "provider",
    "model not found",
    "model reference",
    "unsupported provider",
    "not configured",
)


def classify_llm_exception(exc: Exception) -> LLMError:
    """Classify SDK exceptions into LLMError. Unknown exceptions pass through."""
    import openai

    if isinstance(exc, openai.APITimeoutError):
        return LLMError(str(exc), ErrorCategory.RETRYABLE_TRANSIENT)

    if isinstance(exc, (ValueError, KeyError)):
        msg = str(exc).lower()
        if any(indicator in msg for indicator in _CONFIG_INDICATORS):
            return LLMError(str(exc), ErrorCategory.NON_RETRYABLE_CONFIG)
        raise exc

    status = getattr(exc, "status_code", None)
    if status is not None:
        if status == 400:
            body = getattr(exc, "body", None)
            msg = str(getattr(exc, "message", exc))
            if body and isinstance(body, dict):
                code = body.get("error", {}).get("code", "")
                if "context_length" in code:
                    return LLMError(str(exc), ErrorCategory.RESOURCE_EXHAUSTED, status)
            msg_lower = msg.lower()
            if any(
                k in msg_lower
                for k in (
                    "context_length",
                    "maximum context",
                    "too long",
                    "input length",
                    "range of input",
                    "token limit",
                    "max_tokens",
                    "input_tokens_limit",
                )
            ):
                return LLMError(str(exc), ErrorCategory.RESOURCE_EXHAUSTED, status)
            return LLMError(str(exc), ErrorCategory.NON_RETRYABLE_CLIENT, status)

        if status in _RETRYABLE_STATUS:
            return LLMError(str(exc), ErrorCategory.RETRYABLE_TRANSIENT, status)
        if status in _AUTH_STATUS:
            return LLMError(str(exc), ErrorCategory.RETRYABLE_AUTH, status)
        if status == 404:
            return LLMError(str(exc), ErrorCategory.NON_RETRYABLE_CLIENT, status)
        return LLMError(str(exc), ErrorCategory.RETRYABLE_TRANSIENT, status)

    try:
        import httpx

        if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout)):
            return LLMError(str(exc), ErrorCategory.RETRYABLE_TRANSIENT)
    except ImportError:
        pass

    raise exc
