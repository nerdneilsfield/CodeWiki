from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_validate_repository_raises_when_no_supported_languages(tmp_path):
    from codewiki.cli.utils.errors import RepositoryError
    from codewiki.cli.utils.repo_validator import validate_repository

    with patch("codewiki.cli.utils.repo_validator.detect_supported_languages", return_value=[]):
        with pytest.raises(RepositoryError, match="No supported code files found"):
            validate_repository(tmp_path)


def test_check_writable_output_rejects_existing_file(tmp_path):
    from codewiki.cli.utils.errors import RepositoryError
    from codewiki.cli.utils.repo_validator import check_writable_output

    file_path = tmp_path / "out"
    file_path.write_text("x", encoding="utf-8")

    with pytest.raises(RepositoryError, match="not a directory"):
        check_writable_output(file_path)


def test_check_writable_output_rejects_missing_parent(tmp_path):
    from codewiki.cli.utils.errors import RepositoryError
    from codewiki.cli.utils.repo_validator import check_writable_output

    with pytest.raises(RepositoryError, match="Parent directory does not exist"):
        check_writable_output(tmp_path / "missing-parent" / "docs")


def test_check_writable_output_rejects_unwritable_directory(tmp_path):
    from codewiki.cli.utils.errors import RepositoryError
    from codewiki.cli.utils.repo_validator import check_writable_output

    with patch("codewiki.cli.utils.repo_validator.os.access", return_value=False):
        with pytest.raises(RepositoryError, match="not writable"):
            check_writable_output(tmp_path)


def test_check_writable_output_rejects_unwritable_parent(tmp_path):
    from codewiki.cli.utils.errors import RepositoryError
    from codewiki.cli.utils.repo_validator import check_writable_output

    parent = tmp_path / "parent"
    parent.mkdir()

    with patch("codewiki.cli.utils.repo_validator.os.access", return_value=False):
        with pytest.raises(RepositoryError, match="parent not writable"):
            check_writable_output(parent / "docs")


def test_get_git_commit_hash_returns_empty_on_git_error(tmp_path):
    from codewiki.cli.utils.repo_validator import get_git_commit_hash

    (tmp_path / ".git").mkdir()
    with patch("git.Repo", side_effect=RuntimeError("boom")):
        assert get_git_commit_hash(tmp_path) == ""


def test_get_git_branch_returns_branch_name(tmp_path):
    from codewiki.cli.utils.repo_validator import get_git_branch

    (tmp_path / ".git").mkdir()
    mock_repo = MagicMock()
    mock_repo.active_branch.name = "feature/utils"

    with patch("git.Repo", return_value=mock_repo):
        assert get_git_branch(tmp_path) == "feature/utils"


def test_get_git_branch_returns_empty_on_git_error(tmp_path):
    from codewiki.cli.utils.repo_validator import get_git_branch

    (tmp_path / ".git").mkdir()
    with patch("git.Repo", side_effect=RuntimeError("boom")):
        assert get_git_branch(tmp_path) == ""


def test_count_code_files_counts_supported_extensions(tmp_path):
    from codewiki.cli.utils.repo_validator import count_code_files

    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.ts").write_text("", encoding="utf-8")
    (tmp_path / "c.md").write_text("", encoding="utf-8")

    assert count_code_files(tmp_path) == 2
