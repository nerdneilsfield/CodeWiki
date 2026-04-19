"""Tree refinement stage that freezes the module tree before doc generation."""

from __future__ import annotations

import hashlib
import json
import logging
import os

from codewiki.src.be.incremental import plan_invalidations
from codewiki.src.be.orphan_cleanup import cleanup_internal_artifacts
from codewiki.src.be.parent_segments import force_invalidate_parent_segments
from codewiki.src.be.pipeline import PipelineContext
from codewiki.src.be.component_hash_registry import load_component_hashes, save_component_hashes
from codewiki.src.be.tree_refiner import refine_tree
from codewiki.src.config import MODULE_TREE_FILENAME
from codewiki.src.utils import file_manager

logger = logging.getLogger(__name__)


class TreeRefinementStage:
    name = "TreeRefinementStage"
    failure_policy = "fail_fast"

    async def execute(self, ctx: PipelineContext) -> None:
        if not ctx.module_tree:
            logger.info("TreeRefinementStage: empty module tree, skipping")
            return
        if ctx.cache_manager is None:
            raise RuntimeError("TreeRefinementStage requires ctx.cache_manager")
        middleware = getattr(ctx.generator, "middleware", None) if ctx.generator else None
        if middleware is None:
            raise RuntimeError("TreeRefinementStage requires ctx.generator.middleware")
        cache_dir = ctx.cache_manager.cache_dir
        module_tree_path = os.path.join(ctx.working_dir, MODULE_TREE_FILENAME)
        previous_tree: dict = {}
        if os.path.exists(module_tree_path):
            try:
                previous_tree = file_manager.load_json(module_tree_path) or {}
            except Exception:
                logger.warning("Failed to load previous module_tree.json for incremental diffing")
                previous_tree = {}
        old_component_hashes = load_component_hashes(cache_dir)

        await refine_tree(
            module_tree=ctx.module_tree,
            components=ctx.components,
            refinement_cfg=ctx.config.refinement,
            output_language=ctx.config.output_language,
            cluster_model=ctx.config.cluster_model,
            middleware=middleware,
            cache_manager=ctx.cache_manager,
            cache_dir=cache_dir,
        )

        new_component_hashes = {
            component_id: hashlib.sha256(
                (getattr(component, "source_code", "") or "").encode("utf-8")
            ).hexdigest()
            for component_id, component in ctx.components.items()
        }
        invalidations = plan_invalidations(
            new_tree=ctx.module_tree,
            previous_tree=previous_tree,
            new_component_hashes=new_component_hashes,
            old_component_hashes=old_component_hashes,
            leaf_threshold=ctx.config.incremental.leaf_rerun_threshold,
            parent_threshold=ctx.config.incremental.parent_rerun_threshold,
        )
        if invalidations:
            ctx.cache_manager.invalidate_downstream(invalidations)

            def _walk_for_segments(subtree: dict) -> None:
                for node in subtree.values():
                    module_id = node.get("module_id")
                    if (
                        module_id
                        and f"module:{module_id}" in invalidations
                        and (node.get("children") or {})
                    ):
                        force_invalidate_parent_segments(
                            parent_doc_id=module_id,
                            parent_node=node,
                            cache_manager=ctx.cache_manager,
                        )
                    _walk_for_segments(node.get("children") or {})

            _walk_for_segments(ctx.module_tree)

        def _all_nodes(subtree: dict):
            for node in subtree.values():
                yield node
                yield from _all_nodes(node.get("children") or {})

        new_by_id = {
            node.get("module_id"): node
            for node in _all_nodes(ctx.module_tree)
            if node.get("module_id")
        }
        rename_map: dict[str, str] = {}
        for prev_node in _all_nodes(previous_tree):
            module_id = prev_node.get("module_id")
            if not module_id:
                continue
            new_node = new_by_id.get(module_id)
            if not new_node:
                continue
            old_filename = prev_node.get("_doc_filename")
            new_filename = new_node.get("_doc_filename")
            if old_filename and new_filename and old_filename != new_filename:
                rename_map[str(old_filename)] = str(new_filename)
        ctx.rename_map = rename_map

        file_manager.save_json(ctx.module_tree, module_tree_path)
        save_component_hashes(cache_dir, new_component_hashes)
        cleanup_internal_artifacts(cache_dir, ctx.cache_manager)
