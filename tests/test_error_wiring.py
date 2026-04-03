from unittest.mock import MagicMock, patch

import pytest


class TestCallLlmRaisesLLMError:
    def test_timeout_becomes_llm_error(self):
        import openai

        from codewiki.src.be.errors import ErrorCategory, LLMError
        from codewiki.src.be.llm_services import call_llm

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
                call_llm("test", config)
            assert exc_info.value.category == ErrorCategory.RETRYABLE_TRANSIENT


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
