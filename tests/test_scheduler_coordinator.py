import logging

import pytest

from codewiki.src.be.cache_manager import CacheManager


class _NoopProgress:
    def update(self, n=1):
        return None

    def set_postfix_str(self, s, refresh=False):
        return None

    def close(self):
        return None


@pytest.mark.asyncio
async def test_coordinator_processes_leaves_before_parents(tmp_path):
    """Children must complete before their parent is dispatched."""
    from codewiki.src.be.documentation_scheduler import run_module_queue

    execution_order = []

    async def mock_process(name, components, core_ids, path, working_dir, tree_manager, **kwargs):
        execution_order.append("/".join(path))
        return {}, "mock-model"

    async def mock_root_overview():
        execution_order.append("__root__")

    async def mock_parent(**kwargs):
        execution_order.append("/".join(["Parent"]))
        return type(
            "ParentAssembly",
            (),
            {
                "output_path": "/tmp/fake/parent.md",
                "input_hash": "parent-hash",
                "model": "cluster-model",
            },
        )()

    tree = {
        "Parent": {
            "components": ["a"],
            "children": {
                "Child1": {"components": ["b"], "children": {}},
                "Child2": {"components": ["c"], "children": {}},
            },
        },
    }

    class FakeConfig:
        max_concurrent = 2
        main_model = "test/main"
        cluster_model = "test/cluster"
        output_language = "en"

    import codewiki.src.be.documentation_scheduler as scheduler

    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    cache = CacheManager(str(cache_dir), flush_interval=60)

    original_parent = scheduler.generate_or_assemble_parent_doc
    scheduler.generate_or_assemble_parent_doc = mock_parent
    try:
        await run_module_queue(
            config=FakeConfig(),
            graph_tree=tree,
            components={"a": None, "b": None, "c": None},
            working_dir=str(tmp_path),
            tree_manager=None,
            process_module=mock_process,
            generate_root_overview=mock_root_overview,
            include_root=True,
            progress_factory=lambda **kw: _NoopProgress(),
            cache_manager=cache,
            middleware=object(),
        )
    finally:
        scheduler.generate_or_assemble_parent_doc = original_parent

    parent_idx = execution_order.index("Parent")
    child1_idx = execution_order.index("Parent/Child1")
    child2_idx = execution_order.index("Parent/Child2")
    root_idx = execution_order.index("__root__")
    assert child1_idx < parent_idx
    assert child2_idx < parent_idx
    assert parent_idx < root_idx


@pytest.mark.asyncio
async def test_coordinator_handles_failed_task(monkeypatch, caplog, tmp_path):
    """A failed task must not block other leaf tasks from completing."""
    from codewiki.src.be import documentation_scheduler as scheduler

    call_count = 0
    execution_order = []

    async def _fast_sleep(_delay):
        return None

    monkeypatch.setattr(scheduler.asyncio, "sleep", _fast_sleep)

    async def mock_process(name, components, core_ids, path, working_dir, tree_manager, **kwargs):
        nonlocal call_count
        call_count += 1
        execution_order.append("/".join(path))
        if name == "FailChild":
            raise RuntimeError("intentional failure")
        return {}, "mock"

    tree = {
        "Parent": {
            "components": [],
            "children": {
                "GoodChild": {"components": ["a"], "children": {}},
                "FailChild": {"components": ["b"], "children": {}},
            },
        },
    }

    class FakeConfig:
        max_concurrent = 2
        max_retries = 2
        main_model = "test/main"
        cluster_model = "test/cluster"
        output_language = "en"

    caplog.set_level(logging.WARNING)
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    cache = CacheManager(str(cache_dir), flush_interval=60)

    await scheduler.run_module_queue(
        config=FakeConfig(),
        graph_tree=tree,
        components={"a": None, "b": None},
        working_dir=str(tmp_path),
        tree_manager=None,
        process_module=mock_process,
        include_root=False,
        progress_factory=lambda **kw: _NoopProgress(),
        cache_manager=cache,
        middleware=object(),
    )

    assert call_count >= 2
    assert "Parent" not in execution_order
    assert "Skipping 1 task(s) because dependencies failed" in caplog.text
