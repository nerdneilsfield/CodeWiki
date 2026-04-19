from __future__ import annotations

import os

from codewiki.src.be.orphan_cleanup import cleanup_renamed_user_visible, update_mtime_stamps
from codewiki.src.be.pipeline import PipelineContext


class ModuleGenerationStage:
    name = "ModuleGenerationStage"
    failure_policy = "fail_fast"

    async def execute(self, ctx: PipelineContext) -> None:
        working_dir, summary = await ctx.generator._generate_docs_from_tree(
            ctx.components,
            ctx.leaf_nodes,
            ctx.working_dir,
            ctx.module_tree,
        )
        ctx.working_dir = working_dir
        ctx.result.module_summary = summary

        rename_result = cleanup_renamed_user_visible(
            working_dir=working_dir,
            rename_map=ctx.rename_map,
        )
        if rename_result["warned"]:
            ctx.result.add_warning(
                "Left user-modified renamed docs in place: "
                + ", ".join(sorted(rename_result["warned"]))
            )

        filenames: list[str] = []

        def _collect_filenames(subtree: dict) -> None:
            for node in subtree.values():
                doc_filename = node.get("_doc_filename")
                if doc_filename:
                    filenames.append(doc_filename)
                _collect_filenames(node.get("children") or {})

        _collect_filenames(ctx.module_tree)
        overview_path = os.path.join(working_dir, "overview.md")
        if os.path.exists(overview_path):
            filenames.append("overview.md")
        update_mtime_stamps(working_dir, filenames)

        if summary.total > 0 and len(summary.failed) == summary.total:
            raise RuntimeError("all module generation tasks failed")
        if summary.failed or summary.skipped:
            ctx.result.add_warning(
                f"{len(summary.failed)} modules failed, {len(summary.skipped)} modules skipped"
            )
