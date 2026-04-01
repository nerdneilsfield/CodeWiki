from pathlib import Path
from unittest.mock import patch

import pytest

from codewiki.src.config_loader import ProviderConfig, load_app_config


@pytest.fixture
def runtime_config(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-test")
    app_config = load_app_config(Path("config.example.toml"))
    return app_config.to_runtime_config(repo_path="/tmp/fake-repo")


def test_create_model_from_ref_uses_openai_model_for_openai_provider(runtime_config):
    from codewiki.src.be import llm_services

    sentinel_provider = object()
    sentinel_model = object()
    with patch.object(llm_services, "_make_provider_for_model", return_value=sentinel_provider) as mock_provider, \
         patch.object(llm_services, "OpenAIModel", return_value=sentinel_model) as mock_model:
        result = llm_services.create_model_from_ref(runtime_config, "openai/gpt-4o-mini")

    assert result is sentinel_model
    mock_provider.assert_called_once()
    mock_model.assert_called_once()
    assert mock_model.call_args.kwargs["model_name"] == "gpt-4o-mini"
    assert mock_model.call_args.kwargs["provider"] is sentinel_provider


def test_create_model_from_ref_uses_anthropic_model_for_claude_provider(runtime_config):
    from codewiki.src.be import llm_services

    sentinel_provider = object()
    sentinel_model = object()
    with patch.object(llm_services, "_make_provider_for_model", return_value=sentinel_provider) as mock_provider, \
         patch.object(llm_services, "AnthropicModel", return_value=sentinel_model) as mock_model:
        result = llm_services.create_model_from_ref(runtime_config, "claude/claude-sonnet-4-5-20250929")

    assert result is sentinel_model
    mock_provider.assert_called_once()
    mock_model.assert_called_once()
    assert mock_model.call_args.kwargs["model_name"] == "claude-sonnet-4-5-20250929"
    assert mock_model.call_args.kwargs["provider"] is sentinel_provider


def test_create_model_from_ref_rejects_unsupported_provider_type(runtime_config):
    from codewiki.src.be import llm_services

    runtime_config.providers.append(
        ProviderConfig(name="bad", type="unsupported", model_list=["x"], api_keys=[], extra_headers={})
    )

    with pytest.raises(ValueError, match="unsupported"):
        llm_services.create_model_from_ref(runtime_config, "bad/x")


def test_create_fallback_models_supports_cross_provider_chain(runtime_config):
    from codewiki.src.be import llm_services

    runtime_config.main_model = "openai/gpt-4o-mini"
    runtime_config.fallback_model = "claude/claude-sonnet-4-5-20250929,openai/gpt-4.1"
    runtime_config.long_context_model = None

    created = [object(), object(), object()]
    with patch.object(llm_services, "create_model_from_ref", side_effect=created) as mock_create, \
         patch.object(llm_services, "FallbackModel", side_effect=lambda *args: args) as mock_fallback:
        result = llm_services.create_fallback_models(runtime_config)

    assert result == tuple(created)
    assert mock_create.call_args_list[0].args[1] == "openai/gpt-4o-mini"
    assert mock_create.call_args_list[1].args[1] == "claude/claude-sonnet-4-5-20250929"
    assert mock_create.call_args_list[2].args[1] == "openai/gpt-4.1"
    mock_fallback.assert_called_once()
