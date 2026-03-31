# Async AI Client Pool Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Eliminate per-module `AsyncOpenAI` / `OpenAIProvider` / model-object re-creation so the main documentation-generation trunk reuses a single connection pool across all concurrent workers, and sanitize `Retry-After` header values to prevent blocking or crash from malicious/misconfigured upstream responses.

**Architecture:** Three tasks. First, add `lru_cache`-backed helpers for `AsyncOpenAI` and `OpenAIProvider` in `llm_services.py` so `_make_provider()` always returns the same cached objects. Second, refactor `AgentOrchestrator` to pre-build both model objects at `__init__` time and have `create_agent()` pick between them — eliminating the per-module `select_agent_model()` call that rebuilds the entire fallback chain (and its connection pool) for every module. Third, clamp `Retry-After` header values to `[0, 120]` seconds in both retry loops.

**Tech Stack:** `functools.lru_cache`, `openai.AsyncOpenAI`, `pydantic_ai.providers.openai.OpenAIProvider`, `pydantic_ai.models.openai.OpenAIModel`, `pydantic_ai.models.fallback.FallbackModel`

---

## Background: why this matters

`AgentOrchestrator.create_agent()` (called once per module) currently does:

```
create_agent()
  └─ select_agent_model()          # called every module
       ├─ create_fallback_models() # for normal modules
       │    └─ _make_provider()    # → new AsyncOpenAI + new OpenAIProvider
       └─ create_long_context_model()  # for oversized modules
            └─ _make_provider()    # → another new AsyncOpenAI + OpenAIProvider
```

With `max_concurrent=10` and 200 modules, this creates up to 200 separate httpx connection pools instead of one. The `self.fallback_models` pre-built in `__init__` is **never used** by `create_agent()`.

**Note:** `generate_sub_module_documentations.py:159` also calls `select_agent_model()` per recursive sub-module agent. Task 1's `lru_cache` fix automatically covers this path because all callers flow through `_make_provider()` → `_get_cached_async_provider()`. Task 2 only benefits `AgentOrchestrator`; the sub-module tool still creates lightweight `OpenAIModel` wrappers per call, but without a new connection pool, the cost is negligible.

---

### Task 1: Cache `AsyncOpenAI` and `OpenAIProvider` in `llm_services.py`

**Files:**
- Modify: `codewiki/src/be/llm_services.py` (add two lru_cache helpers before `_make_provider`)
- Create: `tests/test_perf_async_client_pool.py`

**Step 1: Write failing tests**

Create `tests/test_perf_async_client_pool.py`:

```python
"""Verify that AsyncOpenAI clients and OpenAIProviders are module-level singletons."""


def test_cached_async_client_same_object():
    """Same (base_url, api_key) returns the identical AsyncOpenAI instance."""
    from codewiki.src.be.llm_services import _get_cached_async_client

    c1 = _get_cached_async_client("http://test-host/", "key-abc")
    c2 = _get_cached_async_client("http://test-host/", "key-abc")
    assert c1 is c2


def test_cached_async_client_different_keys_give_different_objects():
    """Different API keys yield distinct AsyncOpenAI instances."""
    from codewiki.src.be.llm_services import _get_cached_async_client

    c1 = _get_cached_async_client("http://test-host/", "key-aaa")
    c2 = _get_cached_async_client("http://test-host/", "key-bbb")
    assert c1 is not c2


def test_cached_async_provider_same_object():
    """Same (base_url, api_key) returns the identical OpenAIProvider instance."""
    from codewiki.src.be.llm_services import _get_cached_async_provider

    p1 = _get_cached_async_provider("http://test-host/", "key-xyz")
    p2 = _get_cached_async_provider("http://test-host/", "key-xyz")
    assert p1 is p2


def test_make_provider_reuses_cached_provider():
    """_make_provider() returns the same object on repeated calls with same config."""
    from unittest.mock import MagicMock
    from codewiki.src.be.llm_services import _make_provider

    cfg = MagicMock()
    cfg.llm_base_url = "http://test-host/"
    cfg.llm_api_key = "key-reuse"

    p1 = _make_provider(cfg)
    p2 = _make_provider(cfg)
    assert p1 is p2
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_perf_async_client_pool.py -v 2>&1 | head -30
```

Expected: tests 1–3 fail with `ImportError: cannot import name '_get_cached_async_client'`; test 4 (`test_make_provider_reuses_cached_provider`) fails with `AssertionError` because the current `_make_provider` creates a new object each call.

**Step 3: Implement — add two `lru_cache` helpers and update `_make_provider`**

In `codewiki/src/be/llm_services.py`, add the following two functions immediately **after** `_LLM_TIMEOUT = httpx.Timeout(180.0)` (after line 23) and **before** the existing `_make_provider`:

```python
@lru_cache(maxsize=4)
def _get_cached_async_client(base_url: str, api_key: str) -> AsyncOpenAI:
    """Return (and cache) an AsyncOpenAI client for the given endpoint.

    lru_cache keyed on (base_url, api_key) so the underlying httpx connection
    pool is reused across all concurrent agent.run() calls — no repeated TLS
    handshakes or pool allocations per module.
    maxsize=4 accommodates main + fallback + long-context endpoints.
    """
    return AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=_LLM_TIMEOUT,
    )


@lru_cache(maxsize=4)
def _get_cached_async_provider(base_url: str, api_key: str) -> OpenAIProvider:
    """Return (and cache) an OpenAIProvider wrapping a cached AsyncOpenAI client."""
    return OpenAIProvider(
        openai_client=_get_cached_async_client(base_url, api_key)
    )
```

Then replace the existing `_make_provider` function body:

```python
def _make_provider(config: Config) -> OpenAIProvider:
    """Return (cached) OpenAIProvider for the given config endpoint."""
    return _get_cached_async_provider(config.llm_base_url, config.llm_api_key)
```

**Step 4: Run tests to verify they pass**

```bash
pytest tests/test_perf_async_client_pool.py -v
```

Expected: all 4 tests PASS.

**Step 5: Run full test suite**

```bash
pytest --tb=short -q 2>&1 | tail -5
```

Expected: all existing tests still pass.

**Step 6: Commit**

```bash
git add codewiki/src/be/llm_services.py tests/test_perf_async_client_pool.py
git commit -m "perf(llm): cache AsyncOpenAI and OpenAIProvider via lru_cache

_make_provider() now delegates to lru_cache-backed helpers keyed on
(base_url, api_key).  Eliminates per-module AsyncOpenAI connection pool
allocation and TLS handshake on the pydantic_ai async path."
```

---

### Task 2: Pre-build model objects in `AgentOrchestrator`, remove per-module `select_agent_model()` call

**Files:**
- Modify: `codewiki/src/be/agent_orchestrator.py` (lines 70, 89–120)
- Modify: `tests/test_perf_async_client_pool.py` (add three more test functions)

**Step 1: Write failing tests — append to `tests/test_perf_async_client_pool.py`**

```python
def _make_dummy_config(long_context_model=None):
    from unittest.mock import MagicMock
    cfg = MagicMock()
    cfg.llm_base_url = "http://test-host/"
    cfg.llm_api_key = "key-test"
    cfg.main_model = "main-model"
    cfg.fallback_model = "fallback-model"
    cfg.long_context_model = long_context_model
    cfg.long_context_threshold = 50_000
    cfg.max_tokens = 4096
    cfg.max_depth = 2
    cfg.repo_path = "/tmp"
    cfg.output_language = "en"
    cfg.get_prompt_addition.return_value = None
    return cfg


def test_create_agent_does_not_call_create_fallback_models_per_module():
    """create_agent() must not rebuild FallbackModel on every call."""
    from unittest.mock import patch
    from codewiki.src.be.agent_orchestrator import AgentOrchestrator
    import codewiki.src.be.agent_orchestrator as orch_mod

    cfg = _make_dummy_config()
    with patch.object(orch_mod, "create_fallback_models",
                      wraps=orch_mod.create_fallback_models) as mock_cfm:
        orch = AgentOrchestrator(cfg)
        calls_after_init = mock_cfm.call_count  # exactly 1 call from __init__

        orch.create_agent("mod1", {}, [], estimated_tokens=0)
        orch.create_agent("mod2", {}, [], estimated_tokens=0)
        orch.create_agent("mod3", {}, [], estimated_tokens=0)

    # No additional calls beyond the one in __init__
    assert mock_cfm.call_count == calls_after_init, (
        f"create_fallback_models called {mock_cfm.call_count} times; "
        f"expected {calls_after_init} (only during __init__)"
    )


def test_create_agent_reuses_long_context_model_for_large_prompts():
    """create_agent() must not rebuild long_context_model per module."""
    from unittest.mock import patch
    from codewiki.src.be.agent_orchestrator import AgentOrchestrator
    import codewiki.src.be.agent_orchestrator as orch_mod

    cfg = _make_dummy_config(long_context_model="long-ctx-model")
    with patch.object(orch_mod, "create_long_context_model",
                      wraps=orch_mod.create_long_context_model) as mock_clcm:
        orch = AgentOrchestrator(cfg)
        calls_after_init = mock_clcm.call_count  # exactly 1 call from __init__

        big = cfg.long_context_threshold + 1
        orch.create_agent("mod1", {}, [], estimated_tokens=big)
        orch.create_agent("mod2", {}, [], estimated_tokens=big)

    assert mock_clcm.call_count == calls_after_init, (
        f"create_long_context_model called {mock_clcm.call_count} times; "
        f"expected {calls_after_init} (only during __init__)"
    )


def test_create_agent_uses_fallback_when_no_long_context_model():
    """When long_context_model is None, create_agent always uses self.fallback_models."""
    from unittest.mock import patch
    from codewiki.src.be.agent_orchestrator import AgentOrchestrator
    import codewiki.src.be.agent_orchestrator as orch_mod

    cfg = _make_dummy_config(long_context_model=None)
    with patch.object(orch_mod, "create_long_context_model") as mock_clcm:
        orch = AgentOrchestrator(cfg)
        # Even with a huge token count, no long_context_model should be used
        orch.create_agent("mod1", {}, [], estimated_tokens=999_999)

    mock_clcm.assert_not_called()
    assert orch.long_context_model is None
```

**Step 2: Run new tests to verify they fail**

```bash
pytest tests/test_perf_async_client_pool.py::test_create_agent_does_not_call_create_fallback_models_per_module tests/test_perf_async_client_pool.py::test_create_agent_reuses_long_context_model_for_large_prompts tests/test_perf_async_client_pool.py::test_create_agent_uses_fallback_when_no_long_context_model -v
```

Expected: first two FAIL (call counts > expected); third FAIL or PASS depending on current code.

**Step 3: Implement — update `AgentOrchestrator.__init__` and `create_agent`**

In `codewiki/src/be/agent_orchestrator.py`:

1. Replace the import on line 70 (remove `select_agent_model`, add `create_long_context_model`):

```python
from codewiki.src.be.llm_services import create_fallback_models, create_long_context_model
```

2. Replace `__init__` (lines 89–93):

```python
def __init__(self, config: Config):
    self.config = config
    self.fallback_models = create_fallback_models(config)
    self.long_context_model = (
        create_long_context_model(config) if config.long_context_model else None
    )
    self.custom_instructions = config.get_prompt_addition() if config else None
    self.output_language = config.output_language if config else "en"
```

3. Replace `create_agent` (lines 95–120) — replace the `select_agent_model()` call with direct attribute access:

```python
def create_agent(self, module_name: str, components: Dict[str, Any],
                core_component_ids: List[str],
                estimated_tokens: int = 0) -> Agent:
    """Create an appropriate agent based on module complexity."""
    if (
        self.long_context_model
        and estimated_tokens > self.config.long_context_threshold
    ):
        model = self.long_context_model
    else:
        model = self.fallback_models

    if is_complex_module(components, core_component_ids):
        return Agent(
            model,
            name=module_name,
            deps_type=CodeWikiDeps,
            tools=[
                read_code_components_tool,
                str_replace_editor_tool,
                generate_sub_module_documentation_tool
            ],
            system_prompt=format_system_prompt(module_name, self.custom_instructions, self.output_language),
        )
    else:
        return Agent(
            model,
            name=module_name,
            deps_type=CodeWikiDeps,
            tools=[read_code_components_tool, str_replace_editor_tool],
            system_prompt=format_leaf_system_prompt(module_name, self.custom_instructions, self.output_language),
        )
```

**Step 4: Run all tests to verify they pass**

```bash
pytest tests/test_perf_async_client_pool.py -v
```

Expected: all 7 tests PASS.

**Step 5: Run full test suite**

```bash
pytest --tb=short -q 2>&1 | tail -5
```

Expected: all existing tests still pass.

**Step 6: Commit**

```bash
git add codewiki/src/be/agent_orchestrator.py tests/test_perf_async_client_pool.py
git commit -m "perf(orchestrator): pre-build model objects in __init__, reuse in create_agent

AgentOrchestrator now builds self.fallback_models and self.long_context_model
once at init time.  create_agent() picks between them by token count instead
of calling select_agent_model() (which rebuilt the entire FallbackModel chain,
including a fresh AsyncOpenAI connection pool, for every module)."
```

---

### Task 3: Sanitize `Retry-After` header values to prevent blocking or crash

**Background:** `_parse_retry_after()` in `llm_services.py` returns `float(val)` directly, without clamping. A negative value causes `time.sleep()` to raise `ValueError`; an extremely large value (or `inf`) causes excessive blocking. `documentation_generator.py` has the same pattern in its inline `_get_retry_after`. This was flagged as HIGH severity.

**Files:**
- Modify: `codewiki/src/be/llm_services.py` (`_parse_retry_after`, lines 147–162)
- Modify: `codewiki/src/be/documentation_generator.py` (`_get_retry_after`, lines 403–411)
- Modify: `tests/test_perf_retry_jitter.py` (add two more test functions)

**Step 1: Write failing tests — append to `tests/test_perf_retry_jitter.py`**

```python
def test_parse_retry_after_clamps_negative_value():
    """Negative Retry-After must not be returned (would crash time.sleep)."""
    import openai
    from unittest.mock import MagicMock
    from codewiki.src.be.llm_services import _parse_retry_after

    exc = MagicMock(spec=openai.RateLimitError)
    exc.response.headers = {"retry-after": "-5"}
    result = _parse_retry_after(exc)
    assert result is None or result >= 0, f"Got {result!r}, expected None or >= 0"


def test_parse_retry_after_clamps_oversized_value():
    """Retry-After > 120s must be clamped to 120 to prevent excessive blocking."""
    import openai
    from unittest.mock import MagicMock
    from codewiki.src.be.llm_services import _parse_retry_after

    exc = MagicMock(spec=openai.RateLimitError)
    exc.response.headers = {"retry-after": "9999"}
    result = _parse_retry_after(exc)
    assert result is not None
    assert result <= 120, f"Expected <= 120s but got {result}"
```

**Step 2: Run tests to verify they fail**

```bash
pytest tests/test_perf_retry_jitter.py::test_parse_retry_after_clamps_negative_value tests/test_perf_retry_jitter.py::test_parse_retry_after_clamps_oversized_value -v
```

Expected: both FAIL (current code returns `-5.0` and `9999.0` unclamped).

**Step 3: Implement — sanitize `_parse_retry_after` in `llm_services.py`**

Replace `_parse_retry_after` (lines 147–162):

```python
# Maximum seconds to honour from a Retry-After header.
_MAX_RETRY_AFTER = 120.0


def _parse_retry_after(exc: Exception) -> float | None:
    """Extract Retry-After seconds from a 429 RateLimitError response, if present.

    Returns None for any other exception type, when the header is absent, or
    when the value is negative/non-finite (all treated as 'ignore and fall back
    to jittered delay').  Values above _MAX_RETRY_AFTER are clamped to prevent
    excessive blocking from misconfigured or malicious upstream responses.
    """
    import openai
    if not isinstance(exc, openai.RateLimitError):
        return None
    headers = getattr(getattr(exc, "response", None), "headers", {})
    val = headers.get("retry-after") or headers.get("Retry-After")
    if val:
        try:
            seconds = float(val)
        except (ValueError, OverflowError):
            return None
        if not (0 <= seconds < float("inf")):
            return None
        return min(seconds, _MAX_RETRY_AFTER)
    return None
```

**Step 4: Fix the inline `_get_retry_after` in `documentation_generator.py`**

Find the nested helper `_get_retry_after` inside `_generate_all_docs` (around line 403) and apply the same sanitization. Replace it with:

```python
def _get_retry_after(exc: Exception) -> float | None:
    """Extract and sanitize Retry-After from a 429 response header."""
    import openai
    if not isinstance(exc, openai.RateLimitError):
        return None
    headers = getattr(getattr(exc, "response", None), "headers", {})
    val = headers.get("retry-after") or headers.get("Retry-After")
    if val:
        try:
            seconds = float(val)
        except (ValueError, OverflowError):
            return None
        if not (0 <= seconds < float("inf")):
            return None
        return min(seconds, 120.0)
    return None
```

**Step 5: Run tests to verify they pass**

```bash
pytest tests/test_perf_retry_jitter.py -v
```

Expected: all tests PASS (including the two new ones).

**Step 6: Run full test suite**

```bash
pytest --tb=short -q 2>&1 | tail -5
```

Expected: all tests still pass.

**Step 7: Commit**

```bash
git add codewiki/src/be/llm_services.py codewiki/src/be/documentation_generator.py tests/test_perf_retry_jitter.py
git commit -m "fix(llm): clamp Retry-After header to [0, 120s] to prevent crash or blocking

Negative values would cause time.sleep() to raise ValueError; very large or
infinite values cause excessive blocking.  Both _parse_retry_after (sync path)
and the inline _get_retry_after (async worker path) now clamp to 120s max and
reject negative/non-finite values."
```
