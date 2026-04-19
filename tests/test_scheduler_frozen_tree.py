"""Scheduler should operate strictly on the frozen tree."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from codewiki.src.be.cache_manager import CacheManager, module_artifact_id
from codewiki.src.be.documentation_scheduler import fill_missing_module_docs, run_module_queue
from codewiki.src.be.pipeline import ModuleSummary


@pytest.fixture
def cache_dir(tmp_path):
    path = tmp_path / ".codewiki"
    path.mkdir()
    return str(path)


def _frozen_tree():
    return {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "description": ".",
            "_doc_filename": "top.md",
            "components": [],
            "children": {
                "Left": {
                    "module_id": "left",
                    "title": "Left",
                    "path": "left",
                    "description": ".",
                    "_doc_filename": "top-left.md",
                    "components": ["a.py::A"],
                    "children": {},
                },
                "Right": {
                    "module_id": "right",
                    "title": "Right",
                    "path": "right",
                    "description": ".",
                    "_doc_filename": "top-right.md",
                    "components": ["b.py::B"],
                    "children": {},
                },
            },
        }
    }


@pytest.mark.asyncio
async def test_scheduler_processes_leaves_before_parent(tmp_path, cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    process_order: list[str] = []
    config = MagicMock(max_concurrent=2, main_model="m", output_language="en")
    config.cluster_model = "cluster"
    config.get_prompt_addition.return_value = ""

    async def process_module(
        name, components, task_component_ids, path, working_dir, tree_manager, **kwargs
    ):
        process_order.append("/".join(path))
        return {}, "m"

    async def generate_parent_doc(**kwargs):
        process_order.append(f"parent:{kwargs['parent_doc_id']}")
        return SimpleNamespace(
            output_path=str(tmp_path / "docs" / "top.md"),
            input_hash="parent-hash",
            model="cluster",
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "codewiki.src.be.documentation_scheduler.generate_or_assemble_parent_doc",
        generate_parent_doc,
    )

    await run_module_queue(
        config=config,
        graph_tree=_frozen_tree(),
        components={
            "a.py::A": SimpleNamespace(source_code="a"),
            "b.py::B": SimpleNamespace(source_code="b"),
        },
        working_dir=str(tmp_path / "docs"),
        tree_manager=None,
        process_module=process_module,
        cache_manager=cache,
        include_root=False,
        middleware=SimpleNamespace(call=lambda *args, **kwargs: None),
    )
    monkeypatch.undo()

    top_idx = process_order.index("parent:top")
    left_idx = process_order.index("Top/Left")
    right_idx = process_order.index("Top/Right")
    assert left_idx < top_idx
    assert right_idx < top_idx


@pytest.mark.asyncio
async def test_scheduler_does_not_dispatch_modules_outside_frozen_tree(tmp_path, cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    seen: set[str] = set()
    config = MagicMock(max_concurrent=2, main_model="m", output_language="en")
    config.cluster_model = "cluster"
    config.get_prompt_addition.return_value = ""

    async def process_module(
        name, components, task_component_ids, path, working_dir, tree_manager, **kwargs
    ):
        seen.add("/".join(path))
        return {}, "m"

    async def generate_parent_doc(**kwargs):
        seen.add(f"parent:{kwargs['parent_doc_id']}")
        return SimpleNamespace(
            output_path=str(tmp_path / "docs" / "top.md"),
            input_hash="parent-hash",
            model="cluster",
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "codewiki.src.be.documentation_scheduler.generate_or_assemble_parent_doc",
        generate_parent_doc,
    )

    await run_module_queue(
        config=config,
        graph_tree=_frozen_tree(),
        components={
            "a.py::A": SimpleNamespace(source_code="a"),
            "b.py::B": SimpleNamespace(source_code="b"),
        },
        working_dir=str(tmp_path / "docs"),
        tree_manager=None,
        process_module=process_module,
        cache_manager=cache,
        include_root=False,
        middleware=SimpleNamespace(call=lambda *args, **kwargs: None),
    )
    monkeypatch.undo()

    assert seen == {"Top/Left", "Top/Right", "parent:top"}


@pytest.mark.asyncio
async def test_fill_pass_only_retries_failed_or_cancelled(tmp_path, cache_dir):
    cache = CacheManager(cache_dir, flush_interval=60)
    tree = _frozen_tree()

    for node_info in (
        tree["Top"],
        tree["Top"]["children"]["Left"],
        tree["Top"]["children"]["Right"],
    ):
        artifact = module_artifact_id(node_info["module_id"])
        cache.plan_task(artifact, output_file=node_info["_doc_filename"])
        cache.mark_done(artifact, input_hash="h", output_path="/tmp/x", model="m")

    run_calls: list[dict] = []

    async def fake_run_module_queue(**kwargs):
        run_calls.append(kwargs)
        return ModuleSummary()

    summary = await fill_missing_module_docs(
        config=MagicMock(max_concurrent=2, max_retries=2),
        working_dir=str(tmp_path / "docs"),
        components={"a.py::A": MagicMock(), "b.py::B": MagicMock()},
        tree_manager=MagicMock(get_snapshot=AsyncMock(return_value=tree)),
        run_module_queue=fake_run_module_queue,
        module_doc_exists=lambda *_args, **_kwargs: True,
        cache_manager=cache,
        cancel_token=None,
    )
    assert run_calls == []
    assert summary.total == 0
