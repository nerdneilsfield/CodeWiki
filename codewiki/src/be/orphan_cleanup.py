"""Orphan cleanup helpers for internal cache files and renamed user docs."""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.config import MODULE_PARTS_DIR, REFINEMENT_DIR

logger = logging.getLogger(__name__)

_MTIME_STAMP_FILENAME = ".codewiki_mtime_stamps.json"


def _mtime_stamp_path(working_dir: str) -> str:
    return os.path.join(working_dir, _MTIME_STAMP_FILENAME)


def _load_mtime_stamps(working_dir: str) -> dict[str, float]:
    path = _mtime_stamp_path(working_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    stamps: dict[str, float] = {}
    for key, value in payload.items():
        try:
            stamps[str(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return stamps


def is_user_modified(working_dir: str, filename: str) -> bool:
    """Return True when the file's mtime differs from the recorded stamp."""
    full_path = os.path.join(working_dir, filename)
    if not os.path.exists(full_path):
        return False
    stamps = _load_mtime_stamps(working_dir)
    expected = stamps.get(filename)
    if expected is None:
        return True
    try:
        actual = os.path.getmtime(full_path)
    except OSError:
        return True
    return abs(actual - expected) > 1.0


def update_mtime_stamps(working_dir: str, filenames: list[str]) -> None:
    """Record current mtime of generated files so later cleanup can detect edits."""
    stamps = _load_mtime_stamps(working_dir)
    for filename in filenames:
        full_path = os.path.join(working_dir, filename)
        if not os.path.exists(full_path):
            continue
        try:
            stamps[filename] = os.path.getmtime(full_path)
        except OSError:
            continue
    path = _mtime_stamp_path(working_dir)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(stamps, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _owned_output_paths(cache_manager: CacheManager) -> set[str]:
    owned: set[str] = set()
    for output_file in cache_manager.output_file_assignments().keys():
        if not output_file:
            continue
        owned.add(os.path.abspath(output_file))
    return owned


def cleanup_internal_artifacts(
    cache_dir: str,
    cache_manager: CacheManager,
) -> dict[str, list[str]]:
    """Remove orphaned internal cache artifacts.

    Layer A is intentionally aggressive because these paths are internal
    implementation details and are safe to reconstruct.
    """
    removed_files: list[str] = []
    removed_dirs: list[str] = []
    owned_paths = _owned_output_paths(cache_manager)

    refinement_root = os.path.join(cache_dir, REFINEMENT_DIR)
    if os.path.isdir(refinement_root):
        for entry in os.listdir(refinement_root):
            full_path = os.path.abspath(os.path.join(refinement_root, entry))
            if not os.path.isfile(full_path):
                continue
            if full_path in owned_paths:
                continue
            try:
                os.unlink(full_path)
                removed_files.append(full_path)
            except OSError as exc:
                logger.warning("orphan cleanup: failed to remove %s: %s", full_path, exc)

    parts_root = os.path.join(cache_dir, MODULE_PARTS_DIR)
    if os.path.isdir(parts_root):
        for stem_dir in os.listdir(parts_root):
            full_dir = os.path.join(parts_root, stem_dir)
            if not os.path.isdir(full_dir):
                continue
            entries = [
                os.path.abspath(os.path.join(full_dir, name))
                for name in os.listdir(full_dir)
                if os.path.isfile(os.path.join(full_dir, name))
            ]
            owned_entries = [path for path in entries if path in owned_paths]
            if owned_entries:
                for path in entries:
                    if path in owned_paths:
                        continue
                    try:
                        os.unlink(path)
                        removed_files.append(path)
                    except OSError as exc:
                        logger.warning("orphan cleanup: failed to remove %s: %s", path, exc)
                continue
            try:
                shutil.rmtree(full_dir)
                removed_dirs.append(os.path.abspath(full_dir))
            except OSError as exc:
                logger.warning("orphan cleanup: failed to rmtree %s: %s", full_dir, exc)

    return {"removed_files": removed_files, "removed_dirs": removed_dirs}


def cleanup_renamed_user_visible(
    *,
    working_dir: str,
    rename_map: dict[str, str],
) -> dict[str, list[str]]:
    """Remove user-visible files only when ownership demonstrably moved."""
    removed: list[str] = []
    warned: list[str] = []

    for old_filename, new_filename in rename_map.items():
        if not old_filename or old_filename == new_filename:
            continue
        old_path = os.path.join(working_dir, old_filename)
        if not os.path.exists(old_path):
            continue
        if is_user_modified(working_dir, old_filename):
            warned.append(old_filename)
            logger.warning(
                "orphan cleanup: leaving user-modified file %s in place (ownership moved to %s)",
                old_filename,
                new_filename,
            )
            continue

        def _remove(candidate: Path) -> None:
            if not candidate.exists():
                return
            candidate.unlink()
            removed.append(candidate.name)

        old_file = Path(old_path)
        _remove(old_file)
        if old_file.suffix == ".md":
            _remove(old_file.with_suffix(".html"))
        elif old_file.suffix == ".html":
            _remove(old_file.with_suffix(".md"))

    return {"removed": removed, "warned": warned}
