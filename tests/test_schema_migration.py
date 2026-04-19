from __future__ import annotations

import json

from codewiki.src.be.cache_manager import CACHE_REGISTRY_FILENAME, CacheManager


def test_v1_registry_drops_refinement_entries(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()

    payload = {
        "schema_version": "cache.v1",
        "metadata": {},
        "entries": {
            "refinement:auth": {
                "input_hash": "x",
                "status": "valid",
                "output_path": "/tmp/auth.json",
                "output_file": "auth.json",
                "model": "m",
                "attempt_count": 1,
                "error": "",
                "updated_at": "",
                "depends_on": [],
            },
            "module:other": {
                "input_hash": "y",
                "status": "valid",
                "output_path": "/tmp/other.md",
                "output_file": "other.md",
                "model": "m",
                "attempt_count": 1,
                "error": "",
                "updated_at": "",
                "depends_on": [],
            },
        },
    }
    with open(cache_dir / CACHE_REGISTRY_FILENAME, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)

    cache = CacheManager(str(cache_dir), flush_interval=60)
    assert cache.get_entry("refinement:auth") is None
    module_entry = cache.get_entry("module:other")
    assert module_entry is not None
    assert module_entry.status == "valid"


def test_fresh_cache_writes_v2(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()

    cache = CacheManager(str(cache_dir), flush_interval=60)
    cache.update_metadata(commit_id="abc")
    cache.flush()

    with open(cache_dir / CACHE_REGISTRY_FILENAME, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    assert data["schema_version"] == "cache.v2"
