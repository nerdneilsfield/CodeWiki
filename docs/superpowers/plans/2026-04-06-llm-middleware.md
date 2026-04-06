# LLM Middleware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify all LLM calls through a single middleware layer that handles model routing, overflow retry, and token management.

**Architecture:** New `LLMMiddleware` class provides two entry points: `call()` for single-turn prompts and `create_agent_model()` for pydantic-ai Agents. Both share the same overflow detection, model routing (normal → long-context), and retry logic. `llm_services.py` is stripped to bare SDK wrappers.

**Tech Stack:** Python, pydantic-ai (Model ABC), tiktoken, openai SDK, threading.Lock

**Spec:** `docs/superpowers/specs/2026-04-06-llm-middleware-design.md`

---

### Task 1: Extract `raw_llm_call` from `llm_services.py`

**Files:**
- Modify: `codewiki/src/be/llm_services.py:292-400`
- Test: `tests/test_llm_middleware.py` (create)

- [ ] **Step 1: Write the failing test for raw_llm_call**

```python
# tests/test_llm_middleware.py
from unittest.mock import patch, MagicMock
from codewiki.src.codewiki_config import CodeWikiConfig


def _make_config(tmp_path):
    return CodeWikiConfig(
        repo_path=str(tmp_path / "repo"),
        output_dir=str(tmp_path / "out"),
        dependency_graph_dir=str(tmp_path / "graphs"),
        docs_dir=str(tmp_path / "docs"),
        llm_base_url="http://localhost",
        llm_api_key="test-key",
        main_model="test/main",
        cluster_model="test/cluster",
        fallback_model=["test/fallback"],
        long_context_model="test/long",
        long_context_threshold=100_000,
        max_input_tokens=200_000,
        long_context_max_input_tokens=800_000,
        max_tokens=32_768,
    )


def test_raw_llm_call_exists(tmp_path):
    """raw_llm_call is importable and has correct signature."""
    from codewiki.src.be.llm_services import raw_llm_call
    assert callable(raw_llm_call)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_middleware.py::test_raw_llm_call_exists -v`
Expected: FAIL with `ImportError: cannot import name 'raw_llm_call'`

- [ ] **Step 3: Implement raw_llm_call**

Rename the current `call_llm` to `raw_llm_call` and strip out the model routing logic (lines 305-315). Keep it as a pure SDK call with no routing, no overflow retry. Then create a thin `call_llm` wrapper that calls `raw_llm_call` (temporary — will be deleted in Task 11).

In `codewiki/src/be/llm_services.py`, replace the `call_llm` function (lines 292-400) with:

```python
def raw_llm_call(
    prompt: str,
    config: CodeWikiConfig,
    model: str,
    temperature: float = 0.0,
    stream: bool = False,
) -> "LLMCallResult":
    """Bottom-layer SDK call. No routing, no retry, no truncation."""
    from codewiki.src.be.utils import count_tokens

    try:
        prompt_tokens = count_tokens(prompt)
        _logger.debug(
            f"raw_llm_call: model={model}, prompt_tokens={prompt_tokens}, temperature={temperature}"
        )

        client, provider_type = _create_client_for_model(config, model)
        resolved_stream = False
        if _has_provider_registry(config):
            resolved = resolve_model_ref(model, config.providers)
            resolved_model_name = resolved.model_name
            resolved_stream = resolved.stream
        else:
            resolved_model_name = model

        t0 = time.time()
        usage = None

        if provider_type in {"openai_compatible", "azure_openai"}:
            if stream and resolved_stream:
                content = _call_llm_streaming(
                    client, resolved_model_name, prompt, temperature, config
                )
            else:
                response = client.chat.completions.create(
                    model=resolved_model_name,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                    max_tokens=config.max_tokens,
                )
                if not response.choices:
                    raise ValueError(f"LLM returned empty choices (model={resolved_model_name})")
                content = response.choices[0].message.content
                if content is None:
                    raise ValueError(
                        f"LLM returned null content (model={resolved_model_name}, "
                        f"finish_reason={response.choices[0].finish_reason!r})"
                    )
                if response.usage:
                    usage = LLMCallUsage(
                        input_tokens=response.usage.prompt_tokens or 0,
                        output_tokens=response.usage.completion_tokens or 0,
                        source="api",
                    )
        elif provider_type == "claude":
            response = _call_claude(client, resolved_model_name, prompt, temperature, config)
            parts = []
            for block in response.content:
                text = getattr(block, "text", None)
                if text:
                    parts.append(text)
            content = "".join(parts)
            response_usage = getattr(response, "usage", None)
            if response_usage is not None:
                usage = LLMCallUsage(
                    input_tokens=getattr(response_usage, "input_tokens", 0) or 0,
                    output_tokens=getattr(response_usage, "output_tokens", 0) or 0,
                    source="api",
                )
        else:
            raise ValueError(f"unsupported provider type: {provider_type}")

        if not content:
            raise ValueError(f"LLM returned empty content (model={resolved_model_name})")

        if usage is None:
            usage = LLMCallUsage(
                input_tokens=count_tokens(prompt),
                output_tokens=count_tokens(content),
                source="estimated",
            )

        elapsed = time.time() - t0
        _logger.debug(
            "raw_llm_call: model=%s, elapsed=%.1fs, input_tokens=%s, output_tokens=%s, source=%s",
            model,
            elapsed,
            usage.input_tokens,
            usage.output_tokens,
            usage.source,
        )
        return LLMCallResult(content=content, usage=usage, model=resolved_model_name)
    except CancellationError:
        raise
    except Exception as exc:
        raise classify_llm_exception(exc) from exc


def call_llm(
    prompt: str,
    config: CodeWikiConfig,
    model: str | None = None,
    temperature: float = 0.0,
    stream: bool = False,
) -> "LLMCallResult":
    """Temporary backward-compat wrapper. Will be removed after migration to LLMMiddleware."""
    from codewiki.src.be.utils import count_tokens

    if model is None:
        model = config.main_model

    prompt_tokens = count_tokens(prompt)
    if (
        config.long_context_model
        and prompt_tokens > config.long_context_threshold
        and model == config.main_model
    ):
        _logger.info(
            f"Switching model: {model} → {config.long_context_model} "
            f"(prompt {prompt_tokens} tokens > threshold {config.long_context_threshold})"
        )
        model = config.long_context_model

    return raw_llm_call(prompt, config, model, temperature, stream)
```

- [ ] **Step 4: Run test + full suite to verify nothing breaks**

Run: `uv run pytest tests/test_llm_middleware.py tests/ -x -q --tb=short`
Expected: all pass (including existing tests that use `call_llm`)

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/llm_services.py tests/test_llm_middleware.py
git commit -m "refactor: extract raw_llm_call from call_llm for middleware layer"
```

---

### Task 2: Build `LLMMiddleware` core with tests

**Files:**
- Create: `codewiki/src/be/llm_middleware.py`
- Test: `tests/test_llm_middleware.py` (add tests)

- [ ] **Step 1: Write failing tests for LLMMiddleware**

Add to `tests/test_llm_middleware.py`:

```python
import pytest
from unittest.mock import patch, MagicMock, call
from codewiki.src.be.errors import LLMError, ErrorCategory
from codewiki.src.be.llm_usage import LLMCallResult, LLMCallUsage


def test_middleware_route_model_normal(tmp_path):
    """Tokens under threshold → main_model."""
    from codewiki.src.be.llm_middleware import LLMMiddleware
    mw = LLMMiddleware(_make_config(tmp_path))
    assert mw._route_model(None, 50_000) == "test/main"


def test_middleware_route_model_long_context(tmp_path):
    """Tokens over threshold → long_context_model."""
    from codewiki.src.be.llm_middleware import LLMMiddleware
    mw = LLMMiddleware(_make_config(tmp_path))
    assert mw._route_model(None, 150_000) == "test/long"


def test_middleware_route_model_explicit(tmp_path):
    """Explicit model overrides routing."""
    from codewiki.src.be.llm_middleware import LLMMiddleware
    mw = LLMMiddleware(_make_config(tmp_path))
    assert mw._route_model("custom/model", 999_999) == "custom/model"


def test_middleware_input_budget_normal(tmp_path):
    """Normal model budget = max_input_tokens - max_tokens."""
    from codewiki.src.be.llm_middleware import LLMMiddleware
    mw = LLMMiddleware(_make_config(tmp_path))
    assert mw._input_budget_for_model("test/main") == 200_000 - 32_768


def test_middleware_input_budget_long_context(tmp_path):
    """Long context budget = long_context_max_input_tokens - max_tokens."""
    from codewiki.src.be.llm_middleware import LLMMiddleware
    mw = LLMMiddleware(_make_config(tmp_path))
    assert mw._input_budget_for_model("test/long") == 800_000 - 32_768


def test_middleware_call_routes_and_calls_raw(tmp_path):
    """call() routes to correct model and delegates to raw_llm_call."""
    from codewiki.src.be.llm_middleware import LLMMiddleware
    mw = LLMMiddleware(_make_config(tmp_path))
    fake_result = LLMCallResult(content="ok", usage=LLMCallUsage(10, 5), model="test/main")

    with patch("codewiki.src.be.llm_middleware.raw_llm_call", return_value=fake_result) as mock_raw:
        result = mw.call("short prompt")

    assert result.content == "ok"
    mock_raw.assert_called_once()
    assert mock_raw.call_args[0][2] == "test/main"  # model arg


def test_middleware_call_passes_stream_kwarg(tmp_path):
    """call(stream=True) passes stream to raw_llm_call."""
    from codewiki.src.be.llm_middleware import LLMMiddleware
    mw = LLMMiddleware(_make_config(tmp_path))
    fake_result = LLMCallResult(content="ok", usage=None, model="m")

    with patch("codewiki.src.be.llm_middleware.raw_llm_call", return_value=fake_result) as mock_raw:
        mw.call("prompt", stream=True)

    assert mock_raw.call_args.kwargs.get("stream") is True or mock_raw.call_args[0][4] is True


def test_middleware_overflow_switches_model_then_retries(tmp_path):
    """On overflow: first attempt switches to long_context, second succeeds."""
    from codewiki.src.be.llm_middleware import LLMMiddleware
    mw = LLMMiddleware(_make_config(tmp_path))
    overflow_err = LLMError("context_length_exceeded", ErrorCategory.RESOURCE_EXHAUSTED, 400)
    ok_result = LLMCallResult(content="ok", usage=None, model="test/long")

    with patch("codewiki.src.be.llm_middleware.raw_llm_call", side_effect=[overflow_err, ok_result]) as mock_raw:
        result = mw.call("prompt")

    assert result.content == "ok"
    assert mock_raw.call_count == 2
    # Second call should use long context model
    assert mock_raw.call_args_list[1][0][2] == "test/long"


def test_middleware_overflow_trims_after_model_switch(tmp_path):
    """After switching model, if still overflow → trim prompt."""
    from codewiki.src.be.llm_middleware import LLMMiddleware
    mw = LLMMiddleware(_make_config(tmp_path))
    overflow_err = LLMError("context_length_exceeded", ErrorCategory.RESOURCE_EXHAUSTED, 400)
    ok_result = LLMCallResult(content="ok", usage=None, model="test/long")

    with patch("codewiki.src.be.llm_middleware.raw_llm_call", side_effect=[overflow_err, overflow_err, ok_result]):
        with patch("codewiki.src.be.llm_middleware.count_tokens", return_value=50_000):
            result = mw.call("x" * 200_000, max_retries=3, trim_step=10_000)

    assert result.content == "ok"


def test_middleware_is_context_overflow_detects_llm_error(tmp_path):
    from codewiki.src.be.llm_middleware import LLMMiddleware
    mw = LLMMiddleware(_make_config(tmp_path))
    assert mw._is_context_overflow(
        LLMError("too long", ErrorCategory.RESOURCE_EXHAUSTED, 400)
    ) is True


def test_middleware_is_context_overflow_detects_range_of_input(tmp_path):
    from codewiki.src.be.llm_middleware import LLMMiddleware
    mw = LLMMiddleware(_make_config(tmp_path))
    assert mw._is_context_overflow(
        LLMError("Range of input length should be [1, 202745]", ErrorCategory.NON_RETRYABLE_CLIENT, 400)
    ) is True


def test_middleware_is_context_overflow_rejects_non_overflow(tmp_path):
    from codewiki.src.be.llm_middleware import LLMMiddleware
    mw = LLMMiddleware(_make_config(tmp_path))
    assert mw._is_context_overflow(
        LLMError("model not found", ErrorCategory.NON_RETRYABLE_CONFIG, 404)
    ) is False


def test_middleware_route_then_truncate_uses_correct_budget(tmp_path):
    """Routes to long-context first, then truncates with long-context budget (not normal budget)."""
    from codewiki.src.be.llm_middleware import LLMMiddleware
    config = _make_config(tmp_path)
    mw = LLMMiddleware(config)
    # 300K tokens — exceeds normal (200K) but under long-context (800K)
    fake_result = LLMCallResult(content="ok", usage=None, model="test/long")

    with (
        patch("codewiki.src.be.llm_middleware.count_tokens", return_value=300_000),
        patch("codewiki.src.be.llm_middleware.raw_llm_call", return_value=fake_result) as mock_raw,
    ):
        result = mw.call("big prompt")

    # Should NOT have been truncated (300K < 800K - 32K = 767K)
    assert mock_raw.call_args[0][0] == "big prompt"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm_middleware.py -v -k "not raw_llm_call_exists"`
Expected: FAIL with `ModuleNotFoundError: No module named 'codewiki.src.be.llm_middleware'`

- [ ] **Step 3: Implement LLMMiddleware**

Create `codewiki/src/be/llm_middleware.py`:

```python
"""LLM middleware layer — unified model routing, overflow retry, and token management.

All LLM calls go through this layer, whether from single-turn callers
(overview, guide, cluster, postprocess) or pydantic-ai Agents.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncIterator

from codewiki.src.be.errors import CancellationError, ErrorCategory, LLMError
from codewiki.src.be.llm_services import (
    create_fallback_models,
    create_long_context_model,
    raw_llm_call,
)
from codewiki.src.be.llm_usage import LLMCallResult, LLMUsageStats
from codewiki.src.be.utils import _get_encoder, count_tokens

if TYPE_CHECKING:
    from codewiki.src.codewiki_config import CodeWikiConfig

logger = logging.getLogger(__name__)


class LLMMiddleware:
    """Unified LLM calling layer with model routing and overflow protection.

    Two entry points:
    - ``call()`` for single-turn prompts (replaces ``call_llm``)
    - ``create_agent_model()`` for pydantic-ai Agents (replaces ``select_agent_model``)
    """

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

    def __init__(
        self, config: CodeWikiConfig, usage_stats: LLMUsageStats | None = None
    ):
        self._config = config
        self._usage_stats = usage_stats
        self._usage_lock = threading.Lock()

    # ── Single-turn entry point ───────────────────────────────────────

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
        """Single-turn LLM call with model routing and overflow retry.

        Overflow strategy: switch to long-context model first, then trim prompt.
        """
        prompt_tokens = count_tokens(prompt)

        # Route first, truncate second (so long-context gets its full budget)
        effective_model = self._route_model(model, prompt_tokens)

        input_budget = self._input_budget_for_model(effective_model)
        if prompt_tokens > input_budget:
            prompt = self._truncate(prompt, input_budget)
            prompt_tokens = input_budget

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
            except (LLMError, Exception) as e:
                if isinstance(e, CancellationError):
                    raise
                if not self._is_context_overflow(e):
                    raise
                # First overflow: switch to long-context model
                lc_model = self._config.long_context_model
                if attempt == 0 and lc_model and effective_model != lc_model:
                    effective_model = lc_model
                    # Re-check budget with new model's limit
                    new_budget = self._input_budget_for_model(effective_model)
                    if count_tokens(current_prompt) > new_budget:
                        current_prompt = self._truncate(current_prompt, new_budget)
                    logger.warning(
                        "🔀 Overflow → switching to long-context model: %s",
                        lc_model,
                    )
                    continue
                # Subsequent overflows: trim prompt
                if attempt >= max_retries:
                    raise
                current_tokens = count_tokens(current_prompt)
                new_budget = max(current_tokens - trim_step, 10_000)
                current_prompt = self._truncate(current_prompt, new_budget)
                logger.warning(
                    "✂️ Overflow → trimming prompt %dK → %dK (attempt %d/%d)",
                    current_tokens // 1000,
                    new_budget // 1000,
                    attempt + 1,
                    max_retries,
                )
        # Unreachable, but satisfies type checker
        raise RuntimeError("Exhausted retries without returning or raising")

    # ── pydantic-ai adapter factory ───────────────────────────────────

    def create_agent_model(self) -> MiddlewareModel:
        """Create a pydantic-ai Model that routes through this middleware."""
        return MiddlewareModel(self)

    # ── Internal helpers ──────────────────────────────────────────────

    def _route_model(self, explicit_model: str | None, tokens: int) -> str:
        if explicit_model:
            return explicit_model
        if (
            self._config.long_context_model
            and tokens > self._config.long_context_threshold
        ):
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
                return True
            if isinstance(exc, ModelHTTPError) and exc.status_code == 400:
                msg = str(exc).lower()
                if any(k in msg for k in self._OVERFLOW_KEYWORDS):
                    return True
        except ImportError:
            pass
        try:
            import openai

            if isinstance(exc, openai.APIStatusError) and exc.status_code == 400:
                msg = str(exc).lower()
                if any(k in msg for k in self._OVERFLOW_KEYWORDS):
                    return True
        except ImportError:
            pass
        msg = str(exc).lower()
        return any(k in msg for k in self._OVERFLOW_KEYWORDS)

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


# ── pydantic-ai Model adapter ────────────────────────────────────────

from abc import ABC
from pydantic_ai.models import Model


class MiddlewareModel(Model):
    """pydantic-ai Model subclass that routes every request through LLMMiddleware.

    Handles overflow retry for both ``request()`` and ``request_stream()``:
    1. Switch to long-context model
    2. Trim conversation history (keep first + most recent turns)
    """

    def __init__(self, middleware: LLMMiddleware):
        super().__init__()
        self._middleware = middleware
        self._max_retries = 3

    async def request(self, messages, model_settings, model_request_parameters):
        tokens = self._estimate_message_tokens(messages)
        model_name = self._middleware._route_model(None, tokens)
        real_model = self._resolve_pydantic_model(model_name)
        current_messages = messages

        for attempt in range(self._max_retries + 1):
            try:
                return await real_model.request(
                    current_messages, model_settings, model_request_parameters
                )
            except Exception as e:
                if isinstance(e, CancellationError):
                    raise
                if not self._middleware._is_context_overflow(e):
                    raise
                lc = self._middleware._config.long_context_model
                if attempt == 0 and lc and model_name != lc:
                    model_name = lc
                    real_model = self._resolve_pydantic_model(model_name)
                    logger.warning("🔀 Agent overflow → long-context model")
                    continue
                if attempt >= self._max_retries:
                    raise
                model_budget = self._middleware._input_budget_for_model(model_name)
                current_messages = self._trim_conversation(
                    current_messages, model_budget
                )
                logger.warning(
                    "✂️ Agent overflow → trimming history (attempt %d/%d)",
                    attempt + 1,
                    self._max_retries,
                )

    @asynccontextmanager
    async def request_stream(
        self, messages, model_settings, model_request_parameters, run_context=None
    ) -> AsyncIterator:
        tokens = self._estimate_message_tokens(messages)
        model_name = self._middleware._route_model(None, tokens)
        real_model = self._resolve_pydantic_model(model_name)
        current_messages = messages

        for attempt in range(self._max_retries + 1):
            try:
                async with real_model.request_stream(
                    current_messages, model_settings, model_request_parameters
                ) as stream:
                    yield stream
                    return
            except Exception as e:
                if isinstance(e, CancellationError):
                    raise
                if not self._middleware._is_context_overflow(e):
                    raise
                lc = self._middleware._config.long_context_model
                if attempt == 0 and lc and model_name != lc:
                    model_name = lc
                    real_model = self._resolve_pydantic_model(model_name)
                    logger.warning("🔀 Agent stream overflow → long-context model")
                    continue
                if attempt >= self._max_retries:
                    raise
                model_budget = self._middleware._input_budget_for_model(model_name)
                current_messages = self._trim_conversation(
                    current_messages, model_budget
                )
                logger.warning(
                    "✂️ Agent stream overflow → trimming history (attempt %d/%d)",
                    attempt + 1,
                    self._max_retries,
                )

    def _trim_conversation(self, messages, budget_tokens: int):
        head = messages[:2]
        tail = messages[2:]
        kept_tail: list = []
        used = self._estimate_message_tokens(head)
        for msg in reversed(tail):
            msg_tokens = self._estimate_message_tokens([msg])
            if used + msg_tokens > budget_tokens:
                break
            kept_tail.insert(0, msg)
            used += msg_tokens
        trimmed = len(tail) - len(kept_tail)
        if trimmed:
            logger.info(
                "Trimmed %d early conversation turns, kept %d",
                trimmed,
                len(kept_tail),
            )
        return head + kept_tail

    def _estimate_message_tokens(self, messages) -> int:
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

    def _resolve_pydantic_model(self, model_name: str):
        config = self._middleware._config
        if model_name == config.long_context_model:
            return create_long_context_model(config)
        return create_fallback_models(config)

    @property
    def model_name(self) -> str:
        return self._middleware._config.main_model

    @property
    def system(self) -> str:
        return "openai"

    def __getattr__(self, name: str):
        return getattr(
            self._resolve_pydantic_model(self._middleware._config.main_model),
            name,
        )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_llm_middleware.py -v`
Expected: all pass

- [ ] **Step 5: Run full suite**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/llm_middleware.py tests/test_llm_middleware.py
git commit -m "feat: add LLMMiddleware with model routing and overflow retry"
```

---

### Task 3: Wire middleware into pipeline initialization

**Files:**
- Modify: `codewiki/src/be/documentation_generator.py:13,76-77,400`
- Modify: `codewiki/src/be/agent_tools/deps.py:25-26`

- [ ] **Step 1: Add middleware to DocumentationGenerator.__init__**

In `codewiki/src/be/documentation_generator.py`, add import and create middleware:

```python
# Add import (near line 13)
from codewiki.src.be.llm_middleware import LLMMiddleware

# In __init__ (near line 76-77), add after usage_stats creation:
self.usage_stats = LLMUsageStats()
self.middleware = LLMMiddleware(config, usage_stats=self.usage_stats)
self.agent_orchestrator = AgentOrchestrator(config, usage_stats=self.usage_stats)
```

- [ ] **Step 2: Add middleware field to CodeWikiDeps**

In `codewiki/src/be/agent_tools/deps.py`, add:

```python
from codewiki.src.be.llm_middleware import LLMMiddleware

# Add to CodeWikiDeps dataclass fields (after fallback_models/long_context_model):
middleware: LLMMiddleware | None = None
```

- [ ] **Step 3: Run full suite**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass (new fields are optional/None by default)

- [ ] **Step 4: Commit**

```bash
git add codewiki/src/be/documentation_generator.py codewiki/src/be/agent_tools/deps.py
git commit -m "refactor: wire LLMMiddleware into pipeline and deps"
```

---

### Task 4: Migrate `agent_orchestrator.py`

**Files:**
- Modify: `codewiki/src/be/agent_orchestrator.py:74,101-112,139-164,166-209,444-532`
- Modify: `tests/test_agent_orchestrator_behavior.py`

- [ ] **Step 1: Update AgentOrchestrator to accept and use middleware**

In `codewiki/src/be/agent_orchestrator.py`:

1. Add import: `from codewiki.src.be.llm_middleware import LLMMiddleware`
2. Replace `__init__` to accept `middleware` parameter
3. Replace `create_agent` to use `middleware.create_agent_model()`
4. Delete `_is_context_overflow` method
5. Delete `_CONTEXT_TRIM_STEP`, `_MAX_CONTEXT_RETRIES` constants
6. Simplify `process_module` — remove the overflow retry loop (lines 449-532). The `MiddlewareModel` handles it internally.
7. Remove imports of `create_fallback_models`, `create_long_context_model` (line 74)

Key changes to `__init__`:
```python
def __init__(self, config: CodeWikiConfig, middleware: LLMMiddleware, usage_stats: LLMUsageStats | None = None):
    self.config = config
    self.usage_stats = usage_stats
    self._middleware = middleware
    self.custom_instructions = config.get_prompt_addition() if config else None
    self.output_language = config.output_language if config else "en"
    self.index_products = None
    self.global_assets = None
```

Key changes to `create_agent`:
```python
def create_agent(self, module_name, components, core_component_ids, estimated_tokens=0):
    model = self._middleware.create_agent_model()
    custom_instructions = self.custom_instructions or ""
    # ... rest stays same, just use `model` directly
```

Key changes to `process_module`:

1. Wire middleware into CodeWikiDeps construction (around line 418):
```python
deps = CodeWikiDeps(
    ...,
    middleware=self._middleware,
    fallback_models=None,        # no longer needed, middleware handles routing
    long_context_model=None,     # no longer needed
    ...
)
```

2. The whole overflow loop (lines 449-532) becomes a single call — MiddlewareModel handles overflow internally:
```python
result = await agent.run(
    user_prompt + f"\n\nWrite your documentation to the file: {assigned_filename}",
    deps=deps,
    usage_limits=UsageLimits(request_limit=None),
    event_stream_handler=agent_progress_handler,
)
```

- [ ] **Step 2: Update DocumentationGenerator to pass middleware to orchestrator**

In `codewiki/src/be/documentation_generator.py` (near line 77):
```python
self.agent_orchestrator = AgentOrchestrator(config, middleware=self.middleware, usage_stats=self.usage_stats)
```

- [ ] **Step 3: Update tests**

In `tests/test_agent_orchestrator_behavior.py`, update test helper to pass middleware:
```python
def _make_orchestrator(config, tmp_path):
    from codewiki.src.be.llm_middleware import LLMMiddleware
    middleware = LLMMiddleware(config)
    return AgentOrchestrator(config, middleware=middleware)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_agent_orchestrator_behavior.py tests/ -x -q --tb=short`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/agent_orchestrator.py codewiki/src/be/documentation_generator.py tests/test_agent_orchestrator_behavior.py
git commit -m "refactor: migrate agent_orchestrator to LLMMiddleware"
```

---

### Task 5: Migrate `generate_sub_module_documentations.py`

**Files:**
- Modify: `codewiki/src/be/agent_tools/generate_sub_module_documentations.py:14,235-238,247-269`

- [ ] **Step 1: Replace model selection with middleware**

1. Remove import of `select_agent_model` (line 14)
2. Replace model selection (lines 235-238) with:
```python
model = ctx.deps.middleware.create_agent_model()
```
3. Use `model` directly in Agent creation (lines 247, 261)

Note: `ctx.deps.middleware` is guaranteed non-None after Task 4 wires it into CodeWikiDeps construction.

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add codewiki/src/be/agent_tools/generate_sub_module_documentations.py
git commit -m "refactor: migrate sub-module agent to LLMMiddleware"
```

---

### Task 6: Migrate `documentation_overview.py`

**Files:**
- Modify: `codewiki/src/be/documentation_overview.py:128-137,346-390`

- [ ] **Step 1: Replace call_llm with middleware.call**

1. Replace `call_llm` field with `middleware` field in `OverviewContext`:
```python
# Remove: call_llm: Any = None
# Add:
middleware: Any = None  # LLMMiddleware instance
```

2. Replace the entire LLM call block (lines 372-384) — no fallback, middleware is required:
```python
result = await asyncio.to_thread(ctx.middleware.call, prompt)
parent_docs = result.content
if ctx.usage_stats and result.usage:
    ctx.usage_stats.record(
        result.model or config.main_model,
        result.usage.input_tokens,
        result.usage.output_tokens,
    )
```

3. Remove the manual truncation block (lines 346-359) — middleware handles it.

4. Remove the `import inspect` if no longer used (was only for `iscoroutinefunction` check).

5. Update `DocumentationGenerator` to pass `middleware=self.middleware` to OverviewContext (instead of `call_llm=call_llm`).

6. Remove `from codewiki.src.be.llm_services import call_llm` from `documentation_generator.py` (line 13).

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add codewiki/src/be/documentation_overview.py codewiki/src/be/documentation_generator.py
git commit -m "refactor: migrate documentation_overview to LLMMiddleware"
```

---

### Task 7: Migrate `guide_generator.py`

**Files:**
- Modify: `codewiki/src/be/guide_generator.py:23,231-315`

- [ ] **Step 1: Accept middleware in GuideGenerator.__init__**

Add `middleware: LLMMiddleware | None = None` parameter, store as `self._middleware`.

- [ ] **Step 2: Replace call_llm in _call_llm_with_fallback**

Replace the inner `call_llm` call (lines 286-295) with `self._middleware.call(prompt, model=model_name)`. Keep the outer `with_retry()` wrapper, cancel token, semaphore, timeout→stream fallback, and model chain logic intact.

Note: current code uses `self.cancel_token` (no underscore prefix) and hardcodes `max_retries=2`. Match these exactly:

```python
# Inside the model loop (around line 286-295):
result = await with_retry(
    asyncio.to_thread,
    self._middleware.call,
    prompt,
    model=model_name,
    max_retries=2,
    on_timeout_use_stream=True,
    cancel_token=self.cancel_token,
)
```

- [ ] **Step 3: Update DocumentationGenerator to pass middleware**

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/guide_generator.py codewiki/src/be/documentation_generator.py
git commit -m "refactor: migrate guide_generator to LLMMiddleware"
```

---

### Task 8: Migrate `cluster_modules.py` and `clustering/naming.py`

**Files:**
- Modify: `codewiki/src/be/cluster_modules.py:17,585`
- Modify: `codewiki/src/be/clustering/naming.py:7,111`

- [ ] **Step 1: Accept middleware parameter in clustering functions**

In `cluster_modules.py`, the function that calls `call_llm` (around line 585) needs to accept a `middleware` parameter. Replace:
```python
response = with_retry_sync(call_llm, prompt, config, model=config.cluster_model, max_retries=1)
```
with:
```python
response = with_retry_sync(middleware.call, prompt, model=config.cluster_model, max_retries=1)
```

Similarly in `clustering/naming.py` line 111.

- [ ] **Step 2: Thread middleware parameter through call chain**

Trace the call chain upward and add `middleware` parameter where needed.

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add codewiki/src/be/cluster_modules.py codewiki/src/be/clustering/naming.py
git commit -m "refactor: migrate clustering to LLMMiddleware"
```

---

### Task 9: Migrate `mermaid_validator.py` and `math_validator.py`

**Files:**
- Modify: `codewiki/src/be/postprocess/mermaid_validator.py:17,331-338`
- Modify: `codewiki/src/be/postprocess/math_validator.py:18,323-330`

- [ ] **Step 1: Accept middleware and replace call_llm**

In both files, replace:
```python
result = with_retry_sync(call_llm, prompt, config, model=model_name, ...)
```
with:
```python
result = with_retry_sync(middleware.call, prompt, model=model_name, ...)
```

Thread `middleware` parameter through from the postprocess pipeline entry point.

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add codewiki/src/be/postprocess/mermaid_validator.py codewiki/src/be/postprocess/math_validator.py
git commit -m "refactor: migrate postprocess validators to LLMMiddleware"
```

---

### Task 10: Delete `call_llm` and `select_agent_model`

**Files:**
- Modify: `codewiki/src/be/llm_services.py`

- [ ] **Step 1: Verify no remaining references to call_llm**

Run: `grep -rn "call_llm\|select_agent_model" codewiki/src/ --include="*.py" | grep -v "raw_llm_call" | grep -v "__pycache__"`

Expected: only the definitions in `llm_services.py` (the `call_llm` and `select_agent_model` functions to be deleted). No other references should remain.

- [ ] **Step 2: Delete call_llm and select_agent_model**

Remove the `call_llm` function and `select_agent_model` function from `llm_services.py`.

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add codewiki/src/be/llm_services.py
git commit -m "refactor: remove call_llm and select_agent_model (replaced by LLMMiddleware)"
```

---

### Task 11: Integration test — Agent overflow via request_stream

**Files:**
- Test: `tests/test_llm_middleware.py` (add)

- [ ] **Step 1: Write integration test for MiddlewareModel with Agent**

```python
@pytest.mark.asyncio
async def test_middleware_model_is_valid_pydantic_model(tmp_path):
    """MiddlewareModel passes pydantic-ai's isinstance(model, Model) check."""
    from pydantic_ai.models import Model
    from codewiki.src.be.llm_middleware import LLMMiddleware
    mw = LLMMiddleware(_make_config(tmp_path))
    model = mw.create_agent_model()
    assert isinstance(model, Model)
    assert model.model_name == "test/main"
    assert model.system == "openai"
```

- [ ] **Step 2: Run test**

Run: `uv run pytest tests/test_llm_middleware.py::test_middleware_model_is_valid_pydantic_model -v`
Expected: PASS

- [ ] **Step 3: Run full suite one final time**

Run: `uv run pytest tests/ -x -q --tb=short`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_llm_middleware.py
git commit -m "test: add integration test for MiddlewareModel pydantic-ai compatibility"
```
