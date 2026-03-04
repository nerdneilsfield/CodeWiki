# tests/test_perf_retry_jitter.py
"""Tests for retry jitter and Retry-After header handling."""
from unittest.mock import MagicMock, patch
from codewiki.src.be.llm_services import _sleep_with_jitter, _parse_retry_after


def test_jitter_adds_randomness():
    """Two calls with the same base should produce different sleep durations."""
    sleeps = set()
    for _ in range(20):
        with patch("time.sleep") as mock_sleep:
            _sleep_with_jitter(10.0)
            sleeps.add(mock_sleep.call_args[0][0])
    assert len(sleeps) > 1, "Jitter produced identical delays — likely not random"


def test_jitter_within_bounds():
    """Jitter must stay within [base, base * 1.5]."""
    base = 10.0
    for _ in range(50):
        with patch("time.sleep") as mock_sleep:
            _sleep_with_jitter(base)
            actual = mock_sleep.call_args[0][0]
            assert base <= actual <= base * 1.5 + 0.01, (
                f"Jitter {actual} out of range [{base}, {base * 1.5}]"
            )


def test_parse_retry_after_from_rate_limit_error():
    """_parse_retry_after must extract seconds from 429 response headers."""
    import openai
    exc = MagicMock(spec=openai.RateLimitError)
    exc.response = MagicMock()
    exc.response.headers = {"retry-after": "42"}
    result = _parse_retry_after(exc)
    assert result == 42.0


def test_parse_retry_after_returns_none_for_non_429():
    """_parse_retry_after must return None for non-RateLimitError exceptions."""
    result = _parse_retry_after(ValueError("something else"))
    assert result is None


def test_parse_retry_after_returns_none_when_header_missing():
    """_parse_retry_after must return None when no Retry-After header present."""
    import openai
    exc = MagicMock(spec=openai.RateLimitError)
    exc.response = MagicMock()
    exc.response.headers = {}
    result = _parse_retry_after(exc)
    assert result is None


def test_parse_retry_after_clamps_negative_value():
    """Negative Retry-After must not be returned (would crash time.sleep)."""
    import openai
    from unittest.mock import MagicMock
    from codewiki.src.be.llm_services import _parse_retry_after

    exc = MagicMock(spec=openai.RateLimitError)
    exc.response = MagicMock()
    exc.response.headers = {"retry-after": "-5"}
    result = _parse_retry_after(exc)
    assert result is None, f"Expected None for negative Retry-After, got {result!r}"


def test_parse_retry_after_clamps_oversized_value():
    """Retry-After > 120s must be clamped to 120 to prevent excessive blocking."""
    import openai
    from unittest.mock import MagicMock
    from codewiki.src.be.llm_services import _parse_retry_after

    exc = MagicMock(spec=openai.RateLimitError)
    exc.response = MagicMock()
    exc.response.headers = {"retry-after": "9999"}
    result = _parse_retry_after(exc)
    assert result is not None
    assert result <= 120, f"Expected <= 120s but got {result}"
