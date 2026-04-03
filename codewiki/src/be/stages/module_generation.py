from __future__ import annotations

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
        if summary.total > 0 and len(summary.failed) == summary.total:
            raise RuntimeError("all module generation tasks failed")
        if summary.failed or summary.skipped:
            ctx.result.add_warning(
                f"{len(summary.failed)} modules failed, {len(summary.skipped)} modules skipped"
            )
