import threading
from datetime import datetime
from unittest.mock import MagicMock, patch

from codewiki.src.fe.models import CacheEntry, JobStatus


def test_add_job_marks_failed_when_queue_is_full():
    from codewiki.src.fe.background_worker import BackgroundWorker

    worker = BackgroundWorker.__new__(BackgroundWorker)
    worker._job_lock = threading.Lock()
    worker.job_status = {}
    worker.processing_queue = MagicMock()
    worker.processing_queue.put.side_effect = __import__("queue").Full

    job = JobStatus("job-1", "https://github.com/o/r", "queued", datetime.now())

    assert worker.add_job("job-1", job) is False
    assert job.status == "failed"
    assert "capacity" in (job.error_message or "")


def test_reconstruct_jobs_from_cache_adds_completed_jobs_and_saves(tmp_path):
    from codewiki.src.fe.background_worker import BackgroundWorker

    cache_entry = CacheEntry(
        repo_url="https://github.com/owner/repo",
        repo_url_hash="hash",
        docs_path=str(tmp_path / "docs"),
        created_at=datetime.now(),
        last_accessed=datetime.now(),
    )

    worker = BackgroundWorker.__new__(BackgroundWorker)
    worker._job_lock = threading.Lock()
    worker.job_status = {}
    worker.cache_manager = MagicMock(cache_index={"hash": cache_entry})
    worker.save_job_statuses = MagicMock()

    with patch(
        "codewiki.src.fe.background_worker.GitHubRepoProcessor.get_repo_info",
        return_value={"full_name": "owner/repo"},
    ):
        worker._reconstruct_jobs_from_cache()

    assert "owner--repo" in worker.job_status
    assert worker.job_status["owner--repo"].status == "completed"
    worker.save_job_statuses.assert_called_once()
