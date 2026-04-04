from unittest.mock import MagicMock, patch

import pytest


def test_sanitize_github_url_handles_owner_repo_and_strips_suffix():
    from codewiki.src.be.dependency_analyzer.analysis.cloning import sanitize_github_url

    assert sanitize_github_url("openai/codex.git") == "https://github.com/openai/codex"
    assert (
        sanitize_github_url("https://www.github.com/openai/codex/tree/main")
        == "https://github.com/openai/codex"
    )


def test_parse_github_url_extracts_owner_and_name():
    from codewiki.src.be.dependency_analyzer.analysis.cloning import parse_github_url

    info = parse_github_url("https://github.com/openai/codex.git")
    assert info["owner"] == "openai"
    assert info["name"] == "codex"
    assert info["full_name"] == "openai/codex"


def test_clone_repository_raises_when_git_missing(monkeypatch):
    from codewiki.src.be.dependency_analyzer.analysis import cloning as mod

    monkeypatch.setattr(mod, "GIT_EXECUTABLE_PATH", None)
    with pytest.raises(RuntimeError, match="Git executable not found"):
        mod.clone_repository("https://github.com/openai/codex")


def test_clone_repository_wraps_called_process_error(monkeypatch):
    from codewiki.src.be.dependency_analyzer.analysis import cloning as mod

    monkeypatch.setattr(mod, "GIT_EXECUTABLE_PATH", "/usr/bin/git")
    error = __import__("subprocess").CalledProcessError(
        1, ["git"], stderr="fatal: repository not found"
    )

    with patch("subprocess.run", side_effect=error), patch.object(mod, "cleanup_repository_safe"):
        with pytest.raises(RuntimeError, match="Failed to clone repository"):
            mod.clone_repository("https://github.com/openai/codex")


def test_cleanup_repository_safe_returns_false_on_repeated_permission_error(tmp_path, monkeypatch):
    from codewiki.src.be.dependency_analyzer.analysis import cloning as mod

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    monkeypatch.setattr(mod.os.path, "exists", lambda _p: True)
    with patch("shutil.rmtree", side_effect=PermissionError("denied")):
        assert mod.cleanup_repository_safe(str(repo_dir)) is False
