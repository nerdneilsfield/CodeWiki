from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner


def test_load_config_returns_codewiki_config_for_explicit_toml_path(tmp_path):
    from codewiki.src.codewiki_config import CodeWikiConfig
    from codewiki.src.config_loader import load_config

    config_path = tmp_path / "codewiki.toml"
    config_path.write_text(
        "[runtime]\noutput_dir='docs'\n"
        "[generation]\nmain_model='openai/gpt-4o-mini'\ncluster_model='openai/gpt-4o-mini'\n"
        "[[providers]]\nname='openai'\ntype='openai_compatible'\n"
        "model_list=['gpt-4o-mini']\napi_keys=[]\n",
        encoding="utf-8",
    )

    cfg = load_config(str(config_path), repo_path="/tmp/repo")

    assert isinstance(cfg, CodeWikiConfig)
    assert cfg.repo_path == "/tmp/repo"
    assert cfg.docs_dir == "docs"
    assert cfg.main_model == "openai/gpt-4o-mini"
    assert cfg.cluster_model == "openai/gpt-4o-mini"
    assert cfg.providers[0].name == "openai"
    assert cfg.providers[0].model_list == ["gpt-4o-mini"]


def test_generate_command_exposes_config_option():
    from codewiki.cli.commands.generate import generate_command

    option_names = {opt.name for opt in generate_command.params}
    assert "config_path" in option_names


@pytest.mark.asyncio
async def test_cli_backend_generation_consumes_generation_result(tmp_path):
    from codewiki.cli.adapters.doc_generator import CLIDocumentationGenerator
    from codewiki.src.be.pipeline import GenerationResult, ModuleSummary
    from codewiki.src.codewiki_config import CodeWikiConfig

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    output_dir = tmp_path / "docs"
    output_dir.mkdir()

    adapter = CLIDocumentationGenerator(
        repo_path=repo_dir,
        output_dir=output_dir,
        config=CodeWikiConfig(
            repo_path=str(repo_dir),
            docs_dir=str(output_dir),
            main_model="test/main",
            cluster_model="test/cluster",
            llm_base_url="http://localhost",
        ),
    )

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
        await adapter._run_backend_generation(adapter.config)

    fake_doc_generator.run.assert_awaited_once()
    assert adapter.job.statistics.total_files_analyzed == 1
    assert adapter.job.statistics.leaf_nodes == 1
    assert adapter.job.statistics.total_tokens_used == 5
    assert adapter.job.module_count == 1
    assert set(adapter.job.files_generated) == {"overview.md", "metadata.json"}
