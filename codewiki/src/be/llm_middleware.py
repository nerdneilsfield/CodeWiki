"""LLM middleware layer for routing, overflow protection, and token budgeting."""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

from pydantic_ai.messages import ModelRequest, ModelResponse as MessageModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, ModelResponse, ModelSettings

from codewiki.src.be.errors import CancellationError, ErrorCategory, LLMError
from codewiki.src.be.llm_services import (
    create_fallback_models,
    create_long_context_model,
    raw_llm_call,
)
from codewiki.src.be.llm_usage import LLMCallResult, LLMUsageStats
from codewiki.src.be.utils import _get_encoder, count_tokens

if TYPE_CHECKING:
    from pydantic_ai.result import StreamedResponse
    from pydantic_ai.run import RunContext

    from codewiki.src.codewiki_config import CodeWikiConfig

logger = logging.getLogger(__name__)


class LLMMiddleware:
    """Unified LLM calling layer for direct prompts and pydantic-ai agents."""

    _OVERFLOW_KEYWORDS = (
        "context_length",
        "too long",
        "maximum context",
        "input length",
        "range of input",
        "token limit",
        "max_tokens",
        "input_tokens_limit",
    )

    def __init__(self, config: CodeWikiConfig, usage_stats: LLMUsageStats | None = None):
        self._config = config
        self._usage_stats = usage_stats
        self._usage_lock = threading.Lock()
        self._model_cache: dict[str, Any] = {}
        self._model_cache_lock = threading.Lock()

    def call(
        self,
        prompt: str,
        *,
        model: str | None = None,
        temperature: float = 0.0,
        stream: bool = False,
        max_retries: int = 3,
        trim_step: int = 100_000,
    ) -> LLMCallResult:
        """Single-turn LLM call with routing, truncation, and overflow retry."""
        prompt_tokens = count_tokens(prompt)
        effective_model = self._route_model(model, prompt_tokens)

        input_budget = self._input_budget_for_model(effective_model)
        if prompt_tokens > input_budget:
            prompt = self._truncate(prompt, input_budget)

        current_prompt = prompt
        for attempt in range(max_retries + 1):
            try:
                result = raw_llm_call(
                    current_prompt,
                    self._config,
                    effective_model,
                    temperature,
                    stream=stream,
                )
                self._record_usage(result)
                return result
            except Exception as exc:
                if isinstance(exc, CancellationError):
                    raise
                if not self._is_context_overflow(exc):
                    raise
                lc_model = self._config.long_context_model
                if attempt == 0 and lc_model and effective_model != lc_model:
                    effective_model = lc_model
                    new_budget = self._input_budget_for_model(effective_model)
                    if count_tokens(current_prompt) > new_budget:
                        current_prompt = self._truncate(current_prompt, new_budget)
                    logger.warning(
                        "Overflow detected, switching to long-context model: %s", lc_model
                    )
                    continue
                if attempt >= max_retries:
                    raise
                current_tokens = count_tokens(current_prompt)
                new_budget = max(current_tokens - trim_step, 10_000)
                current_prompt = self._truncate(current_prompt, new_budget)
                logger.warning(
                    "Overflow detected, trimming prompt %dK -> %dK (attempt %d/%d)",
                    current_tokens // 1000,
                    new_budget // 1000,
                    attempt + 1,
                    max_retries,
                )

        raise RuntimeError("Exhausted retries without returning or raising")

    def create_agent_model(self) -> "MiddlewareModel":
        return MiddlewareModel(self)

    def get_cached_pydantic_model(self, model_name: str):
        with self._model_cache_lock:
            cached = self._model_cache.get(model_name)
            if cached is not None:
                return cached
            if model_name == self._config.long_context_model:
                cached = create_long_context_model(self._config)
            else:
                cached = create_fallback_models(self._config)
            self._model_cache[model_name] = cached
            return cached

    def _route_model(self, explicit_model: str | None, tokens: int) -> str:
        if explicit_model:
            return explicit_model
        if self._config.long_context_model and tokens > self._config.long_context_threshold:
            return self._config.long_context_model
        return self._config.main_model

    def _input_budget_for_model(self, model: str) -> int:
        if model == self._config.long_context_model:
            return self._config.long_context_max_input_tokens - self._config.max_tokens
        return self._config.max_input_tokens - self._config.max_tokens

    def _is_context_overflow(self, exc: Exception) -> bool:
        if isinstance(exc, LLMError) and exc.category == ErrorCategory.RESOURCE_EXHAUSTED:
            return True
        try:
            from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded

            if isinstance(exc, UsageLimitExceeded):
                msg = str(exc).lower()
                return "input_tokens_limit" in msg or "request_tokens_limit" in msg
            if isinstance(exc, ModelHTTPError) and exc.status_code == 400:
                msg = str(exc).lower()
                if any(keyword in msg for keyword in self._OVERFLOW_KEYWORDS):
                    return True
        except ImportError:
            pass
        try:
            import openai

            if isinstance(exc, openai.APIStatusError) and exc.status_code == 400:
                msg = str(exc).lower()
                if any(keyword in msg for keyword in self._OVERFLOW_KEYWORDS):
                    return True
        except ImportError:
            pass
        msg = str(exc).lower()
        return any(keyword in msg for keyword in self._OVERFLOW_KEYWORDS)

    def _truncate(self, text: str, max_tokens: int) -> str:
        enc = _get_encoder("gpt-4")
        tokens = enc.encode(text)
        if len(tokens) <= max_tokens:
            return text
        return enc.decode(tokens[:max_tokens])

    def _record_usage(self, result: LLMCallResult) -> None:
        if self._usage_stats and result.usage:
            with self._usage_lock:
                self._usage_stats.record(
                    result.model,
                    result.usage.input_tokens,
                    result.usage.output_tokens,
                )


class MiddlewareModel(Model):
    """pydantic-ai model adapter that delegates routing and overflow handling."""

    def __init__(self, middleware: LLMMiddleware):
        super().__init__()
        self._middleware = middleware
        self._max_retries = 3

    async def request(
        self,
        messages,
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        tokens = self._estimate_message_tokens(messages)
        model_name = self._middleware._route_model(None, tokens)
        real_model = self._resolve_pydantic_model(model_name)
        current_messages = list(messages)

        for attempt in range(self._max_retries + 1):
            try:
                return await real_model.request(
                    current_messages,
                    model_settings,
                    model_request_parameters,
                )
            except Exception as exc:
                if isinstance(exc, CancellationError):
                    raise
                if not self._middleware._is_context_overflow(exc):
                    raise
                lc_model = self._middleware._config.long_context_model
                if attempt == 0 and lc_model and model_name != lc_model:
                    model_name = lc_model
                    real_model = self._resolve_pydantic_model(model_name)
                    logger.warning("Agent overflow detected, switching to long-context model")
                    continue
                if attempt >= self._max_retries:
                    raise
                model_budget = self._middleware._input_budget_for_model(model_name)
                current_messages = self._trim_conversation(current_messages, model_budget)
                logger.warning(
                    "Agent overflow detected, trimming history (attempt %d/%d)",
                    attempt + 1,
                    self._max_retries,
                )

        raise RuntimeError("Exhausted agent retries without returning or raising")

    @asynccontextmanager
    async def request_stream(
        self,
        messages,
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: RunContext[Any] | None = None,
    ) -> AsyncIterator[StreamedResponse]:
        tokens = self._estimate_message_tokens(messages)
        model_name = self._middleware._route_model(None, tokens)
        real_model = self._resolve_pydantic_model(model_name)
        current_messages = list(messages)
        stream_cm: AbstractAsyncContextManager[StreamedResponse] | None = None
        stream: StreamedResponse | None = None

        for attempt in range(self._max_retries + 1):
            try:
                stream_cm = real_model.request_stream(
                    current_messages,
                    model_settings,
                    model_request_parameters,
                    run_context=run_context,
                )
                stream = await stream_cm.__aenter__()
                break
            except Exception as exc:
                if isinstance(exc, CancellationError):
                    raise
                if not self._middleware._is_context_overflow(exc):
                    raise
                lc_model = self._middleware._config.long_context_model
                if attempt == 0 and lc_model and model_name != lc_model:
                    model_name = lc_model
                    real_model = self._resolve_pydantic_model(model_name)
                    logger.warning(
                        "Agent stream overflow detected, switching to long-context model"
                    )
                    continue
                if attempt >= self._max_retries:
                    raise
                model_budget = self._middleware._input_budget_for_model(model_name)
                current_messages = self._trim_conversation(current_messages, model_budget)
                logger.warning(
                    "Agent stream overflow detected, trimming history (attempt %d/%d)",
                    attempt + 1,
                    self._max_retries,
                )
        else:
            raise RuntimeError("Exhausted agent stream retries without returning or raising")

        if stream_cm is None or stream is None:
            raise RuntimeError("Failed to acquire stream")

        try:
            yield stream
        except BaseException as exc:
            suppress = await stream_cm.__aexit__(type(exc), exc, exc.__traceback__)
            if not suppress:
                raise
        else:
            await stream_cm.__aexit__(None, None, None)

    def _trim_conversation(self, messages, budget_tokens: int):
        if len(messages) <= 2:
            return list(messages)

        head, tail = self._split_conversation(messages)
        kept_tail = []
        used = self._estimate_message_tokens(head)
        if used >= budget_tokens or not tail:
            return head
        for msg in reversed(tail):
            msg_tokens = self._estimate_message_tokens([msg])
            if used + msg_tokens > budget_tokens:
                break
            kept_tail.insert(0, msg)
            used += msg_tokens
        trimmed = len(tail) - len(kept_tail)
        if trimmed:
            logger.info("Trimmed %d early conversation turns, kept %d", trimmed, len(kept_tail))
        return head + kept_tail

    def _split_conversation(self, messages: Sequence[Any]) -> tuple[list[Any], list[Any]]:
        if len(messages) <= 2:
            return list(messages), []

        head: list[Any] = []
        index = 0
        while index < len(messages) and isinstance(messages[index], ModelRequest):
            head.append(messages[index])
            index += 1
        if not head:
            head.append(messages[0])
            index = 1
        if index < len(messages) and isinstance(messages[index], MessageModelResponse):
            head.append(messages[index])
            index += 1
        return head, list(messages[index:])

    def _estimate_message_tokens(self, messages) -> int:
        total_tokens = 0
        for msg in messages:
            for part in getattr(msg, "parts", []):
                text = getattr(part, "content", None) or ""
                if isinstance(text, str):
                    total_tokens += count_tokens(text)
                args = getattr(part, "args", None)
                if args:
                    if isinstance(args, str):
                        total_tokens += count_tokens(args)
                    elif isinstance(args, dict):
                        total_tokens += sum(count_tokens(str(v)) for v in args.values())
        return total_tokens

    def _resolve_pydantic_model(self, model_name: str):
        return self._middleware.get_cached_pydantic_model(model_name)

    @property
    def model_name(self) -> str:
        return self._middleware._config.main_model

    @property
    def system(self) -> str:
        return "openai"

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(
            self._resolve_pydantic_model(self._middleware._config.main_model),
            name,
        )
