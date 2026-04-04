from pathlib import Path
from unittest.mock import MagicMock, patch

from codewiki.src.be.pipeline import GenerationResult
from codewiki.src.codewiki_config import CodeWikiConfig


def test_background_worker_load_runtime_config_uses_toml(tmp_path):
    from codewiki.src.fe.background_worker import BackgroundWorker

    config_path = tmp_path / "codewiki.toml"
    config_path.write_text(
        "[runtime]\noutput_dir='docs'\n[generation]\nmain_model='openai/gpt-4o-mini'\ncluster_model='openai/gpt-4o-mini'\n[[providers]]\nname='openai'\ntype='openai_compatible'\nmodel_list=['gpt-4o-mini']\napi_keys=[]\n",
        encoding="utf-8",
    )

    worker = BackgroundWorker(cache_manager=MagicMock(), config_path=str(config_path))

    with patch(
        "codewiki.src.fe.background_worker.load_config",
        return_value=CodeWikiConfig(
            repo_path="/tmp/repo",
            docs_dir="/tmp/docs",
            main_model="openai/gpt-4o-mini",
            cluster_model="openai/gpt-4o-mini",
        ),
    ) as mock_load:
        result = worker._load_runtime_config(repo_path="/tmp/repo", docs_dir="/tmp/docs")

    assert isinstance(result, CodeWikiConfig)
    mock_load.assert_called_once()


def test_background_worker_load_runtime_config_raises_without_config_path(tmp_path):
    from codewiki.src.fe.background_worker import BackgroundWorker

    worker = BackgroundWorker(cache_manager=MagicMock(), config_path=None)

    try:
        worker._load_runtime_config(repo_path="/tmp/repo", docs_dir="/tmp/docs")
    except RuntimeError as exc:
        assert "config_path" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError when config_path is missing")


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

    runtime_config = CodeWikiConfig(
        repo_path=str(tmp_path / "repo"),
        docs_dir=str(tmp_path / "docs"),
        main_model="openai/gpt-4o-mini",
        cluster_model="openai/gpt-4o-mini",
    )

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

    with (
        patch("codewiki.src.fe.background_worker.load_config", return_value=runtime_config),
        patch("codewiki.src.fe.background_worker.GitHubRepoProcessor") as mock_gh,
        patch("codewiki.src.fe.background_worker.DocumentationGenerator") as mock_gen_cls,
    ):
        mock_gh.get_repo_info.return_value = {
            "full_name": "owner/repo",
            "clone_url": "https://github.com/owner/repo.git",
        }
        mock_gh.clone_repository.return_value = True
        mock_gen = mock_gen_cls.return_value
        mock_gen.run.return_value = GenerationResult(status="complete")

        fake_loop = MagicMock()
        fake_loop.run_until_complete = MagicMock(return_value=GenerationResult(status="complete"))
        with (
            patch("asyncio.new_event_loop", return_value=fake_loop),
            patch("asyncio.set_event_loop"),
        ):
            worker._process_job("test-job")

    assert job.main_model == "openai/gpt-4o-mini", (
        f"Expected 'openai/gpt-4o-mini' but got '{job.main_model}'. "
        "main_model must come from the TOML config, not the MAIN_MODEL global."
    )


def test_process_job_marks_cancelled_when_generator_returns_cancelled(tmp_path):
    from datetime import datetime

    from codewiki.src.be.pipeline import GenerationResult
    from codewiki.src.fe.background_worker import BackgroundWorker
    from codewiki.src.fe.models import JobStatus

    config_path = tmp_path / "codewiki.toml"
    config_path.write_text(
        "[runtime]\noutput_dir='docs'\n"
        "[generation]\nmain_model='openai/gpt-4o-mini'\ncluster_model='openai/gpt-4o-mini'\n"
        "[[providers]]\nname='openai'\ntype='openai_compatible'\nmodel_list=['gpt-4o-mini']\napi_keys=[]\n",
        encoding="utf-8",
    )

    runtime_config = CodeWikiConfig(
        repo_path=str(tmp_path / "repo"),
        docs_dir=str(tmp_path / "docs"),
        main_model="openai/gpt-4o-mini",
        cluster_model="openai/gpt-4o-mini",
    )

    cache_manager = MagicMock()
    cache_manager.get_cached_docs.return_value = None

    worker = BackgroundWorker(cache_manager=cache_manager, config_path=str(config_path))
    job = JobStatus(
        job_id="cancelled-job",
        repo_url="https://github.com/owner/repo",
        status="queued",
        created_at=datetime.now(),
    )
    worker.job_status["cancelled-job"] = job

    with (
        patch("codewiki.src.fe.background_worker.load_config", return_value=runtime_config),
        patch("codewiki.src.fe.background_worker.GitHubRepoProcessor") as mock_gh,
        patch("codewiki.src.fe.background_worker.DocumentationGenerator") as mock_gen_cls,
    ):
        mock_gh.get_repo_info.return_value = {
            "full_name": "owner/repo",
            "clone_url": "https://github.com/owner/repo.git",
        }
        mock_gh.clone_repository.return_value = True
        mock_gen = mock_gen_cls.return_value
        mock_gen.run.return_value = GenerationResult(status="cancelled", warnings=["cancelled"])

        fake_loop = MagicMock()
        fake_loop.run_until_complete = MagicMock(
            return_value=GenerationResult(status="cancelled", warnings=["cancelled"])
        )
        with (
            patch("asyncio.new_event_loop", return_value=fake_loop),
            patch("asyncio.set_event_loop"),
        ):
            worker._process_job("cancelled-job")

    assert job.status == "cancelled"
    assert job.generation_status == "cancelled"
