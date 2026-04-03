import pytest


class TestErrorCategory:
    def test_retryable_transient(self):
        from codewiki.src.be.errors import ErrorCategory

        assert ErrorCategory.RETRYABLE_TRANSIENT.value == "retryable_transient"

    def test_all_categories_exist(self):
        from codewiki.src.be.errors import ErrorCategory

        names = {c.name for c in ErrorCategory}
        assert names == {
            "RETRYABLE_TRANSIENT",
            "RETRYABLE_AUTH",
            "NON_RETRYABLE_CLIENT",
            "NON_RETRYABLE_CONFIG",
            "RESOURCE_EXHAUSTED",
        }


class TestLLMError:
    def test_is_retryable_transient(self):
        from codewiki.src.be.errors import ErrorCategory, LLMError

        err = LLMError("timeout", ErrorCategory.RETRYABLE_TRANSIENT)
        assert err.is_retryable

    def test_is_retryable_auth(self):
        from codewiki.src.be.errors import ErrorCategory, LLMError

        err = LLMError("auth", ErrorCategory.RETRYABLE_AUTH)
        assert err.is_retryable

    def test_not_retryable_client(self):
        from codewiki.src.be.errors import ErrorCategory, LLMError

        err = LLMError("bad input", ErrorCategory.NON_RETRYABLE_CLIENT, status_code=400)
        assert not err.is_retryable

    def test_not_retryable_config(self):
        from codewiki.src.be.errors import ErrorCategory, LLMError

        err = LLMError("no key", ErrorCategory.NON_RETRYABLE_CONFIG)
        assert not err.is_retryable


class TestClassifyLlmException:
    def test_timeout_is_transient(self):
        import openai

        from codewiki.src.be.errors import ErrorCategory, classify_llm_exception

        exc = openai.APITimeoutError(request=None)
        result = classify_llm_exception(exc)
        assert result.category == ErrorCategory.RETRYABLE_TRANSIENT

    def test_rate_limit_is_transient(self):
        from codewiki.src.be.errors import ErrorCategory, classify_llm_exception

        class FakeAPIError(Exception):
            def __init__(self, status_code):
                self.status_code = status_code
                super().__init__(f"status {status_code}")

        result = classify_llm_exception(FakeAPIError(429))
        assert result.category == ErrorCategory.RETRYABLE_TRANSIENT

    def test_400_is_client_error(self):
        from codewiki.src.be.errors import ErrorCategory, classify_llm_exception

        class FakeAPIError(Exception):
            def __init__(self):
                self.status_code = 400
                self.message = "bad request"
                self.body = None
                super().__init__("bad request")

        result = classify_llm_exception(FakeAPIError())
        assert result.category == ErrorCategory.NON_RETRYABLE_CLIENT

    def test_context_length_is_resource_exhausted(self):
        from codewiki.src.be.errors import ErrorCategory, classify_llm_exception

        class FakeAPIError(Exception):
            def __init__(self):
                self.status_code = 400
                self.message = "context_length_exceeded"
                self.body = {"error": {"code": "context_length_exceeded"}}
                super().__init__("context_length_exceeded")

        result = classify_llm_exception(FakeAPIError())
        assert result.category == ErrorCategory.RESOURCE_EXHAUSTED

    def test_config_value_error_is_config(self):
        from codewiki.src.be.errors import ErrorCategory, classify_llm_exception

        result = classify_llm_exception(ValueError("API key not configured"))
        assert result.category == ErrorCategory.NON_RETRYABLE_CONFIG

    def test_runtime_value_error_reraised(self):
        from codewiki.src.be.errors import classify_llm_exception

        with pytest.raises(ValueError, match="LLM returned empty"):
            classify_llm_exception(ValueError("LLM returned empty content"))

    def test_unknown_error_reraised(self):
        from codewiki.src.be.errors import classify_llm_exception

        with pytest.raises(RuntimeError, match="unexpected"):
            classify_llm_exception(RuntimeError("unexpected"))


class TestCancellationError:
    def test_is_independent(self):
        from codewiki.src.be.errors import CancellationError, LLMError

        err = CancellationError("cancelled")
        assert not isinstance(err, LLMError)
