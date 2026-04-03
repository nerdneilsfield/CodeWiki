import pytest

from codewiki.src.be.pipeline import ModuleFailure, ModuleSkip, ModuleSummary


class _NoopProgress:
    def update(self, n=1):
        return None

    def set_postfix_str(self, s, refresh=False):
        return None

    def close(self):
        return None


@pytest.mark.asyncio
async def test_run_module_queue_returns_module_summary():
    from codewiki.src.be import documentation_scheduler as scheduler

    async def _fast_sleep(_seconds):
        return None

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(scheduler.asyncio, "sleep", _fast_sleep)

    async def mock_process(name, components, core_ids, path, working_dir, tree_manager, **kwargs):
        if name == "FailModule":
            raise RuntimeError("test failure")
        return {}, "mock-model"

    tree = {
        "GoodModule": {"components": ["a"], "children": {}},
        "FailModule": {"components": ["b"], "children": {}},
    }

    class FakeConfig:
        max_concurrent = 2
        max_retries = 2
        main_model = "test/main"

    summary = await scheduler.run_module_queue(
        config=FakeConfig(),
        graph_tree=tree,
        components={"a": None, "b": None},
        working_dir="/tmp/fake",
        tree_manager=None,
        process_module=mock_process,
        include_root=False,
        progress_factory=lambda **kw: _NoopProgress(),
    )

    assert isinstance(summary, ModuleSummary)
    assert summary.total == 2
    assert summary.completed == ["module:goodmodule"]
    assert summary.failed == [
        ModuleFailure(doc_id="module:failmodule", error="test failure", retried=True)
    ]
    assert summary.skipped == []
    assert summary.retried_then_succeeded == []
    monkeypatch.undo()


@pytest.mark.asyncio
async def test_run_module_queue_records_skipped_parent_modules():
    from codewiki.src.be import documentation_scheduler as scheduler

    async def _fast_sleep(_seconds):
        return None

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(scheduler.asyncio, "sleep", _fast_sleep)

    async def mock_process(name, components, core_ids, path, working_dir, tree_manager, **kwargs):
        if name == "FailChild":
            raise RuntimeError("test failure")
        return {}, "mock-model"

    tree = {
        "Parent": {
            "components": [],
            "children": {
                "GoodChild": {"components": ["a"], "children": {}},
                "FailChild": {"components": ["b"], "children": {}},
            },
        }
    }

    class FakeConfig:
        max_concurrent = 2
        max_retries = 2
        main_model = "test/main"

    summary = await scheduler.run_module_queue(
        config=FakeConfig(),
        graph_tree=tree,
        components={"a": None, "b": None},
        working_dir="/tmp/fake",
        tree_manager=None,
        process_module=mock_process,
        include_root=False,
        progress_factory=lambda **kw: _NoopProgress(),
    )

    assert isinstance(summary, ModuleSummary)
    assert summary.total == 3
    assert summary.completed == ["module:parent-goodchild"]
    assert summary.failed == [
        ModuleFailure(doc_id="module:parent-failchild", error="test failure", retried=True)
    ]
    assert summary.skipped == [ModuleSkip(doc_id="module:parent", reason="dependency failed")]
    monkeypatch.undo()
