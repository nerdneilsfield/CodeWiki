# Resilience Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured error classification, cooperative pipeline cancellation, and an explicit LLM retry wrapper with streaming fallback to the CodeWiki generation pipeline.

**Architecture:** Three phases following the spec's dependency order. Phase 1 (#5) defines error types that Phase 2 (#6) and Phase 3 (#2) consume. `CancellationError` from Phase 1 is used by the cancellation token in Phase 2. `LLMError.is_retryable` drives retry decisions in Phase 3. Streaming fallback is a sub-task of Phase 3, restricted to `openai_compatible` providers with model-level `stream=true` configuration.

**Tech Stack:** Python 3.13, asyncio, threading, pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `codewiki/src/be/errors.py` | Create | ErrorCategory, LLMError, PipelineError, CancellationError, classify_llm_exception |
| `codewiki/src/be/cancellation.py` | Create | CancellationToken |
| `codewiki/src/be/llm_retry.py` | Create | with_retry, LLMRetryExhausted |
| `codewiki/src/be/llm_services.py` | Modify | Wrap exceptions with classify_llm_exception; add stream parameter; restore _call_llm_streaming |
| `codewiki/src/be/pipeline.py` | Modify | Cancelled status; CancellationError handling; cancel_token on PipelineContext |
| `codewiki/src/be/documentation_scheduler.py` | Modify | Use LLMError.is_retryable; cancel check in coordinator |
| `codewiki/src/be/guide_generator.py` | Modify | Accept cancel_token; wrap calls with with_retry |
| `codewiki/src/be/docs_fixer.py` | Modify | Wrap calls with with_retry |
| `codewiki/src/be/cluster_modules.py` | Modify | Wrap calls with with_retry |
| `codewiki/src/be/clustering/naming.py` | Modify | Wrap calls with with_retry |
| `codewiki/src/config_loader.py` | Modify | Parse polymorphic model_list; ResolvedModel.stream field |
| `codewiki/src/codewiki_config.py` | Modify | ProviderConfig.model_list accepts str|dict items |
| `codewiki/src/fe/background_worker.py` | Modify | cancel_tokens dict; cancel_job(); CancellationError handling |
| `codewiki/src/fe/routes.py` | Modify | Cancel API endpoint; cancelled status display |
| `codewiki/src/fe/models.py` | Modify | cancelled status on JobStatus/JobStatusResponse |
| `codewiki/cli/adapters/doc_generator.py` | Modify | cancelled display branch |

---

## Phase 1: #5 Structured Error Classification

### Task 1: Error types + classify_llm_exception

**Files:**
- Create: `codewiki/src/be/errors.py`
- Create: `tests/test_error_classification.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_error_classification.py
import pytest


class TestErrorCategory:
    def test_retryable_transient(self):
        from codewiki.src.be.errors import ErrorCategory
        assert ErrorCategory.RETRYABLE_TRANSIENT.value == "retryable_transient"

    def test_all_categories_exist(self):
        from codewiki.src.be.errors import ErrorCategory
        names = {c.name for c in ErrorCategory}
        assert names == {
            "RETRYABLE_TRANSIENT", "RETRYABLE_AUTH",
            "NON_RETRYABLE_CLIENT", "NON_RETRYABLE_CONFIG",
            "RESOURCE_EXHAUSTED",
        }


class TestLLMError:
    def test_is_retryable_transient(self):
        from codewiki.src.be.errors import LLMError, ErrorCategory
        err = LLMError("timeout", ErrorCategory.RETRYABLE_TRANSIENT)
        assert err.is_retryable

    def test_is_retryable_auth(self):
        from codewiki.src.be.errors import LLMError, ErrorCategory
        err = LLMError("auth", ErrorCategory.RETRYABLE_AUTH)
        assert err.is_retryable

    def test_not_retryable_client(self):
        from codewiki.src.be.errors import LLMError, ErrorCategory
        err = LLMError("bad input", ErrorCategory.NON_RETRYABLE_CLIENT, status_code=400)
        assert not err.is_retryable

    def test_not_retryable_config(self):
        from codewiki.src.be.errors import LLMError, ErrorCategory
        err = LLMError("no key", ErrorCategory.NON_RETRYABLE_CONFIG)
        assert not err.is_retryable


class TestClassifyLlmException:
    def test_timeout_is_transient(self):
        from codewiki.src.be.errors import classify_llm_exception, ErrorCategory
        import openai
        exc = openai.APITimeoutError(request=None)
        result = classify_llm_exception(exc)
        assert result.category == ErrorCategory.RETRYABLE_TRANSIENT

    def test_rate_limit_is_transient(self):
        from codewiki.src.be.errors import classify_llm_exception, ErrorCategory

        # Simple exception with status_code attribute — no MagicMock __mro__ hacks
        class FakeAPIError(Exception):
            def __init__(self, status_code):
                self.status_code = status_code
                super().__init__(f"status {status_code}")

        result = classify_llm_exception(FakeAPIError(429))
        assert result.category == ErrorCategory.RETRYABLE_TRANSIENT

    def test_400_is_client_error(self):
        from codewiki.src.be.errors import classify_llm_exception, ErrorCategory

        class FakeAPIError(Exception):
            def __init__(self):
                self.status_code = 400
                self.message = "bad request"
                self.body = None
                super().__init__("bad request")

        result = classify_llm_exception(FakeAPIError())
        assert result.category == ErrorCategory.NON_RETRYABLE_CLIENT

    def test_context_length_is_resource_exhausted(self):
        from codewiki.src.be.errors import classify_llm_exception, ErrorCategory

        class FakeAPIError(Exception):
            def __init__(self):
                self.status_code = 400
                self.message = "context_length_exceeded"
                self.body = {"error": {"code": "context_length_exceeded"}}
                super().__init__("context_length_exceeded")

        result = classify_llm_exception(FakeAPIError())
        assert result.category == ErrorCategory.RESOURCE_EXHAUSTED

    def test_value_error_is_config(self):
        from codewiki.src.be.errors import classify_llm_exception, ErrorCategory
        result = classify_llm_exception(ValueError("missing model"))
        assert result.category == ErrorCategory.NON_RETRYABLE_CONFIG

    def test_unknown_error_reraised(self):
        from codewiki.src.be.errors import classify_llm_exception
        with pytest.raises(RuntimeError, match="unexpected"):
            classify_llm_exception(RuntimeError("unexpected"))


class TestCancellationError:
    def test_is_independent(self):
        from codewiki.src.be.errors import CancellationError, LLMError
        err = CancellationError("cancelled")
        assert not isinstance(err, LLMError)
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_error_classification.py -v`

- [ ] **Step 3: Implement errors.py**

```python
# codewiki/src/be/errors.py
"""Structured error classification for LLM and pipeline errors."""
from enum import Enum


class ErrorCategory(Enum):
    RETRYABLE_TRANSIENT = "retryable_transient"
    RETRYABLE_AUTH = "retryable_auth"
    NON_RETRYABLE_CLIENT = "non_retryable_client"
    NON_RETRYABLE_CONFIG = "non_retryable_config"
    RESOURCE_EXHAUSTED = "resource_exhausted"


class LLMError(Exception):
    """LLM API call error with automatic classification."""
    def __init__(self, message: str, category: ErrorCategory,
                 status_code: int | None = None):
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
    """Pipeline stage error with category."""
    def __init__(self, message: str, category: ErrorCategory, stage: str = ""):
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
    pass


_RETRYABLE_STATUS = {429, 500, 502, 503, 529}
_AUTH_STATUS = {401, 403}


def classify_llm_exception(exc: Exception) -> LLMError:
    """Classify an SDK exception into LLMError. Re-raises unknown exceptions."""
    import openai

    # Timeout (no status_code)
    if isinstance(exc, openai.APITimeoutError):
        return LLMError(str(exc), ErrorCategory.RETRYABLE_TRANSIENT)

    # Config/input errors
    if isinstance(exc, (ValueError, KeyError)):
        return LLMError(str(exc), ErrorCategory.NON_RETRYABLE_CONFIG)

    # SDK status errors
    status = getattr(exc, "status_code", None)
    if status is not None:
        # Context length exceeded (400 but with specific code)
        if status == 400:
            body = getattr(exc, "body", None)
            msg = str(getattr(exc, "message", exc))
            if body and isinstance(body, dict):
                code = body.get("error", {}).get("code", "")
                if "context_length" in code:
                    return LLMError(str(exc), ErrorCategory.RESOURCE_EXHAUSTED, status)
            if "context_length" in msg or "maximum context" in msg.lower():
                return LLMError(str(exc), ErrorCategory.RESOURCE_EXHAUSTED, status)
            return LLMError(str(exc), ErrorCategory.NON_RETRYABLE_CLIENT, status)

        if status in _RETRYABLE_STATUS:
            return LLMError(str(exc), ErrorCategory.RETRYABLE_TRANSIENT, status)
        if status in _AUTH_STATUS:
            return LLMError(str(exc), ErrorCategory.RETRYABLE_AUTH, status)
        if status == 404:
            return LLMError(str(exc), ErrorCategory.NON_RETRYABLE_CLIENT, status)
        return LLMError(str(exc), ErrorCategory.RETRYABLE_TRANSIENT, status)

    # Connection errors (httpx)
    try:
        import httpx
        if isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout)):
            return LLMError(str(exc), ErrorCategory.RETRYABLE_TRANSIENT)
    except ImportError:
        pass

    # Unknown — re-raise, don't wrap
    raise exc
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_error_classification.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/errors.py tests/test_error_classification.py
git commit -m "feat(errors): add structured error classification with LLMError/PipelineError/CancellationError"
```

---

### Task 2: Wire classify_llm_exception into call_llm + update PipelineRunner + scheduler

**Files:**
- Modify: `codewiki/src/be/llm_services.py`
- Modify: `codewiki/src/be/pipeline.py`
- Modify: `codewiki/src/be/documentation_scheduler.py`
- Modify: `codewiki/src/fe/models.py`
- Modify: `codewiki/cli/adapters/doc_generator.py`
- Create: `tests/test_error_wiring.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_error_wiring.py
import pytest
from unittest.mock import MagicMock, patch


class TestCallLlmRaisesLLMError:
    def test_timeout_becomes_llm_error(self):
        from codewiki.src.be.llm_services import call_llm
        from codewiki.src.be.errors import LLMError, ErrorCategory
        import openai

        config = MagicMock()
        config.main_model = "test"
        config.max_tokens = 100
        config.long_context_model = None
        config.providers = None
        config.llm_base_url = "http://localhost"
        config.llm_api_key = "key"

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = openai.APITimeoutError(request=None)

        with patch("codewiki.src.be.llm_services._create_client_for_model",
                    return_value=(mock_client, "openai_compatible")):
            with pytest.raises(LLMError) as exc_info:
                call_llm("test", config)
            assert exc_info.value.category == ErrorCategory.RETRYABLE_TRANSIENT


class TestPipelineRunnerCancelled:
    @pytest.mark.asyncio
    async def test_cancelled_status_in_result(self):
        from codewiki.src.be.pipeline import PipelineRunner, PipelineContext
        from codewiki.src.be.errors import CancellationError

        class CancelStage:
            name = "cancel"
            failure_policy = "fail_fast"
            async def execute(self, ctx):
                raise CancellationError("user cancelled")

        runner = PipelineRunner([CancelStage()])
        ctx = PipelineContext(config=None)
        result = await runner.execute(ctx)
        assert result.status == "cancelled"
```

- [ ] **Step 2: Implement wiring**

In `codewiki/src/be/llm_services.py`, wrap the existing try block at the end of `call_llm`:
```python
    try:
        # ... existing provider dispatch + content extraction ...
        return LLMCallResult(content=content, usage=usage, model=model)
    except CancellationError:
        raise
    except Exception as exc:
        from codewiki.src.be.errors import classify_llm_exception
        raise classify_llm_exception(exc) from exc
```

In `codewiki/src/be/pipeline.py`:
- Change `Literal["complete", "degraded", "failed"]` → `Literal["complete", "degraded", "failed", "cancelled"]`
- Update `PipelineRunner.execute()`:
```python
    except CancellationError:
        ctx.result.status = "cancelled"
        logger.info("⏹ Pipeline cancelled")
        break
    except Exception as exc:
        # existing fail_fast / degraded_ok logic
```

In `codewiki/src/fe/models.py`: add `"cancelled"` to `JobStatus.status` docstring; `generation_status` already accepts `Optional[str]`.

In `codewiki/cli/adapters/doc_generator.py`: add `elif result.status == "cancelled":` display branch.

In `codewiki/src/be/documentation_scheduler.py`: **add** `LLMError` category checks as a new branch in `_retry_delay`, but **keep** existing `UnexpectedModelBehavior` and `_is_context_length_error` checks for now. The agent path (via `process_module` → `pydantic_ai`) raises `UnexpectedModelBehavior`, not `LLMError`, so deleting the old checks would break agent retries. The ordering is:
```python
def _retry_delay(attempt, exc):
    # New: LLMError from direct call_llm callers
    if isinstance(exc, LLMError):
        if exc.category == ErrorCategory.RESOURCE_EXHAUSTED:
            return 0  # skip retries
        if not exc.is_retryable:
            return 0  # immediate re-attempt with different params
        return retry_delays[attempt - 1]
    # Existing: pydantic_ai agent errors (keep until agent path is unified)
    if isinstance(exc, UnexpectedModelBehavior) or _is_context_length_error(exc):
        return 0
    return retry_delays[attempt - 1]
```
The old branches will be removed in a future task when agent errors are also classified as `LLMError`.

- [ ] **Step 3: Run tests + regression**

Run: `pytest tests/test_error_wiring.py tests/test_error_classification.py tests/test_pipeline_types.py tests/test_pipeline_stages.py -v`

- [ ] **Step 4: Commit**

```bash
git add codewiki/src/be/llm_services.py codewiki/src/be/pipeline.py codewiki/src/be/documentation_scheduler.py codewiki/src/fe/models.py codewiki/cli/adapters/doc_generator.py tests/test_error_wiring.py
git commit -m "feat(errors): wire classify_llm_exception into call_llm, pipeline, and scheduler"
```

---

## Phase 2: #6 Cancellable Pipeline

### Task 3: CancellationToken + PipelineRunner integration

**Files:**
- Create: `codewiki/src/be/cancellation.py`
- Modify: `codewiki/src/be/pipeline.py`
- Create: `tests/test_cancellation.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cancellation.py
import pytest
import threading
from codewiki.src.be.errors import CancellationError


class TestCancellationToken:
    def test_not_cancelled_initially(self):
        from codewiki.src.be.cancellation import CancellationToken
        token = CancellationToken()
        assert not token.is_cancelled

    def test_cancel_sets_flag(self):
        from codewiki.src.be.cancellation import CancellationToken
        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled

    def test_check_raises_when_cancelled(self):
        from codewiki.src.be.cancellation import CancellationToken
        token = CancellationToken()
        token.cancel()
        with pytest.raises(CancellationError):
            token.check()

    def test_check_does_not_raise_when_not_cancelled(self):
        from codewiki.src.be.cancellation import CancellationToken
        token = CancellationToken()
        token.check()  # should not raise

    def test_thread_safe(self):
        from codewiki.src.be.cancellation import CancellationToken
        token = CancellationToken()
        results = []

        def cancel_from_thread():
            token.cancel()
            results.append("cancelled")

        t = threading.Thread(target=cancel_from_thread)
        t.start()
        t.join()
        assert token.is_cancelled
        assert results == ["cancelled"]


class TestPipelineRunnerWithCancelToken:
    @pytest.mark.asyncio
    async def test_cancels_before_stage(self):
        from codewiki.src.be.cancellation import CancellationToken
        from codewiki.src.be.pipeline import PipelineRunner, PipelineContext

        executed = []

        class Stage:
            name = "stage1"
            failure_policy = "degraded_ok"
            async def execute(self, ctx):
                executed.append(self.name)

        token = CancellationToken()
        token.cancel()  # pre-cancel
        ctx = PipelineContext(config=None, cancel_token=token)

        runner = PipelineRunner([Stage()])
        result = await runner.execute(ctx)

        assert result.status == "cancelled"
        assert "stage1" not in executed
```

- [ ] **Step 2: Implement**

`codewiki/src/be/cancellation.py`:
```python
import threading
from codewiki.src.be.errors import CancellationError


class CancellationToken:
    """Cooperative cancellation token — thread-safe via threading.Event."""

    def __init__(self):
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check(self) -> None:
        if self._cancelled.is_set():
            raise CancellationError("Operation cancelled")
```

`PipelineContext`: add `cancel_token: Any = None`.

`PipelineRunner.execute()`: before each `stage.execute(ctx)`, add:
```python
if ctx.cancel_token and ctx.cancel_token.is_cancelled:
    ctx.result.status = "cancelled"
    logger.info("⏹ Pipeline cancelled before stage %s", stage.name)
    break
```

- [ ] **Step 3: Run tests + commit**

```bash
pytest tests/test_cancellation.py -v
git add codewiki/src/be/cancellation.py codewiki/src/be/pipeline.py tests/test_cancellation.py
git commit -m "feat(cancel): add CancellationToken with pipeline integration"
```

---

### Task 4: Wire cancellation into scheduler, guide_generator, background_worker, web API

**Files:**
- Modify: `codewiki/src/be/documentation_scheduler.py`
- Modify: `codewiki/src/be/guide_generator.py`
- Modify: `codewiki/src/fe/background_worker.py`
- Modify: `codewiki/src/fe/routes.py`
- Create: `tests/test_cancel_wiring.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cancel_wiring.py
import pytest
import threading


class TestBackgroundWorkerCancel:
    def test_cancel_job_returns_true_for_active(self):
        from codewiki.src.fe.background_worker import BackgroundWorker
        from codewiki.src.be.cancellation import CancellationToken

        worker = BackgroundWorker.__new__(BackgroundWorker)
        worker._cancel_tokens = {"j1": CancellationToken()}
        worker._job_lock = threading.Lock()
        assert worker.cancel_job("j1") is True

    def test_cancel_job_returns_false_for_missing(self):
        from codewiki.src.fe.background_worker import BackgroundWorker

        worker = BackgroundWorker.__new__(BackgroundWorker)
        worker._cancel_tokens = {}
        worker._job_lock = threading.Lock()
        assert worker.cancel_job("nonexistent") is False


class TestGuideGeneratorCancelToken:
    def test_accepts_cancel_token(self):
        """GuideGenerator constructor must accept cancel_token parameter."""
        import inspect
        from codewiki.src.be.guide_generator import GuideGenerator
        sig = inspect.signature(GuideGenerator.__init__)
        assert "cancel_token" in sig.parameters
```

- [ ] **Step 2: Implement**

**Scheduler coordinator** — after each `done_queue.get()`, check cancel:
```python
if cancel_token and cancel_token.is_cancelled:
    logger.info("⏹ Scheduler cancelled — stopping work queue")
    break
```

Pass `cancel_token` into `run_module_queue` via a new parameter.

**GuideGenerator.__init__** — add `cancel_token=None`. In `_call_llm_with_fallback` entry:
```python
if self.cancel_token:
    self.cancel_token.check()
```

**BackgroundWorker**:
- `self._cancel_tokens: dict[str, CancellationToken] = {}` in `__init__`
- `cancel_job(job_id)` method
- `_process_job`: create token, pass to pipeline context, catch `CancellationError` → set `job.status = "cancelled"`

**routes.py**: add cancel endpoint:
```python
async def cancel_job(self, request: Request, job_id: str):
    if self.background_worker.cancel_job(job_id):
        return JSONResponse({"status": "cancelling"})
    raise HTTPException(status_code=404, detail="Job not found or not running")
```

- [ ] **Step 3: Run tests + regression**

```bash
pytest tests/test_cancel_wiring.py tests/test_cancellation.py -v
```

- [ ] **Step 4: Commit**

```bash
git add codewiki/src/be/documentation_scheduler.py codewiki/src/be/guide_generator.py codewiki/src/fe/background_worker.py codewiki/src/fe/routes.py tests/test_cancel_wiring.py
git commit -m "feat(cancel): wire cancellation into scheduler, guide generator, web worker, and API"
```

---

## Phase 3: #2 LLM Retry Wrapper + Streaming Fallback

### Task 5: with_retry wrapper

**Files:**
- Create: `codewiki/src/be/llm_retry.py`
- Create: `tests/test_llm_retry.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_llm_retry.py
import asyncio
import pytest
from codewiki.src.be.errors import LLMError, ErrorCategory, CancellationError


class TestWithRetry:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_try(self):
        from codewiki.src.be.llm_retry import with_retry

        async def ok():
            return "success"

        result = await with_retry(ok, max_retries=3)
        assert result == "success"

    @pytest.mark.asyncio
    async def test_retries_transient_error(self):
        from codewiki.src.be.llm_retry import with_retry

        calls = []
        async def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise LLMError("timeout", ErrorCategory.RETRYABLE_TRANSIENT)
            return "ok"

        result = await with_retry(flaky, max_retries=5)
        assert result == "ok"
        assert len(calls) == 3

    @pytest.mark.asyncio
    async def test_does_not_retry_non_retryable(self):
        from codewiki.src.be.llm_retry import with_retry

        calls = []
        async def bad_input():
            calls.append(1)
            raise LLMError("bad", ErrorCategory.NON_RETRYABLE_CLIENT, status_code=400)

        with pytest.raises(LLMError):
            await with_retry(bad_input, max_retries=5)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_auth_retried_once(self):
        from codewiki.src.be.llm_retry import with_retry

        calls = []
        async def auth_fail():
            calls.append(1)
            raise LLMError("auth", ErrorCategory.RETRYABLE_AUTH, status_code=401)

        with pytest.raises(LLMError):
            await with_retry(auth_fail, max_retries=5)
        assert len(calls) == 2  # original + 1 auth retry

    @pytest.mark.asyncio
    async def test_cancellation_stops_retry(self):
        from codewiki.src.be.llm_retry import with_retry
        from codewiki.src.be.cancellation import CancellationToken

        token = CancellationToken()
        calls = []

        async def slow():
            calls.append(1)
            if len(calls) == 1:
                token.cancel()  # cancel after first try
                raise LLMError("timeout", ErrorCategory.RETRYABLE_TRANSIENT)
            return "should not reach"

        with pytest.raises(CancellationError):
            await with_retry(slow, max_retries=5, cancel_token=token)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_exhausted_raises_llm_retry_exhausted(self):
        from codewiki.src.be.llm_retry import with_retry, LLMRetryExhausted

        async def always_fail():
            raise LLMError("fail", ErrorCategory.RETRYABLE_TRANSIENT)

        with pytest.raises(LLMRetryExhausted) as exc_info:
            await with_retry(always_fail, max_retries=2)
        assert exc_info.value.attempts == 3  # 1 original + 2 retries
```

- [ ] **Step 2: Implement llm_retry.py**

```python
# codewiki/src/be/llm_retry.py
"""LLM call retry wrapper with exponential backoff and error classification."""
import asyncio
import logging
import random
from typing import TypeVar, Callable, Awaitable, Any

from codewiki.src.be.errors import LLMError, ErrorCategory, CancellationError

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
    msg = str(error).lower()
    return (
        "timeout" in msg
        or "524" in msg
        or "cloudflare" in msg
        or "stream disconnected" in msg
    )


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
            # Streaming fallback on timeout
            if on_timeout_use_stream and _is_timeout(exc):
                kwargs["stream"] = True
                _logger.info("Timeout detected, switching to streaming for next retry")
            retry_after = _get_retry_after(exc)
            delay = _compute_delay(attempt, retry_after)
            _logger.warning(
                "LLM retry %d/%d in %.1fs: %s", attempt, total_attempts, delay, exc
            )
            if cancel_token:
                cancel_token.check()
            await asyncio.sleep(delay)
        except Exception:
            raise  # Unknown errors pass through

    raise LLMRetryExhausted(last_error, total_attempts)  # type: ignore
```

- [ ] **Step 3: Run tests + commit**

```bash
pytest tests/test_llm_retry.py -v
git add codewiki/src/be/llm_retry.py tests/test_llm_retry.py
git commit -m "feat(retry): add with_retry wrapper with exponential backoff and cancellation"
```

---

### Task 6: Wire with_retry into callers + streaming config + _call_llm_streaming

**Files:**
- Modify: `codewiki/src/be/guide_generator.py`
- Modify: `codewiki/src/be/docs_fixer.py`
- Modify: `codewiki/src/be/cluster_modules.py`
- Modify: `codewiki/src/be/clustering/naming.py`
- Modify: `codewiki/src/be/llm_services.py` (add stream param + restore streaming)
- Modify: `codewiki/src/config_loader.py` (polymorphic model_list)
- Create: `tests/test_streaming_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_streaming_config.py
import pytest


class TestPolymorphicModelList:
    def test_string_item_defaults_stream_false(self):
        from codewiki.src.config_loader import resolve_model_ref
        from codewiki.src.codewiki_config import ProviderConfig

        # resolve_model_ref requires provider/model format
        provider = ProviderConfig(
            name="openai", type="openai_compatible",
            base_url="http://localhost",
            api_keys=["key"],
            model_list=["gpt-4o"],
        )
        resolved = resolve_model_ref("openai/gpt-4o", [provider])
        assert resolved.stream is False

    def test_dict_item_with_stream_true(self):
        from codewiki.src.config_loader import resolve_model_ref
        from codewiki.src.codewiki_config import ProviderConfig

        provider = ProviderConfig(
            name="openai", type="openai_compatible",
            base_url="http://localhost",
            api_keys=["key"],
            model_list=[{"name": "gpt-4o", "stream": True}],
        )
        resolved = resolve_model_ref("openai/gpt-4o", [provider])
        assert resolved.stream is True


class TestCallLlmStreamParam:
    def test_call_llm_accepts_stream_parameter(self):
        import inspect
        from codewiki.src.be.llm_services import call_llm
        sig = inspect.signature(call_llm)
        assert "stream" in sig.parameters
```

- [ ] **Step 2: Implement streaming config**

In `codewiki/src/config_loader.py`:
- `ResolvedModel` add `stream: bool = False`
- In `_load_provider_configs`, normalize `model_list` items: `str` items become `{"name": str, "stream": False}`; dict items keep their `stream` value. After normalization, `ProviderConfig.model_list` is always `list[str]` (just names) — the stream metadata is stored separately on each `ProviderConfig` as `model_options: dict[str, dict]` (keyed by model name)
- In `resolve_model_ref`, look up `provider.model_options.get(model_name, {}).get("stream", False)` and set `ResolvedModel.stream`
- **Key constraint:** downstream code (`llm_services.py`, `guide_generator.py`, etc.) never sees `str | dict` union type. They only access `ResolvedModel.stream` — the polymorphism is fully resolved at load time

In `codewiki/src/be/llm_services.py`:
- Add `stream: bool = False` parameter to `call_llm`
- Restore `_call_llm_streaming` (OpenAI streaming only)
- When `stream=True` and `provider_type in {"openai_compatible", "azure_openai"}`, use streaming path
- Streaming usage = `LLMCallUsage(source="estimated")`

- [ ] **Step 3: Wire with_retry into callers**

**guide_generator.py** `_call_llm_with_fallback`:
```python
from codewiki.src.be.llm_retry import with_retry
from codewiki.src.be.errors import LLMError

for model_name in models:
    try:
        async with self._semaphore:
            if self.cancel_token:
                self.cancel_token.check()
            # Check if model supports streaming
            model_has_stream = self._model_supports_stream(model_name)
            result = await with_retry(
                asyncio.to_thread,
                call_llm, prompt, self.config, model=model_name,
                max_retries=2,
                cancel_token=self.cancel_token,
                on_timeout_use_stream=model_has_stream,
            )
            ...
    except LLMError as exc:
        if not exc.is_retryable:
            last_exc = exc
            continue  # skip to next model
        raise  # transient exhausted → bubble up
```

**docs_fixer.py**: wrap each `call_llm` in `with_retry(..., max_retries=1)`.

**cluster_modules.py** / **naming.py**: wrap each `call_llm` in `with_retry(..., max_retries=1)`.

- [ ] **Step 4: Run tests + full regression**

```bash
pytest tests/test_streaming_config.py tests/test_llm_retry.py -v
pytest tests/ -q -k "not network" --timeout=60
```

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/guide_generator.py codewiki/src/be/docs_fixer.py codewiki/src/be/cluster_modules.py codewiki/src/be/clustering/naming.py codewiki/src/be/llm_services.py codewiki/src/config_loader.py codewiki/src/codewiki_config.py tests/test_streaming_config.py
git commit -m "feat(retry): wire with_retry into callers, restore streaming with model-level config"
```

---

### Task 7: Full regression

- [ ] **Step 1: Compile all new files**

```bash
python3 -m py_compile codewiki/src/be/errors.py codewiki/src/be/cancellation.py codewiki/src/be/llm_retry.py
```

- [ ] **Step 2: Run all new tests**

```bash
pytest -v tests/test_error_classification.py tests/test_error_wiring.py tests/test_cancellation.py tests/test_cancel_wiring.py tests/test_llm_retry.py tests/test_streaming_config.py
```

- [ ] **Step 3: Full regression**

```bash
make test
```

- [ ] **Step 4: Commit if fixes needed**

```bash
git commit -m "test: verify resilience improvements"
```
