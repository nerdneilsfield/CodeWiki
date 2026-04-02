# tests/test_perf_utils.py
import time
from codewiki.src.be.utils import count_tokens, _get_encoder


def test_count_tokens_basic():
    assert count_tokens("hello world", model="gpt-4") == 2


def test_count_tokens_unknown_model_fallback():
    result = count_tokens("hello world", model="some-unknown-model-xyz")
    assert result > 0


def test_count_tokens_encoder_is_cached():
    """Second call with same model must return the same encoder object (cache hit)."""
    enc1 = _get_encoder("gpt-4")
    enc2 = _get_encoder("gpt-4")
    assert enc1 is enc2, "Expected cached encoder, got two different objects"


def test_count_tokens_speed():
    """100 calls should complete in under 50 ms total (cached path)."""
    text = "The quick brown fox " * 50
    count_tokens(text, model="gpt-4")  # warm cache
    start = time.perf_counter()
    for _ in range(100):
        count_tokens(text, model="gpt-4")
    elapsed = time.perf_counter() - start
    assert elapsed < 0.05, f"100 calls took {elapsed:.3f}s — encoder not cached"
