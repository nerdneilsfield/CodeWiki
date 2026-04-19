"""Persisted component source hashes for incremental invalidation."""

from __future__ import annotations

import json
import os

_FILENAME = "component_hashes.json"


def _path(cache_dir: str) -> str:
    return os.path.join(cache_dir, _FILENAME)


def load_component_hashes(cache_dir: str) -> dict[str, str]:
    path = _path(cache_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items()}


def save_component_hashes(cache_dir: str, hashes: dict[str, str]) -> None:
    path = _path(cache_dir)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(hashes, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
