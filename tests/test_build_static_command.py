from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner


def test_build_static_command_fails_for_missing_directory(tmp_path):
    from codewiki.cli.commands.build_static import build_static_command

    result = CliRunner().invoke(build_static_command, [str(tmp_path / "missing")])

    assert result.exit_code == 1
    assert "directory not found" in result.output.lower()


def test_build_static_command_reports_no_markdown(tmp_path):
    from codewiki.cli.commands.build_static import build_static_command

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    fake_generator = MagicMock()
    fake_generator.generate.return_value = []

    with patch(
        "codewiki.cli.static_generator.StaticHTMLGenerator",
        return_value=fake_generator,
    ):
        result = CliRunner().invoke(build_static_command, [str(docs_dir)])

    assert result.exit_code == 0
    assert "nothing generated" in result.output.lower()


def test_build_static_command_lists_generated_files(tmp_path):
    from codewiki.cli.commands.build_static import build_static_command

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    fake_generator = MagicMock()
    fake_generator.generate.return_value = ["index.html", "guide.html"]

    with patch(
        "codewiki.cli.static_generator.StaticHTMLGenerator",
        return_value=fake_generator,
    ):
        result = CliRunner().invoke(build_static_command, [str(docs_dir), "--no-repo-links"])

    assert result.exit_code == 0
    assert "index.html" in result.output
    assert "guide.html" in result.output
    fake_generator.generate.assert_called_once_with(Path(docs_dir).resolve(), hide_repo_links=True)
