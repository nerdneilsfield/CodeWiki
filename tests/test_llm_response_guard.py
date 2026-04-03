from unittest.mock import MagicMock, patch

import pytest


def _make_config():
    """Build a minimal Config-like mock that passes call_llm's checks."""
    config = MagicMock()
    config.main_model = "test-model"
    config.max_tokens = 1000
    config.long_context_model = None
    config.long_context_threshold = 200_000
    config.providers = None
    config.llm_base_url = "http://localhost:4000"
    config.llm_api_key = "test-key"
    return config


class TestLlmResponseGuard:
    def test_empty_choices_raises_value_error(self):
        from codewiki.src.be.llm_services import call_llm

        config = _make_config()
        mock_response = MagicMock()
        mock_response.choices = []

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch(
            "codewiki.src.be.llm_services._create_client_for_model",
            return_value=(mock_client, "openai_compatible"),
        ):
            with pytest.raises(ValueError, match="empty choices"):
                call_llm("test prompt", config)

    def test_none_content_raises_value_error(self):
        from codewiki.src.be.llm_services import call_llm

        config = _make_config()
        mock_choice = MagicMock()
        mock_choice.message.content = None
        mock_choice.finish_reason = "length"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch(
            "codewiki.src.be.llm_services._create_client_for_model",
            return_value=(mock_client, "openai_compatible"),
        ):
            with pytest.raises(ValueError, match="null content"):
                call_llm("test prompt", config)

    def test_empty_streaming_content_raises_value_error(self):
        from codewiki.src.be.llm_services import call_llm

        config = _make_config()
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("cloudflare timeout")

        with (
            patch(
                "codewiki.src.be.llm_services._create_client_for_model",
                return_value=(mock_client, "openai_compatible"),
            ),
            patch("codewiki.src.be.llm_services._call_llm_streaming", return_value=""),
            patch("codewiki.src.be.llm_services._sleep_with_jitter", return_value=None),
        ):
            with pytest.raises(ValueError, match="empty content"):
                call_llm("test prompt", config)

    def test_empty_claude_content_raises_value_error(self):
        from codewiki.src.be.llm_services import call_llm

        config = _make_config()

        with (
            patch(
                "codewiki.src.be.llm_services._create_client_for_model",
                return_value=(MagicMock(), "claude"),
            ),
            patch("codewiki.src.be.llm_services._call_claude", return_value=""),
        ):
            with pytest.raises(ValueError, match="empty content"):
                call_llm("test prompt", config)
