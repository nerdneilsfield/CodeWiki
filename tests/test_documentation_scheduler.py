import asyncio
from types import SimpleNamespace

import pytest

from codewiki.src.be.generation_state import DocTask, GenerationState, GenerationStateManager


class _DummyTreeManager:
    def __init__(self, tree):
        self._tree = tree

    async def get_snapshot(self):
        return self._tree


class _DummyProgress:
    def __init__(self, *args, **kwargs):
        pass

    def set_postfix_str(self, *args, **kwargs):
        pass

    def update(self, *args, **kwargs):
        pass

    def close(self):
        pass


@pytest.mark.asyncio
async def test_run_module_queue_processes_children_before_parent(tmp_path, monkeypatch):
    from codewiki.src.be.documentation_scheduler import run_module_queue
    monkeypatch.setattr("codewiki.src.be.documentation_scheduler.tqdm", _DummyProgress)

    graph_tree = {
        "Parent": {
            "components": ["p"],
            "children": {
                "ChildA": {"components": ["a"], "children": {}},
                "ChildB": {"components": ["b"], "children": {}},
            },
        }
    }
    order = []

    async def process_module(name, components, component_ids, path, working_dir, tree_manager, gen_state=None, state_mgr=None):
        order.append(("module", tuple(path)))
        return {}, "test/model"

    async def generate_root_overview():
        order.append(("overview", ()))

    config = SimpleNamespace(max_concurrent=2, main_model="test/main")
    await asyncio.wait_for(
        run_module_queue(
            config=config,
            graph_tree=graph_tree,
            components={},
            working_dir=str(tmp_path),
            tree_manager=_DummyTreeManager(graph_tree),
            process_module=process_module,
            generate_root_overview=generate_root_overview,
            include_root=True,
        ),
        timeout=2,
    )

    child_positions = [i for i, item in enumerate(order) if item[1] in {("Parent", "ChildA"), ("Parent", "ChildB")}]
    parent_position = order.index(("module", ("Parent",)))
    overview_position = order.index(("overview", ()))

    assert child_positions
    assert all(pos < parent_position for pos in child_positions)
    assert parent_position < overview_position


@pytest.mark.asyncio
async def test_run_module_queue_marks_failed_tasks_in_state(tmp_path, monkeypatch):
    from codewiki.src.be.documentation_scheduler import run_module_queue
    monkeypatch.setattr("codewiki.src.be.documentation_scheduler.tqdm", _DummyProgress)

    async def _fast_sleep(_seconds):
        return None

    monkeypatch.setattr("codewiki.src.be.documentation_scheduler.asyncio.sleep", _fast_sleep)

    graph_tree = {"Leaf": {"components": ["x"], "children": {}}}
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

    async def process_module(name, components, component_ids, path, working_dir, tree_manager, gen_state=None, state_mgr=None):
        raise RuntimeError("boom")

    config = SimpleNamespace(max_concurrent=1, main_model="test/main")
    await asyncio.wait_for(
        run_module_queue(
            config=config,
            graph_tree=graph_tree,
            components={},
            working_dir=str(tmp_path),
            tree_manager=_DummyTreeManager(graph_tree),
            process_module=process_module,
            generate_root_overview=None,
            include_root=False,
            gen_state=state,
            state_mgr=manager,
        ),
        timeout=2,
    )

    assert state.get_task("module:leaf").status == "failed"
    assert "boom" in state.get_task("module:leaf").last_error


@pytest.mark.asyncio
async def test_fill_missing_module_docs_retries_only_missing_modules(tmp_path):
    from codewiki.src.be.documentation_scheduler import fill_missing_module_docs

    graph_tree = {
        "Parent": {
            "components": ["p"],
            "children": {
                "ChildA": {"components": ["a"], "children": {}},
                "ChildB": {"components": ["b"], "children": {}},
            },
        }
    }
    retried = []

    async def run_module_queue(**kwargs):
        retried.append(kwargs["desc"])

    def module_doc_exists(_working_dir, module_path, _module_tree, _gen_state=None):
        return module_path != ["Parent", "ChildB"]

    config = SimpleNamespace(max_retries=2)
    await asyncio.wait_for(
        fill_missing_module_docs(
            config=config,
            working_dir=str(tmp_path),
            components={},
            tree_manager=_DummyTreeManager(graph_tree),
            run_module_queue=run_module_queue,
            module_doc_exists=module_doc_exists,
        ),
        timeout=2,
    )

    assert retried == ["Fill pass 1/2", "Fill pass 2/2"]
