from unittest.mock import MagicMock, patch


def test_is_valid_github_url_accepts_standard_and_www_hosts():
    from codewiki.src.fe.github_processor import GitHubRepoProcessor

    assert GitHubRepoProcessor.is_valid_github_url("https://github.com/openai/codex") is True
    assert GitHubRepoProcessor.is_valid_github_url("https://www.github.com/openai/codex") is True
    assert GitHubRepoProcessor.is_valid_github_url("https://example.com/openai/codex") is False
    assert GitHubRepoProcessor.is_valid_github_url("https://github.com/openai") is False


def test_get_repo_info_strips_git_suffix():
    from codewiki.src.fe.github_processor import GitHubRepoProcessor

    info = GitHubRepoProcessor.get_repo_info("https://github.com/openai/codex.git")

    assert info["owner"] == "openai"
    assert info["repo"] == "codex"
    assert info["full_name"] == "openai/codex"
    assert info["clone_url"] == "https://github.com/openai/codex.git"


def test_clone_repository_uses_shallow_clone_without_commit_id(tmp_path):
    from codewiki.src.fe.github_processor import GitHubRepoProcessor

    mock_result = MagicMock(returncode=0)

    with patch("subprocess.run", return_value=mock_result) as mock_run:
        assert (
            GitHubRepoProcessor.clone_repository(
                "https://github.com/openai/codex.git",
                str(tmp_path / "repo"),
            )
            is True
        )

    clone_args = mock_run.call_args.args[0]
    assert clone_args[:4] == ["git", "clone", "--depth", "1"]


def test_clone_repository_returns_false_when_checkout_fails(tmp_path):
    from codewiki.src.fe.github_processor import GitHubRepoProcessor

    clone_ok = MagicMock(returncode=0, stderr="")
    checkout_fail = MagicMock(returncode=1, stderr="bad revision")

    with patch("subprocess.run", side_effect=[clone_ok, checkout_fail]):
        assert (
            GitHubRepoProcessor.clone_repository(
                "https://github.com/openai/codex.git",
                str(tmp_path / "repo"),
                "abcd1234",
            )
            is False
        )
