# tests/test_perf_llm_client.py
from unittest.mock import MagicMock
from codewiki.src.be.llm_services import create_openai_client


def _make_config(base_url="https://api.example.com", api_key="sk-test"):
    cfg = MagicMock()
    cfg.llm_base_url = base_url
    cfg.llm_api_key = api_key
    return cfg


def test_same_config_returns_same_client():
    """create_openai_client must return the same object for identical config."""
    cfg = _make_config()
    c1 = create_openai_client(cfg)
    c2 = create_openai_client(cfg)
    assert c1 is c2, "Expected cached client, got two different objects"


def test_different_url_returns_different_client():
    """Different base_url must produce a distinct client."""
    c1 = create_openai_client(_make_config(base_url="https://a.example.com"))
    c2 = create_openai_client(_make_config(base_url="https://b.example.com"))
    assert c1 is not c2


def test_different_api_key_returns_different_client():
    """Different api_key must produce a distinct client."""
    c1 = create_openai_client(_make_config(api_key="sk-aaa"))
    c2 = create_openai_client(_make_config(api_key="sk-bbb"))
    assert c1 is not c2
