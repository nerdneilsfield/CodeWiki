from unittest.mock import MagicMock, patch

import pytest


class TestLlmCallResult:
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


class TestLlmUsageStats:
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

        config = MagicMock()
        config.main_model = "test"
        config.max_tokens = 100
        config.long_context_model = None
        config.long_context_threshold = 200_000
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

        with patch(
            "codewiki.src.be.llm_services._create_client_for_model",
            return_value=(mock_client, "openai_compatible"),
        ):
            result = call_llm("test", config)

        assert isinstance(result, LLMCallResult)
        assert result.content == "response text"
        assert result.usage is not None
        assert result.usage.input_tokens == 10

    def test_call_llm_no_retry_loop(self):
        """call_llm must raise on first failure, not retry."""
        from codewiki.src.be.llm_services import call_llm

        config = MagicMock()
        config.main_model = "test"
        config.max_tokens = 100
        config.long_context_model = None
        config.long_context_threshold = 200_000
        config.providers = None
        config.llm_base_url = "http://localhost"
        config.llm_api_key = "key"

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("simulated LLM failure")

        with patch(
            "codewiki.src.be.llm_services._create_client_for_model",
            return_value=(mock_client, "openai_compatible"),
        ):
            with pytest.raises(Exception):
                call_llm("test", config)

        assert mock_client.chat.completions.create.call_count == 1
