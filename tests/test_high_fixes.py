import inspect
from unittest.mock import MagicMock, patch

import pytest


class TestH6NoDeprecatedEventLoop:
    def test_scheduler_uses_get_running_loop(self):
        from codewiki.src.be import documentation_scheduler

        source = inspect.getsource(documentation_scheduler)
        assert "get_event_loop" not in source
        assert "get_running_loop" in source


class TestH7NoForcedDebugLevel:
    def test_ast_parser_does_not_force_debug(self):
        from codewiki.src.be.dependency_analyzer import ast_parser

        source = inspect.getsource(ast_parser)
        assert "setLevel(logging.DEBUG)" not in source


class TestH8CommitIdCaseInsensitive:
    def test_uppercase_sha_is_lowered_before_checkout(self):
        from codewiki.src.fe.github_processor import GitHubRepoProcessor

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            GitHubRepoProcessor.clone_repository(
                "https://github.com/user/repo", "/tmp/target", "ABCD1234"
            )

        for call in mock_run.call_args_list:
            args = call[0][0] if call[0] else call[1].get("args", [])
            if "checkout" in args:
                assert "abcd1234" in args
                assert "ABCD1234" not in args
                break
        else:
            pytest.fail("checkout call not observed")

    def test_invalid_commit_id_raises(self):
        from codewiki.src.fe.github_processor import GitHubRepoProcessor

        with pytest.raises(ValueError, match="[Ii]nvalid"):
            GitHubRepoProcessor.clone_repository(
                "https://github.com/user/repo", "/tmp/target", "--upload-pack=evil"
            )
