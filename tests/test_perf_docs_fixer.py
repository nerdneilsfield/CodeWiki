# tests/test_perf_docs_fixer.py
"""Tests for incremental fix_docs caching and parallel Phase 1."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_config():
    cfg = MagicMock()
    cfg.main_model = "gpt-4"
    cfg.max_tokens = 4096
    cfg.llm_base_url = "https://api.example.com"
    cfg.llm_api_key = "sk-test"
    return cfg


SAMPLE_MD = "# Hello\n\nSome text.\n"


def test_cache_file_created_after_run(tmp_path):
    """fix_docs must create .fix_docs_cache.json after processing."""
    from codewiki.src.be.docs_fixer import fix_docs

    (tmp_path / "overview.md").write_text(SAMPLE_MD)

    fix_docs(str(tmp_path), _make_config())

    cache_file = tmp_path / ".fix_docs_cache.json"
    assert cache_file.exists(), ".fix_docs_cache.json not created"
    cache = json.loads(cache_file.read_text())
    assert "overview.md" in cache


def test_llm_skipped_on_second_run(tmp_path):
    """Phase 2+3 (LLM) must be skipped for unchanged files on second run."""
    from codewiki.src.be.docs_fixer import fix_docs

    (tmp_path / "overview.md").write_text(SAMPLE_MD)

    # First run populates cache
    fix_docs(str(tmp_path), _make_config())

    # Second run — track call_llm calls
    with patch("codewiki.src.be.docs_fixer.call_llm") as mock_llm:
        fix_docs(str(tmp_path), _make_config())
        assert mock_llm.call_count == 0, f"LLM called {mock_llm.call_count} times on unchanged file"


def test_phase1_runs_in_parallel(tmp_path):
    """Phase 1 formatting must process multiple files concurrently."""
    from codewiki.src.be.docs_fixer import fix_docs

    for i in range(10):
        (tmp_path / f"doc_{i}.md").write_text(SAMPLE_MD)

    def slow_format(text, stats):
        time.sleep(0.02)  # 20 ms artificial delay per file
        return text

    with patch("codewiki.src.be.docs_fixer._format_markdown", side_effect=slow_format):
        t0 = time.perf_counter()
        fix_docs(str(tmp_path), _make_config())
        elapsed = time.perf_counter() - t0

    # Serial: 10 * 20ms = 200ms. Parallel (>=2 workers): < 160ms.
    assert elapsed < 0.16, (
        f"Phase 1 appears serial: {elapsed:.3f}s for 10 files "
        f"(expected <0.16s with parallel execution)"
    )
