from unittest.mock import MagicMock, patch

import pytest


class TestCallLlmRaisesLLMError:
    def test_timeout_becomes_llm_error(self):
        import openai

        from codewiki.src.be.errors import ErrorCategory, LLMError
        from codewiki.src.be.llm_services import raw_llm_call

        config = MagicMock()
        config.main_model = "test"
        config.max_tokens = 100
        config.long_context_model = None
        config.providers = None
        config.llm_base_url = "http://localhost"
        config.llm_api_key = "key"

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = openai.APITimeoutError(request=None)

        with patch(
            "codewiki.src.be.llm_services._create_client_for_model",
            return_value=(mock_client, "openai_compatible"),
        ):
            with pytest.raises(LLMError) as exc_info:
                raw_llm_call("test", config, "test")
            assert exc_info.value.category == ErrorCategory.RETRYABLE_TRANSIENT

    def test_streaming_path_returns_estimated_usage(self):
        from types import SimpleNamespace

        from codewiki.src.be.llm_services import raw_llm_call

        config = MagicMock()
        config.main_model = "openai/gpt-4o"
        config.max_tokens = 100
        config.long_context_model = None
        config.long_context_threshold = 999999
        config.providers = [object()]

        chunk1 = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="hello "))])
        chunk2 = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="world"))])
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = [chunk1, chunk2]

        with (
            patch(
                "codewiki.src.be.llm_services._create_client_for_model",
                return_value=(mock_client, "openai_compatible"),
            ),
            patch(
                "codewiki.src.be.llm_services.resolve_model_ref",
                return_value=SimpleNamespace(model_name="gpt-4o", stream=True),
            ),
            patch("codewiki.src.be.utils.count_tokens", side_effect=[12, 12, 34]),
        ):
            result = raw_llm_call("test", config, "openai/gpt-4o", stream=True)

        assert result.content == "hello world"
        assert result.usage is not None
        assert result.usage.source == "estimated"
        mock_client.chat.completions.create.assert_called_once()
        assert mock_client.chat.completions.create.call_args.kwargs["stream"] is True


class TestPipelineRunnerCancelled:
    @pytest.mark.asyncio
    async def test_cancelled_status_in_result(self):
        from codewiki.src.be.errors import CancellationError
        from codewiki.src.be.pipeline import PipelineContext, PipelineRunner

        class CancelStage:
            name = "cancel"
            failure_policy = "fail_fast"

            async def execute(self, ctx):
                raise CancellationError("user cancelled")

        runner = PipelineRunner([CancelStage()])
        ctx = PipelineContext(config=None)
        result = await runner.execute(ctx)
        assert result.status == "cancelled"
