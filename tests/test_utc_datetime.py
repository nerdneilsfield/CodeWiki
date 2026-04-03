import inspect
import json

import pytest


class TestNoNaiveDatetime:
    @pytest.mark.parametrize(
        "module_path",
        [
            "codewiki.src.fe.routes",
            "codewiki.src.fe.background_worker",
            "codewiki.src.fe.cache_manager",
        ],
    )
    def test_no_naive_datetime_now(self, module_path):
        import importlib

        mod = importlib.import_module(module_path)
        source = inspect.getsource(mod)
        naive_calls = [
            i
            for i, line in enumerate(source.split("\n"), 1)
            if "datetime.now()" in line and "timezone" not in line and "utc" not in line.lower()
        ]
        assert not naive_calls, f"Naive datetime.now() at lines: {naive_calls}"


class TestNaiveDatetimeBackwardCompat:
    def test_cache_manager_reads_naive_timestamps_without_error(self, tmp_path):
        from codewiki.src.fe.cache_manager import CacheManager

        index_data = {
            "abc123": {
                "repo_url": "http://example.com/repo",
                "repo_url_hash": "abc123",
                "docs_path": str(tmp_path / "docs"),
                "created_at": "2026-01-01T12:00:00",
                "last_accessed": "2026-01-01T12:00:00",
            }
        }
        (tmp_path / "docs").mkdir()
        (tmp_path / "cache_index.json").write_text(json.dumps(index_data), encoding="utf-8")

        mgr = CacheManager(cache_dir=str(tmp_path))
        mgr.get_cached_docs("http://example.com/repo")
        mgr.cleanup_expired_cache()
