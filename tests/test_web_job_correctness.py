import threading
from datetime import datetime

from codewiki.src.fe.models import JobStatus


class TestH1SnapshotAPI:
    def test_snapshot_jobs_returns_copied_objects(self):
        from codewiki.src.fe.background_worker import BackgroundWorker

        worker = BackgroundWorker.__new__(BackgroundWorker)
        worker.job_status = {
            "j1": JobStatus(
                job_id="j1",
                repo_url="http://x",
                status="completed",
                created_at=datetime.now(),
            ),
        }
        worker._job_lock = threading.Lock()
        snap = worker.snapshot_jobs()
        snap["j1"].status = "mutated"
        assert worker.job_status["j1"].status == "completed"

    def test_snapshot_job_returns_none_for_missing(self):
        from codewiki.src.fe.background_worker import BackgroundWorker

        worker = BackgroundWorker.__new__(BackgroundWorker)
        worker.job_status = {}
        worker._job_lock = threading.Lock()
        assert worker.snapshot_job("nonexistent") is None


class TestH2GenerationResultFields:
    def test_job_status_has_generation_fields(self):
        job = JobStatus(
            job_id="j1",
            repo_url="http://x",
            status="completed",
            created_at=datetime.now(),
            generation_status="degraded",
            degradation_reasons=["IndexBuild failed"],
            module_summary={"total": 10, "completed": ["a"], "failed": []},
        )
        assert job.generation_status == "degraded"
        assert len(job.degradation_reasons) == 1
        assert job.module_summary["total"] == 10
