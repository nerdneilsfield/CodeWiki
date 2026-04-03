import os
import time


class TestM5CacheManagerDirtyFlag:
    def test_get_cached_docs_does_not_write_immediately(self, tmp_path):
        from codewiki.src.fe.cache_manager import CacheManager

        mgr = CacheManager(cache_dir=str(tmp_path))
        mgr.add_to_cache("http://repo", str(tmp_path / "docs"))
        index_path = tmp_path / "cache_index.json"
        mtime_before = index_path.stat().st_mtime if index_path.exists() else 0
        time.sleep(0.05)
        mgr.get_cached_docs("http://repo")
        mtime_after = index_path.stat().st_mtime if index_path.exists() else 0
        assert mtime_after == mtime_before, "get_cached_docs should not write to disk"


class TestM7EvidenceSnippetsUsesEdgeIndex:
    def test_no_full_scan(self):
        import inspect
        from codewiki.src.be.generation.context_pack import _build_evidence_snippets

        source = inspect.getsource(_build_evidence_snippets)
        assert "edge_index" in source


class TestS5CleanupHours:
    def test_cleanup_hours_reasonable(self):
        from codewiki.src.fe.config import WebAppConfig

        assert WebAppConfig.JOB_CLEANUP_HOURS <= 168


class TestS1NoStrayTestFiles:
    def test_no_root_test_files(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        stray = [f for f in os.listdir(root) if f.startswith("test_") and f.endswith(".py")]
        assert not stray, f"Stray test files in root: {stray}"
