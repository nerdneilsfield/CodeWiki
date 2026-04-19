import json
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.be.dependency_analyzer.models.core import Node
from codewiki.src.be.refinement_cache import refinement_artifact_id
from codewiki.src.be.pipeline import PipelineContext
from codewiki.src.be.stages.tree_refinement import TreeRefinementStage
from codewiki.src.codewiki_config import CodeWikiConfig, RefinementConfig
from codewiki.src.config import MODULE_TREE_FILENAME


def _node(component_id: str, file_path: str) -> Node:
    return Node(
        id=component_id,
        name=component_id.split("::")[-1],
        component_type="function",
        file_path=file_path,
        relative_path=file_path,
        source_code="pass",
    )


def _make_context(tmp_path):
    cache_dir = tmp_path / "docs" / ".codewiki"
    cache_dir.mkdir(parents=True)
    cfg = CodeWikiConfig(
        repo_path=str(tmp_path / "repo"),
        docs_dir=str(tmp_path / "docs"),
        llm_base_url="http://localhost",
        llm_api_key="x",
        main_model="m",
        cluster_model="c",
        refinement=RefinementConfig(
            max_depth=2,
            min_components_for_split=2,
            min_distinct_files_for_split=2,
        ),
    )
    cache = CacheManager(str(cache_dir), flush_interval=60)
    components = {f"f{i}.py::C{i}": _node(f"f{i}.py::C{i}", f"f{i}.py") for i in range(4)}
    module_tree = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "description": ".",
            "components": list(components.keys()),
            "children": {},
        }
    }
    middleware = MagicMock()
    middleware.call = AsyncMock(
        return_value=MagicMock(
            text=json.dumps({"should_split": False, "children": {}}),
            model="fake",
        )
    )
    ctx = PipelineContext(
        config=cfg,
        working_dir=str(tmp_path / "docs"),
        components=components,
        leaf_nodes=list(components.keys()),
        module_tree=module_tree,
        cache_manager=cache,
    )
    ctx.generator = MagicMock()
    ctx.generator.middleware = middleware
    return ctx, middleware


@pytest.mark.asyncio
async def test_tree_refinement_stage_assigns_filenames_to_top_nodes(tmp_path):
    ctx, _ = _make_context(tmp_path)
    stage = TreeRefinementStage()
    await stage.execute(ctx)
    assert ctx.module_tree["Top"]["_doc_filename"] == "top.md"


@pytest.mark.asyncio
async def test_tree_refinement_stage_writes_refinement_cache_entry(tmp_path):
    ctx, _ = _make_context(tmp_path)
    stage = TreeRefinementStage()
    await stage.execute(ctx)
    entry = ctx.cache_manager.get_entry("refinement:top")
    assert entry is not None
    assert entry.status == "valid"


@pytest.mark.asyncio
async def test_tree_refinement_stage_is_idempotent_on_second_run(tmp_path):
    ctx, middleware = _make_context(tmp_path)
    stage = TreeRefinementStage()
    await stage.execute(ctx)
    first_calls = middleware.call.await_count
    await stage.execute(ctx)
    assert middleware.call.await_count == first_calls


@pytest.mark.asyncio
async def test_tree_refinement_stage_writes_module_tree_json(tmp_path):
    ctx, _ = _make_context(tmp_path)
    stage = TreeRefinementStage()
    await stage.execute(ctx)
    module_tree_path = os.path.join(ctx.working_dir, MODULE_TREE_FILENAME)
    assert os.path.exists(module_tree_path)
    with open(module_tree_path, "r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert loaded["Top"]["_doc_filename"] == "top.md"


@pytest.mark.asyncio
async def test_tree_refinement_stage_invalidates_changed_leaf_modules(tmp_path):
    ctx, _ = _make_context(tmp_path)
    stage = TreeRefinementStage()
    module_artifact = "module:top"
    ctx.cache_manager.plan_task(module_artifact, output_file="top.md")
    ctx.cache_manager.mark_done(
        module_artifact,
        input_hash="old-hash",
        output_path=str(tmp_path / "docs" / "top.md"),
        model="m",
        output_file="top.md",
    )

    await stage.execute(ctx)
    first_status = ctx.cache_manager.get_entry(module_artifact)
    assert first_status is not None
    assert first_status.status == "stale"


@pytest.mark.asyncio
async def test_tree_refinement_stage_records_rename_map(tmp_path, monkeypatch):
    ctx, _ = _make_context(tmp_path)
    previous_tree = {
        "Top": {
            "module_id": "top",
            "title": "Top",
            "path": "top",
            "description": ".",
            "_doc_filename": "top_old.md",
            "components": list(ctx.components.keys()),
            "children": {},
        }
    }
    with open(os.path.join(ctx.working_dir, MODULE_TREE_FILENAME), "w", encoding="utf-8") as fh:
        json.dump(previous_tree, fh)

    async def fake_refine_tree(**kwargs):
        kwargs["module_tree"]["Top"]["_doc_filename"] = "top_new.md"
        return kwargs["module_tree"]

    monkeypatch.setattr(
        "codewiki.src.be.stages.tree_refinement.refine_tree",
        fake_refine_tree,
    )

    stage = TreeRefinementStage()
    await stage.execute(ctx)

    assert ctx.rename_map == {"top_old.md": "top_new.md"}
