"""Tests for the Task 0 generation-state ledger foundation."""

import asyncio
import json
import logging
from pathlib import Path

import pytest

from codewiki.src.be.generation_state import (
    DocTask,
    GenerationState,
    GenerationStateManager,
    SCHEMA_VERSION,
)
from codewiki.src.config import GENERATION_STATE_FILENAME, internal_file_path, postprocess_fix_links


def test_config_generation_state_constants_and_internal_path(tmp_path):
    assert GENERATION_STATE_FILENAME == "generation_state.json"
    assert postprocess_fix_links is True

    path = internal_file_path(str(tmp_path), GENERATION_STATE_FILENAME)

    assert path == str(tmp_path / ".codewiki" / "generation_state.json")
    assert Path(path).parent.exists()


def test_doc_task_lifecycle_and_staleness():
    task = DocTask(
        doc_id="module:cli",
        kind="module",
        module_path=["CLI"],
        output_file="cli.md",
    )

    assert task.status == "planned"
    assert task.source == "manifest"
    assert task.is_stale("anything") is False

    task.mark_running()
    assert task.status == "running"

    task.mark_completed(content_hash="sha256:abc", model="gpt-4o")
    assert task.status == "completed"
    assert task.content_hash == "sha256:abc"
    assert task.model == "gpt-4o"
    assert task.attempt_count == 1
    assert task.is_stale("sha256:new") is True

    task.mark_failed("boom")
    assert task.status == "failed"
    assert task.last_error == "boom"
    assert task.attempt_count == 2


def test_generation_state_add_lookup_and_duplicate_output_rejected():
    state = GenerationState(repo_commit="abc123", config_fingerprint="cfg456")
    task = DocTask(
        doc_id="module:cli",
        kind="module",
        module_path=["CLI"],
        output_file="cli.md",
    )
    state._add_task(task)

    assert state.schema_version == SCHEMA_VERSION
    assert state.get_task("module:cli") is task
    assert state.get_output_file("module:cli") == "cli.md"

    with pytest.raises(ValueError, match="already assigned"):
        state._add_task(
            DocTask(
                doc_id="module:other",
                kind="module",
                module_path=["Other"],
                output_file="cli.md",
            )
        )


def test_generation_state_actionable_and_ready_task_ids():
    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="child",
            kind="module",
            module_path=["Child"],
            output_file="child.md",
            status="completed",
        )
    )
    state._add_task(
        DocTask(
            doc_id="ready",
            kind="module",
            module_path=["Ready"],
            output_file="ready.md",
            status="ready",
        )
    )
    state._add_task(
        DocTask(
            doc_id="retry",
            kind="module",
            module_path=["Retry"],
            output_file="retry.md",
            status="failed",
        )
    )
    state._add_task(
        DocTask(
            doc_id="stale",
            kind="module",
            module_path=["Stale"],
            output_file="stale.md",
            status="stale",
        )
    )
    state._add_task(
        DocTask(
            doc_id="planned",
            kind="overview",
            module_path=["Parent"],
            output_file="parent.md",
            depends_on=["child"],
        )
    )
    state._add_task(
        DocTask(
            doc_id="blocked",
            kind="overview",
            module_path=["Blocked"],
            output_file="blocked.md",
            depends_on=["child", "missing"],
        )
    )

    assert set(state.actionable_task_ids()) == {"ready", "retry", "stale"}
    assert set(state.ready_task_ids()) == {"ready", "retry", "stale", "planned"}

    promoted = state._promote_ready()
    assert promoted == 1
    assert state.get_task("planned").status == "ready"
    assert state.get_task("blocked").status == "planned"


def test_generation_state_mark_stale_and_save_load_round_trip(tmp_path):
    state = GenerationState(repo_commit="abc123", config_fingerprint="cfg456")
    state._add_task(
        DocTask(
            doc_id="module:cli",
            kind="module",
            module_path=["CLI"],
            output_file="cli.md",
            status="completed",
            input_hash="old",
            content_hash="sha256:xyz",
            source="manifest",
        )
    )
    state._add_task(
        DocTask(
            doc_id="module:ready",
            kind="module",
            module_path=["Ready"],
            output_file="ready.md",
            status="ready",
        )
    )

    state._mark_stale_tasks({"module:cli": "new", "module:ready": "whatever"})
    assert state.get_task("module:cli").status == "stale"
    assert state.get_task("module:ready").status == "ready"

    path = tmp_path / GENERATION_STATE_FILENAME
    state._save(str(path))
    loaded = GenerationState.load(str(path))

    assert loaded.repo_commit == "abc123"
    assert loaded.config_fingerprint == "cfg456"
    assert loaded.get_task("module:cli").content_hash == "sha256:xyz"
    assert loaded.get_task("module:cli").source == "manifest"


def test_generation_state_load_missing_file_returns_empty_state(tmp_path):
    loaded = GenerationState.load(str(tmp_path / GENERATION_STATE_FILENAME))

    assert loaded.tasks == {}
    assert loaded.repo_commit == ""
    assert loaded.config_fingerprint == ""


def test_ready_task_ids_treat_skipped_dependencies_as_satisfied():
    state = GenerationState()
    state._add_task(
        DocTask(
            doc_id="child",
            kind="module",
            module_path=["Child"],
            output_file="child.md",
            status="skipped",
        )
    )
    state._add_task(
        DocTask(
            doc_id="parent",
            kind="overview",
            module_path=["Parent"],
            output_file="parent.md",
            depends_on=["child"],
        )
    )

    assert state.ready_task_ids() == ["parent"]


def test_generation_state_manager_wraps_mutations(tmp_path):
    state = GenerationState()
    manager = GenerationStateManager(state, str(tmp_path / GENERATION_STATE_FILENAME))

    async def _run():
        await manager.bulk_add_tasks(
            [
                DocTask(
                    doc_id="parent",
                    kind="overview",
                    module_path=["Parent"],
                    output_file="parent.md",
                    depends_on=["child"],
                ),
                DocTask(
                    doc_id="child",
                    kind="module",
                    module_path=["Child"],
                    output_file="child.md",
                    status="completed",
                ),
            ]
        )
        await manager.promote_ready()
        await manager.mark_running("parent")
        await manager.mark_completed("parent", content_hash="sha256:done", model="gpt-4o")
        await manager.mark_failed("parent", "boom")
        await manager.register_discovered_task(
            DocTask(
                doc_id="disc",
                kind="module",
                module_path=["Parent", "Disc"],
                output_file="parent-disc.md",
                source="discovered",
                parent_doc_id="parent",
            )
        )
        await manager.mark_stale({"parent": "new"})
        await manager.flush()

    asyncio.run(_run())

    assert state.get_task("parent").status == "failed"
    assert state.get_task("parent").attempt_count == 2
    assert state.get_task("parent").content_hash == "sha256:done"
    assert state.get_task("disc").source == "discovered"
    assert state.get_task("disc").parent_doc_id == "parent"
    persisted = json.loads((tmp_path / GENERATION_STATE_FILENAME).read_text(encoding="utf-8"))
    assert persisted["schema_version"] == SCHEMA_VERSION


def test_generation_state_manager_updates_metadata(tmp_path):
    state = GenerationState()
    manager = GenerationStateManager(state, str(tmp_path / GENERATION_STATE_FILENAME))

    async def _run():
        await manager.update_metadata("commit-123", "cfg-456")

    asyncio.run(_run())

    assert state.repo_commit == "commit-123"
    assert state.config_fingerprint == "cfg-456"


def test_generation_state_manager_mark_stale_noop_does_not_dirty(tmp_path):
    state = GenerationState()
    manager = GenerationStateManager(state, str(tmp_path / GENERATION_STATE_FILENAME))

    async def _run():
        await manager.mark_stale({"missing": "new"})

    asyncio.run(_run())

    assert manager._dirty is False


def test_generation_state_manager_registers_discovered_tasks_as_planned(tmp_path):
    state = GenerationState()
    manager = GenerationStateManager(state, str(tmp_path / GENERATION_STATE_FILENAME))

    async def _run():
        await manager.register_discovered_task(
            DocTask(
                doc_id="disc",
                kind="module",
                module_path=["Parent", "Disc"],
                output_file="disc.md",
                source="manifest",
                status="running",
            )
        )

    asyncio.run(_run())

    task = state.get_task("disc")
    assert task is not None
    assert task.source == "discovered"
    assert task.status == "planned"


def test_generation_state_load_skips_output_file_collisions(tmp_path, caplog):
    path = tmp_path / GENERATION_STATE_FILENAME
    path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "repo_commit": "abc",
                "config_fingerprint": "cfg",
                "tasks": [
                    {
                        "doc_id": "module:first",
                        "kind": "module",
                        "module_path": ["First"],
                        "output_file": "same.md",
                    },
                    {
                        "doc_id": "module:second",
                        "kind": "module",
                        "module_path": ["Second"],
                        "output_file": "same.md",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING):
        loaded = GenerationState.load(str(path))

    assert loaded.get_task("module:first") is not None
    assert loaded.get_task("module:second") is None
    assert "output_file collision" in caplog.text
