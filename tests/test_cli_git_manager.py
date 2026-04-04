from pathlib import Path
from unittest.mock import MagicMock, patch

import git
import pytest


def test_git_manager_rejects_non_repo(tmp_path):
    from codewiki.cli.git_manager import GitManager
    from codewiki.cli.utils.errors import RepositoryError

    with patch("codewiki.cli.git_manager.git.Repo", side_effect=git.InvalidGitRepositoryError()):
        with pytest.raises(RepositoryError, match="Not a git repository"):
            GitManager(tmp_path)


def test_check_clean_working_directory_reports_changed_and_untracked(tmp_path):
    from codewiki.cli.git_manager import GitManager

    repo = MagicMock()
    repo.is_dirty.return_value = True
    repo.index.diff.return_value = [
        MagicMock(a_path="a.py"),
        MagicMock(a_path="b.py"),
        MagicMock(a_path="c.py"),
        MagicMock(a_path="d.py"),
    ]
    repo.untracked_files = ["new.txt", "other.txt"]

    with patch("codewiki.cli.git_manager.git.Repo", return_value=repo):
        manager = GitManager(tmp_path)

    is_clean, message = manager.check_clean_working_directory()

    assert not is_clean
    assert "Modified: a.py, b.py, c.py" in message
    assert "... and 1 more" in message
    assert "Untracked: new.txt, other.txt" in message


def test_create_documentation_branch_adds_counter_on_collision(tmp_path):
    from codewiki.cli.git_manager import GitManager

    repo = MagicMock()
    repo.is_dirty.return_value = False
    existing_branch = MagicMock()
    existing_branch.name = "docs/codewiki-20260404-120000"
    repo.branches = [existing_branch]
    branch = MagicMock()
    repo.create_head.return_value = branch

    fake_datetime = MagicMock()
    fake_datetime.now.return_value.strftime.return_value = "20260404-120000"

    with (
        patch("codewiki.cli.git_manager.git.Repo", return_value=repo),
        patch("codewiki.cli.git_manager.datetime", fake_datetime),
    ):
        manager = GitManager(tmp_path)
        name = manager.create_documentation_branch()

    assert name == "docs/codewiki-20260404-120000-1"
    repo.create_head.assert_called_once_with(name)
    branch.checkout.assert_called_once()


def test_commit_documentation_wraps_git_errors(tmp_path):
    from codewiki.cli.git_manager import GitManager
    from codewiki.cli.utils.errors import RepositoryError

    repo = MagicMock()
    repo.index.add.side_effect = git.GitCommandError("add", 1)

    with patch("codewiki.cli.git_manager.git.Repo", return_value=repo):
        manager = GitManager(tmp_path)

    with pytest.raises(RepositoryError, match="Failed to commit documentation"):
        manager.commit_documentation(Path("docs"))


def test_get_github_pr_url_converts_ssh_remote(tmp_path):
    from codewiki.cli.git_manager import GitManager

    repo = MagicMock()
    remote = MagicMock(url="git@github.com:owner/repo.git")
    repo.remote.return_value = remote

    with patch("codewiki.cli.git_manager.git.Repo", return_value=repo):
        manager = GitManager(tmp_path)

    assert (
        manager.get_github_pr_url("docs/codewiki-branch")
        == "https://github.com/owner/repo/compare/docs/codewiki-branch"
    )
