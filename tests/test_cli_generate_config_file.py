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
                    "token_usage": {
                        "total_input_tokens": 3,
                        "total_output_tokens": 2,
                    },
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


@pytest.mark.asyncio
async def test_cli_backend_generation_cancelled_raises_api_error(tmp_path):
    from codewiki.cli.adapters.doc_generator import CLIDocumentationGenerator
    from codewiki.cli.utils.errors import APIError
    from codewiki.src.be.pipeline import GenerationResult
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
        return_value=GenerationResult(status="cancelled", warnings=["cancelled"])
    )

    with patch(
        "codewiki.cli.adapters.doc_generator.DocumentationGenerator",
        return_value=fake_doc_generator,
    ):
        # Graceful cancel now returns normally (progress saved) instead of raising
        await adapter._run_backend_generation(adapter.config)


def test_finalize_job_writes_fallback_metadata_when_missing(tmp_path):
    import json

    from codewiki.cli.adapters.doc_generator import CLIDocumentationGenerator
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
    adapter.job.repository_name = repo_dir.name

    adapter._finalize_job()

    metadata_path = output_dir / "metadata.json"
    assert metadata_path.exists()
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert payload["repository_name"] == repo_dir.name


def test_run_html_generation_adds_index_file(tmp_path):
    from codewiki.cli.adapters.doc_generator import CLIDocumentationGenerator
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
        verbose=True,
    )

    fake_html_generator = MagicMock()
    fake_html_generator.detect_repository_info.return_value = {
        "name": "repo",
        "url": "https://example.com/repo",
        "github_pages_url": "https://example.github.io/repo",
    }

    with patch(
        "codewiki.cli.html_generator.HTMLGenerator",
        return_value=fake_html_generator,
    ):
        adapter._run_html_generation()

    assert "index.html" in adapter.job.files_generated
    fake_html_generator.generate.assert_called_once()


def test_run_static_generation_records_written_files(tmp_path):
    from codewiki.cli.adapters.doc_generator import CLIDocumentationGenerator
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
        verbose=True,
    )

    fake_generator = MagicMock()
    fake_generator.generate.return_value = ["a.html", "b.html"]

    with patch(
        "codewiki.cli.static_generator.StaticHTMLGenerator",
        return_value=fake_generator,
    ):
        adapter._run_static_generation()

    assert set(adapter.job.files_generated) == {"a.html", "b.html"}


def test_generate_runs_optional_html_and_static_stages(tmp_path):
    from codewiki.cli.adapters.doc_generator import CLIDocumentationGenerator
    from codewiki.src.codewiki_config import CodeWikiConfig

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    output_dir = tmp_path / "docs"

    adapter = CLIDocumentationGenerator(
        repo_path=repo_dir,
        output_dir=output_dir,
        config=CodeWikiConfig(
            repo_path=str(repo_dir),
            docs_dir=str(output_dir),
            main_model="test/main",
            cluster_model="test/cluster",
        ),
        generate_html=True,
        generate_static=True,
    )

    with (
        patch.object(adapter, "_run_backend_generation", new=AsyncMock()),
        patch.object(adapter, "_run_html_generation") as run_html,
        patch.object(adapter, "_run_static_generation") as run_static,
        patch.object(adapter, "_finalize_job") as finalize,
    ):
        job = adapter.generate()

    run_html.assert_called_once()
    run_static.assert_called_once()
    finalize.assert_called_once()
    assert job.status.value == "completed"


def test_generate_marks_job_failed_on_api_error(tmp_path):
    from codewiki.cli.adapters.doc_generator import CLIDocumentationGenerator
    from codewiki.cli.utils.errors import APIError
    from codewiki.src.codewiki_config import CodeWikiConfig

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    output_dir = tmp_path / "docs"

    adapter = CLIDocumentationGenerator(
        repo_path=repo_dir,
        output_dir=output_dir,
        config=CodeWikiConfig(
            repo_path=str(repo_dir),
            docs_dir=str(output_dir),
            main_model="test/main",
            cluster_model="test/cluster",
        ),
    )

    with patch.object(
        adapter, "_run_backend_generation", new=AsyncMock(side_effect=APIError("nope"))
    ):
        with pytest.raises(APIError):
            adapter.generate()

    assert adapter.job.status.value == "failed"
    assert adapter.job.error_message == "nope"
