"""LLM execution layer that routes requests based on actual input size.

Wraps a primary model and a long-context model. On each request,
estimates the token count from the actual messages and routes to
the appropriate model transparently.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

logger = logging.getLogger(__name__)


class ContextRoutingModel:
    """Routes to long-context model when input exceeds threshold.

    Acts as a drop-in replacement for a pydantic-ai Model. Delegates
    all calls to either the primary or long-context model based on
    estimated message size.

    Usage:
        primary = create_fallback_models(config)
        long_ctx = create_long_context_model(config)
        model = ContextRoutingModel(primary, long_ctx, threshold=200_000)
        agent = Agent(model, ...)
    """

    def __init__(
        self,
        primary: Any,
        long_context: Any,
        threshold: int = 200_000,
    ):
        self._primary = primary
        self._long_context = long_context
        self._threshold = threshold

    def _estimate_message_tokens(self, messages: list) -> int:
        """Rough token estimate from message content length."""
        total_chars = 0
        for msg in messages:
            for part in getattr(msg, "parts", []):
                text = getattr(part, "content", None) or ""
                if isinstance(text, str):
                    total_chars += len(text)
                args = getattr(part, "args", None)
                if args:
                    if isinstance(args, str):
                        total_chars += len(args)
                    elif isinstance(args, dict):
                        total_chars += sum(len(str(v)) for v in args.values())
        return total_chars // 3

    def _select_model(self, messages: list) -> Any:
        est = self._estimate_message_tokens(messages)
        if est > self._threshold:
            logger.info(
                "🔀 Context routing: ~%dK tokens > %dK threshold → long-context model",
                est // 1000,
                self._threshold // 1000,
            )
            return self._long_context
        return self._primary

    # ── Delegate all Model interface methods ──────────────────────────

    async def request(self, messages, model_settings, model_request_parameters):
        model = self._select_model(messages)
        return await model.request(messages, model_settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(
        self, messages, model_settings, model_request_parameters, run_context=None
    ):
        model = self._select_model(messages)
        async with model.request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as stream:
            yield stream

    @property
    def model_name(self) -> str | None:
        return getattr(self._primary, "model_name", None)

    @property
    def system(self) -> str:
        return getattr(self._primary, "system", "allow")

    def __getattr__(self, name: str) -> Any:
        """Forward any other attribute access to the primary model."""
        return getattr(self._primary, name)
