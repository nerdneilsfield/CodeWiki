import json
from types import SimpleNamespace

import pytest

from codewiki.src.be.generation_state import DocTask, GenerationState, GenerationStateManager


class _NoopProgress:
    def __init__(self, *args, **kwargs):
        pass

    def set_postfix_str(self, *args, **kwargs):
        pass

    def update(self, *args, **kwargs):
        pass

    def close(self):
        pass


@pytest.mark.asyncio
async def test_mark_running_sets_dirty_without_writing(tmp_path):
    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="module:a",
            kind="module",
            module_path=["A"],
            output_file="a.md",
            status="ready",
        )
    )
    path = tmp_path / "generation_state.json"
    state._save(str(path))

    manager = GenerationStateManager(state, str(path))
    await manager.mark_running("module:a")

    assert manager._dirty is True
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    disk_task = {task["doc_id"]: task for task in on_disk["tasks"]}
    assert disk_task["module:a"]["status"] == "ready"


@pytest.mark.asyncio
async def test_flush_persists_dirty_state(tmp_path):
    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="module:a",
            kind="module",
            module_path=["A"],
            output_file="a.md",
            status="ready",
        )
    )
    path = tmp_path / "generation_state.json"
    state._save(str(path))

    manager = GenerationStateManager(state, str(path))
    await manager.mark_running("module:a")
    await manager.flush()

    assert manager._dirty is False
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    disk_task = {task["doc_id"]: task for task in on_disk["tasks"]}
    assert disk_task["module:a"]["status"] == "running"


@pytest.mark.asyncio
async def test_run_module_queue_flushes_on_done_queue(tmp_path, monkeypatch):
    from codewiki.src.be.documentation_scheduler import run_module_queue

    flush_calls = []
    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="module:leaf",
            kind="module",
            module_path=["Leaf"],
            output_file="leaf.md",
            status="ready",
        )
    )
    manager = GenerationStateManager(state, str(tmp_path / "generation_state.json"))

    original_flush = manager.flush

    async def tracking_flush():
        flush_calls.append("flush")
        await original_flush()

    monkeypatch.setattr(manager, "flush", tracking_flush)

    async def process_module(
        name,
        components,
        component_ids,
        path,
        working_dir,
        tree_manager,
        gen_state=None,
        state_mgr=None,
    ):
        return {}, "test/model"

    graph_tree = {"Leaf": {"components": ["x"], "children": {}}}
    config = SimpleNamespace(max_concurrent=1, main_model="test/main")
    await run_module_queue(
        config=config,
        graph_tree=graph_tree,
        components={},
        working_dir=str(tmp_path),
        tree_manager=None,
        process_module=process_module,
        include_root=False,
        gen_state=state,
        state_mgr=manager,
        progress_factory=lambda **kwargs: _NoopProgress(),
    )

    assert flush_calls == ["flush"]
