from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codewiki.src.be.errors import CancellationError, ErrorCategory, LLMError


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
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_cancellation_stops_retry(self):
        from codewiki.src.be.cancellation import CancellationToken
        from codewiki.src.be.llm_retry import with_retry

        token = CancellationToken()
        calls = []

        async def slow():
            calls.append(1)
            if len(calls) == 1:
                token.cancel()
                raise LLMError("timeout", ErrorCategory.RETRYABLE_TRANSIENT)
            return "should not reach"

        with pytest.raises(CancellationError):
            await with_retry(slow, max_retries=5, cancel_token=token)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_retry_after_header_takes_priority(self):
        from codewiki.src.be.llm_retry import with_retry

        calls = []

        class RetryAfterCause(Exception):
            def __init__(self):
                self.response = MagicMock()
                self.response.headers = {"retry-after": "7.5"}
                super().__init__("retry-after")

        async def flaky():
            calls.append(1)
            if len(calls) < 2:
                err = LLMError("rate limit", ErrorCategory.RETRYABLE_TRANSIENT, status_code=429)
                err.__cause__ = RetryAfterCause()
                raise err
            return "ok"

        sleep_mock = AsyncMock()
        with patch("asyncio.sleep", sleep_mock):
            result = await with_retry(flaky, max_retries=3)

        assert result == "ok"
        sleep_mock.assert_awaited_once_with(7.5)

    @pytest.mark.asyncio
    async def test_exhausted_raises_llm_retry_exhausted(self):
        from codewiki.src.be.llm_retry import LLMRetryExhausted, with_retry

        async def always_fail():
            raise LLMError("fail", ErrorCategory.RETRYABLE_TRANSIENT)

        with pytest.raises(LLMRetryExhausted) as exc_info:
            await with_retry(always_fail, max_retries=2)
        assert exc_info.value.attempts == 3
