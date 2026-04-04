from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


def test_resolve_generation_config_path_prefers_explicit(tmp_path):
    from codewiki.cli.commands.generate import _resolve_generation_config_path

    config_file = tmp_path / "codewiki.toml"
    config_file.write_text("x", encoding="utf-8")

    assert _resolve_generation_config_path(str(config_file)) == config_file


def test_resolve_generation_config_path_raises_when_missing(tmp_path):
    from codewiki.cli.commands.generate import _resolve_generation_config_path
    from codewiki.cli.utils.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        _resolve_generation_config_path(str(tmp_path / "missing.toml"))


def test_normalize_model_override_uses_single_provider_prefix():
    from codewiki.cli.commands.generate import _normalize_model_override
    from codewiki.src.codewiki_config import CodeWikiConfig, ProviderConfig

    config = CodeWikiConfig(
        repo_path="/tmp/repo",
        docs_dir="docs",
        main_model="openai/gpt-4o-mini",
        cluster_model="openai/gpt-4o-mini",
        providers=[ProviderConfig(name="openai", type="openai_compatible", model_list=["gpt-4o"])],
    )

    assert _normalize_model_override(config, "gpt-4o") == "openai/gpt-4o"


def test_normalize_model_override_rejects_ambiguous_short_name():
    from codewiki.cli.commands.generate import _normalize_model_override
    from codewiki.src.codewiki_config import CodeWikiConfig, ProviderConfig

    config = CodeWikiConfig(
        repo_path="/tmp/repo",
        docs_dir="docs",
        main_model="openai/gpt-4o-mini",
        cluster_model="openai/gpt-4o-mini",
        providers=[
            ProviderConfig(name="openai", type="openai_compatible", model_list=["gpt-4o"]),
            ProviderConfig(name="claude", type="claude", model_list=["sonnet"]),
        ],
    )

    with pytest.raises(ValueError, match="Ambiguous model ref"):
        _normalize_model_override(config, "gpt-4o")


def test_build_runtime_overrides_merges_agent_instructions(tmp_path):
    from codewiki.cli.commands.generate import _build_runtime_overrides
    from codewiki.src.codewiki_config import CodeWikiConfig

    config = CodeWikiConfig(
        repo_path="/tmp/repo",
        docs_dir="docs",
        main_model="openai/gpt-4o-mini",
        cluster_model="openai/gpt-4o-mini",
        providers=[],
        agent_instructions={"focus_modules": ["src/core"]},
    )

    overrides = _build_runtime_overrides(
        output_dir=tmp_path / "docs",
        runtime_instructions={"custom_instructions": "extra"},
        max_tokens=None,
        max_token_per_module=None,
        max_token_per_leaf_module=None,
        max_depth=None,
        max_concurrent=None,
        max_retries=None,
        language="ZH",
        main_model=None,
        cluster_model=None,
        long_context_model=None,
        long_context_threshold=None,
        base_config=config,
    )

    assert overrides.output_language == "zh"
    assert overrides.agent_instructions == {
        "focus_modules": ["src/core"],
        "custom_instructions": "extra",
    }


def test_generate_command_cancels_when_user_declines_overwrite(tmp_path, monkeypatch):
    from codewiki.cli.commands.generate import generate_command

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    docs_dir = repo_dir / "docs"
    docs_dir.mkdir()
    (docs_dir / "existing.md").write_text("x", encoding="utf-8")
    config_file = repo_dir / "config.toml"
    config_file.write_text("x", encoding="utf-8")

    runner = CliRunner()
    with (
        monkeypatch.context() as m,
        patch("codewiki.cli.commands.generate.validate_repository", return_value=(repo_dir, [])),
        patch("codewiki.cli.commands.generate.load_config", return_value=MagicMock(providers=[])),
        patch("codewiki.cli.commands.generate.click.confirm", return_value=False),
    ):
        m.chdir(repo_dir)
        result = runner.invoke(generate_command, ["--config", str(config_file)])

    assert result.exit_code == 0
    assert "cancelled by user" in result.output.lower()


def test_generate_command_creates_branch_when_requested(tmp_path, monkeypatch):
    from codewiki.cli.commands.generate import generate_command
    from codewiki.src.codewiki_config import CodeWikiConfig

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config_file = repo_dir / "config.toml"
    config_file.write_text("x", encoding="utf-8")

    config = CodeWikiConfig(
        repo_path=str(repo_dir),
        docs_dir="docs",
        main_model="openai/gpt-4o-mini",
        cluster_model="openai/gpt-4o-mini",
        providers=[],
    )
    fake_job = MagicMock(
        files_generated=["overview.md"],
        module_count=1,
        statistics=MagicMock(total_files_analyzed=2, total_tokens_used=3),
    )
    fake_generator = MagicMock()
    fake_generator.generate.return_value = fake_job
    fake_git_manager = MagicMock()
    fake_git_manager.check_clean_working_directory.return_value = (True, "clean")
    fake_git_manager.create_documentation_branch.return_value = "docs/codewiki-20260404"

    runner = CliRunner()
    with (
        monkeypatch.context() as m,
        patch("codewiki.cli.commands.generate.validate_repository", return_value=(repo_dir, [])),
        patch("codewiki.cli.commands.generate.load_config", side_effect=[config, config]),
        patch("codewiki.cli.commands.generate.validate_llm_credentials"),
        patch("codewiki.cli.commands.generate.is_git_repository", return_value=True),
        patch("codewiki.cli.commands.generate.check_writable_output"),
        patch(
            "codewiki.cli.commands.generate.CLIDocumentationGenerator", return_value=fake_generator
        ),
        patch("codewiki.cli.commands.generate.get_git_commit_hash", return_value="abc123"),
        patch("codewiki.cli.commands.generate.get_git_branch", return_value="main"),
        patch("codewiki.cli.commands.generate.display_post_generation_instructions"),
        patch("codewiki.cli.git_manager.GitManager", return_value=fake_git_manager),
    ):
        m.chdir(repo_dir)
        result = runner.invoke(generate_command, ["--config", str(config_file), "--create-branch"])

    assert result.exit_code == 0
    fake_git_manager.create_documentation_branch.assert_called_once()
    fake_generator.generate.assert_called_once()


def test_generate_command_exits_with_api_error(tmp_path, monkeypatch):
    from codewiki.cli.commands.generate import generate_command
    from codewiki.cli.utils.errors import EXIT_API_ERROR, APIError
    from codewiki.src.codewiki_config import CodeWikiConfig

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config_file = repo_dir / "config.toml"
    config_file.write_text("x", encoding="utf-8")

    config = CodeWikiConfig(
        repo_path=str(repo_dir),
        docs_dir="docs",
        main_model="openai/gpt-4o-mini",
        cluster_model="openai/gpt-4o-mini",
        providers=[],
    )
    fake_generator = MagicMock()
    fake_generator.generate.side_effect = APIError("backend failed")

    runner = CliRunner()
    with (
        monkeypatch.context() as m,
        patch("codewiki.cli.commands.generate.validate_repository", return_value=(repo_dir, [])),
        patch("codewiki.cli.commands.generate.load_config", side_effect=[config, config]),
        patch("codewiki.cli.commands.generate.validate_llm_credentials"),
        patch("codewiki.cli.commands.generate.is_git_repository", return_value=False),
        patch("codewiki.cli.commands.generate.check_writable_output"),
        patch(
            "codewiki.cli.commands.generate.CLIDocumentationGenerator", return_value=fake_generator
        ),
    ):
        m.chdir(repo_dir)
        result = runner.invoke(generate_command, ["--config", str(config_file)])

    assert result.exit_code == EXIT_API_ERROR
    assert "backend failed" in result.output.lower()
