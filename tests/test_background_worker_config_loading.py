from pathlib import Path
from unittest.mock import MagicMock, patch


def test_background_worker_build_runtime_config_uses_app_config(tmp_path):
    from codewiki.src.fe.background_worker import BackgroundWorker

    config_path = tmp_path / "codewiki.toml"
    config_path.write_text("[runtime]\noutput_dir='docs'\n[generation]\nmain_model='openai/gpt-4o-mini'\ncluster_model='openai/gpt-4o-mini'\n[[providers]]\nname='openai'\ntype='openai_compatible'\nmodel_list=['gpt-4o-mini']\napi_keys=[]\n", encoding="utf-8")

    app_config = MagicMock()
    sentinel_runtime = object()
    app_config.to_runtime_config.return_value = sentinel_runtime

    worker = BackgroundWorker(cache_manager=MagicMock(), config_path=str(config_path))

    # Pass pre-loaded app_config — load_app_config should NOT be called again.
    with patch("codewiki.src.fe.background_worker.load_app_config") as mock_load:
        result = worker._build_runtime_config(
            temp_repo_dir="/tmp/repo", docs_dir="/tmp/docs", app_config=app_config
        )

    assert result is sentinel_runtime
    mock_load.assert_not_called()


def test_background_worker_build_runtime_config_loads_when_no_app_config(tmp_path):
    """When app_config is not passed, _build_runtime_config falls back to loading it."""
    from codewiki.src.fe.background_worker import BackgroundWorker

    config_path = tmp_path / "codewiki.toml"
    config_path.write_text("[runtime]\noutput_dir='docs'\n[generation]\nmain_model='openai/gpt-4o-mini'\ncluster_model='openai/gpt-4o-mini'\n[[providers]]\nname='openai'\ntype='openai_compatible'\nmodel_list=['gpt-4o-mini']\napi_keys=[]\n", encoding="utf-8")

    app_config = MagicMock()
    sentinel_runtime = object()
    app_config.to_runtime_config.return_value = sentinel_runtime

    worker = BackgroundWorker(cache_manager=MagicMock(), config_path=str(config_path))

    with patch("codewiki.src.fe.background_worker.load_app_config", return_value=app_config) as mock_load:
        result = worker._build_runtime_config(temp_repo_dir="/tmp/repo", docs_dir="/tmp/docs")

    assert result is sentinel_runtime
    mock_load.assert_called_once_with(Path(config_path))


def test_process_job_sets_main_model_from_toml(tmp_path):
    """job.main_model must reflect the model declared in the TOML, not the global constant."""
    from codewiki.src.fe.background_worker import BackgroundWorker
    from codewiki.src.fe.models import JobStatus
    from datetime import datetime

    config_path = tmp_path / "codewiki.toml"
    config_path.write_text(
        "[runtime]\noutput_dir='docs'\n"
        "[generation]\nmain_model='openai/gpt-4o-mini'\ncluster_model='openai/gpt-4o-mini'\n"
        "[[providers]]\nname='openai'\ntype='openai_compatible'\nmodel_list=['gpt-4o-mini']\napi_keys=[]\n",
        encoding="utf-8",
    )

    app_config = MagicMock()
    app_config.generation.main_model = "openai/gpt-4o-mini"
    runtime_config = MagicMock(docs_dir=str(tmp_path / "docs"))
    app_config.to_runtime_config.return_value = runtime_config

    cache_manager = MagicMock()
    cache_manager.get_cached_docs.return_value = None  # force full generation path

    worker = BackgroundWorker(cache_manager=cache_manager, config_path=str(config_path))

    job = JobStatus(
        job_id="test-job",
        repo_url="https://github.com/owner/repo",
        status="queued",
        created_at=datetime.now(),
    )
    worker.job_status["test-job"] = job

    with patch("codewiki.src.fe.background_worker.load_app_config", return_value=app_config), \
         patch("codewiki.src.fe.background_worker.GitHubRepoProcessor") as mock_gh, \
         patch("codewiki.src.fe.background_worker.DocumentationGenerator") as mock_gen_cls:
        mock_gh.get_repo_info.return_value = {"full_name": "owner/repo", "clone_url": "https://github.com/owner/repo.git"}
        mock_gh.clone_repository.return_value = True
        mock_gen = mock_gen_cls.return_value

        fake_loop = MagicMock()
        fake_loop.run_until_complete = MagicMock(return_value=None)
        with patch("asyncio.new_event_loop", return_value=fake_loop), \
             patch("asyncio.set_event_loop"):
            worker._process_job("test-job")

    assert job.main_model == "openai/gpt-4o-mini", (
        f"Expected 'openai/gpt-4o-mini' but got '{job.main_model}'. "
        "main_model must come from the TOML config, not the MAIN_MODEL global."
    )
