from pathlib import Path
from unittest.mock import patch

import pytest


def test_ensure_directory_wraps_permission_error(tmp_path):
    from codewiki.cli.utils.errors import FileSystemError
    from codewiki.cli.utils.fs import ensure_directory

    with patch.object(Path, "mkdir", side_effect=PermissionError):
        with pytest.raises(FileSystemError, match="Permission denied"):
            ensure_directory(tmp_path / "blocked")


def test_check_writable_uses_parent_for_missing_path(tmp_path):
    from codewiki.cli.utils.fs import check_writable

    parent = tmp_path / "new-dir"
    parent.mkdir()
    missing = parent / "out.txt"
    with patch("codewiki.cli.utils.fs.os.access", return_value=True):
        assert check_writable(missing) is True


def test_safe_read_wraps_file_not_found(tmp_path):
    from codewiki.cli.utils.errors import FileSystemError
    from codewiki.cli.utils.fs import safe_read

    with pytest.raises(FileSystemError, match="File not found"):
        safe_read(tmp_path / "missing.txt")


def test_find_files_respects_extensions_and_non_recursive(tmp_path):
    from codewiki.cli.utils.fs import find_files

    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.txt").write_text("", encoding="utf-8")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "c.py").write_text("", encoding="utf-8")

    found = find_files(tmp_path, extensions=[".py"], recursive=False)

    assert found == [tmp_path / "a.py"]


def test_cleanup_directory_keeps_hidden_entries(tmp_path):
    from codewiki.cli.utils.fs import cleanup_directory

    (tmp_path / "visible.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".hidden.txt").write_text("x", encoding="utf-8")
    visible_dir = tmp_path / "visible_dir"
    visible_dir.mkdir()
    (visible_dir / "child.txt").write_text("x", encoding="utf-8")

    cleanup_directory(tmp_path, keep_hidden=True)

    assert not (tmp_path / "visible.txt").exists()
    assert not visible_dir.exists()
    assert (tmp_path / ".hidden.txt").exists()
