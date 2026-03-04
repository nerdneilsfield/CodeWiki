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
