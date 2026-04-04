from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from codewiki.src.fe.models import JobStatus


class _WorkerStub:
    def __init__(self):
        self.jobs: dict[str, JobStatus] = {}
        self.saved = 0
        self.deleted: list[str] = []

    def snapshot_jobs(self):
        return dict(self.jobs)

    def snapshot_job(self, job_id: str):
        return self.jobs.get(job_id)

    def set_job(self, job_id: str, job: JobStatus):
        self.jobs[job_id] = job

    def save_job_statuses(self):
        self.saved += 1

    def delete_job(self, job_id: str):
        self.deleted.append(job_id)
        self.jobs.pop(job_id, None)

    def add_job(self, job_id: str, job: JobStatus):
        self.jobs[job_id] = job
        return True

    def cancel_job(self, job_id: str):
        return False


class _CacheStub:
    def __init__(self, docs_path: str | None = None):
        self.docs_path = docs_path

    def get_cached_docs(self, repo_url: str, commit_id: str | None):
        return self.docs_path


@pytest.mark.asyncio
async def test_index_post_rejects_invalid_commit_id():
    from codewiki.src.fe.routes import WebRoutes

    routes = WebRoutes(_WorkerStub(), _CacheStub())

    with pytest.raises(HTTPException, match="Invalid commit ID format"):
        await routes.index_post(MagicMock(), repo_url="https://github.com/a/b", commit_id="bad!")


@pytest.mark.asyncio
async def test_index_post_returns_existing_processing_job_message(monkeypatch):
    from codewiki.src.fe import routes as routes_module
    from codewiki.src.fe.routes import WebRoutes

    worker = _WorkerStub()
    existing = JobStatus(
        job_id="owner--repo",
        repo_url="https://github.com/owner/repo",
        status="processing",
        created_at=datetime.now(timezone.utc),
        progress="running",
    )
    worker.jobs[existing.job_id] = existing
    routes = WebRoutes(worker, _CacheStub())
    captured = {}

    def fake_render(template, context):
        captured["context"] = context
        return "rendered"

    monkeypatch.setattr(routes_module, "render_template", fake_render)
    monkeypatch.setattr(routes_module.GitHubRepoProcessor, "is_valid_github_url", lambda _u: True)
    monkeypatch.setattr(
        routes_module.GitHubRepoProcessor,
        "get_repo_info",
        lambda _u: {"full_name": "owner/repo"},
    )

    response = await routes.index_post(
        MagicMock(),
        repo_url="https://github.com/owner/repo",
        commit_id="",
    )

    assert response.status_code == 200
    assert "already being processed" in captured["context"]["message"]
    assert captured["context"]["message_type"] == "error"


@pytest.mark.asyncio
async def test_index_post_cache_hit_creates_completed_job(tmp_path, monkeypatch):
    from codewiki.src.fe import routes as routes_module
    from codewiki.src.fe.routes import WebRoutes

    docs_dir = tmp_path / "cached-docs"
    docs_dir.mkdir()
    worker = _WorkerStub()
    routes = WebRoutes(worker, _CacheStub(str(docs_dir)))

    monkeypatch.setattr(routes_module, "render_template", lambda _t, _c: "rendered")
    monkeypatch.setattr(routes_module.GitHubRepoProcessor, "is_valid_github_url", lambda _u: True)
    monkeypatch.setattr(
        routes_module.GitHubRepoProcessor,
        "get_repo_info",
        lambda _u: {"full_name": "owner/repo"},
    )

    response = await routes.index_post(
        MagicMock(),
        repo_url="https://github.com/owner/repo",
        commit_id="abc123",
    )

    assert response.status_code == 200
    job = worker.jobs["owner--repo"]
    assert job.status == "completed"
    assert job.docs_path == str(docs_dir)
    assert worker.saved == 1


def test_render_generated_docs_sync_rejects_path_traversal(tmp_path):
    from codewiki.src.fe.routes import WebRoutes

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    worker = _WorkerStub()
    routes = WebRoutes(worker, _CacheStub())

    with pytest.raises(HTTPException, match="Access denied"):
        routes._render_generated_docs_sync(
            docs_dir,
            "https://github.com/owner/repo",
            "job-1",
            "../secret.txt",
        )


def test_cleanup_old_jobs_removes_only_old_completed_and_failed():
    from codewiki.src.fe.routes import WebRoutes

    worker = _WorkerStub()
    now = datetime.now(timezone.utc)
    worker.jobs = {
        "done": JobStatus("done", "http://x", "completed", now - timedelta(hours=30)),
        "failed": JobStatus("failed", "http://x", "failed", now - timedelta(hours=30)),
        "cancelled": JobStatus("cancelled", "http://x", "cancelled", now - timedelta(hours=30)),
        "processing": JobStatus("processing", "http://x", "processing", now - timedelta(hours=30)),
    }
    routes = WebRoutes(worker, _CacheStub())

    routes.cleanup_old_jobs()

    assert worker.deleted == ["done", "failed"]
    assert "cancelled" in worker.jobs
    assert "processing" in worker.jobs
