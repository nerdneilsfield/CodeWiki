from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest


class _StubWorker:
    def __init__(self, docs_path: Path):
        self._job = SimpleNamespace(
            job_id="job-1",
            repo_url="https://github.com/example/repo",
            status="completed",
            docs_path=str(docs_path),
        )
        self.job_status = {"job-1": self._job}

    def get_job_status(self, job_id: str):
        return self.job_status.get(job_id)

    def save_job_statuses(self):
        return None


class _StubCache:
    def get_cached_docs(self, repo_url: str, commit_id: str | None):
        return None


@pytest.mark.asyncio
async def test_web_routes_serve_generated_docs_uses_to_thread(tmp_path, monkeypatch):
    from codewiki.src.fe.background_worker import BackgroundWorker
    from codewiki.src.fe.cache_manager import CacheManager
    from codewiki.src.fe.routes import WebRoutes
    from codewiki.src.fe import routes as routes_module

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "overview.md").write_text("# Overview\n", encoding="utf-8")
    (docs_dir / "module_tree.json").write_text("{}", encoding="utf-8")

    called = {}

    async def _fake_to_thread(func, *args, **kwargs):
        called["func"] = func.__name__
        return func(*args, **kwargs)

    monkeypatch.setattr(routes_module.asyncio, "to_thread", _fake_to_thread)

    routes = WebRoutes(
        cast(BackgroundWorker, _StubWorker(docs_dir)),
        cast(CacheManager, _StubCache()),
    )
    response = await routes.serve_generated_docs("job-1")

    assert called["func"] == "_render_generated_docs_sync"
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_visualise_docs_index_uses_to_thread(tmp_path, monkeypatch):
    from codewiki.src.fe import visualise_docs

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    overview = docs_dir / "overview.md"
    overview.write_text("# Overview\n", encoding="utf-8")

    monkeypatch.setattr(visualise_docs, "DOCS_FOLDER", str(docs_dir))
    monkeypatch.setattr(visualise_docs, "MODULE_TREE", {})

    called = {}

    async def _fake_to_thread(func, *args, **kwargs):
        called["func"] = func.__name__
        return func(*args, **kwargs)

    monkeypatch.setattr(visualise_docs.asyncio, "to_thread", _fake_to_thread)

    response = await visualise_docs.index()

    assert called["func"] == "_render_overview_sync"
    assert response.status_code == 200
