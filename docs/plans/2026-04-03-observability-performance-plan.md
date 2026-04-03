# Observability + Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LLM token tracking, structured logging with structlog, per-module context filtering, batch state writes, and consolidated retry ownership across the CodeWiki generation pipeline.

**Architecture:** Three phases. Phase 1 sets up structlog infrastructure. Phase 2 migrates `call_llm` to return structured results (content + usage) AND removes its built-in retry loop in one pass — avoiding double migration. Phase 3 applies performance optimizations. Each phase produces independently testable, committable work.

**Tech Stack:** Python 3.13, structlog, pytest, asyncio

**Key constraint:** O1+O3 (token tracking) and P5 (retry removal) both change `call_llm`'s contract. They are merged into one task to avoid double migration.

**Execution constraint:** Task 3 and Task 4 MUST be executed as one continuous batch — do not insert other tasks between them. Both touch `call_llm` / usage wiring; a half-migrated state between them will break callers.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `codewiki/src/logging_setup.py` | Create | structlog configuration (CLI console + web JSON) |
| `codewiki/src/be/llm_services.py` | Modify | `LLMCallResult` return type, remove retry loop, add usage extraction |
| `codewiki/src/be/llm_usage.py` | Create | `LLMUsageStats`, `LLMCallResult`, `LLMCallUsage` data models |
| `codewiki/src/be/generation/glossary.py` | Modify | `GlossaryEntry` structured model, `build_glossary` returns structured |
| `codewiki/src/be/generation/context_pack.py` | Modify | Per-module filtering, EdgeIndex usage |
| `codewiki/src/be/generation_state.py` | Modify | Dirty flag + flush API |
| `codewiki/src/be/documentation_scheduler.py` | Modify | Flush on done_queue, retry helpers moved here, parent hash recompute |
| `codewiki/src/be/documentation_tree_utils.py` | Modify | P6: parent input_hash includes child content_hash |
| `codewiki/src/be/documentation_generator.py` | Modify | Wire usage_stats, flush points |
| `codewiki/src/be/agent_orchestrator.py` | Modify | Read result.usage(), pass filtered context |
| `codewiki/src/be/agent_tools/generate_sub_module_documentations.py` | Modify | Read result.usage() |
| `codewiki/src/be/guide_generator.py` | Modify | Adapt to LLMCallResult |
| `codewiki/src/be/docs_fixer.py` | Modify | Adapt to LLMCallResult |
| `codewiki/src/be/cluster_modules.py` | Modify | Adapt to LLMCallResult |
| `codewiki/src/be/clustering/naming.py` | Modify | Adapt to LLMCallResult |
| `codewiki/cli/commands/generate.py` | Modify | O2: config logging |
| `codewiki/cli/adapters/doc_generator.py` | Modify | Wire structlog, usage_stats to metadata |
| `codewiki/src/fe/web_app.py` | Modify | Wire structlog web config |
| `codewiki/src/fe/background_worker.py` | Modify | Wire structlog web config |
| `codewiki/src/fe/cache_manager.py` | Modify | Replace print() with structlog |
| `codewiki/src/fe/github_processor.py` | Modify | Replace print() with structlog |
| `codewiki/src/be/dependency_analyzer/analysis/cloning.py` | Modify | Replace print() with structlog |
| `pyproject.toml` | Modify | Add structlog dependency |

---

## Phase 1: Logging Infrastructure

### Task 1: O4 — structlog setup + print() cleanup

**Files:**
- Create: `codewiki/src/logging_setup.py`
- Modify: `pyproject.toml`
- Modify: `codewiki/cli/adapters/doc_generator.py`
- Modify: `codewiki/src/fe/web_app.py`
- Modify: `codewiki/src/fe/background_worker.py`
- Modify: `codewiki/src/fe/cache_manager.py`
- Modify: `codewiki/src/fe/github_processor.py`
- Modify: `codewiki/src/be/dependency_analyzer/analysis/cloning.py`
- Create: `tests/test_logging_setup.py`

- [ ] **Step 1: Add structlog dependency**

In `pyproject.toml` dependencies, add:
```toml
"structlog>=24.1.0",
```

Run: `pip install structlog`

- [ ] **Step 2: Write failing tests**

```python
# tests/test_logging_setup.py
import logging
import pytest


class TestLoggingSetup:
    def test_configure_cli_logging_sets_codewiki_info(self):
        from codewiki.src.logging_setup import configure_cli_logging
        configure_cli_logging(verbose=False)
        logger = logging.getLogger("codewiki")
        assert logger.level <= logging.INFO

    def test_configure_cli_logging_suppresses_third_party(self):
        from codewiki.src.logging_setup import configure_cli_logging
        configure_cli_logging(verbose=False)
        for name in ["httpx", "openai", "httpcore"]:
            assert logging.getLogger(name).level >= logging.WARNING

    def test_configure_cli_verbose_enables_debug(self):
        from codewiki.src.logging_setup import configure_cli_logging
        configure_cli_logging(verbose=True)
        logger = logging.getLogger("codewiki")
        assert logger.level <= logging.DEBUG

    def test_configure_web_logging_exists(self):
        from codewiki.src.logging_setup import configure_web_logging
        configure_web_logging()  # should not raise
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_logging_setup.py -v`
Expected: FAIL (module does not exist)

- [ ] **Step 4: Implement logging_setup.py**

```python
# codewiki/src/logging_setup.py
"""Structured logging configuration using structlog.

Two modes:
- CLI: colored console output for human readability
- Web: JSON output for log aggregators
"""
import logging
import structlog

_THIRD_PARTY_LOGGERS = [
    "httpx", "openai", "httpcore", "pydantic_ai",
    "uvicorn", "fastapi", "watchfiles",
]


def configure_cli_logging(verbose: bool = False) -> None:
    """Configure structlog for CLI usage with colored console output."""
    level = logging.DEBUG if verbose else logging.INFO

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Set codewiki logger level
    logging.getLogger("codewiki").setLevel(level)

    # Suppress third-party noise
    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Root handler for stdlib logging
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(colors=True),
        ))
        root.addHandler(handler)
    root.setLevel(level)


def configure_web_logging() -> None:
    """Configure structlog for web/worker with JSON output."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.getLogger("codewiki").setLevel(logging.INFO)
    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
```

- [ ] **Step 5: Wire into entry points**

In `codewiki/cli/adapters/doc_generator.py`, replace `_configure_backend_logging()` with:
```python
from codewiki.src.logging_setup import configure_cli_logging
configure_cli_logging(verbose=self.verbose)
```

In `codewiki/src/fe/web_app.py` `create_app()`, add:
```python
from codewiki.src.logging_setup import configure_web_logging
configure_web_logging()
```

In `codewiki/src/fe/background_worker.py` worker init, add same `configure_web_logging()` call.

- [ ] **Step 6: Replace print() calls with structlog**

In `codewiki/src/fe/cache_manager.py`: replace `print(f"Error ...")` with `logger.error(...)` (add `import structlog; logger = structlog.get_logger()` at top).

In `codewiki/src/fe/github_processor.py`: same pattern for all 4 `print()` calls.

In `codewiki/src/be/dependency_analyzer/analysis/cloning.py`: replace `print(f"⚠️ Warning: ...")` with `logger.warning(...)`.

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_logging_setup.py -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add codewiki/src/logging_setup.py pyproject.toml codewiki/cli/adapters/doc_generator.py codewiki/src/fe/web_app.py codewiki/src/fe/background_worker.py codewiki/src/fe/cache_manager.py codewiki/src/fe/github_processor.py codewiki/src/be/dependency_analyzer/analysis/cloning.py tests/test_logging_setup.py
git commit -m "feat(logging): add structlog, replace print() calls, wire entry points"
```

---

### Task 2: O5 + O2 — INFO visibility + config logging

**Files:**
- Modify: `codewiki/src/logging_setup.py` (O5 already done in Task 1)
- Modify: `codewiki/cli/commands/generate.py`
- Create: `tests/test_config_logging.py`

O5 is already handled by Task 1 (codewiki INFO enabled, third-party WARNING). This task adds O2.

- [ ] **Step 1: Write failing test**

```python
# tests/test_config_logging.py
import pytest
from unittest.mock import MagicMock


def test_generate_logs_effective_config(caplog):
    """generate command must log effective config at INFO level."""
    import logging
    caplog.set_level(logging.INFO)

    # Verify the log_effective_config function exists and logs expected fields
    from codewiki.cli.commands.generate import log_effective_config

    config = MagicMock()
    config.main_model = "gpt-4o"
    config.cluster_model = "gpt-4o"
    config.fallback_model = "glm-4p5"
    config.max_tokens = 32768
    config.max_concurrent = 3
    config.output_language = "zh"
    config.providers = [MagicMock(), MagicMock()]

    log_effective_config(config)

    log_text = caplog.text
    assert "gpt-4o" in log_text
    assert "32768" in log_text or "32_768" in log_text
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/test_config_logging.py -v`

- [ ] **Step 3: Implement config logging**

In `codewiki/cli/commands/generate.py`, add:

```python
import structlog

_logger = structlog.get_logger("codewiki.cli.generate")

def log_effective_config(config) -> None:
    """Log the effective runtime configuration at INFO level."""
    _logger.info(
        "effective_config",
        main_model=config.main_model,
        cluster_model=config.cluster_model,
        fallback_model=config.fallback_model,
        max_tokens=config.max_tokens,
        max_concurrent=config.max_concurrent,
        output_language=config.output_language,
        providers=len(config.providers) if config.providers else 0,
    )
```

Call `log_effective_config(config)` in the generate command, after Config is built and before generation starts.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_config_logging.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codewiki/cli/commands/generate.py tests/test_config_logging.py
git commit -m "feat(logging): log effective config at INFO on every generate run"
```

---

## Phase 2: call_llm Interface Migration (O1+O3+P5 merged)

### Task 3: LLM data models + call_llm migration

**Files:**
- Create: `codewiki/src/be/llm_usage.py`
- Modify: `codewiki/src/be/llm_services.py`
- Modify: `codewiki/src/be/docs_fixer.py`
- Modify: `codewiki/src/be/cluster_modules.py`
- Modify: `codewiki/src/be/clustering/naming.py`
- Modify: `codewiki/src/be/guide_generator.py`
- Modify: `codewiki/src/be/documentation_overview.py`
- Create: `tests/test_llm_usage.py`

This task does THREE things in one pass (to avoid double-migrating call_llm):
1. Create `LLMCallResult` / `LLMUsageStats` models
2. Change `call_llm` to return `LLMCallResult` instead of `str`
3. Remove `call_llm`'s built-in retry loop

- [ ] **Step 1: Write failing tests**

```python
# tests/test_llm_usage.py
import pytest


class TestLLMCallResult:
    def test_content_access(self):
        from codewiki.src.be.llm_usage import LLMCallResult, LLMCallUsage
        result = LLMCallResult(
            content="hello",
            usage=LLMCallUsage(input_tokens=10, output_tokens=5, source="api"),
        )
        assert result.content == "hello"
        assert result.usage.input_tokens == 10
        assert result.usage.source == "api"

    def test_none_usage(self):
        from codewiki.src.be.llm_usage import LLMCallResult
        result = LLMCallResult(content="hello", usage=None)
        assert result.usage is None


class TestLLMUsageStats:
    def test_record_accumulates(self):
        from codewiki.src.be.llm_usage import LLMUsageStats
        stats = LLMUsageStats()
        stats.record("gpt-4o", input_tokens=100, output_tokens=50)
        stats.record("gpt-4o", input_tokens=200, output_tokens=100)
        assert stats.total_input_tokens == 300
        assert stats.total_output_tokens == 150
        assert stats.total_requests == 2
        assert stats.by_model["gpt-4o"]["input"] == 300

    def test_record_multiple_models(self):
        from codewiki.src.be.llm_usage import LLMUsageStats
        stats = LLMUsageStats()
        stats.record("gpt-4o", input_tokens=100, output_tokens=50)
        stats.record("glm-4p5", input_tokens=200, output_tokens=100)
        assert stats.total_requests == 2
        assert "gpt-4o" in stats.by_model
        assert "glm-4p5" in stats.by_model

    def test_to_dict(self):
        from codewiki.src.be.llm_usage import LLMUsageStats
        stats = LLMUsageStats()
        stats.record("gpt-4o", input_tokens=100, output_tokens=50)
        d = stats.to_dict()
        assert d["total_input_tokens"] == 100
        assert d["by_model"]["gpt-4o"]["requests"] == 1


class TestCallLlmReturnsResult:
    def test_call_llm_returns_llm_call_result(self):
        from codewiki.src.be.llm_usage import LLMCallResult
        from codewiki.src.be.llm_services import call_llm
        from unittest.mock import MagicMock, patch

        config = MagicMock()
        config.main_model = "test"
        config.max_tokens = 100
        config.long_context_model = None
        config.providers = None
        config.llm_base_url = "http://localhost"
        config.llm_api_key = "key"

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 5
        mock_choice = MagicMock()
        mock_choice.message.content = "response text"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("codewiki.src.be.llm_services._create_client_for_model",
                    return_value=(mock_client, "openai_compatible")):
            result = call_llm("test", config)

        assert isinstance(result, LLMCallResult)
        assert result.content == "response text"
        assert result.usage is not None
        assert result.usage.input_tokens == 10

    def test_call_llm_no_retry_loop(self):
        """call_llm must raise on first failure, not retry."""
        from codewiki.src.be.llm_services import call_llm
        from unittest.mock import MagicMock, patch

        config = MagicMock()
        config.main_model = "test"
        config.max_tokens = 100
        config.long_context_model = None
        config.providers = None
        config.llm_base_url = "http://localhost"
        config.llm_api_key = "key"

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("simulated LLM failure")

        with patch("codewiki.src.be.llm_services._create_client_for_model",
                    return_value=(mock_client, "openai_compatible")):
            with pytest.raises(Exception):
                call_llm("test", config)

        # Must be called exactly once (no retry)
        assert mock_client.chat.completions.create.call_count == 1
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_llm_usage.py -v`

- [ ] **Step 3: Create llm_usage.py**

```python
# codewiki/src/be/llm_usage.py
"""LLM usage tracking data models."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LLMCallUsage:
    """Token usage from a single LLM call."""
    input_tokens: int = 0
    output_tokens: int = 0
    source: str = "api"  # "api" or "estimated"


@dataclass
class LLMCallResult:
    """Return value from call_llm: content + optional usage."""
    content: str
    usage: Optional[LLMCallUsage] = None
    model: str = ""


@dataclass
class LLMUsageStats:
    """Accumulated token usage across a full generation run."""
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_requests: int = 0
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)

    def record(self, model: str, input_tokens: int, output_tokens: int,
               source: str = "api") -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_requests += 1
        if model not in self.by_model:
            self.by_model[model] = {"input": 0, "output": 0, "requests": 0}
        self.by_model[model]["input"] += input_tokens
        self.by_model[model]["output"] += output_tokens
        self.by_model[model]["requests"] += 1

    def to_dict(self) -> dict:
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_requests": self.total_requests,
            "by_model": dict(self.by_model),
        }
```

- [ ] **Step 4: Migrate call_llm**

In `codewiki/src/be/llm_services.py`:

1. Remove the retry loop (`for attempt, delay in enumerate([0] + _RETRY_DELAYS):`)
2. Remove `_RETRY_DELAYS`, `_parse_retry_after`, `_sleep_with_jitter`, retry notification code
3. Change return type from `str` to `LLMCallResult`
4. Extract `response.usage` when available

The new `call_llm` structure:

```python
def call_llm(
    prompt: str,
    config: Config,
    model: str | None = None,
    temperature: float = 0.0,
) -> LLMCallResult:
    from codewiki.src.be.utils import count_tokens
    from codewiki.src.be.llm_usage import LLMCallResult, LLMCallUsage

    if model is None:
        model = config.main_model
    # ... long context model switch (keep as-is) ...

    client, provider_type = _create_client_for_model(config, model)
    if _has_provider_registry(config):
        _, resolved_model_name = _get_provider_config(config, model)
    else:
        resolved_model_name = model

    t0 = time.time()
    usage = None

    if provider_type in {"openai_compatible", "azure_openai"}:
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
            raise ValueError(f"LLM returned null content (model={resolved_model_name})")
        if not content:
            raise ValueError(f"LLM returned empty content (model={resolved_model_name})")
        # Extract real usage
        if response.usage:
            usage = LLMCallUsage(
                input_tokens=response.usage.prompt_tokens or 0,
                output_tokens=response.usage.completion_tokens or 0,
                source="api",
            )
    elif provider_type == "claude":
        content = _call_claude(client, resolved_model_name, prompt, temperature, config)
        if not content:
            raise ValueError(f"LLM returned empty content (model={resolved_model_name})")
    else:
        raise ValueError(f"unsupported provider type: {provider_type}")

    # Fallback usage estimation when API doesn't return usage
    if usage is None:
        usage = LLMCallUsage(
            input_tokens=count_tokens(prompt),
            output_tokens=count_tokens(content),
            source="estimated",
        )

    elapsed = time.time() - t0
    _logger.debug(
        "call_llm: model=%s, elapsed=%.1fs, input_tokens=%s, output_tokens=%s, source=%s",
        model, elapsed, usage.input_tokens, usage.output_tokens, usage.source,
    )

    return LLMCallResult(content=content, usage=usage, model=model)
```

- [ ] **Step 5: Update all call_llm callers to use .content**

Each caller currently does `result = call_llm(...)` and uses `result` as a string. Change to `result = call_llm(...).content` or unpack:

**docs_fixer.py** (math/mermaid repair): `fixed = call_llm(prompt, config).content`
**cluster_modules.py**: `raw = call_llm(prompt, config, model=config.cluster_model).content`
**clustering/naming.py**: `raw = call_llm(prompt, config, model=config.cluster_model).content`
**guide_generator.py**: Already has `_call_llm_with_fallback` wrapper — update it to call `.content`
**documentation_overview.py**: `parent_docs = call_llm(prompt, config).content` (inside `asyncio.to_thread`)

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_llm_usage.py tests/test_llm_response_guard.py -v`
Expected: All PASS

- [ ] **Step 7: Run full regression**

Run: `pytest tests/ -q -k "not network" --timeout=60`

- [ ] **Step 8: Commit**

```bash
git add codewiki/src/be/llm_usage.py codewiki/src/be/llm_services.py codewiki/src/be/docs_fixer.py codewiki/src/be/cluster_modules.py codewiki/src/be/clustering/naming.py codewiki/src/be/guide_generator.py codewiki/src/be/documentation_overview.py tests/test_llm_usage.py
git commit -m "refactor(llm): return LLMCallResult with usage, remove built-in retry loop"
```

---

### Task 4: Wire LLMUsageStats into generation pipeline

**Files:**
- Modify: `codewiki/src/be/documentation_generator.py`
- Modify: `codewiki/src/be/agent_orchestrator.py`
- Modify: `codewiki/src/be/agent_tools/generate_sub_module_documentations.py`
- Modify: `codewiki/src/be/guide_generator.py` (pass + record usage from _call_llm_with_fallback)
- Modify: `codewiki/src/be/docs_fixer.py` (record usage from repair calls)
- Modify: `codewiki/src/be/cluster_modules.py` (record usage from clustering calls)
- Modify: `codewiki/cli/adapters/doc_generator.py`
- Create: `tests/test_usage_stats_wiring.py`

**Critical ordering fix:** Current code writes `metadata.json` BEFORE running guides and docs_fixer (`documentation_generator.py:462`, `doc_generator.py:332`). Metadata write must be moved to AFTER all generation and postprocessing completes, otherwise guide/fixer token usage is lost.

- [ ] **Step 1: Write failing test**

```python
# tests/test_usage_stats_wiring.py
import pytest


def test_usage_stats_created_on_generator():
    from codewiki.src.be.documentation_generator import DocumentationGenerator
    from codewiki.src.be.llm_usage import LLMUsageStats
    from unittest.mock import MagicMock

    config = MagicMock()
    config.repo_path = "/tmp/fake"
    config.docs_dir = "/tmp/fake/docs"
    config.output_dir = "/tmp/fake/output"
    config.dependency_graph_dir = "/tmp/fake/graphs"
    config.max_depth = 2

    gen = DocumentationGenerator(config)
    assert isinstance(gen.usage_stats, LLMUsageStats)


def test_usage_stats_record_accumulates():
    from codewiki.src.be.llm_usage import LLMUsageStats
    stats = LLMUsageStats()
    stats.record("model-a", 100, 50)
    stats.record("model-a", 200, 100)
    d = stats.to_dict()
    assert d["total_input_tokens"] == 300
    assert d["total_requests"] == 2
```

- [ ] **Step 2: Implement wiring**

In `DocumentationGenerator.__init__()`:
```python
from codewiki.src.be.llm_usage import LLMUsageStats
self.usage_stats = LLMUsageStats()
```

In `AgentOrchestrator`, after `result = await sub_agent.run(...)`, read usage.
Note: `result.usage()` returns a `Usage` object with `input_tokens`/`output_tokens`. When fallback models are used, `models_used` is a comma-separated string like `"model-a, model-b"`. Record against the total only — do NOT use the composite string as a by_model key. Instead, record per individual model response:
```python
for msg in result.all_messages():
    if isinstance(msg, ModelResponse) and msg.model_name:
        # Each response has its own token contribution
        self.usage_stats.record(
            model=msg.model_name,
            input_tokens=0,  # per-message breakdown not available
            output_tokens=0,
        )
# Record totals from result.usage()
if result.usage():
    usage = result.usage()
    self.usage_stats.total_input_tokens += usage.input_tokens or 0
    self.usage_stats.total_output_tokens += usage.output_tokens or 0
    self.usage_stats.total_requests += usage.requests or 0
```

In **non-agent callers** (`guide_generator.py`, `docs_fixer.py`, `cluster_modules.py`, `clustering/naming.py`): each already gets `LLMCallResult` from Task 3. Pass `usage_stats` as parameter, and after each `call_llm()`, record:
```python
result = call_llm(prompt, config)
if usage_stats and result.usage:
    usage_stats.record(result.model, result.usage.input_tokens, result.usage.output_tokens)
```

In `create_documentation_metadata()`, accept `usage_stats` parameter and include in metadata:
```python
if usage_stats:
    metadata["statistics"]["token_usage"] = usage_stats.to_dict()
```

- [ ] **Step 3: Fix metadata write order**

Current code writes metadata BEFORE guides and docs_fixer, so their token usage is lost.

In `documentation_generator.py:run()`: move the `create_documentation_metadata()` call from its current position (line 462, before guide generation) to AFTER `guide_gen.run()` and `fix_docs()` (after line 477):

```python
            # MOVED: was before guide_gen.run(), now after fix_docs()
            self.create_documentation_metadata(working_dir, components, len(leaf_nodes),
                                                usage_stats=self.usage_stats)
```

In CLI adapter `doc_generator.py`: move `create_documentation_metadata()` from line 332 to AFTER `guide_gen.run()` (after line 349):

```python
            # MOVED: was before guide generation, now after
            doc_generator.create_documentation_metadata(
                working_dir, components, len(leaf_nodes),
                usage_stats=doc_generator.usage_stats,
            )
```
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_usage_stats_wiring.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add codewiki/src/be/documentation_generator.py codewiki/src/be/agent_orchestrator.py codewiki/src/be/agent_tools/generate_sub_module_documentations.py codewiki/cli/adapters/doc_generator.py tests/test_usage_stats_wiring.py
git commit -m "feat(usage): wire LLMUsageStats into generation pipeline and metadata"
```

---

## Phase 3: Performance

### Task 5: P3 — EdgeIndex for edge classification

**Files:**
- Modify: `codewiki/src/be/generation/context_pack.py:152-182`
- Create: `tests/test_edge_classification.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_edge_classification.py
import pytest
from unittest.mock import MagicMock, patch, call


def test_classify_edges_uses_edge_index_api():
    """Verify _classify_edges calls edge_index.callees_of/callers_of, not iterating edges list."""
    from codewiki.src.be.generation.context_pack import _classify_edges
    from codewiki.src.be.index.edge_index import EdgeIndex
    from codewiki.src.be.index.models import SymbolEdge, EdgeType

    edges = [
        SymbolEdge(from_symbol="A", to_symbol="B", edge_type=EdgeType.CALLS),
        SymbolEdge(from_symbol="B", to_symbol="C", edge_type=EdgeType.CALLS),
        SymbolEdge(from_symbol="X", to_symbol="Y", edge_type=EdgeType.CALLS),
    ]
    edge_index = EdgeIndex(edges)

    # Wrap with spy to verify the index API is called
    original_callees = edge_index.callees_of
    original_callers = edge_index.callers_of
    callees_calls = []
    callers_calls = []

    def spy_callees(sid):
        callees_calls.append(sid)
        return original_callees(sid)

    def spy_callers(sid):
        callers_calls.append(sid)
        return original_callers(sid)

    edge_index.callees_of = spy_callees
    edge_index.callers_of = spy_callers

    module_syms = {"A", "B"}
    index_products = MagicMock()
    index_products.edge_index = edge_index

    boundary, internal = _classify_edges(module_syms, index_products)

    # Must have called EdgeIndex API (not iterated raw edges list)
    assert len(callees_calls) > 0, "_classify_edges did not call edge_index.callees_of()"
    assert len(callers_calls) > 0, "_classify_edges did not call edge_index.callers_of()"

    # Correctness checks
    assert any("A" in e and "B" in e for e in internal)
    assert any("B" in e and "C" in e for e in boundary)
    assert not any("X" in e for e in boundary + internal)
```

- [ ] **Step 2: Run test — expect FAIL**

Run: `pytest tests/test_edge_classification.py -v`
Expected: FAIL — test verifies `edge_index.callees_of` was called, which current full-scan implementation doesn't do

- [ ] **Step 3: Rewrite _classify_edges to use EdgeIndex**

```python
# codewiki/src/be/generation/context_pack.py — replace _classify_edges

def _classify_edges(module_sym_ids: set[str], index_products):
    """Split edges into boundary (cross-module) and internal using EdgeIndex."""
    boundary = []
    internal = []
    seen: set[tuple[str, str, str]] = set()

    edge_index = index_products.edge_index

    for sid in module_sym_ids:
        for edge in edge_index.callees_of(sid) + edge_index.callers_of(sid):
            if not edge.to_symbol:
                continue
            edge_key = (edge.from_symbol, edge.to_symbol, edge.edge_type.value)
            if edge_key in seen:
                continue
            seen.add(edge_key)

            from_in = edge.from_symbol in module_sym_ids
            to_in = edge.to_symbol in module_sym_ids

            desc = f"{edge.from_symbol} --{edge.edge_type.value}--> {edge.to_symbol}"
            if edge.confidence:
                desc += f" [{edge.confidence.value}]"

            if from_in and to_in:
                internal.append(desc)
            elif from_in or to_in:
                boundary.append(desc)

    return boundary[:15], internal[:15]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_edge_classification.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/generation/context_pack.py tests/test_edge_classification.py
git commit -m "perf(context): use EdgeIndex for edge classification instead of full scan"
```

---

### Task 6: P6 — Parent input_hash includes child content_hash

**Files:**
- Modify: `codewiki/src/be/documentation_tree_utils.py:179-189`
- Modify: `codewiki/src/be/documentation_scheduler.py`
- Modify: `codewiki/src/be/documentation_generator.py` (pass existing_state to build_generation_tasks)
- Create: `tests/test_parent_hash_child_content.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_parent_hash_child_content.py
import pytest
from codewiki.src.be.documentation_tree_utils import stable_hash


def test_parent_hash_changes_when_child_content_changes():
    """Parent input_hash must differ when child content_hash differs."""
    child_ids = ["module:child_a", "module:child_b"]
    components = ["comp1", "comp2"]
    lang = "en"
    version = "v7"

    hash_v1 = stable_hash([*components, *child_ids, lang, version,
                           "child_a_hash_v1", "child_b_hash_v1"])
    hash_v2 = stable_hash([*components, *child_ids, lang, version,
                           "child_a_hash_v2", "child_b_hash_v1"])

    assert hash_v1 != hash_v2, "Parent hash must change when child content changes"
```

- [ ] **Step 2: Run test — expect PASS** (this tests stable_hash, which already works)

- [ ] **Step 3: Modify build_generation_tasks to include child content_hash**

In `codewiki/src/be/documentation_tree_utils.py`, in the `_walk` function around line 179, change the `input_hash` computation for parent/overview tasks:

```python
                    # For parent tasks, include child content hashes from existing ledger
                    child_content_hashes = []
                    if existing_state:
                        for cid in nested_child_ids:
                            existing_task = existing_state.get_task(cid)
                            if existing_task and existing_task.content_hash:
                                child_content_hashes.append(existing_task.content_hash)

                    input_hash=stable_hash(
                        [
                            *sorted(info.get("components", [])),
                            *nested_child_ids,
                            *child_content_hashes,
                            config.output_language,
                            "v7",
                        ]
                    ),
```

The `build_generation_tasks` function needs access to the existing state for this. Add `existing_state: GenerationState | None = None` parameter.

- [ ] **Step 4: Modify scheduler to recompute parent hash on unblock**

In `documentation_scheduler.py`, in the coordinator coroutine, when a parent is about to be enqueued (all children done), recompute the parent's `input_hash` with child content hashes:

```python
# Inside coordinator, when parent_key unblocks:
if gen_state and state_mgr:
    parent_doc_id = doc_id_for_path(graph_tree, all_tasks[parent_key][0])
    parent_task = gen_state.get_task(parent_doc_id)
    if parent_task and parent_task.status == "completed":
        # Recompute hash using SAME inputs as build_generation_tasks:
        # components + child_ids + child_content_hashes + language + version
        _, _, parent_info, _ = all_tasks[parent_key]
        child_keys = [k for k, v in child_to_parent.items() if v == parent_key]
        child_doc_ids = [doc_id_for_path(graph_tree, all_tasks[ck][0]) for ck in child_keys]
        child_content_hashes = []
        for cid in child_doc_ids:
            ct = gen_state.get_task(cid)
            if ct and ct.content_hash:
                child_content_hashes.append(ct.content_hash)
        new_hash = stable_hash([
            *sorted(parent_info.get("components", [])),
            *child_doc_ids,
            *child_content_hashes,
            parent_task.language,
            "v7",
        ])
        if new_hash != parent_task.input_hash:
            await state_mgr.mark_stale({parent_doc_id: new_hash})
```

**Important:** The hash inputs here MUST match the inputs in `build_generation_tasks._walk()` (components + child_ids + child_content_hashes + language + version). If either side changes its formula, the other must be updated in sync.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_parent_hash_child_content.py -v`

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/documentation_tree_utils.py codewiki/src/be/documentation_scheduler.py tests/test_parent_hash_child_content.py
git commit -m "perf(cache): include child content_hash in parent input_hash for stale detection"
```

---

### Task 7: P2 — Glossary/link_map per-module filtering

**Files:**
- Modify: `codewiki/src/be/generation/glossary.py`
- Modify: `codewiki/src/be/generation/context_pack.py`
- Modify: `codewiki/src/be/agent_orchestrator.py`
- Create: `tests/test_glossary_filtering.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_glossary_filtering.py
import pytest


class TestGlossaryEntry:
    def test_structured_glossary_entry(self):
        from codewiki.src.be.generation.glossary import GlossaryEntry
        entry = GlossaryEntry(
            term="MyClass",
            definition="A class.",
            symbol_id="py:src/foo.py#MyClass(class)",
            file_path="src/foo.py",
            kind="class",
        )
        assert entry.symbol_id == "py:src/foo.py#MyClass(class)"


class TestFilterGlossary:
    def test_filters_by_symbol_ids(self):
        from codewiki.src.be.generation.glossary import GlossaryEntry, filter_glossary
        glossary = {
            "A": GlossaryEntry("A", "def A", "sym:A", "src/a.py", "function"),
            "B": GlossaryEntry("B", "def B", "sym:B", "src/b.py", "function"),
            "C": GlossaryEntry("C", "def C", "sym:C", "src/c.py", "function"),
        }
        relevant = filter_glossary(glossary, relevant_symbol_ids={"sym:A", "sym:B"})
        assert "A" in relevant
        assert "B" in relevant
        assert "C" not in relevant

    def test_path_proximity_adds_entries(self):
        from codewiki.src.be.generation.glossary import GlossaryEntry, filter_glossary
        glossary = {
            "A": GlossaryEntry("A", "def A", "sym:A", "src/auth/a.py", "function"),
            "B": GlossaryEntry("B", "def B", "sym:B", "src/auth/b.py", "function"),
            "C": GlossaryEntry("C", "def C", "sym:C", "src/db/c.py", "function"),
        }
        # sym:A is in the relevant set; B should be added by path proximity (same dir)
        relevant = filter_glossary(
            glossary,
            relevant_symbol_ids={"sym:A"},
            module_file_paths={"src/auth/a.py"},
        )
        assert "A" in relevant
        assert "B" in relevant  # same directory
        assert "C" not in relevant  # different directory

    def test_token_limit_truncates(self):
        from codewiki.src.be.generation.glossary import GlossaryEntry, filter_glossary
        glossary = {
            f"sym_{i}": GlossaryEntry(f"sym_{i}", "x" * 100, f"id:{i}", f"src/{i}.py", "function")
            for i in range(100)
        }
        relevant = filter_glossary(
            glossary,
            relevant_symbol_ids={f"id:{i}" for i in range(100)},
            token_limit=500,
        )
        # Should be truncated to fit token limit
        assert len(relevant) < 100
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_glossary_filtering.py -v`

- [ ] **Step 3: Implement GlossaryEntry and filter_glossary**

In `codewiki/src/be/generation/glossary.py`:

```python
from dataclasses import dataclass

@dataclass
class GlossaryEntry:
    term: str
    definition: str
    symbol_id: str
    file_path: str
    kind: str
```

Change `build_glossary` to return `dict[str, GlossaryEntry]`:
```python
        entry = GlossaryEntry(
            term=sym.name,
            definition=definition,
            symbol_id=sym.symbol_id,
            file_path=sym.file_path,
            kind=sym.kind.value,
        )
        glossary[sym.name] = entry
```

Add `filter_glossary`:
```python
def filter_glossary(
    glossary: dict[str, GlossaryEntry],
    relevant_symbol_ids: set[str],
    module_file_paths: set[str] | None = None,
    token_limit: int = 4000,
) -> dict[str, GlossaryEntry]:
    """Filter glossary to module-relevant entries with token budget."""
    from codewiki.src.be.utils import count_tokens

    # Priority A: direct symbol match
    priority_a = {k: v for k, v in glossary.items() if v.symbol_id in relevant_symbol_ids}

    # Priority B: path proximity
    priority_b = {}
    if module_file_paths:
        module_dirs = {os.path.dirname(p) for p in module_file_paths}
        for k, v in glossary.items():
            if k not in priority_a and os.path.dirname(v.file_path) in module_dirs:
                priority_b[k] = v

    # Merge and apply token limit
    result = {}
    token_count = 0
    for source in [priority_a, priority_b]:
        for k, v in source.items():
            entry_tokens = count_tokens(f"{v.term}: {v.definition}")
            if token_count + entry_tokens > token_limit:
                return result
            result[k] = v
            token_count += entry_tokens

    return result
```

- [ ] **Step 4: Update context_pack.py to use filtered glossary**

In `build_context_pack()`, accept `module_file_paths` and pass through to filtering. In `format_context_pack_section()`, format `GlossaryEntry` objects as strings.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_glossary_filtering.py -v`

- [ ] **Step 6: Commit**

```bash
git add codewiki/src/be/generation/glossary.py codewiki/src/be/generation/context_pack.py codewiki/src/be/agent_orchestrator.py tests/test_glossary_filtering.py
git commit -m "perf(context): filter glossary/link_map per module with token budget"
```

---

### Task 8: P1 — Batch state writes with dirty flag

**Files:**
- Modify: `codewiki/src/be/generation_state.py`
- Modify: `codewiki/src/be/documentation_scheduler.py`
- Modify: `codewiki/src/be/documentation_generator.py`
- Create: `tests/test_batch_state_writes.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_batch_state_writes.py
import pytest
import asyncio


class TestDirtyFlagFlush:
    def test_mutation_sets_dirty(self):
        from codewiki.src.be.generation_state import GenerationState, GenerationStateManager, DocTask
        state = GenerationState()
        mgr = GenerationStateManager(state, "/tmp/fake.json")
        assert not mgr._dirty

    @pytest.mark.asyncio
    async def test_mark_running_sets_dirty_no_write(self, tmp_path):
        from codewiki.src.be.generation_state import GenerationState, GenerationStateManager, DocTask
        state = GenerationState()
        state._add_task(DocTask(doc_id="a", kind="module", module_path=["A"],
                                output_file="a.md", status="ready"))
        path = str(tmp_path / "state.json")
        state._save(path)

        mgr = GenerationStateManager(state, path)
        await mgr.mark_running("a")

        assert mgr._dirty
        # File should NOT have been written yet (no auto-save)
        import json
        with open(path) as f:
            on_disk = json.load(f)
        disk_task = {t["doc_id"]: t for t in on_disk["tasks"]}
        assert disk_task["a"]["status"] != "running"  # still old status on disk

    @pytest.mark.asyncio
    async def test_flush_writes_and_resets_dirty(self, tmp_path):
        from codewiki.src.be.generation_state import GenerationState, GenerationStateManager, DocTask
        state = GenerationState()
        state._add_task(DocTask(doc_id="a", kind="module", module_path=["A"],
                                output_file="a.md", status="ready"))
        path = str(tmp_path / "state.json")
        state._save(path)

        mgr = GenerationStateManager(state, path)
        await mgr.mark_running("a")
        await mgr.flush()

        assert not mgr._dirty
        import json
        with open(path) as f:
            on_disk = json.load(f)
        assert on_disk["tasks"][0]["status"] == "running"
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/test_batch_state_writes.py -v`

- [ ] **Step 3: Implement dirty flag + flush**

In `GenerationStateManager`:

```python
class GenerationStateManager:
    def __init__(self, state: GenerationState, persist_path: str):
        self._state = state
        self._persist_path = persist_path
        self._lock = asyncio.Lock()
        self._dirty = False

    async def flush(self) -> None:
        """Write to disk if dirty, then reset flag."""
        async with self._lock:
            if self._dirty:
                self._state._save(self._persist_path)
                self._dirty = False

    async def mark_running(self, doc_id: str) -> None:
        async with self._lock:
            self._state._update_task_status(doc_id, "running")
            self._dirty = True
            # NO _save here

    async def mark_completed(self, doc_id: str, content_hash: str,
                              model: str = "", input_hash: str = "") -> None:
        async with self._lock:
            task = self._state.get_task(doc_id)
            if task is None:
                raise KeyError(f"Unknown doc_id: {doc_id}")
            task.mark_completed(content_hash=content_hash, model=model, input_hash=input_hash)
            self._dirty = True

    # Same pattern for mark_failed, register_discovered_task, etc.
    # All set self._dirty = True instead of calling _save

    async def bulk_add_tasks(self, tasks: list) -> None:
        async with self._lock:
            for task in tasks:
                self._state._add_task(task)
            self._state._save(self._persist_path)  # flush immediately after bulk init
            self._dirty = False
```

In `documentation_scheduler.py` coordinator, after processing each done_queue message:
```python
if state_mgr:
    await state_mgr.flush()
```

In `documentation_generator.py` run() finally block:
```python
if self._state_mgr:
    await self._state_mgr.flush()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_batch_state_writes.py tests/test_scheduler_coordinator.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add codewiki/src/be/generation_state.py codewiki/src/be/documentation_scheduler.py codewiki/src/be/documentation_generator.py tests/test_batch_state_writes.py
git commit -m "perf(state): batch writes with dirty flag, flush on done_queue"
```

---

### Task 9: Move retry helpers to scheduler

**Files:**
- Modify: `codewiki/src/be/llm_services.py` (remove retry helpers)
- Modify: `codewiki/src/be/documentation_scheduler.py` (move helpers here)

This is the cleanup pass after Task 3 removed call_llm's retry loop. The retry helpers (`_RETRY_DELAYS`, `_parse_retry_after`, `_sleep_with_jitter`, `_MAX_RETRY_AFTER`) may still be in llm_services.py if Task 3 only removed the loop but not the helpers. Move them to the scheduler where they belong.

- [ ] **Step 1: Check if helpers are still in llm_services.py**

If `_RETRY_DELAYS`, `_parse_retry_after`, `_sleep_with_jitter` still exist in `llm_services.py`, move them to `documentation_scheduler.py`. If they were already removed in Task 3, skip this task.

- [ ] **Step 2: Update scheduler imports**

Make sure `documentation_scheduler.py` no longer imports `_MAX_RETRY_AFTER` from `llm_services` (which was flagged as importing a private symbol across module boundaries).

- [ ] **Step 3: Commit**

```bash
git add codewiki/src/be/llm_services.py codewiki/src/be/documentation_scheduler.py
git commit -m "refactor(retry): move retry helpers from llm_services to scheduler"
```

---

### Task 10: Full regression verification

- [ ] **Step 1: Compile all modified files**

```bash
python3 -m py_compile codewiki/src/logging_setup.py codewiki/src/be/llm_usage.py codewiki/src/be/llm_services.py codewiki/src/be/generation_state.py codewiki/src/be/generation/glossary.py codewiki/src/be/generation/context_pack.py codewiki/src/be/documentation_scheduler.py codewiki/src/be/documentation_tree_utils.py codewiki/src/be/documentation_generator.py
```

- [ ] **Step 2: Run all new tests**

```bash
pytest -v tests/test_logging_setup.py tests/test_config_logging.py tests/test_llm_usage.py tests/test_usage_stats_wiring.py tests/test_edge_classification.py tests/test_parent_hash_child_content.py tests/test_glossary_filtering.py tests/test_batch_state_writes.py
```

- [ ] **Step 3: Run full regression**

```bash
pytest tests/ -q -k "not network" --timeout=60
```

- [ ] **Step 4: Commit if any fixes needed**

```bash
git commit -m "test: verify observability + performance audit fixes"
```
