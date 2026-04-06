from __future__ import annotations

from codewiki.src.be.pipeline import PipelineContext


class StateInitStage:
    name = "StateInitStage"
    failure_policy = "fail_fast"

    async def execute(self, ctx: PipelineContext) -> None:
        await ctx.generator._initialize_cache_from_tree(
            ctx.module_tree,
            ctx.working_dir,
        )
