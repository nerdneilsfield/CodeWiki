"""Verify that AsyncOpenAI clients and OpenAIProviders are module-level singletons."""


def test_cached_async_client_same_object():
    """Same (base_url, api_key) returns the identical AsyncOpenAI instance."""
    from codewiki.src.be.llm_services import _get_cached_async_client

    c1 = _get_cached_async_client("http://test-host/", "key-abc")
    c2 = _get_cached_async_client("http://test-host/", "key-abc")
    assert c1 is c2


def test_cached_async_client_different_keys_give_different_objects():
    """Different API keys yield distinct AsyncOpenAI instances."""
    from codewiki.src.be.llm_services import _get_cached_async_client

    c1 = _get_cached_async_client("http://test-host/", "key-aaa")
    c2 = _get_cached_async_client("http://test-host/", "key-bbb")
    assert c1 is not c2


def test_cached_async_provider_same_object():
    """Same (base_url, api_key) returns the identical OpenAIProvider instance."""
    from codewiki.src.be.llm_services import _get_cached_async_provider

    p1 = _get_cached_async_provider("http://test-host/", "key-xyz")
    p2 = _get_cached_async_provider("http://test-host/", "key-xyz")
    assert p1 is p2


def test_make_provider_reuses_cached_provider():
    """_make_provider() returns the same object on repeated calls with same config."""
    from unittest.mock import MagicMock
    from codewiki.src.be.llm_services import _make_provider

    cfg = MagicMock()
    cfg.llm_base_url = "http://test-host/"
    cfg.llm_api_key = "key-reuse"

    p1 = _make_provider(cfg)
    p2 = _make_provider(cfg)
    assert p1 is p2


def _make_dummy_config(long_context_model=None):
    from unittest.mock import MagicMock

    cfg = MagicMock()
    cfg.llm_base_url = "http://test-host/"
    cfg.llm_api_key = "key-test"
    cfg.main_model = "main-model"
    cfg.fallback_model = "fallback-model"
    cfg.long_context_model = long_context_model
    cfg.long_context_threshold = 50_000
    cfg.max_tokens = 4096
    cfg.max_depth = 2
    cfg.repo_path = "/tmp"
    cfg.output_language = "en"
    cfg.get_prompt_addition.return_value = None
    return cfg


def test_create_agent_does_not_call_create_fallback_models_per_module():
    """create_agent() must not rebuild FallbackModel on every call."""
    from unittest.mock import patch
    from codewiki.src.be.agent_orchestrator import AgentOrchestrator
    import codewiki.src.be.agent_orchestrator as orch_mod

    cfg = _make_dummy_config()
    with patch.object(
        orch_mod, "create_fallback_models", wraps=orch_mod.create_fallback_models
    ) as mock_cfm:
        orch = AgentOrchestrator(cfg)
        calls_after_init = mock_cfm.call_count  # exactly 1 call from __init__

        orch.create_agent("mod1", {}, [], estimated_tokens=0)
        orch.create_agent("mod2", {}, [], estimated_tokens=0)
        orch.create_agent("mod3", {}, [], estimated_tokens=0)

    # No additional calls beyond the one in __init__
    assert mock_cfm.call_count == calls_after_init, (
        f"create_fallback_models called {mock_cfm.call_count} times; "
        f"expected {calls_after_init} (only during __init__)"
    )


def test_create_agent_reuses_long_context_model_for_large_prompts():
    """create_agent() must not rebuild long_context_model per module."""
    from unittest.mock import patch
    from codewiki.src.be.agent_orchestrator import AgentOrchestrator
    import codewiki.src.be.agent_orchestrator as orch_mod

    cfg = _make_dummy_config(long_context_model="long-ctx-model")
    with patch.object(
        orch_mod, "create_long_context_model", wraps=orch_mod.create_long_context_model
    ) as mock_clcm:
        orch = AgentOrchestrator(cfg)
        calls_after_init = mock_clcm.call_count  # exactly 1 call from __init__

        big = cfg.long_context_threshold + 1
        orch.create_agent("mod1", {}, [], estimated_tokens=big)
        orch.create_agent("mod2", {}, [], estimated_tokens=big)

    assert mock_clcm.call_count == calls_after_init, (
        f"create_long_context_model called {mock_clcm.call_count} times; "
        f"expected {calls_after_init} (only during __init__)"
    )


def test_create_agent_uses_fallback_when_no_long_context_model():
    """When long_context_model is None, create_agent always uses self.fallback_models."""
    from unittest.mock import patch
    from codewiki.src.be.agent_orchestrator import AgentOrchestrator
    import codewiki.src.be.agent_orchestrator as orch_mod

    cfg = _make_dummy_config(long_context_model=None)
    with patch.object(orch_mod, "create_long_context_model") as mock_clcm:
        orch = AgentOrchestrator(cfg)
        # Even with a huge token count, no long_context_model should be used
        orch.create_agent("mod1", {}, [], estimated_tokens=999_999)

    mock_clcm.assert_not_called()
    assert orch.long_context_model is None
