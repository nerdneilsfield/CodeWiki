from __future__ import annotations

from codewiki.src.be.component_hash_registry import (
    load_component_hashes,
    save_component_hashes,
)


def test_save_and_load_roundtrip(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()

    hashes = {"a.py::A": "h1", "b.py::B": "h2"}
    save_component_hashes(str(cache_dir), hashes)

    assert load_component_hashes(str(cache_dir)) == hashes


def test_load_missing_returns_empty(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()

    assert load_component_hashes(str(cache_dir)) == {}
