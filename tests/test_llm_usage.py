from concurrent.futures import ThreadPoolExecutor
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

    def test_record_is_thread_safe_under_concurrent_updates(self):
        from codewiki.src.be.llm_usage import LLMUsageStats

        stats = LLMUsageStats()
        workers = 16
        iterations = 2000

        def _worker():
            for _ in range(iterations):
                stats.record("gpt-4o", input_tokens=1, output_tokens=2)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(lambda _i: _worker(), range(workers)))

        expected_requests = workers * iterations
        assert stats.to_dict() == {
            "total_input_tokens": expected_requests,
            "total_output_tokens": expected_requests * 2,
            "total_requests": expected_requests,
            "by_model": {
                "gpt-4o": {
                    "input": expected_requests,
                    "output": expected_requests * 2,
                    "requests": expected_requests,
                }
            },
        }


class TestRawLlmCallReturnsResult:
    def test_raw_llm_call_returns_llm_call_result(self):
        from codewiki.src.be.llm_usage import LLMCallResult
        from codewiki.src.be.llm_services import raw_llm_call

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
            result = raw_llm_call("test", config, "test")

        assert isinstance(result, LLMCallResult)
        assert result.content == "response text"
        assert result.usage is not None
        assert result.usage.input_tokens == 10

    def test_raw_llm_call_uses_anthropic_api_usage_when_available(self):
        from codewiki.src.be.llm_services import raw_llm_call

        config = MagicMock()
        config.main_model = "claude-test"
        config.max_tokens = 100
        config.long_context_model = None
        config.long_context_threshold = 200_000
        config.providers = None
        config.llm_base_url = "http://localhost"
        config.llm_api_key = "key"

        response = MagicMock()
        response.content = [MagicMock(text="claude response")]
        response.usage = MagicMock(input_tokens=21, output_tokens=8)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = response

        with patch(
            "codewiki.src.be.llm_services._create_client_for_model",
            return_value=(mock_client, "claude"),
        ):
            result = raw_llm_call("test", config, "claude-test")

        assert result.content == "claude response"
        assert result.usage is not None
        assert result.usage.input_tokens == 21
        assert result.usage.output_tokens == 8
        assert result.usage.source == "api"

    def test_raw_llm_call_no_retry_loop(self):
        """raw_llm_call must raise on first failure, not retry."""
        from codewiki.src.be.llm_services import raw_llm_call

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
                raw_llm_call("test", config, "test")

        assert mock_client.chat.completions.create.call_count == 1


class TestAgentUsageAccounting:
    def test_single_model_agent_usage_attributes_totals_to_that_model(self):
        from codewiki.src.be.llm_usage import LLMUsageStats, record_agent_run_usage

        usage_stats = LLMUsageStats()
        record_agent_run_usage(usage_stats, ["gpt-4o"], 30, 12, 1)

        assert usage_stats.to_dict() == {
            "total_input_tokens": 30,
            "total_output_tokens": 12,
            "total_requests": 1,
            "by_model": {"gpt-4o": {"input": 30, "output": 12, "requests": 1}},
        }

    def test_multi_model_agent_usage_does_not_fabricate_by_model_tokens(self):
        from codewiki.src.be.llm_usage import LLMUsageStats, record_agent_run_usage

        usage_stats = LLMUsageStats()
        record_agent_run_usage(usage_stats, ["gpt-4o", "glm-4p5"], 44, 18, 2)

        assert usage_stats.to_dict() == {
            "total_input_tokens": 44,
            "total_output_tokens": 18,
            "total_requests": 2,
            "by_model": {},
        }
