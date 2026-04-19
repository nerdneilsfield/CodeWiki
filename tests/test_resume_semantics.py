from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from codewiki.src.be.cache_manager import CacheManager
from codewiki.src.be.documentation_tree_utils import compute_module_input_hash
from codewiki.src.be.documentation_scheduler import run_module_queue
from codewiki.src.be.parent_segments import (
    compute_assembled_parent_input_hash,
    compute_child_segment_input_hash,
    compute_opening_input_hash,
    compute_overview_input_hash,
)


def test_running_entries_become_stale_on_load(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    registry = {
        "schema_version": "cache.v2",
        "metadata": {},
        "entries": {
            "module:foo": {
                "input_hash": "h",
                "status": "running",
                "output_path": "x",
                "output_file": "foo.md",
                "model": "m",
                "attempt_count": 1,
                "error": "",
                "updated_at": "",
                "depends_on": [],
            }
        },
    }
    with open(cache_dir / "cache_registry.json", "w", encoding="utf-8") as handle:
        json.dump(registry, handle)

    cache = CacheManager(str(cache_dir), flush_interval=60)
    entry = cache.get_entry("module:foo")
    assert entry is not None
    assert entry.status == "stale"


def test_resume_does_not_rerun_valid_parents(tmp_path):
    cache_dir = tmp_path / ".codewiki"
    cache_dir.mkdir()
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    cache = CacheManager(str(cache_dir), flush_interval=60)

    tree = {
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
                    "_doc_filename": "left.md",
                    "components": ["a"],
                    "children": {},
                },
                "Right": {
                    "module_id": "right",
                    "title": "Right",
                    "path": "right",
                    "description": ".",
                    "_doc_filename": "right.md",
                    "components": ["b"],
                    "children": {},
                },
            },
        }
    }
    components = {
        "a": SimpleNamespace(source_code="a", depends_on=set()),
        "b": SimpleNamespace(source_code="b", depends_on=set()),
    }
    config = SimpleNamespace(
        max_concurrent=2,
        output_language="en",
        cluster_model="cluster",
        get_prompt_addition=lambda: "",
    )

    leaf_hashes = {}
    for artifact_id, output_file, module_name, module_info, module_path in [
        ("module:left", "left.md", "Left", tree["Top"]["children"]["Left"], ["Top", "Left"]),
        ("module:right", "right.md", "Right", tree["Top"]["children"]["Right"], ["Top", "Right"]),
    ]:
        output_path = docs_dir / output_file
        output_path.write_text(output_file, encoding="utf-8")
        input_hash = compute_module_input_hash(
            module_name,
            module_path,
            module_info,
            components,
            config,
            assigned_file=output_file,
        )
        cache.plan_task(artifact_id, output_file=output_file)
        cache.mark_done(
            artifact_id,
            input_hash=input_hash,
            output_path=str(output_path),
            model="m",
            output_file=output_file,
        )
        leaf_hashes[artifact_id] = input_hash

    opening_hash = compute_opening_input_hash(
        title="Top",
        path="top",
        description=".",
        output_language="en",
    )
    overview_hash = compute_overview_input_hash(
        title="Top",
        path="top",
        description=".",
        direct_child_pairs=[
            ("module:left", leaf_hashes["module:left"]),
            ("module:right", leaf_hashes["module:right"]),
        ],
        output_language="en",
    )
    child_hashes = [
        compute_child_segment_input_hash(
            child_module_id="module:left",
            child_title="Left",
            child_path="left",
            child_description=".",
            child_input_hash=leaf_hashes["module:left"],
            output_language="en",
        ),
        compute_child_segment_input_hash(
            child_module_id="module:right",
            child_title="Right",
            child_path="right",
            child_description=".",
            child_input_hash=leaf_hashes["module:right"],
            output_language="en",
        ),
    ]
    parent_hash = compute_assembled_parent_input_hash(
        opening_hash=opening_hash,
        overview_hash=overview_hash,
        child_segment_hashes=child_hashes,
        output_language="en",
    )
    top_path = docs_dir / "top.md"
    top_path.write_text("top", encoding="utf-8")
    cache.plan_task("module:top", output_file="top.md")
    cache.mark_done(
        "module:top",
        input_hash=parent_hash,
        output_path=str(top_path),
        model="cluster",
        output_file="top.md",
    )

    process_count = {"leaf": 0, "parent": 0}

    async def fake_process(*_args, **_kwargs):
        process_count["leaf"] += 1
        return {}, "m"

    async def fake_parent(**_kwargs):
        process_count["parent"] += 1
        return SimpleNamespace(output_path=str(top_path), input_hash=parent_hash, model="cluster")

    import codewiki.src.be.documentation_scheduler as scheduler_mod

    async def _run():
        original_parent = scheduler_mod.generate_or_assemble_parent_doc
        scheduler_mod.generate_or_assemble_parent_doc = fake_parent
        try:
            await run_module_queue(
                config=config,
                graph_tree=tree,
                components=components,
                working_dir=str(docs_dir),
                tree_manager=None,
                process_module=fake_process,
                cache_manager=cache,
                include_root=False,
                middleware=SimpleNamespace(call=lambda *args, **kwargs: None),
            )
        finally:
            scheduler_mod.generate_or_assemble_parent_doc = original_parent

    asyncio.run(_run())

    assert process_count == {"leaf": 0, "parent": 0}
