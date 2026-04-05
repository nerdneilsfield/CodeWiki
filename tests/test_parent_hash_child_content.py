import logging

import pytest

from codewiki.src.be.generation_state import DocTask, GenerationState, GenerationStateManager


def _tree() -> dict:
    return {
        "Parent": {
            "components": ["parent-comp"],
            "children": {
                "ChildA": {"components": ["a"], "children": {}},
                "ChildB": {"components": ["b"], "children": {}},
            },
        }
    }


def test_build_generation_tasks_includes_child_content_hashes(tmp_path):
    from codewiki.src.be.documentation_tree_utils import (
        build_generation_tasks,
        freeze_doc_filenames,
    )

    tree = _tree()
    freeze_doc_filenames(tree)

    config = type(
        "Cfg",
        (),
        {
            "output_language": "en",
        },
    )()

    existing_state = GenerationState()
    existing_state._add_task(
        DocTask(
            doc_id="module:parent-childa",
            kind="module",
            module_path=["Parent", "ChildA"],
            output_file="child-a.md",
            status="completed",
            content_hash="child-a-v1",
        )
    )
    existing_state._add_task(
        DocTask(
            doc_id="module:parent-childb",
            kind="module",
            module_path=["Parent", "ChildB"],
            output_file="child-b.md",
            status="completed",
            content_hash="child-b-v1",
        )
    )

    tasks_v1 = build_generation_tasks(tree, config, existing_state=existing_state)
    parent_v1 = next(task for task in tasks_v1 if task.doc_id == "module:parent")

    existing_state.get_task("module:parent-childa").content_hash = "child-a-v2"
    tasks_v2 = build_generation_tasks(tree, config, existing_state=existing_state)
    parent_v2 = next(task for task in tasks_v2 if task.doc_id == "module:parent")

    assert parent_v1.input_hash != parent_v2.input_hash


@pytest.mark.asyncio
async def test_scheduler_marks_completed_parent_stale_when_child_hash_changes(tmp_path, caplog):
    from codewiki.src.be.documentation_scheduler import run_module_queue
    from codewiki.src.utils import doc_id_for_path

    tree = _tree()
    state = GenerationState()
    manager = GenerationStateManager(state, str(tmp_path / "state.json"))
    execution_order: list[str] = []

    await manager.bulk_add_tasks(
        [
            DocTask(
                doc_id="module:parent",
                kind="overview",
                module_path=["Parent"],
                output_file="parent.md",
                status="completed",
                input_hash="old-parent-hash",
                language="en",
            ),
            DocTask(
                doc_id="module:parent-childa",
                kind="module",
                module_path=["Parent", "ChildA"],
                output_file="child-a.md",
                status="completed",
                content_hash="old-child-a",
            ),
            DocTask(
                doc_id="module:parent-childb",
                kind="module",
                module_path=["Parent", "ChildB"],
                output_file="child-b.md",
                status="completed",
                content_hash="old-child-b",
            ),
        ]
    )

    class FakeConfig:
        max_concurrent = 2
        max_retries = 0
        main_model = "test/main"

    async def mock_process(name, components, core_ids, path, working_dir, tree_manager, **kwargs):
        execution_order.append("/".join(path))
        await kwargs["state_mgr"].mark_completed(
            doc_id_for_path(tree, path),
            content_hash=f"{name}-new-hash",
            model="mock",
        )
        return {}, "mock"

    class _NoopProgress:
        def update(self, n=1):
            return None

        def set_postfix_str(self, s, refresh=False):
            return None

        def close(self):
            return None

    caplog.set_level(logging.INFO)

    await run_module_queue(
        config=FakeConfig(),
        graph_tree=tree,
        components={"a": None, "b": None, "parent-comp": None},
        working_dir=str(tmp_path),
        tree_manager=None,
        process_module=mock_process,
        include_root=False,
        gen_state=state,
        state_mgr=manager,
        progress_factory=lambda **kw: _NoopProgress(),
    )

    parent = state.get_task("module:parent")
    assert parent is not None
    assert parent.status == "completed"
    assert parent.input_hash != "old-parent-hash"
    # Completed leaf tasks are skipped (not re-processed); only the parent runs
    assert execution_order == ["Parent"]
    assert "Task module:parent marked stale (input changed)" in caplog.text


@pytest.mark.asyncio
async def test_scheduler_persists_child_aware_hash_for_first_parent_generation(tmp_path):
    from codewiki.src.be.documentation_scheduler import run_module_queue
    from codewiki.src.utils import doc_id_for_path

    tree = _tree()
    state = GenerationState()
    manager = GenerationStateManager(state, str(tmp_path / "state.json"))

    await manager.bulk_add_tasks(
        [
            DocTask(
                doc_id="module:parent",
                kind="overview",
                module_path=["Parent"],
                output_file="parent.md",
                status="planned",
                input_hash="structural-baseline",
                language="en",
            ),
            DocTask(
                doc_id="module:parent-childa",
                kind="module",
                module_path=["Parent", "ChildA"],
                output_file="child-a.md",
                status="planned",
                language="en",
            ),
            DocTask(
                doc_id="module:parent-childb",
                kind="module",
                module_path=["Parent", "ChildB"],
                output_file="child-b.md",
                status="planned",
                language="en",
            ),
        ]
    )

    class FakeConfig:
        max_concurrent = 2
        max_retries = 0
        main_model = "test/main"

    async def mock_process(name, components, core_ids, path, working_dir, tree_manager, **kwargs):
        await kwargs["state_mgr"].mark_completed(
            doc_id_for_path(tree, path),
            content_hash=f"{name}-new-hash",
            model="mock",
        )
        return {}, "mock"

    class _NoopProgress:
        def update(self, n=1):
            return None

        def set_postfix_str(self, s, refresh=False):
            return None

        def close(self):
            return None

    await run_module_queue(
        config=FakeConfig(),
        graph_tree=tree,
        components={"a": None, "b": None, "parent-comp": None},
        working_dir=str(tmp_path),
        tree_manager=None,
        process_module=mock_process,
        include_root=False,
        gen_state=state,
        state_mgr=manager,
        progress_factory=lambda **kw: _NoopProgress(),
    )

    parent = state.get_task("module:parent")
    assert parent is not None
    assert parent.status == "completed"
    assert parent.input_hash != "structural-baseline"
    assert parent.input_hash
