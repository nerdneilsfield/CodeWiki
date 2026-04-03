import threading


class TestBackgroundWorkerCancel:
    def test_cancel_job_returns_true_for_active(self):
        from codewiki.src.be.cancellation import CancellationToken
        from codewiki.src.fe.background_worker import BackgroundWorker

        worker = BackgroundWorker.__new__(BackgroundWorker)
        worker._cancel_tokens = {"j1": CancellationToken()}
        worker._job_lock = threading.Lock()
        assert worker.cancel_job("j1") is True

    def test_cancel_job_returns_false_for_missing(self):
        from codewiki.src.fe.background_worker import BackgroundWorker

        worker = BackgroundWorker.__new__(BackgroundWorker)
        worker._cancel_tokens = {}
        worker._job_lock = threading.Lock()
        assert worker.cancel_job("nonexistent") is False


class TestGuideGeneratorCancelToken:
    def test_accepts_cancel_token(self):
        import inspect

        from codewiki.src.be.guide_generator import GuideGenerator

        sig = inspect.signature(GuideGenerator.__init__)
        assert "cancel_token" in sig.parameters
