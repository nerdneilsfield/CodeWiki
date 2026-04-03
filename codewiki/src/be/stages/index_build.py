from __future__ import annotations

from codewiki.src.be.pipeline import PipelineContext


class IndexBuildStage:
    name = "IndexBuildStage"
    failure_policy = "degraded_ok"

    async def execute(self, ctx: PipelineContext) -> None:
        await ctx.generator._build_index(ctx)
