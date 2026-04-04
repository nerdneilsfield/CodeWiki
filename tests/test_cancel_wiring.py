import threading

import pytest


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


@pytest.mark.asyncio
async def test_scheduler_cancellation_stops_before_next_leaf():
    from codewiki.src.be.cancellation import CancellationToken
    from codewiki.src.be.documentation_scheduler import run_module_queue

    class _NoopProgress:
        def update(self, n=1):
            return None

        def set_postfix_str(self, s, refresh=False):
            return None

        def close(self):
            return None

    executed = []
    token = CancellationToken()

    async def mock_process(name, components, core_ids, path, working_dir, tree_manager, **kwargs):
        executed.append(name)
        if name == "First":
            token.cancel()
        return {}, "mock-model"

    tree = {
        "First": {"components": ["a"], "children": {}},
        "Second": {"components": ["b"], "children": {}},
    }

    class FakeConfig:
        max_concurrent = 1
        max_retries = 2
        main_model = "test/main"

    await run_module_queue(
        config=FakeConfig(),
        graph_tree=tree,
        components={"a": None, "b": None},
        working_dir="/tmp/fake",
        tree_manager=None,
        process_module=mock_process,
        include_root=False,
        progress_factory=lambda **kw: _NoopProgress(),
        cancel_token=token,
    )

    assert executed == ["First"]
