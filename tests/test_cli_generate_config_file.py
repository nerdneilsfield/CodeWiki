from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from codewiki.cli.utils.errors import ConfigurationError


def test_load_generation_app_config_uses_explicit_toml_path(tmp_path):
    from codewiki.cli.commands import generate as mod

    config_path = tmp_path / "codewiki.toml"
    config_path.write_text(
        "[runtime]\noutput_dir='docs'\n[generation]\nmain_model='openai/gpt-4o-mini'\ncluster_model='openai/gpt-4o-mini'\n[[providers]]\nname='openai'\ntype='openai_compatible'\nmodel_list=['gpt-4o-mini']\napi_keys=[]\n",
        encoding="utf-8",
    )
    sentinel = object()

    with patch.object(mod, "load_app_config", return_value=sentinel) as mock_load:
        result = mod._load_generation_app_config(str(config_path))

    assert result is sentinel
    mock_load.assert_called_once_with(Path(config_path))


def test_load_generation_app_config_falls_back_to_legacy_manager_when_no_config_path():
    from codewiki.cli.commands import generate as mod

    legacy_manager = MagicMock()
    legacy_manager.load.return_value = True
    legacy_manager.is_configured.return_value = True
    legacy_manager.get_config.return_value = MagicMock()
    legacy_manager.get_api_key.return_value = "sk-test"
    sentinel = object()

    with (
        patch.object(mod, "ConfigManager", return_value=legacy_manager),
        patch.object(mod, "_legacy_config_to_app_config", return_value=sentinel) as mock_convert,
    ):
        result = mod._load_generation_app_config(None)

    assert result is sentinel
    mock_convert.assert_called_once_with(legacy_manager.get_config.return_value, "sk-test")


def test_load_generation_app_config_raises_when_no_config_and_no_legacy_config():
    from codewiki.cli.commands import generate as mod

    legacy_manager = MagicMock()
    legacy_manager.load.return_value = False

    with patch.object(mod, "ConfigManager", return_value=legacy_manager):
        with pytest.raises(ConfigurationError, match="config init"):
            mod._load_generation_app_config(None)


def test_generate_command_exposes_config_option():
    from codewiki.cli.commands.generate import generate_command

    option_names = {opt.name for opt in generate_command.params}
    assert "config_path" in option_names


def test_generate_command_uses_new_config_loading_path(tmp_path):
    from codewiki.cli.commands import generate as mod

    runner = CliRunner()
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    config_path = tmp_path / "codewiki.toml"
    config_path.write_text(
        "[runtime]\noutput_dir='docs'\n"
        "[generation]\nmain_model='openai/gpt-4o-mini'\ncluster_model='openai/gpt-4o-mini'\n"
        "[[providers]]\nname='openai'\ntype='openai_compatible'\nmodel_list=['gpt-4o-mini']\napi_keys=[]\n",
        encoding="utf-8",
    )

    app_config = MagicMock()
    runtime_config = MagicMock(
        main_model="openai/gpt-4o-mini",
        max_tokens=32768,
        max_token_per_module=36369,
        max_token_per_leaf_module=16000,
        max_depth=2,
        max_concurrent=3,
    )
    app_config.to_runtime_config.return_value = runtime_config

    fake_job = MagicMock(
        files_generated=[],
        module_count=0,
        statistics=MagicMock(total_files_analyzed=0, total_tokens_used=0),
    )

    with (
        patch.object(mod, "_load_generation_app_config", return_value=app_config) as mock_load,
        patch.object(mod, "validate_repository", return_value=(repo_dir, {})),
        patch.object(mod, "check_writable_output"),
        patch.object(mod, "is_git_repository", return_value=False),
        patch.object(mod, "CLIDocumentationGenerator") as mock_generator_cls,
        patch.object(mod, "display_post_generation_instructions"),
    ):
        mock_generator = mock_generator_cls.return_value
        mock_generator.generate.return_value = fake_job

        result = runner.invoke(
            mod.generate_command,
            ["--config", str(config_path), "--output", str(tmp_path / "docs")],
            catch_exceptions=False,
        )

    assert result.exit_code == 0
    mock_load.assert_called_once_with(str(config_path))


@pytest.mark.asyncio
async def test_cli_backend_generation_consumes_generation_result(tmp_path):
    from codewiki.cli.adapters.doc_generator import CLIDocumentationGenerator
    from codewiki.src.be.pipeline import GenerationResult, ModuleSummary

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    output_dir = tmp_path / "docs"
    output_dir.mkdir()

    adapter = CLIDocumentationGenerator(
        repo_path=repo_dir,
        output_dir=output_dir,
        config={
            "main_model": "test/main",
            "cluster_model": "test/cluster",
            "base_url": "http://localhost",
            "api_key": "x",
        },
    )
    backend_config = adapter._build_backend_config()

    fake_doc_generator = MagicMock()
    fake_doc_generator.run = AsyncMock(
        return_value=GenerationResult(
            status="complete",
            warnings=[],
            module_summary=ModuleSummary(completed=["module:comp"], total=1),
            metadata={
                "statistics": {
                    "total_components": 1,
                    "leaf_nodes": 1,
                    "token_usage": {"total_input": 3, "total_output": 2},
                }
            },
        )
    )

    with (
        patch(
            "codewiki.cli.adapters.doc_generator.DocumentationGenerator",
            return_value=fake_doc_generator,
        ),
        patch("codewiki.src.utils.file_manager.load_json", return_value={"Root": {"children": {}}}),
        patch("os.listdir", return_value=["overview.md", "metadata.json"]),
    ):
        await adapter._run_backend_generation(backend_config)

    fake_doc_generator.run.assert_awaited_once()
    assert adapter.job.statistics.total_files_analyzed == 1
    assert adapter.job.statistics.leaf_nodes == 1
    assert adapter.job.statistics.total_tokens_used == 5
    assert adapter.job.module_count == 1
    assert set(adapter.job.files_generated) == {"overview.md", "metadata.json"}
