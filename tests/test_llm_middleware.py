from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest


def _load_middleware_module():
    return pytest.importorskip("codewiki.src.be.llm_middleware")


def _make_config(tmp_path):
    from codewiki.src.codewiki_config import CodeWikiConfig

    return CodeWikiConfig(
        repo_path=str(tmp_path / "repo"),
        output_dir=str(tmp_path / "out"),
        dependency_graph_dir=str(tmp_path / "graphs"),
        docs_dir=str(tmp_path / "docs"),
        max_depth=2,
        llm_base_url="http://localhost:4000",
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


def test_raw_llm_call_is_importable():
    mw_mod = _load_middleware_module()
    assert callable(mw_mod.raw_llm_call)


def test_middleware_routes_and_passes_stream_kwarg(tmp_path):
    from codewiki.src.be.llm_usage import LLMCallResult

    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))
    fake_result = LLMCallResult(content="ok", usage=None, model="test/long")

    with (
        patch("codewiki.src.be.llm_middleware.count_tokens", return_value=250_000),
        patch("codewiki.src.be.llm_middleware.raw_llm_call", return_value=fake_result) as mock_raw,
    ):
        result = middleware.call("prompt", stream=True)

    assert result.content == "ok"
    assert mock_raw.call_count == 1
    assert mock_raw.call_args.args[2] == "test/long"
    assert mock_raw.call_args.kwargs["stream"] is True


def test_middleware_switches_to_long_context_then_retries(tmp_path):
    from codewiki.src.be.errors import ErrorCategory, LLMError
    from codewiki.src.be.llm_usage import LLMCallResult

    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))
    overflow = LLMError("context_length_exceeded", ErrorCategory.RESOURCE_EXHAUSTED, 400)
    fake_result = LLMCallResult(content="ok", usage=None, model="test/long")

    with (
        patch("codewiki.src.be.llm_middleware.count_tokens", return_value=50_000),
        patch(
            "codewiki.src.be.llm_middleware.raw_llm_call",
            side_effect=[overflow, fake_result],
        ) as mock_raw,
    ):
        result = middleware.call("prompt")

    assert result.content == "ok"
    assert mock_raw.call_count == 2
    assert mock_raw.call_args_list[0].args[2] == "test/main"
    assert mock_raw.call_args_list[1].args[2] == "test/long"


def test_middleware_model_is_valid_pydantic_model(tmp_path):
    from pydantic_ai.models import Model

    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))
    model = middleware.create_agent_model()

    assert isinstance(model, Model)
    assert model.model_name == "test/main"
    assert model.system == "openai"


@pytest.mark.asyncio
async def test_request_overflow_switches_model_and_succeeds(tmp_path):
    from codewiki.src.be.errors import ErrorCategory, LLMError

    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))
    model = middleware.create_agent_model()
    overflow = LLMError("context_length_exceeded", ErrorCategory.RESOURCE_EXHAUSTED, 400)

    class FakeFallbackModel:
        def __init__(self):
            self.calls = 0

        async def request(self, messages, model_settings, model_request_parameters):
            self.calls += 1
            raise overflow

    class FakeLongModel:
        def __init__(self):
            self.calls = 0

        async def request(self, messages, model_settings, model_request_parameters):
            self.calls += 1
            return SimpleNamespace(model_name="test/long", parts=[], usage=None)

    fake_long = FakeLongModel()
    fake_fallback = FakeFallbackModel()

    with (
        patch.object(model, "_estimate_message_tokens", return_value=50_000),
        patch("codewiki.src.be.llm_middleware.create_long_context_model", return_value=fake_long),
        patch("codewiki.src.be.llm_middleware.create_fallback_models", return_value=fake_fallback),
    ):
        result = await model.request(
            [SimpleNamespace(parts=[SimpleNamespace(content="prompt")])], None, None
        )

    assert result.model_name == "test/long"
    assert fake_fallback.calls == 1
    assert fake_long.calls == 1


def test_middleware_model_private_getattr_raises_attribute_error(tmp_path):
    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))
    model = middleware.create_agent_model()

    with pytest.raises(AttributeError):
        getattr(model, "_missing_private_attr")


def test_resolve_pydantic_model_uses_cache(tmp_path):
    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))
    model = middleware.create_agent_model()

    with patch(
        "codewiki.src.be.llm_middleware.create_fallback_models", return_value=object()
    ) as mock_create:
        first = model._resolve_pydantic_model("test/main")
        second = model._resolve_pydantic_model("test/main")

    assert first is second
    assert mock_create.call_count == 1


@pytest.mark.asyncio
async def test_request_stream_overflow_switches_model_and_trims_history(tmp_path):
    from codewiki.src.be.errors import ErrorCategory, LLMError

    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))
    model = middleware.create_agent_model()
    overflow = LLMError("context_length_exceeded", ErrorCategory.RESOURCE_EXHAUSTED, 400)

    class FakeFallbackModel:
        def __init__(self):
            self.calls = 0

        def request_stream(
            self, messages, model_settings, model_request_parameters, run_context=None
        ):
            self.calls += 1
            raise overflow

    class FakeLongModel:
        def __init__(self):
            self.calls = 0

        def request_stream(
            self, messages, model_settings, model_request_parameters, run_context=None
        ):
            self.calls += 1

            @asynccontextmanager
            async def _stream():
                yield SimpleNamespace(content="streamed")

            return _stream()

    fake_long = FakeLongModel()
    fake_fallback = FakeFallbackModel()

    with (
        patch.object(model, "_estimate_message_tokens", return_value=50_000),
        patch("codewiki.src.be.llm_middleware.create_long_context_model", return_value=fake_long),
        patch("codewiki.src.be.llm_middleware.create_fallback_models", return_value=fake_fallback),
    ):
        messages = [
            SimpleNamespace(parts=[SimpleNamespace(content="system prompt")]),
            SimpleNamespace(parts=[SimpleNamespace(content="user prompt")]),
            SimpleNamespace(parts=[SimpleNamespace(content="assistant turn 1")]),
            SimpleNamespace(parts=[SimpleNamespace(content="assistant turn 2")]),
        ]

        async with model.request_stream(messages, None, None) as stream:
            assert stream.content == "streamed"

    assert fake_fallback.calls == 1
    assert fake_long.calls == 1


def test_is_context_overflow_handles_non_overflow(tmp_path):
    from codewiki.src.be.errors import ErrorCategory, LLMError

    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))
    assert (
        middleware._is_context_overflow(
            LLMError("model not found", ErrorCategory.NON_RETRYABLE_CONFIG, 404)
        )
        is False
    )


def test_middleware_trims_prompt_after_repeated_overflow(tmp_path):
    from codewiki.src.be.errors import ErrorCategory, LLMError
    from codewiki.src.be.llm_usage import LLMCallResult

    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))
    overflow = LLMError("context_length_exceeded", ErrorCategory.RESOURCE_EXHAUSTED, 400)
    fake_result = LLMCallResult(content="ok", usage=None, model="test/main")

    with (
        patch("codewiki.src.be.llm_middleware.count_tokens", side_effect=[50_000, 50_000, 50_000]),
        patch(
            "codewiki.src.be.llm_middleware.raw_llm_call",
            side_effect=[overflow, overflow, fake_result],
        ) as mock_raw,
        patch.object(
            middleware, "_truncate", side_effect=lambda text, _: f"{text}::trimmed"
        ) as mock_truncate,
    ):
        result = middleware.call("prompt", model="test/main", max_retries=3, trim_step=10_000)

    assert result.content == "ok"
    assert mock_truncate.called
    assert mock_raw.call_args_list[-1].args[0].endswith("::trimmed")


def test_trim_conversation_preserves_first_exchange_and_latest_tail(tmp_path):
    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))
    model = middleware.create_agent_model()

    messages = [
        mw_mod.ModelRequest(parts=[SimpleNamespace(content="system+user")]),
        mw_mod.MessageModelResponse(parts=[SimpleNamespace(content="first reply")]),
        SimpleNamespace(parts=[SimpleNamespace(content="older turn")]),
        SimpleNamespace(parts=[SimpleNamespace(content="latest turn")]),
    ]

    with patch.object(model, "_estimate_message_tokens", side_effect=[4, 3, 3, 3]):
        trimmed = model._trim_conversation(messages, budget_tokens=9)

    assert trimmed[0] is messages[0]
    assert trimmed[1] is messages[1]
    assert trimmed[-1] is messages[-1]
    assert messages[2] not in trimmed


def test_trim_conversation_early_returns_for_short_history(tmp_path):
    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))
    model = middleware.create_agent_model()
    messages = [SimpleNamespace(parts=[]), SimpleNamespace(parts=[])]

    assert model._trim_conversation(messages, budget_tokens=1) == messages


@pytest.mark.asyncio
async def test_request_stream_does_not_retry_after_stream_has_been_yielded(tmp_path):
    from codewiki.src.be.errors import ErrorCategory, LLMError

    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))
    model = middleware.create_agent_model()
    overflow = LLMError("context_length_exceeded", ErrorCategory.RESOURCE_EXHAUSTED, 400)

    class FakeStreamModel:
        def __init__(self):
            self.calls = 0

        def request_stream(
            self, messages, model_settings, model_request_parameters, run_context=None
        ):
            self.calls += 1

            @asynccontextmanager
            async def _stream():
                try:
                    yield SimpleNamespace(content="streamed")
                finally:
                    raise overflow

            return _stream()

    fake_model = FakeStreamModel()

    with patch("codewiki.src.be.llm_middleware.create_fallback_models", return_value=fake_model):
        with pytest.raises(type(overflow)):
            async with model.request_stream([SimpleNamespace(parts=[])], None, None):
                pass

    assert fake_model.calls == 1


def test_is_context_overflow_detects_model_http_error(tmp_path):
    from pydantic_ai.exceptions import ModelHTTPError

    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))

    exc = ModelHTTPError(
        400, "test/main", {"error": {"message": "Range of input length should be [1, 202745]"}}
    )
    assert middleware._is_context_overflow(exc) is True


def test_is_context_overflow_detects_input_usage_limit(tmp_path):
    from pydantic_ai.exceptions import UsageLimitExceeded

    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))

    exc = UsageLimitExceeded("Exceeded the input_tokens_limit of 1000 (input_tokens=1200)")
    assert middleware._is_context_overflow(exc) is True


def test_is_context_overflow_ignores_request_limit_usage_error(tmp_path):
    from pydantic_ai.exceptions import UsageLimitExceeded

    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))

    exc = UsageLimitExceeded("The next request would exceed the request_limit of 50")
    assert middleware._is_context_overflow(exc) is False


def test_is_context_overflow_detects_openai_bad_request(tmp_path):
    import openai

    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))

    response = httpx.Response(400, request=httpx.Request("POST", "http://localhost"))
    exc = openai.BadRequestError(
        "context_length_exceeded",
        response=response,
        body={"error": {"message": "context_length_exceeded"}},
    )
    assert middleware._is_context_overflow(exc) is True


@pytest.mark.asyncio
async def test_request_stream_reclassifies_quota_exhausted_429(tmp_path):
    from codewiki.src.be.errors import ErrorCategory, LLMError
    from pydantic_ai.exceptions import ModelHTTPError

    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))
    model = middleware.create_agent_model()

    class FakeModel:
        def request_stream(
            self, messages, model_settings, model_request_parameters, run_context=None
        ):
            raise ModelHTTPError(
                429,
                "test/main",
                {"message": "monthly quota exhausted", "type": "limitation", "code": "429"},
            )

    with patch("codewiki.src.be.llm_middleware.create_fallback_models", return_value=FakeModel()):
        with pytest.raises(LLMError) as exc_info:
            async with model.request_stream([SimpleNamespace(parts=[])], None, None):
                pass

    assert exc_info.value.category == ErrorCategory.NON_RETRYABLE_CONFIG


@pytest.mark.asyncio
async def test_request_reclassifies_quota_exhausted_429(tmp_path):
    from codewiki.src.be.errors import ErrorCategory, LLMError
    from pydantic_ai.exceptions import ModelHTTPError

    mw_mod = _load_middleware_module()
    middleware = mw_mod.LLMMiddleware(_make_config(tmp_path))
    model = middleware.create_agent_model()

    class FakeModel:
        async def request(self, messages, model_settings, model_request_parameters):
            raise ModelHTTPError(
                429,
                "test/main",
                {"message": "monthly quota exhausted", "type": "limitation", "code": "429"},
            )

    with patch("codewiki.src.be.llm_middleware.create_fallback_models", return_value=FakeModel()):
        with pytest.raises(LLMError) as exc_info:
            await model.request([SimpleNamespace(parts=[])], None, None)

    assert exc_info.value.category == ErrorCategory.NON_RETRYABLE_CONFIG
