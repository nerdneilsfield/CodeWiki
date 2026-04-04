from pathlib import Path
from unittest.mock import patch


def test_compute_github_pages_url_for_github_repo():
    from codewiki.cli.utils.instructions import compute_github_pages_url

    assert (
        compute_github_pages_url("https://github.com/openai/codex", "codex")
        == "https://openai.github.io/codex/"
    )


def test_compute_github_pages_url_falls_back_for_non_github_repo():
    from codewiki.cli.utils.instructions import compute_github_pages_url

    assert (
        compute_github_pages_url("https://gitlab.com/openai/codex", "codex")
        == "https://YOUR_USERNAME.github.io/codex/"
    )


def test_get_pr_creation_url_strips_git_suffix():
    from codewiki.cli.utils.instructions import get_pr_creation_url

    assert (
        get_pr_creation_url("https://github.com/openai/codex.git", "docs-branch")
        == "https://github.com/openai/codex/compare/docs-branch"
    )


def test_display_post_generation_instructions_emits_summary_lines(tmp_path):
    from codewiki.cli.utils import instructions as mod

    lines = []

    with patch.object(mod.logger, "info", side_effect=lines.append):
        mod.display_post_generation_instructions(
            output_dir=tmp_path,
            repo_name="codex",
            repo_url="https://github.com/openai/codex",
            branch_name="docs-branch",
            github_pages=True,
            files_generated=["overview.md", "guide.md"],
            statistics={
                "module_count": 3,
                "total_files_analyzed": 12,
                "generation_time": 125,
            },
        )

    assert "Documentation generated successfully" in lines
    assert "Generated files:" in lines
    assert any("Total modules:" in line for line in lines)
    assert any("compare/docs-branch" in line for line in lines)
    assert any("openai.github.io/codex/" in line for line in lines)


def test_display_generation_summary_logs_success_and_failure(tmp_path):
    from codewiki.cli.utils import instructions as mod

    with patch.object(mod.logger, "info") as mock_info:
        mod.display_generation_summary(True, output_dir=tmp_path)

    with patch.object(mod.logger, "error") as mock_error:
        mod.display_generation_summary(False, error_message="line1\nline2")

    assert mock_info.call_count == 2
    assert mock_error.call_count == 3
