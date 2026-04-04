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
    async def test_cancellation_stops_fallback_chain_immediately(self, monkeypatch, tmp_path):
        import asyncio

        from codewiki.src.be.cancellation import CancellationToken
        from codewiki.src.be.errors import CancellationError
        from codewiki.src.be.guide_generator import GuideGenerator
        from codewiki.src.codewiki_config import CodeWikiConfig

        config = CodeWikiConfig(
            repo_path="/tmp/fake-repo",
            output_dir=str(tmp_path / "output"),
            dependency_graph_dir=str(tmp_path / "dg"),
            docs_dir=str(tmp_path / "docs"),
            max_depth=2,
            llm_base_url="http://localhost:4000/",
            llm_api_key="sk-test",
            main_model="openai/model-a",
            cluster_model="openai/model-a",
            fallback_model="openai/model-b,openai/model-c",
        )
        token = CancellationToken()
        gen = GuideGenerator(
            config=config,
            components={},
            module_tree={},
            working_dir=str(tmp_path),
            cancel_token=token,
        )
        gen._semaphore = asyncio.Semaphore(1)

        attempted_models = []

        async def fake_with_retry(_operation, *args, **kwargs):
            attempted_models.append(kwargs["model"])
            raise CancellationError("cancelled during llm call")

        monkeypatch.setattr("codewiki.src.be.guide_generator.with_retry", fake_with_retry)

        with pytest.raises(CancellationError):
            await gen._call_llm_with_fallback("prompt")

        assert attempted_models == ["openai/model-a"]


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
