import asyncio
from types import SimpleNamespace

import pytest

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.be.pipeline import ModuleSummary


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
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    cache = CacheManager(str(cache_dir), flush_interval=60)

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

    async def process_module(
        name,
        components,
        component_ids,
        path,
        working_dir,
        tree_manager,
    ):
        order.append(("module", tuple(path)))
        return {}, "test/model"

    async def generate_parent_doc(**kwargs):
        order.append(("parent", ("Parent",)))
        return SimpleNamespace(
            output_path=str(tmp_path / "parent.md"),
            input_hash="parent-hash",
            model="test/cluster",
        )

    async def generate_root_overview():
        order.append(("overview", ()))

    monkeypatch.setattr(
        "codewiki.src.be.documentation_scheduler.generate_or_assemble_parent_doc",
        generate_parent_doc,
    )

    config = SimpleNamespace(
        max_concurrent=2,
        main_model="test/main",
        cluster_model="test/cluster",
        output_language="en",
    )
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
            cache_manager=cache,
            middleware=SimpleNamespace(call=lambda *args, **kwargs: None),
        ),
        timeout=2,
    )

    child_positions = [
        i for i, item in enumerate(order) if item[1] in {("Parent", "ChildA"), ("Parent", "ChildB")}
    ]
    parent_position = order.index(("parent", ("Parent",)))
    overview_position = order.index(("overview", ()))

    assert child_positions
    assert all(pos < parent_position for pos in child_positions)
    assert parent_position < overview_position


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
        return ModuleSummary()

    def module_doc_exists(_working_dir, module_path, _module_tree):
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


@pytest.mark.asyncio
async def test_run_module_queue_routes_parent_nodes_to_segment_assembly(tmp_path, monkeypatch):
    from codewiki.src.be.documentation_scheduler import run_module_queue

    monkeypatch.setattr("codewiki.src.be.documentation_scheduler.tqdm", _DummyProgress)
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    cache = CacheManager(str(cache_dir), flush_interval=60)

    graph_tree = {
        "Parent": {
            "components": [
                "a1",
                "a2",
                "a3",
                "a4",
                "a5",
                "a6",
                "b1",
                "b2",
                "b3",
                "b4",
                "b5",
                "b6",
            ],
            "children": {
                "ChildA": {"components": ["a1", "a2", "a3", "a4", "a5", "a6"], "children": {}},
                "ChildB": {"components": ["b1", "b2", "b3", "b4", "b5", "b6"], "children": {}},
            },
        }
    }
    components = {
        "a1": SimpleNamespace(depends_on={"b1"}, source_code="a1"),
        "a2": SimpleNamespace(depends_on=set(), source_code="a2"),
        "a3": SimpleNamespace(depends_on=set(), source_code="a3"),
        "a4": SimpleNamespace(depends_on=set(), source_code="a4"),
        "a5": SimpleNamespace(depends_on=set(), source_code="a5"),
        "a6": SimpleNamespace(depends_on=set(), source_code="a6"),
        "b1": SimpleNamespace(depends_on={"a1"}, source_code="b1"),
        "b2": SimpleNamespace(depends_on=set(), source_code="b2"),
        "b3": SimpleNamespace(depends_on=set(), source_code="b3"),
        "b4": SimpleNamespace(depends_on=set(), source_code="b4"),
        "b5": SimpleNamespace(depends_on=set(), source_code="b5"),
        "b6": SimpleNamespace(depends_on=set(), source_code="b6"),
    }
    seen = {}
    parent_calls = []

    async def process_module(
        name,
        components,
        component_ids,
        path,
        working_dir,
        tree_manager,
    ):
        seen[tuple(path)] = list(component_ids)
        return {}, "test/model"

    async def generate_parent_doc(**kwargs):
        parent_calls.append(kwargs["parent_doc_id"])
        return SimpleNamespace(
            output_path=str(tmp_path / "parent.md"),
            input_hash="parent-hash",
            model="test/cluster",
        )

    monkeypatch.setattr(
        "codewiki.src.be.documentation_scheduler.generate_or_assemble_parent_doc",
        generate_parent_doc,
    )

    config = SimpleNamespace(
        max_concurrent=2,
        main_model="test/main",
        cluster_model="test/cluster",
        output_language="en",
    )
    await asyncio.wait_for(
        run_module_queue(
            config=config,
            graph_tree=graph_tree,
            components=components,
            working_dir=str(tmp_path),
            tree_manager=_DummyTreeManager(graph_tree),
            process_module=process_module,
            include_root=False,
            cache_manager=cache,
            middleware=SimpleNamespace(call=lambda *args, **kwargs: None),
        ),
        timeout=2,
    )

    assert ("Parent",) not in seen
    assert set(seen) == {("Parent", "ChildA"), ("Parent", "ChildB")}
    assert parent_calls == ["module:parent"]
