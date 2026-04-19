from __future__ import annotations

import os
import time

import pytest

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.be.orphan_cleanup import (
    cleanup_internal_artifacts,
    cleanup_renamed_user_visible,
    is_user_modified,
    update_mtime_stamps,
)
from codewiki.src.config import MODULE_PARTS_DIR, REFINEMENT_DIR


@pytest.fixture
def cache_dir(tmp_path):
    cache_path = tmp_path / ".codewiki"
    cache_path.mkdir()
    return str(cache_path)


def test_cleanup_internal_artifacts_removes_orphans(tmp_path, cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)

    refinement_root = tmp_path / ".codewiki" / REFINEMENT_DIR
    refinement_root.mkdir(parents=True, exist_ok=True)
    orphan_refinement = refinement_root / "ghost.json"
    orphan_refinement.write_text("{}", encoding="utf-8")

    owned_refinement = refinement_root / "auth.json"
    owned_refinement.write_text("{}", encoding="utf-8")
    cache.plan_task("refinement:auth", output_file=str(owned_refinement))
    cache.mark_done("refinement:auth", input_hash="x", output_path=str(owned_refinement), model="m")

    parts_root = tmp_path / ".codewiki" / MODULE_PARTS_DIR
    (parts_root / "ghost_module").mkdir(parents=True, exist_ok=True)
    orphan_part = parts_root / "ghost_module" / "opening.md"
    orphan_part.write_text("ghost", encoding="utf-8")

    (parts_root / "auth").mkdir(parents=True, exist_ok=True)
    owned_part = parts_root / "auth" / "opening.md"
    owned_part.write_text("auth opening", encoding="utf-8")
    cache.plan_task("module:auth:segment:opening", output_file=str(owned_part))
    cache.mark_done(
        "module:auth:segment:opening",
        input_hash="x",
        output_path=str(owned_part),
        model="m",
    )

    result = cleanup_internal_artifacts(cache_dir, cache)

    assert not orphan_refinement.exists()
    assert owned_refinement.exists()
    assert not orphan_part.exists()
    assert owned_part.exists()
    assert result["removed_files"]
    assert result["removed_dirs"]


def test_cleanup_renamed_user_visible_deletes_old_and_html(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    old_file = docs / "auth_old.md"
    new_file = docs / "auth_new.md"
    old_file.write_text("old content", encoding="utf-8")
    new_file.write_text("new content", encoding="utf-8")
    old_html = docs / "auth_old.html"
    old_html.write_text("<html>old</html>", encoding="utf-8")
    update_mtime_stamps(str(docs), ["auth_old.md", "auth_old.html"])

    result = cleanup_renamed_user_visible(
        working_dir=str(docs),
        rename_map={"auth_old.md": "auth_new.md"},
    )

    assert "auth_old.md" in result["removed"]
    assert "auth_old.html" in result["removed"]
    assert not old_file.exists()
    assert not old_html.exists()
    assert new_file.exists()


def test_cleanup_renamed_user_visible_warns_on_user_modified_file(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    old_file = docs / "auth_old.md"
    old_file.write_text("old content", encoding="utf-8")
    update_mtime_stamps(str(docs), ["auth_old.md"])
    time.sleep(1.1)
    old_file.write_text("user edit", encoding="utf-8")

    result = cleanup_renamed_user_visible(
        working_dir=str(docs),
        rename_map={"auth_old.md": "auth_new.md"},
    )

    assert old_file.exists()
    assert "auth_old.md" in result["warned"]
    assert is_user_modified(str(docs), "auth_old.md") is True


def test_cleanup_renamed_user_visible_leaves_untracked_file_alone(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    untracked = docs / "user_notes.md"
    untracked.write_text("hand-written notes", encoding="utf-8")

    result = cleanup_renamed_user_visible(working_dir=str(docs), rename_map={})

    assert untracked.exists()
    assert result["removed"] == []
